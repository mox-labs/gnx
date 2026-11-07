"""ModelRuntime behaviour: tagging, structured output with repair, the contract check."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any, Literal

import pytest
from hardline_helpers import DictSecrets, mock_runtime
from pydantic import BaseModel

from hardline import (
    CallRecord,
    Completion,
    ConfigError,
    ContractError,
    Endpoint,
    Message,
    ModelRegistry,
    ModelRuntime,
    ModelSpec,
    RawCompletion,
    Request,
    RequestParams,
    SchemaError,
    build_runtime,
)
from hardline.adapters._out.mock import MockBackend
from hardline.adapters._out.yaml_source import YamlConfigSource

if TYPE_CHECKING:
    from pathlib import Path


class Verdict(BaseModel):
    supported: bool
    reason: str


async def test_completion_carries_family_usage_and_provenance() -> None:
    rt = mock_runtime(
        {"q": {"backend": "mock", "model": "qwen-8b", "family": "qwen", "local": True}}
    )
    c = await rt.complete("q", "hello there")
    assert c.text == "mock:qwen-8b:hello there"
    assert (c.family, c.provider, c.local) == ("qwen", "mock", True)
    assert (c.record.backend, c.record.attempts) == ("mock", 1)
    assert c.usage.input_tokens == 2 and c.usage.output_tokens == 2  # "mock:qwen-8b:hello there"
    assert c.latency_ms >= 0 and c.request_id


async def test_the_reply_names_who_served_it() -> None:
    # HL-2 (review D3, F-3). The row knew its provider and hardline's telemetry reported it, but
    # the reply did not carry it, so a caller asked hardline's registry again and wrote the
    # "else the backend's name" rule a second time.
    rt = mock_runtime(
        {
            "a": {
                "backend": "mock",
                "model": "m",
                "family": "x",
                "provider": "ollama",
                "retries": 0,
                "fallbacks": ["b"],
            },
            "b": {"backend": "mock", "model": "m", "family": "y"},
            "down": {
                "backend": "mock",
                "model": "m",
                "family": "x",
                "provider": "ollama",
                "retries": 0,
                "fallbacks": ["b"],
                "options": {"script": [{"error": "unavailable"}]},
            },
        }
    )
    assert (await rt.complete("a", "hi")).provider == "ollama"
    assert (await rt.complete("b", "hi")).provider == "mock"  # no provider: the backend's name
    fell_back = await rt.complete("down", "hi")
    assert (fell_back.name, fell_back.provider) == ("b", "mock")  # the row that answered


async def test_default_model_used_when_none_named() -> None:
    rt = mock_runtime({"q": {"backend": "mock", "model": "m", "family": "qwen"}}, default="q")
    assert (await rt.complete(None, "x")).name == "q"


async def test_a_backend_is_told_where_and_how_to_send_and_nothing_of_the_runtimes_policy() -> None:
    # The whole registry row crossed the port, so every backend saw the runtime's retry,
    # fallback and limit policy, the row's family, and where its key lives: a backend could
    # come to depend on any of them. It is told what one attempt needs, and handed the key.
    told: list[Endpoint] = []

    class Spy(MockBackend):
        def check(self, spec: Endpoint) -> list[str]:
            told.append(spec)
            return super().check(spec)

        async def complete(
            self, spec: Endpoint, request: Request, api_key: str | None
        ) -> RawCompletion:
            told.append(spec)
            assert api_key == "k"
            return await super().complete(spec, request, api_key)

    row = {"backend": "mock", "model": "m", "family": "qwen", "api_key": "env:K"}
    rt = build_runtime(
        {
            "models": {
                "a": {**row, "retries": 3, "fallbacks": ["b"], "limits": {"attempts": 9}},
                "b": row,
            }
        },
        backends={"mock": Spy()},
        secrets=DictSecrets({"env:K": "k"}),
    )
    await rt.complete("a", "hi")
    assert told  # checked at composition, then called
    for endpoint in told:
        assert set(type(endpoint).model_fields) == {
            "name",
            "backend",
            "model",
            "provider",
            "base_url",
            "proxy",
            "timeout_s",
            "structured",
            "options",
            "has_key",
        }
        assert endpoint.has_key is True


async def test_system_message_is_prepended() -> None:
    seen: list[tuple[str, ...]] = []

    class Spy(MockBackend):
        async def complete(self, spec, request, api_key):  # type: ignore[no-untyped-def]
            seen.append(tuple(m.role for m in request.messages))
            return await super().complete(spec, request, api_key)

    rt = build_runtime(
        {"models": {"q": {"backend": "spy", "model": "m", "family": "qwen"}}},
        backends={"spy": Spy()},
        secrets=DictSecrets(),
    )
    await rt.complete("q", [Message(role="user", content="a")], system="be brief")
    assert seen == [("system", "user")]


async def test_extract_validates_on_first_attempt() -> None:
    rt = mock_runtime(
        {
            "q": {
                "backend": "mock",
                "model": "m",
                "family": "qwen",
                "options": {"default": '```json\n{"supported": true, "reason": "quoted"}\n```'},
            }
        }
    )
    s = await rt.extract("q", "is it supported?", Verdict)
    assert s.value == Verdict(supported=True, reason="quoted")
    assert s.completion.record.attempts == 1


@pytest.mark.parametrize(
    "reply",
    [
        'Answer [1] below: {"supported": true, "reason": "r"}',
        'Use the {reason} field: {"supported": true, "reason": "r"}',
    ],
)
async def test_extract_finds_the_answer_after_bracketed_prose(reply: str) -> None:
    # The first bracketed span was taken as the answer and the valid one discarded, which
    # cost a repair turn, or a SchemaError with no repairs allowed.
    row = {"backend": "mock", "model": "m", "family": "qwen", "schema_retries": 0}
    rt = mock_runtime({"q": {**row, "options": {"default": reply}}})
    s = await rt.extract("q", "?", Verdict)
    assert s.value == Verdict(supported=True, reason="r") and s.completion.record.attempts == 1


class Reported(MockBackend):
    """The mock, keeping every RawCompletion it returned and every request it was sent."""

    def __init__(self) -> None:
        super().__init__()
        self.returned: list[RawCompletion] = []
        self.sent: list[Request] = []

    async def complete(
        self, spec: Endpoint, request: Request, api_key: str | None
    ) -> RawCompletion:
        self.sent.append(request)
        raw = await super().complete(spec, request, api_key)
        self.returned.append(raw)
        return raw


def _reported(models: dict[str, Any], **kw: Any) -> tuple[ModelRuntime, Reported]:
    backend = Reported()
    rt = build_runtime({"models": models}, backends={"mock": backend}, secrets=DictSecrets(), **kw)
    return rt, backend


async def test_planted_schema_failure_is_repaired_once() -> None:
    rt, backend = _reported(
        {
            "q": {
                "backend": "mock",
                "model": "m",
                "family": "qwen",
                "options": {
                    "script": [
                        "Sure! It is supported.",
                        '{"supported": false, "reason": "no quote"}',
                    ]
                },
            }
        }
    )
    s = await rt.extract("q", "?", Verdict)
    assert s.value.supported is False
    assert s.completion.record.attempts == 2
    # This asserted only `> 0`, which the answering attempt alone satisfies. Usage is the
    # sum of what both calls reported.
    first, second = backend.returned
    assert s.completion.usage == first.usage + second.usage
    assert s.completion.usage.output_tokens == 4 + 5  # "Sure! It is supported." + the object


async def test_schema_failure_after_retries_raises_with_evidence() -> None:
    rt = mock_runtime(
        {
            "q": {
                "backend": "mock",
                "model": "m",
                "family": "qwen",
                "options": {"script": ['{"supported": "maybe"}']},
            }
        }
    )
    with pytest.raises(SchemaError) as e:
        await rt.extract("q", "?", Verdict)
    assert e.value.attempts == 2
    assert e.value.last_text == '{"supported": "maybe"}'
    assert "reason" in e.value.validation


@pytest.mark.parametrize(("repairs", "attempts"), [(0, 1), (3, 4)])
async def test_the_rows_schema_retries_bounds_the_repairs(repairs: int, attempts: int) -> None:
    # HL-4 (review D5, F-5). The repair count was a `build_runtime` argument that no row,
    # digest or record named, so every extract matrix made got one repair no document showed.
    # It is the row's, and the row asked for sets it, as it sets the call's `limits`.
    row = {"backend": "mock", "model": "m", "family": "qwen", "options": {"script": ["nope"]}}
    rt = mock_runtime({"q": {**row, "schema_retries": repairs}})
    with pytest.raises(SchemaError) as e:
        await rt.extract("q", "?", Verdict)
    assert e.value.attempts == attempts


async def test_repair_turn_shows_the_model_its_own_reply_and_the_error() -> None:
    turns: list[list[tuple[str, str]]] = []

    class Spy(MockBackend):
        async def complete(self, spec, request, api_key):  # type: ignore[no-untyped-def]
            turns.append([(m.role, m.content) for m in request.messages])
            return await super().complete(spec, request, api_key)

    rt = build_runtime(
        {
            "models": {
                "q": {
                    "backend": "spy",
                    "model": "m",
                    "family": "qwen",
                    "options": {"script": ["not json", '{"supported": true, "reason": "r"}']},
                }
            }
        },
        backends={"spy": Spy()},
        secrets=DictSecrets(),
    )
    await rt.extract("q", "?", Verdict)
    second = turns[1]
    assert second[-2] == ("assistant", "not json")
    assert second[-1][0] == "user" and "did not validate" in second[-1][1]


async def test_backend_returning_wrong_shape_is_a_contract_error() -> None:
    class Liar:
        structured_modes = frozenset({"prompt"})

        async def complete(self, spec: Any, request: Any, api_key: Any) -> Any:
            return {"text": "a dict, not RawCompletion"}

    rt = ModelRuntime(
        ModelRegistry.from_config(
            {"models": {"q": {"backend": "liar", "model": "m", "family": "x"}}}
        ),
        {"liar": Liar()},
        DictSecrets(),
    )
    with pytest.raises(ContractError, match="not RawCompletion"):
        await rt.complete("q", "hi")


async def test_secret_reference_is_resolved_and_handed_to_backend() -> None:
    got: list[str | None] = []

    class Spy(MockBackend):
        async def complete(self, spec, request, api_key):  # type: ignore[no-untyped-def]
            got.append(api_key)
            return RawCompletion(text="ok", model=spec.model)

    secrets = DictSecrets({"env:KEY": "s3cret"})
    rt = build_runtime(
        {"models": {"q": {"backend": "spy", "model": "m", "family": "x", "api_key": "env:KEY"}}},
        backends={"spy": Spy()},
        secrets=secrets,
    )
    await rt.complete("q", "hi")
    assert got == ["s3cret"] and secrets.asked == ["env:KEY"]


async def test_call_overrides_beat_row_defaults() -> None:
    seen: list[tuple[int, float | None]] = []

    class Spy(MockBackend):
        async def complete(self, spec, request, api_key):  # type: ignore[no-untyped-def]
            seen.append((request.max_tokens, request.temperature))
            return await super().complete(spec, request, api_key)

    rt = build_runtime(
        {
            "models": {
                "q": {
                    "backend": "spy",
                    "model": "m",
                    "family": "x",
                    "max_tokens": 50,
                    "temperature": 0.2,
                }
            }
        },
        backends={"spy": Spy()},
        secrets=DictSecrets(),
    )
    await rt.complete("q", "a")
    await rt.complete("q", "a", max_tokens=7, temperature=0.0)
    assert seen == [(50, 0.2), (7, 0.0)]


# --- the error contract: everything raised on purpose is a HardlineError -------


@pytest.mark.parametrize(
    ("call", "match"),
    [
        ({"messages": []}, "at least one message"),
        ({"max_tokens": 0}, "max_tokens"),
        ({"temperature": -1.0}, "temperature"),
        ({"temperature": float("nan")}, "temperature"),
    ],
)
async def test_a_bad_call_argument_is_a_config_error(call: dict[str, Any], match: str) -> None:
    # Before: a bare ValueError (no messages), a pydantic ValidationError (max_tokens=0), and a
    # negative temperature sent to the provider. None of them is caught by `except HardlineError`.
    rt = mock_runtime({"q": {"backend": "mock", "model": "m", "family": "x"}})
    kwargs: dict[str, Any] = {"messages": "hi", **call}
    with pytest.raises(ConfigError, match=match):
        await rt.complete("q", **kwargs)
    with pytest.raises(ConfigError, match=match):
        await rt.extract("q", output=Verdict, **kwargs)


def test_a_negative_schema_retries_is_a_config_error() -> None:
    # A row key now (HL-4): refused at composition, naming the key, like any bad row value.
    with pytest.raises(ConfigError) as e:
        mock_runtime({"q": {"backend": "mock", "model": "m", "family": "x", "schema_retries": -1}})
    assert [p.path for p in e.value.problems] == ["models.q.schema_retries"]


# --- the promise for `complete`: a reply that is not a whole answer never looks like one --


@pytest.mark.parametrize(
    ("options", "answered"),
    [
        ({"default": "an answer"}, True),
        ({"default": "an answ", "finish_reason": "length"}, False),
        ({"default": "", "finish_reason": "stop"}, False),
        ({"default": "", "refusal": "I can't help with that."}, False),
        ({"default": "filtered", "finish_reason": "content_filter"}, False),
    ],
)
async def test_a_completion_says_whether_it_is_a_whole_answer(
    options: dict[str, Any], answered: bool
) -> None:
    rt = mock_runtime({"q": {"backend": "mock", "model": "m", "family": "x", "options": options}})
    c = await rt.complete("q", "hi")
    assert c.answered is answered
    assert c.finish_reason == (
        "refusal" if "refusal" in options else options.get("finish_reason", "stop")
    )
    assert c.refusal == options.get("refusal")


async def test_answered_is_in_the_json_and_cannot_contradict_the_reply() -> None:
    # HO8 (review F-9). `answered` was a property, so `complete --json` left it out and the skill
    # taught an agent to recompute it; one that got the rule slightly wrong took a cut reply for
    # an answer. It is in the JSON, and JSON that claims otherwise than its own fields is refused.
    from pydantic import ValidationError

    rt = mock_runtime(
        {
            "q": {
                "backend": "mock",
                "model": "m",
                "family": "x",
                "options": {"finish_reason": "length"},
            }
        }
    )
    c = await rt.complete("q", "hi")
    dumped = c.model_dump(mode="json")
    assert dumped["answered"] is False
    assert Completion.model_validate(dumped) == c
    with pytest.raises(ValidationError, match="answered"):
        Completion.model_validate({**dumped, "answered": True})


# --- the call's whole record -------------------------------------------------------


def _sha256(turns: tuple[Message, ...]) -> str:
    """The documented turns digest, computed here independently of hardline's own."""
    pairs = [[m.role, m.content] for m in turns]
    encoded = json.dumps(pairs, separators=(",", ":"), ensure_ascii=False).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


async def test_extract_records_the_turns_actually_sent_repair_turns_included() -> None:
    rt, backend = _reported(
        {
            "q": {
                "backend": "mock",
                "model": "m",
                "family": "x",
                "options": {"script": ["not json", '{"supported": true, "reason": "r"}']},
            }
        }
    )
    s = await rt.extract("q", "?", Verdict, system="be strict")
    answered = backend.sent[-1].messages
    assert [m.role for m in answered] == ["system", "user", "user", "assistant", "user"]
    assert s.completion.record.turns_digest == _sha256(answered)


async def test_complete_records_the_turns_it_sent() -> None:
    rt, backend = _reported({"q": {"backend": "mock", "model": "m", "family": "x"}})
    c = await rt.complete("q", "hi", system="s")
    assert c.record.turns_digest == _sha256(backend.sent[-1].messages)


async def test_the_record_holds_the_params_the_answering_row_was_sent() -> None:
    # The caller passed nothing; the answering row's defaults were sent, and only hardline knows.
    rt = mock_runtime(
        {
            "a": {
                "backend": "mock",
                "model": "m",
                "family": "x",
                "retries": 0,
                "fallbacks": ["b"],
                "options": {"script": [{"error": "unavailable"}]},
            },
            "b": {
                "backend": "mock",
                "model": "m",
                "family": "y",
                "max_tokens": 77,
                "temperature": 0.3,
            },
        }
    )
    c = await rt.complete("a", "hi")
    assert c.record.params == RequestParams(max_tokens=77, temperature=0.3, structured=None)


async def test_an_unset_temperature_is_recorded_as_the_providers_default() -> None:
    # HO6. Neither the call nor the row set a temperature, so none was sent and the provider's
    # own default applied: a value hardline cannot know. The record says that, not a bare null
    # a reader could take for "zero" or "unrecorded".
    rt = mock_runtime({"q": {"backend": "mock", "model": "m", "family": "x"}})
    c = await rt.complete("q", "hi")
    assert c.record.params.temperature == "provider_default"
    assert c.record.params.model_dump(mode="json")["temperature"] == "provider_default"
    assert (await rt.complete("q", "hi", temperature=0.0)).record.params.temperature == 0.0


@pytest.mark.parametrize("mode", ["prompt", "json_object", "json_schema"])
async def test_the_record_holds_the_structured_mode_used(
    mode: Literal["prompt", "json_object", "json_schema"],
) -> None:
    rt = mock_runtime(
        {
            "q": {
                "backend": "mock",
                "model": "m",
                "family": "x",
                "structured": mode,
                "options": {"default": '{"supported": true, "reason": "r"}'},
            }
        }
    )
    s = await rt.extract("q", "?", Verdict, max_tokens=9, temperature=0.0)
    assert s.completion.record.params == RequestParams(
        max_tokens=9, temperature=0.0, structured=mode
    )


async def test_the_record_names_the_row_its_digest_and_the_tier_of_each_field(
    tmp_path: Path,
) -> None:
    user = tmp_path / "user.yaml"
    user.write_text("models:\n  q: {backend: mock, model: m, family: x, max_tokens: 10}\n")
    project = tmp_path / "proj.yaml"
    project.write_text("models:\n  q: {max_tokens: 20}\n")
    rt = build_runtime(
        sources=[YamlConfigSource(user, rung="user"), YamlConfigSource(project, rung="cwd")],
        backends={"mock": MockBackend()},
    )
    c = await rt.complete("q", "hi")
    # Requirement changed in 0.3 (HO5, review D5): the rungs named only the fields a tier set,
    # and left the rest to be inferred ("a field not named kept the model's default"). They
    # name every field, `default` for each no tier set.
    assert c.record.row.rungs == {
        **_DEFAULTED,
        "backend": f"user {user}",
        "model": f"user {user}",
        "family": f"user {user}",
        "max_tokens": f"cwd {project}",
    }
    spec = rt.registry.get("q")
    canonical = json.dumps(spec.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    assert c.record.row.digest == "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


#: A row's rungs for the fields no tier set: every field of a row but its name, which its key is.
_DEFAULTED = {field: "default" for field in ModelSpec.model_fields if field != "name"}


def test_the_rungs_name_every_field_of_the_row() -> None:
    # HO5: a default is shown, never implied. Pinned against the row's own fields, so a field
    # added to ModelSpec is named in every record without this test or the registry changing.
    rt = mock_runtime({"q": {"backend": "mock", "model": "m", "family": "x"}})
    rungs = rt.registry.record("q").rungs
    assert set(rungs) == set(ModelSpec.model_fields) - {"name"}
    assert {field for field, rung in rungs.items() if rung == "default"} == set(rungs) - {
        "backend",
        "model",
        "family",
    }
    assert rt.registry.get("q").schema_retries == 1  # the repair count, now a row value


async def test_a_default_location_is_recorded_as_the_backends() -> None:
    from hardline.adapters._out.openai_compat import OpenAICompatBackend

    rt = build_runtime(
        {"models": {"o": {"backend": "openai-compat", "model": "m", "family": "x"}}},
        backends={"openai-compat": OpenAICompatBackend()},
        label="matrix.models",
    )
    assert rt.registry.record("o").rungs == {
        **_DEFAULTED,
        "backend": "matrix.models",
        "model": "matrix.models",
        "family": "matrix.models",
        "base_url": "default of backend openai-compat",
    }


# --- who upgrades first -------------------------------------------------------


async def test_a_completion_reads_back_its_own_json_and_refuses_keys_it_does_not_know() -> None:
    # Completion is extra="forbid": its JSON round-trips, and a reader refuses a key a newer
    # hardline added. So the reader of stored Completions upgrades before any writer does, as
    # the Completion docstring states.
    from pydantic import ValidationError

    rt = mock_runtime({"q": {"backend": "mock", "model": "m", "family": "x"}})
    c = await rt.complete("q", "hi")
    assert Completion.model_validate_json(c.model_dump_json()) == c
    with pytest.raises(ValidationError, match="added_later"):
        Completion.model_validate({**c.model_dump(mode="json"), "added_later": 1})
    assert "upgrades first" in (Completion.__doc__ or "")


async def test_a_call_record_ignores_keys_a_later_minor_adds_and_refuses_another_major() -> None:
    # Review D2: what hardline records of a call grows in `hardline.v1.call`, not in the reply,
    # and a reader of that document ignores the fields it does not know within its major and
    # refuses another major (MRC:c35). So a stored record a newer 0.x wrote still reads, and a
    # reader of stored Completions need not upgrade first for a field the record gained.
    from pydantic import ValidationError

    rt = mock_runtime({"q": {"backend": "mock", "model": "m", "family": "x"}})
    c = await rt.complete("q", "hi")
    dumped = c.record.model_dump(mode="json")
    newer = {
        **dumped,
        "added_later": 1,
        "params": {**dumped["params"], "added_later": 1},
        "row": {**dumped["row"], "added_later": 1},
        "attempt_log": [{**dumped["attempt_log"][0], "added_later": 1}],
    }
    assert CallRecord.model_validate(newer) == c.record
    assert Completion.model_validate({**c.model_dump(mode="json"), "record": newer}) == c
    with pytest.raises(ValidationError, match="type_url"):
        CallRecord.model_validate({**dumped, "type_url": "hardline.v2.call"})
    with pytest.raises(ValidationError, match="type_url"):
        Completion.model_validate(
            {**c.model_dump(mode="json"), "type_url": "hardline.v1/completion"}
        )
