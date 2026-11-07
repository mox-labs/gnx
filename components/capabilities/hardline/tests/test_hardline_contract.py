"""hardline's published surfaces, pinned (CONTRACT.md; owner ruling G6).

A failure here is not a test to update to the code. It says a promised name or shape changed:
make the change an addition, or make it a break the CHANGELOG says under "Removed (breaking)"
or "Changed (breaking)", moving a document's type-URL major where its keys are removed or
renamed, and then update the snapshot.
"""

from __future__ import annotations

import inspect
from typing import Any

import hardline
from hardline import (
    CALL_TYPE_URL,
    COMPLETION_TYPE_URL,
    CallRecord,
    CheckedBackend,
    ClosableBackend,
    Completion,
    LocatedBackend,
    ModelBackend,
    ModelSpec,
    RawCompletion,
    ToolCallingBackend,
)


def _members(protocol: type) -> dict[str, str]:
    """Each member of ``protocol``, as its signature reads, a property as its return type."""
    found: dict[str, str] = {}
    for name in sorted(protocol.__protocol_attrs__):  # type: ignore[attr-defined]
        member = inspect.getattr_static(protocol, name)
        if isinstance(member, property):
            found[name] = f"property -> {inspect.signature(member.fget).return_annotation}"  # type: ignore[arg-type]
        else:
            found[name] = str(inspect.signature(member))
    return found


def test_the_reply_holds_what_any_runtime_can_say_and_the_record_what_only_hardline_knows() -> None:
    # HL-1 (review D2). Every fact of a call was a top-level field of the reply (14 to 22 in one
    # release), and a consumer copied each one. The reply holds what any model runtime can say;
    # how hardline made the call is its own document, versioned apart, where growth lands.
    # `raw`, a provider's untyped wire payload nobody read, is in neither.
    assert list(Completion.model_fields) == [
        "type_url",
        "name",
        "family",
        "model",
        "provider",
        "local",
        "text",
        "tool_calls",
        "finish_reason",
        "native_finish_reason",
        "refusal",
        "usage",
        "request_id",
        "latency_ms",
        "record",
    ]
    assert list(Completion.model_computed_fields) == ["answered"]
    assert list(CallRecord.model_fields) == [
        "type_url",
        "backend",
        "attempts",
        "retries",
        "fallback_from",
        "params",
        "attempt_log",
        "turns_digest",
        "row",
    ]
    assert "raw" not in RawCompletion.model_fields
    # Dotted, as matrix, ix and recon spell theirs, and hardline its error envelope. The reply's
    # major moved because 0.2 promised no key is removed while `hardline.v1/completion` holds.
    assert (COMPLETION_TYPE_URL, CALL_TYPE_URL) == ("hardline.v2.completion", "hardline.v1.call")


def test_the_backend_port_and_its_optional_protocols_are_as_promised() -> None:
    assert _members(ModelBackend) == {
        "complete": "(self, endpoint: 'Endpoint', request: 'Request', api_key: 'str | None') "
        "-> 'RawCompletion'",
        "structured_modes": "property -> frozenset[str]",
    }
    assert _members(CheckedBackend) == {"check": "(self, endpoint: 'Endpoint') -> 'Sequence[str]'"}
    assert _members(ClosableBackend) == {"aclose": "(self) -> 'None'"}
    assert _members(ToolCallingBackend) == {"supports_tools": "property -> Literal[True]"}
    assert _members(LocatedBackend) == {"default_base_url": "property -> str"}
    # Each is read with isinstance, so each must be runtime-checkable.
    for protocol in (
        ModelBackend,
        CheckedBackend,
        ClosableBackend,
        ToolCallingBackend,
        LocatedBackend,
    ):
        assert getattr(protocol, "_is_runtime_protocol", False), protocol.__name__


def test_the_registry_rows_keys_and_defaults_are_as_promised() -> None:
    # Config files in users' repositories name these keys: a removal or rename breaks them.
    def default(field: Any) -> object:
        return "required" if field.is_required() else field.get_default(call_default_factory=True)

    assert {name: default(field) for name, field in ModelSpec.model_fields.items()} == {
        "name": "required",
        "backend": "required",
        "model": "required",
        "family": "required",
        "provider": None,
        "base_url": None,
        "proxy": None,
        "api_key": None,
        "local": False,
        "structured": "prompt",
        "max_tokens": 1024,
        "temperature": None,
        "timeout_s": 120.0,
        "retries": 2,
        "schema_retries": 1,
        "fallbacks": (),
        "limits": hardline.CallLimits(),
        "options": {},
    }


def test_the_public_names_are_as_promised() -> None:
    assert sorted(hardline.__all__) == sorted(
        [
            "CALL_TYPE_URL",
            "COMPLETION_TYPE_URL",
            "RETRYABLE",
            "Answered",
            "Attempt",
            "AttemptFailed",
            "BackendError",
            "CallBound",
            "CallEvent",
            "CallLimits",
            "CallObserver",
            "CallRecord",
            "CheckedBackend",
            "ClosableBackend",
            "Completion",
            "ConfigError",
            "ConfigSource",
            "ContractError",
            "Endpoint",
            "FellBack",
            "FinishReason",
            "HardlineError",
            "LocatedBackend",
            "Message",
            "ModelBackend",
            "ModelRegistry",
            "ModelRuntime",
            "ModelSpec",
            "Problem",
            "RawCompletion",
            "Reason",
            "Request",
            "RequestParams",
            "RowRecord",
            "SchemaError",
            "SecretError",
            "SecretResolver",
            "Structured",
            "ToolCall",
            "ToolCallingBackend",
            "ToolSpec",
            "UnknownModelError",
            "Usage",
            "__version__",
            "answer_problems",
            "backend_problems",
            "build_runtime",
            "configure_telemetry",
            "discover_backends",
            "discover_sources",
            "get_skill",
            "load_registry",
        ]
    )
    assert all(hasattr(hardline, name) for name in hardline.__all__)
