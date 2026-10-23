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

from hardline.domain.digests import digest
from hardline.domain.errors import ConfigError, UnknownModelError
from hardline.domain.types import ModelSpec, Problem, RowRecord

_TOP_LEVEL_KEYS = frozenset({"default", "models"})


def _kind(error_type: str) -> str:
    """The shared problem kind of a pydantic error type."""
    if error_type == "extra_forbidden":
        return "unknown_key"
    if error_type == "missing":
        return "missing_key"
    if error_type.endswith(("_type", "_parsing")):
        return "wrong_shape"
    return "invalid_value"


def _scalar(value: Any) -> str | int | float | bool | None:
    return value if isinstance(value, str | int | float | bool) else None


def _validation_problems(
    fields: Mapping[str, str], where: str, name: str, err: ValidationError
) -> list[Problem]:
    """One problem per issue, each naming the tier that set the field, else ``where``."""
    found = []
    for issue in err.errors():
        loc = ".".join(str(p) for p in issue["loc"])
        found.append(
            Problem(
                path=f"models.{name}.{loc}" if loc else f"models.{name}",
                kind=_kind(issue["type"]),
                message=issue["msg"],
                got=_scalar(issue.get("input")),
                source=fields.get(str(issue["loc"][0]), where) if issue["loc"] else where,
            )
        )
    return found


def unknown_keys(found: set[str], source: str) -> ConfigError:
    """The error for top-level keys a tier may not have."""
    return ConfigError(
        f"{source}: unknown top-level key(s) {sorted(found)}. Legal: {sorted(_TOP_LEVEL_KEYS)}",
        problems=[
            Problem(
                path=key,
                kind="unknown_key",
                message="not a top-level key",
                allowed=tuple(sorted(_TOP_LEVEL_KEYS)),
                source=source,
            )
            for key in sorted(found)
        ],
    )


def models_not_a_mapping(source: str) -> ConfigError:
    return ConfigError(
        f"{source}: 'models' must be a mapping of name -> spec",
        problems=[
            Problem(
                path="models",
                kind="wrong_shape",
                message="must be a mapping of name -> spec",
                source=source,
            )
        ],
    )


class ModelRegistry:
    """An immutable name → ModelSpec table with an optional default.

    ``rungs`` maps a row name to ``{field: tier}``, the tier that set each field the
    configuration set; ``record(name)`` hands it out with the row's digest.
    """

    def __init__(
        self,
        specs: Mapping[str, ModelSpec],
        default: str | None = None,
        rungs: Mapping[str, Mapping[str, str]] | None = None,
    ) -> None:
        self._specs = dict(specs)
        self._rungs = {name: dict((rungs or {}).get(name, {})) for name in self._specs}
        if default is not None and default not in self._specs:
            raise ConfigError(
                f"default model {default!r} is not in the registry. "
                f"Known: {', '.join(sorted(self._specs)) or '(none)'}",
                problems=[
                    Problem(
                        path="default",
                        kind="name_unknown",
                        message=f"{default!r} is not in the registry",
                        allowed=tuple(sorted(self._specs)),
                        got=default,
                    )
                ],
            )
        self._default = default

    @classmethod
    def from_config(
        cls,
        data: Mapping[str, Any],
        *,
        source: str = "<config>",
        origins: Mapping[str, Mapping[str, str]] | None = None,
    ) -> ModelRegistry:
        """Validate a ``{default?, models: {...}}`` mapping.

        ``origins`` maps a model name to ``{field: source}``, the tier that set each field, in
        the order the tiers were read. An error in a merged config then points at the file
        that set the bad field (or, for a missing one, the last file that touched the row), and
        the registry keeps the map as each row's rungs. Without it, every field is ``source``'s.
        """
        unknown = set(data) - _TOP_LEVEL_KEYS
        if unknown:
            raise unknown_keys(unknown, source)
        raw_models = data.get("models") or {}
        if not isinstance(raw_models, Mapping):
            raise models_not_a_mapping(source)

        specs: dict[str, ModelSpec] = {}
        problems: list[Problem] = []
        rungs: dict[str, dict[str, str]] = {}
        for name, raw in raw_models.items():
            if not isinstance(raw, Mapping):
                problems.append(
                    Problem(
                        path=f"models.{name}",
                        kind="wrong_shape",
                        message="must be a mapping",
                        source=source,
                    )
                )
                continue
            fields = dict((origins or {}).get(name) or dict.fromkeys(raw, source))
            fields.pop("name", None)  # the key names the row; its tiers are its fields'
            where = next(reversed(fields.values()), source)
            if "name" in raw and raw["name"] != name:
                problems.append(
                    Problem(
                        path=f"models.{name}.name",
                        kind="invalid_value",
                        message="must match its key or be omitted",
                        got=_scalar(raw["name"]),
                        source=where,
                    )
                )
                continue
            try:
                specs[str(name)] = ModelSpec.model_validate({**raw, "name": name})
            except ValidationError as e:
                problems.extend(_validation_problems(fields, where, str(name), e))
            rungs[str(name)] = fields
        if problems:
            raise ConfigError(
                "invalid model registry:\n"
                + "\n".join(f"  {p.source}: {p.path}: {p.message}" for p in problems),
                problems=problems,
            )

        default = data.get("default")
        return cls(specs, default=str(default) if default is not None else None, rungs=rungs)

    def with_locations(self, defaults: Mapping[str, str]) -> ModelRegistry:
        """This registry with ``defaults[backend]`` written into each row that names no
        ``base_url``, so every row records where it sends, and the rung says whose it was."""
        specs = dict(self._specs)
        rungs = {name: dict(fields) for name, fields in self._rungs.items()}
        for name, spec in self._specs.items():
            if spec.base_url is None and spec.backend in defaults:
                specs[name] = spec.model_copy(update={"base_url": defaults[spec.backend]})
                rungs[name]["base_url"] = f"default of backend {spec.backend}"
        return ModelRegistry(specs, default=self._default, rungs=rungs)

    def record(self, name: str) -> RowRecord:
        """Row ``name`` as configured: its digest, and the tier that set each field."""
        spec = self.get(name)
        return RowRecord(digest=digest(spec.model_dump(mode="json")), rungs=self._rungs[name])

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
