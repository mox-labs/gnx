"""Keep secrets out of error messages.

Provider SDKs echo a partial key back in auth errors. A BackendError message ends up in
logs, experiment results and CI output, so the key is removed before wrapping.
"""

from __future__ import annotations


def scrub(message: str, secret: str | None) -> str:
    if not secret:
        return message
    scrubbed = message.replace(secret, "***")
    # Providers often echo a prefix or suffix ("sk-ab...wxyz"): remove long fragments too.
    if len(secret) >= 12:
        for fragment in (secret[:8], secret[-6:]):
            scrubbed = scrubbed.replace(fragment, "***")
    return scrubbed
