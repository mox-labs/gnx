"""``matrix.v1.model-step``: one model call as an ordinary component.

The model counterpart of the agent step. It names a configured **model runtime**, not an
agent: a model call has no loop, no tools and no turns. What it requires and provides comes
from its config:

* it **requires** the kinds its ``inputs`` name, and renders their rows into the prompt;
* it **provides** the call's record, a :class:`ModelCall` carrying the prompt as sent, under
  the kind ``response`` names; and, when ``output`` names a kind, the reply as one row of that
  kind. When a package registered a schema for that kind, the runtime is asked for that
  structure and validates the reply against it (hardline repairs a reply that does not fit);
  otherwise the reply must be one JSON document. The executor validates the row against the
  kind's schema like any other.

Both the prompt's placeholders and the model name are checked when the step is built: a
typo, or a model the runtime cannot resolve, is a ``config`` problem at composition.

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

from matrix.domain.errors import ConfigError, ContractError, NotFoundError
from matrix.domain.ids import check_type_url
from matrix.domain.model_call import ModelRequest
from matrix.domain.template import check_template, json_document, render

if TYPE_CHECKING:
    from collections.abc import Mapping

    from matrix.domain.component import Inputs, RunContext
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
        prompt = self.render(inputs)
        request = ModelRequest(
            prompt=prompt, model=self._config.model, system=self._config.system, output=self._output
        )
        call = await self._runtime.call(request)
        call = call.model_copy(update={"prompt": prompt})
        rows: dict[str, Any] = {self._config.response: call}
        if self._config.output is not None:
            # One row, whatever its shape: a JSON array the model returned is not several.
            reply = call.value if self._output is not None else self._json(call.text)
            rows[self._config.output] = (reply,)
        return rows

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
