"""Matrix configuration errors. Messages name the source and key path."""

from __future__ import annotations


class ConfigError(Exception):
    """A matrix config, agent definition, or component config is invalid.

    Always names where the bad value came from (a file, a registry type_url, a definition
    path) so the fix starts from the message.
    """
