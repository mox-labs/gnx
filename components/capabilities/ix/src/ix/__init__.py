"""ix: Intelligent Experimentation.

Evals, benchmarks, and QoS experiments for AI agents and skills.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("ix")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0+unknown"
