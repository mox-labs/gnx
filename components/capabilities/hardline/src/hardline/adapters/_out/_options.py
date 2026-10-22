"""A backend's ``options``, read against the backend's own model.

``ModelSpec.options`` is an open mapping because each backend reads different keys. Each
backend therefore declares a pydantic model of the keys it reads, and both its ``check`` (at
composition) and its call read the options through it, so a typo or a wrong value fails
``hardline check`` rather than the row's first call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ValidationError

from hardline.domain.errors import ConfigError

if TYPE_CHECKING:
    from hardline.domain.types import ModelSpec


def option_problems(model: type[BaseModel], spec: ModelSpec) -> list[str]:
    """``options.<key>: <message>`` for each way ``spec.options`` fails ``model``."""
    try:
        model.model_validate(spec.options)
    except ValidationError as e:
        return [
            f"options.{'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}"
            for issue in e.errors()
        ]
    return []


def read_options[M: BaseModel](model: type[M], spec: ModelSpec) -> M:
    """``spec.options`` as ``model``, or a ConfigError naming the row and each bad key."""
    try:
        return model.model_validate(spec.options)
    except ValidationError:
        problems = option_problems(model, spec)
        raise ConfigError(
            "\n".join(f"models.{spec.name}.{problem}" for problem in problems)
        ) from None
