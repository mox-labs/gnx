"""What a backend must be: the ``ModelBackend`` port's requirements, as a check a backend's author
runs against an installed hardline.

    from hardline import answer_problems, backend_problems

    def test_my_backend_conforms():
        assert backend_problems(MyBackend()) == []

    async def test_my_backend_answers():
        spec = ModelSpec(name="t", backend="mine", model="m", family="x", base_url=...)
        assert await answer_problems(MyBackend(transport=...), spec) == []

Each returns what is wrong, one sentence a problem, and is empty for a backend that conforms.
``backend_problems`` reads the members; ``answer_problems`` makes one call, with ``spec`` a row
the backend can serve (a test's own server or transport: the helper sends what the backend
sends).
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, get_args

from hardline.domain.ports._out.backend import ModelBackend
from hardline.domain.types import FinishReason, Message, RawCompletion, Request, StructuredMode

if TYPE_CHECKING:
    from hardline.domain.types import ModelSpec

#: The structured modes a row may ask for; a backend serves ``prompt`` and any of the others.
_MODES = frozenset(get_args(StructuredMode))


def backend_problems(backend: object) -> list[str]:
    """What keeps ``backend`` from being a ``ModelBackend`` hardline can compose.

    Required: ``structured_modes``, a frozenset of known modes that includes ``prompt`` (every
    backend serves the schema stated in an instruction; the runtime validates either way), and
    an async ``complete(spec, request, api_key)``. Optional, and checked when present: a
    callable ``check(spec)``, an async ``aclose()``, and ``default_base_url``, an http(s) URL.
    """
    found: list[str] = []
    modes = getattr(backend, "structured_modes", None)
    if modes is None:
        found.append("structured_modes: missing; a frozenset of the modes the backend serves")
    elif not isinstance(modes, frozenset):
        found.append(f"structured_modes: a {type(modes).__name__}, not a frozenset")
    else:
        if "prompt" not in modes:
            found.append("structured_modes: must include 'prompt', which every backend serves")
        found.extend(
            f"structured_modes: {mode!r} is not a mode; known: {', '.join(sorted(_MODES))}"
            for mode in sorted(map(str, modes - _MODES))
        )
    complete = getattr(backend, "complete", None)
    if not inspect.iscoroutinefunction(complete):
        found.append("complete: must be an async method complete(spec, request, api_key)")
    check = getattr(backend, "check", None)
    if check is not None and not callable(check):
        found.append("check: present but not callable; check(spec) returns a row's problems")
    aclose = getattr(backend, "aclose", None)
    if aclose is not None and not inspect.iscoroutinefunction(aclose):
        found.append("aclose: present but not an async method")
    url = getattr(backend, "default_base_url", None)
    if url is not None and not (isinstance(url, str) and url.startswith(("http://", "https://"))):
        found.append(f"default_base_url: {url!r} is not an http:// or https:// URL")
    if not found and not isinstance(backend, ModelBackend):
        found.append("not a ModelBackend")
    return found


async def answer_problems(
    backend: object, spec: ModelSpec, api_key: str | None = None
) -> list[str]:
    """What is wrong with one call of ``backend`` for ``spec``: its ``check`` must accept the
    row, and ``complete`` must return a ``RawCompletion`` that says why the reply ended.

    A reply with no ``finish_reason`` is a problem here though the port's type allows it: a
    caller can then never tell a whole answer from a cut one (``Completion.answered``).
    """
    found = backend_problems(backend)
    if found:
        return found
    check = getattr(backend, "check", None)
    if callable(check):
        found.extend(f"check refused the row: {problem}" for problem in check(spec))
        if found:
            return found
    request = Request(messages=(Message(role="user", content="Say ok."),), max_tokens=16)
    raw = await backend.complete(spec, request, api_key)  # type: ignore[attr-defined]
    if not isinstance(raw, RawCompletion):
        return [f"complete returned {type(raw).__name__}, not RawCompletion"]
    if raw.finish_reason is None:
        found.append(
            "complete set no finish_reason, so no reply is ever answered; map the provider's "
            f"own reason to one of {', '.join(get_args(FinishReason))}"
        )
    return found
