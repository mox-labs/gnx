"""Recon — mechanical collection system. Collect, normalize, query."""

__version__ = "0.10.0"

# What every HTTP collector sends unless the source sets `user_agent:`. It says what is
# calling and where to read about it; a mission that needs another identity names it.
DEFAULT_USER_AGENT = f"recon/{__version__} (+https://github.com/mox-labs/gnx)"
