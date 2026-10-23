"""The backend conformance helper: what a third-party author runs against an installed hardline.

The port's requirements were stated in a test in hardline's repository, which does not ship
in the wheel, and that test checked only that the members existed: not that ``complete`` is
async, not that it returns the port's shape, and it required a ``prompt`` mode no document
stated. ``hardline.backend_problems`` and ``hardline.answer_problems`` are those requirements,
shipped, and every installed backend is held to them here.
"""

from __future__ import annotations

from typing import Any

import httpx2
import pytest

from hardline import (
    Endpoint,
    ModelSpec,
    RawCompletion,
    Request,
    answer_problems,
    backend_problems,
    discover_backends,
)
from hardline.adapters._out.mock import MockBackend
from hardline.adapters._out.openai_compat import OpenAICompatBackend


@pytest.mark.parametrize("name", sorted(discover_backends()))
def test_every_installed_backend_conforms(name: str) -> None:
    assert backend_problems(discover_backends()[name]) == []


async def test_a_backend_that_answers_conforms_on_a_call() -> None:
    spec = ModelSpec(name="q", backend="mock", model="m", family="x")
    assert await answer_problems(MockBackend(), spec) == []

    def reply(request: httpx2.Request) -> httpx2.Response:
        message = {"role": "assistant", "content": "ok"}
        choice = {"index": 0, "finish_reason": "stop", "message": message}
        return httpx2.Response(200, json={"model": "m", "choices": [choice]})

    wire = OpenAICompatBackend(httpx2.MockTransport(reply))
    located = spec.model_copy(update={"backend": "openai-compat", "base_url": "http://x.test/v1"})
    assert await answer_problems(wire, located) == []


class _Good:
    structured_modes = frozenset({"prompt"})

    async def complete(self, spec: Endpoint, request: Request, api_key: str | None) -> Any:
        return RawCompletion(text="ok", model=spec.model, finish_reason="stop")


def _variant(**members: Any) -> object:
    return type("Variant", (_Good,), members)()


@pytest.mark.parametrize(
    ("backend", "problem"),
    [
        (object(), "structured_modes"),
        (_variant(structured_modes={"prompt"}), "a frozenset"),
        (_variant(structured_modes=frozenset({"json_schema"})), "'prompt'"),
        (_variant(structured_modes=frozenset({"prompt", "xml"})), "'xml'"),
        (_variant(complete=lambda self, spec, request, api_key: None), "async"),
        (_variant(check="not callable"), "check"),
        (_variant(aclose=lambda self: None), "aclose"),
        (_variant(default_base_url="localhost:8080"), "default_base_url"),
    ],
)
def test_each_requirement_is_checked(backend: object, problem: str) -> None:
    found = backend_problems(backend)
    assert any(problem in p for p in found), found


async def test_a_call_that_breaks_the_port_is_found() -> None:
    spec = ModelSpec(name="q", backend="x", model="m", family="x")

    async def wrong_shape(self: Any, spec: Any, request: Any, api_key: Any) -> Any:
        return {"text": "a dict"}

    found = await answer_problems(_variant(complete=wrong_shape), spec)
    assert any("RawCompletion" in p for p in found), found

    async def says_nothing(self: Any, spec: Any, request: Any, api_key: Any) -> Any:
        return RawCompletion(text="ok", model=spec.model)

    found = await answer_problems(_variant(complete=says_nothing), spec)
    assert any("finish_reason" in p for p in found), found

    def refuses(self: Any, spec: Any) -> list[str]:
        return ["options.region: required"]

    found = await answer_problems(_variant(check=refuses), spec)
    assert any("options.region" in p for p in found), found
