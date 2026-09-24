from __future__ import annotations

from typing import Any

from modelrt import ModelRuntime, build_runtime
from modelrt.adapters._out.mock import MockBackend


class DictSecrets:
    """Secret resolver backed by a dict — records what was asked for."""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = values or {}
        self.asked: list[str] = []

    def resolve(self, reference: str) -> str:
        self.asked.append(reference)
        return self.values[reference]


def mock_runtime(models: dict[str, Any], **kw: Any) -> ModelRuntime:
    return build_runtime(
        {"models": models, **({"default": kw.pop("default")} if "default" in kw else {})},
        backends={"mock": MockBackend()},
        secrets=kw.pop("secrets", DictSecrets()),
        **kw,
    )
