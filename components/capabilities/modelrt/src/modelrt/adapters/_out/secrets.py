"""EnvFileSecretResolver — ``env:NAME`` and ``file:PATH`` references.

Errors name the reference and never the value. There is deliberately no fallback to an
ambient variable the config did not name: a model row that says ``env:GEMINI_API_KEY``
reads that variable and nothing else.
"""

from __future__ import annotations

import os
from pathlib import Path

from modelrt.domain.errors import SecretError


class EnvFileSecretResolver:
    def resolve(self, reference: str) -> str:
        scheme, _, target = reference.partition(":")
        if scheme == "env":
            value = os.environ.get(target)
            if not value:
                raise SecretError(f"secret {reference!r}: environment variable {target} is not set")
            return value
        if scheme == "file":
            path = Path(target).expanduser()
            try:
                value = path.read_text().strip()
            except OSError as e:
                raise SecretError(f"secret {reference!r}: cannot read {path}: {e.strerror}") from e
            if not value:
                raise SecretError(f"secret {reference!r}: {path} is empty")
            return value
        raise SecretError(f"secret {reference!r}: unknown scheme {scheme!r}; use env: or file:")
