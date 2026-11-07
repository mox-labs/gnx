"""matrix-hardline: hardline as a matrix model runtime.

matrix ships no model runtime that calls a model, and hardline knows nothing of matrix. This
distribution depends on both and holds the one adapter between them
(:mod:`matrix_hardline.runtime`), registered from its own ``matrix.extensions`` entry point:
install it, and ``type: matrix-hardline.v1.model-runtime`` under ``matrix.model_runtimes`` is
one model call through hardline, in any model family it has a registry row for.
"""

from importlib.metadata import PackageNotFoundError, version

from .runtime import (
    FAILED_CALL_TYPE_URL,
    TYPE_URL,
    FailedCall,
    HardlineModelRuntime,
    HardlineModelRuntimeConfig,
    reason,
    register,
)

try:
    __version__ = version("matrix-hardline")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0+unknown"

__all__ = [
    "FAILED_CALL_TYPE_URL",
    "TYPE_URL",
    "FailedCall",
    "HardlineModelRuntime",
    "HardlineModelRuntimeConfig",
    "__version__",
    "reason",
    "register",
]
