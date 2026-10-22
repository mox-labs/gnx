"""``matrix.v1.model-step``: one model call as an ordinary component.

The model counterpart of the agent step. It names a configured **model runtime**, not an
agent: a model call has no loop, no tools and no turns. What it requires and provides comes
from its config:

* it **requires** the kinds its ``inputs`` name, and renders their rows into the prompt;
* it **provides** the call's reply, a :class:`ModelCall` carrying the prompt as sent and
  pointing at the call's row in ``matrix.v1.session``, which it records, under the kind
  ``response`` names; and, when ``output`` names a kind, the reply as one row of that
  kind. When a package registered a schema for that kind, the runtime is asked for that
  structure and validates the reply against it (hardline repairs a reply that does not fit);
  otherwise the reply must be one JSON document. The executor validates the row against the
  kind's schema like any other.

**A reply that is not a whole answer fails the step.** A reply cut at ``max_tokens``, refused,
withheld by the provider, ended to ask for a tool, ended for a reason no one named, or empty is
never provided: a component that requires the kind would take it as an answer, and nothing in
the wiring could say otherwise. The step raises :class:`ModelRuntimeError`, reason ``refused``
(a refusal, or the provider withholding the reply) or ``failed`` (the rest), naming the
``finish_reason``. A runtime that does not say why the reply ended (``finish_reason`` None)
is not taken to have cut it, and the row keeps the None.

Both the prompt's placeholders and the model name are checked when the step is built: a
typo, or a model the runtime cannot resolve, is a ``config`` problem at composition. A
runtime that holds connections (``aclose()``) has them closed when the run ends, in the run's
event loop, which made them.

Config::

    runtime: fast                         # a matrix.model_runtimes name
    model: haiku                          # a name the runtime resolves; unset, its default
    system: "You extract claims."         # optional
    inputs: {diff: acme.v1.diff}          # placeholder -> the kind it stands for
    prompt: "Claims in:\\n{diff}"          # {placeholder}s, checked against inputs when built
    response: acme.v1.claims-call         # default: matrix.v1.model-call; one per step
    output: acme.v1.claims                # optional: the reply, as one row of this kind
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from matrix.domain.errors import ConfigError, ContractError, ModelRuntimeError, NotFoundError
from matrix.domain.ids import check_type_url
from matrix.domain.model_call import ModelRequest
from matrix.domain.session import call_session, digest, record_session
from matrix.domain.template import check_template, json_document, render

if TYPE_CHECKING:
    from collections.abc import Mapping

    from matrix.domain.component import Inputs, RunContext
    from matrix.domain.model_call import ModelCall
    from matrix.domain.ports._out.model_runtime import ModelRuntime

MODEL_STEP = "matrix.v1.model-step"
MODEL_CALL = "matrix.v1.model-call"
PROMPT = "matrix.v1.prompt"


class ModelStepConfig(BaseModel):
    """A model step: one call on `runtime` (a configured model runtime) to `model` (a name it
    resolves; unset, its default), with `prompt`, a template whose {placeholders} `inputs`
    fill with the rows of the kinds they name, and an optional `system` prompt. `response` is
    the kind its call record is provided as; a kind has one provider, so each model step in a
    run names its own (the default, matrix.v1.model-call, serves one). `output`, when set, is
    a kind for the reply itself: asked for as that kind's registered schema, or else read as
    one JSON document."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    runtime: str
    model: str | None = None
    system: str | None = None
    #: placeholder name -> the kind whose rows fill it
    inputs: dict[str, str] = {"prompt": PROMPT}
    #: ``None``: a single input's value is the prompt as it stands.
    prompt: str | None = None
    #: The kind the call's :class:`ModelCall` is provided as: one per step in a run.
    response: str = MODEL_CALL
    #: The kind the reply is provided as.
    output: str | None = None


class ModelStep:
    """Makes one model call with the rows it requires rendered into the prompt."""

    def __init__(
        self,
        config: ModelStepConfig,
        *,
        model_runtimes: Mapping[str, ModelRuntime],
        schemas: Mapping[str, type[BaseModel]] | None = None,
        runtime_url: str | None = None,
        drives: tuple[str, ...] = (),
    ) -> None:
        for placeholder, kind in config.inputs.items():
            check_type_url(kind, where=f"model-step inputs.{placeholder}")
        check_type_url(config.response, where="model-step response")
        if config.output is not None:
            check_type_url(config.output, where="model-step output")
        if config.prompt is None and len(config.inputs) != 1:
            raise ConfigError(
                f"model-step on {config.runtime!r} has {len(config.inputs)} inputs and no "
                "prompt template",
                fix="give a prompt template with a {placeholder} for each input",
            )
        if config.prompt is not None:
            check_template(
                config.prompt, config.inputs, where=f"model-step prompt on {config.runtime!r}"
            )
        runtime = model_runtimes.get(config.runtime)
        if runtime is None:
            raise NotFoundError(
                f"model-step: no model runtime named {config.runtime!r}. "
                f"Composed: {', '.join(sorted(model_runtimes)) or '(none)'}",
                fix="name a model runtime defined under matrix.model_runtimes",
            )
        runtime.check(config.model)  # an unknown model is refused now, not mid-run
        self._config = config
        self._runtime = runtime
        self._runtime_url = runtime_url
        self._drives = tuple(drives)
        self._output = (schemas or {}).get(config.output) if config.output is not None else None
        self.requires: frozenset[str] = frozenset(config.inputs.values())
        self.provides: frozenset[str] = frozenset(
            {config.response} | ({config.output} if config.output is not None else set())
        )

    def render(self, inputs: Mapping[str, tuple[Any, ...]]) -> str:
        """The prompt for these rows: one row as itself, several as a JSON list."""
        where = f"model-step on {self._config.runtime!r}"
        return render(self._config.prompt, self._config.inputs, inputs, where=where)

    async def run(self, inputs: Inputs, ctx: RunContext) -> dict[str, Any]:
        close = getattr(self._runtime, "aclose", None)
        if callable(close):  # the runtime's connections close in the loop that made them
            ctx.defer(close)
        prompt = self.render(inputs)
        request = ModelRequest(
            prompt=prompt, model=self._config.model, system=self._config.system, output=self._output
        )
        try:
            call = await self._runtime.call(request)
        except Exception as e:
            self._record(request, e.call if isinstance(e, ModelRuntimeError) else None, e)
            raise
        call = call.model_copy(update={"prompt": prompt, "session": self._record(request, call)})
        self._whole(call)
        rows: dict[str, Any] = {self._config.response: call.as_reply()}
        if self._config.output is not None:
            # One row, whatever its shape: a JSON array the model returned is not several.
            reply = call.value if self._output is not None else self._json(call.text)
            rows[self._config.output] = (reply,)
        return rows

    def _record(
        self, request: ModelRequest, call: ModelCall | None, error: Exception | None = None
    ) -> int | None:
        """Record the call in the run's session table, whether its reply is whole, not whole,
        or it failed (``error``, with what the runtime knew of the call); its row, or None
        outside a run."""
        output = request.output
        sent = digest(
            {
                "prompt": request.prompt,
                "system": request.system,
                "model": request.model,
                "output": None if output is None else output.model_json_schema(),
            }
        )
        return record_session(
            call_session(
                call, sent=sent, runtime=self._runtime_url, drives=self._drives, error=error
            )
        )

    def _whole(self, call: ModelCall) -> None:
        """Raise unless ``call``'s reply is a whole answer (see the module docstring)."""
        reason = call.finish_reason
        if call.refusal is not None:
            case = "refusal"
        elif reason is not None and reason != "stop":
            case = reason
        elif not call.text:
            case = "empty"
        else:
            return
        why, fix = _NOT_WHOLE[case]
        if call.refusal is not None:
            why = f"{why}: {call.refusal.strip()[:_EXCERPT]!r}"
        elif case == "other":
            why = f"{why} ({call.native_finish_reason!r})"
        model = call.name or call.model or self._config.model or "(default)"
        raise ModelRuntimeError(
            f"the model step on {self._config.runtime!r}: model {model!r} gave no whole "
            f"answer: {why} (finish_reason {reason!r})",
            reason="refused" if case in ("refusal", "content_filter") else "failed",
            fix=fix,
            model=model,
            finish_reason=reason,
            native_finish_reason=call.native_finish_reason,
        )

    def _json(self, text: str) -> Any:
        kind = self._config.output
        try:
            return json_document(text)
        except LookupError:
            excerpt = text.strip()[:_EXCERPT] or "(empty)"
            raise ContractError(
                f"the model step on {self._config.runtime!r} was to reply with JSON for {kind} "
                f"(the step's `output`), but its reply holds no JSON document: {excerpt!r}",
                fix=f"ask for one JSON document for {kind} in the step's prompt or system "
                "prompt, or register a schema for the kind so the runtime asks for it; on the "
                "mock model runtime, make its reply that JSON; or drop `output`",
                table=kind,
            ) from None


#: How much of a reply a contract error quotes.
_EXCERPT = 200

#: Why a reply is not a whole answer, by finish reason, and what to do about it.
_NOT_WHOLE: dict[str, tuple[str, str]] = {
    "length": (
        "the reply was cut off at max_tokens",
        "raise max_tokens (the runtime's, or the model's registry row's), or ask for less",
    ),
    "refusal": (
        "the model refused",
        "change the prompt or the system prompt, or call another model",
    ),
    "content_filter": (
        "the provider withheld or cut the reply",
        "change the prompt or the system prompt, or call another model",
    ),
    "tool_call": (
        "the model asked for a tool, which a model call does not run",
        "use an agent step for work that needs tools, or tell the model to answer directly",
    ),
    "other": (
        "the provider ended the reply for a reason matrix does not name",
        "read the provider's own reason (native_finish_reason) and change the prompt or model",
    ),
    "empty": (
        "the reply is empty",
        "change the prompt or the system prompt so the model answers, or call another model",
    ),
}
