"""ix-cosmic-ray: cosmic-ray's mutation operators as an ix mutant source.

ix measures whether a test suite fails when the code is wrong, over sampled mutants, and makes
no mutant itself: a mutant source does, at ix's ``ix.mutant-source`` extension point. This
distribution is one, over cosmic-ray (MIT), used as a generator library alone. Install it, and
``ix mutants sample`` draws from cosmic-ray's operators (:mod:`ix_cosmic_ray.generator`);
cosmic-ray's runner, its session database and its dependencies never reach ix itself.
"""

from importlib.metadata import PackageNotFoundError, version

from ix_cosmic_ray.source import TYPE_URL, build, register

try:
    __version__ = version("ix-cosmic-ray")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0+unknown"

__all__ = ["TYPE_URL", "__version__", "build", "register"]
