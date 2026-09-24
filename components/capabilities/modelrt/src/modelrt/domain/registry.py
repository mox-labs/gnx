"""ModelRegistry — model name to ModelSpec, validated once at the boundary.

The registry is data: adding a model is a config change. Validation errors name the
source the row came from and the key path inside it, and list the legal values where
there is a closed set — the gap matrix's untyped ``create(**config)`` left open.

Config shape (the ``models`` section of any tier)::

    default: qwen3-8b            # optional
    models:
      qwen3-8b:
        backend: openai-compat
        base_url: http://127.0.0.1:8080/v1
        model: mlx-community/Qwen3-8B-4bit
        family: qwen
        local: true
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

from pydantic import ValidationError

from modelrt.domain.errors import ConfigError, UnknownModelError
from modelrt.domain.types import ModelSpec

_TOP_LEVEL_KEYS = frozenset({"default", "models"})


def _format_validation(source: str, name: str, err: ValidationError) -> str:
    lines = []
    for issue in err.errors():
        loc = ".".join(str(p) for p in issue["loc"])
        key = f"models.{name}.{loc}" if loc else f"models.{name}"
        lines.append(f"  {source}: {key}: {issue['msg']}")
    return "\n".join(lines)


class ModelRegistry:
    """An immutable name → ModelSpec table with an optional default."""

    def __init__(self, specs: Mapping[str, ModelSpec], default: str | None = None) -> None:
        self._specs = dict(specs)
        if default is not None and default not in self._specs:
            raise ConfigError(
                f"default model {default!r} is not in the registry. "
                f"Known: {', '.join(sorted(self._specs)) or '(none)'}"
            )
        self._default = default

    @classmethod
    def from_config(
        cls,
        data: Mapping[str, Any],
        *,
        source: str = "<config>",
        origins: Mapping[str, str] | None = None,
    ) -> ModelRegistry:
        """Validate a ``{default?, models: {...}}`` mapping.

        ``origins`` maps a model name to the source that last defined it, so an error in
        a merged config still points at the right file.
        """
        unknown = set(data) - _TOP_LEVEL_KEYS
        if unknown:
            raise ConfigError(
                f"{source}: unknown top-level key(s) {sorted(unknown)}. "
                f"Legal: {sorted(_TOP_LEVEL_KEYS)}"
            )
        raw_models = data.get("models") or {}
        if not isinstance(raw_models, Mapping):
            raise ConfigError(f"{source}: 'models' must be a mapping of name -> spec")

        specs: dict[str, ModelSpec] = {}
        problems: list[str] = []
        for name, raw in raw_models.items():
            where = (origins or {}).get(name, source)
            if not isinstance(raw, Mapping):
                problems.append(f"  {where}: models.{name}: must be a mapping")
                continue
            if "name" in raw and raw["name"] != name:
                problems.append(f"  {where}: models.{name}.name: must match its key or be omitted")
                continue
            try:
                specs[str(name)] = ModelSpec.model_validate({**raw, "name": name})
            except ValidationError as e:
                problems.append(_format_validation(where, str(name), e))
        if problems:
            raise ConfigError("invalid model registry:\n" + "\n".join(problems))

        default = data.get("default")
        return cls(specs, default=str(default) if default is not None else None)

    def get(self, name: str | None = None) -> ModelSpec:
        """Look up a model by name, or the default when ``name`` is None."""
        key = name if name is not None else self._default
        if key is None:
            raise UnknownModelError(
                "no model named and no default set. "
                f"Known: {', '.join(sorted(self._specs)) or '(none)'}"
            )
        spec = self._specs.get(key)
        if spec is None:
            raise UnknownModelError(
                f"unknown model {key!r}. Known: {', '.join(sorted(self._specs)) or '(none)'}"
            )
        return spec

    @property
    def default(self) -> str | None:
        return self._default

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._specs))

    def __iter__(self) -> Iterator[ModelSpec]:
        return iter(self._specs[n] for n in self.names())

    def __contains__(self, name: object) -> bool:
        return name in self._specs

    def __len__(self) -> int:
        return len(self._specs)
