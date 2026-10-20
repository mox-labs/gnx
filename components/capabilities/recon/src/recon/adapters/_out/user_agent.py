"""The User-Agent every HTTP collector sends unless its source sets ``user_agent:``: what is
calling and where to read about it.

Its own module, without the HTTP client, so a plan can show the header a run would send.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    _VERSION = version("recon")
except PackageNotFoundError:  # running from a source tree that was never installed
    _VERSION = "0+unknown"

DEFAULT_USER_AGENT = f"recon/{_VERSION} (+https://github.com/mox-labs/gnx)"
