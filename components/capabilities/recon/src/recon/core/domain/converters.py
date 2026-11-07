"""Which converter converts a body (:class:`~recon.core.domain.collector.Converter`, the entries
at ``recon.converter``): the one the body names, by its config name, or else the one its media
type picks.

A converter claims media types as ``type/subtype`` (``application/json``), a structured suffix
(``+json``: any ``application/*+json``), ``type/*`` or ``*/*``. A body's media type, its
parameters left out and its case ignored, is converted by the most specific claim: its
``type/subtype``, then its suffix, then its ``type/*``, then ``*/*``. Parameters are the
converter's to read (``application/vnd.recon.mcp-result+json; read=text``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from recon.core.domain.collector import Converter


def essence(media_type: str) -> str:
    """``type/subtype`` of a media type, lowercase, its parameters left out."""
    return media_type.split(";", 1)[0].strip().lower()


def claims(media_type: str) -> list[str]:
    """What a media type is matched by, most specific first."""
    found = essence(media_type)
    kind, _, subtype = found.partition("/")
    order = [found]
    if "+" in subtype:
        order.append("+" + subtype.rsplit("+", 1)[1])
    order += [f"{kind}/*", "*/*"]
    return order


def claim_problem(claim: str) -> str | None:
    """Why a converter's claim is not one, or None."""
    if claim.startswith("+") and len(claim) > 1 and "/" not in claim:
        return None
    kind, slash, subtype = claim.partition("/")
    if slash and kind and subtype and claim == claim.lower() and ";" not in claim:
        return None
    return "a claim is type/subtype, type/*, */* or +suffix, lowercase, with no parameters"


def pick(media_type: str, converters: Mapping[str, Converter]) -> Converter | None:
    """The converter for ``media_type`` among ``converters`` (by claim), or None."""
    for claim in claims(media_type):
        if claim in converters:
            return converters[claim]
    return None


def claimed(named: Iterable[tuple[str, Converter]]) -> tuple[dict[str, Converter], list[str]]:
    """Each claim's converter, in the order given (the first claim of a media type keeps it),
    and a sentence for each claim a later converter could not take."""
    by_claim: dict[str, Converter] = {}
    owners: dict[str, str] = {}
    refused = []
    for name, converter in named:
        for claim in sorted(converter.media_types):
            if claim in by_claim:
                refused.append(f"{name} claims {claim}, which {owners[claim]} converts")
                continue
            by_claim[claim], owners[claim] = converter, name
    return by_claim, refused


class Converters:
    """The installed converters, by config name and by the media types they claim."""

    def __init__(self, named: Mapping[str, Converter], by_claim: Mapping[str, Converter]):
        self.named = dict(named)
        self.by_claim = dict(by_claim)

    def __bool__(self) -> bool:
        return bool(self.named)

    def pick(self, media_type: str, name: str | None = None) -> Converter | None:
        """The converter ``name`` names, or (with no name) the one ``media_type`` picks."""
        if name is not None:
            return self.named.get(name)
        return pick(media_type, self.by_claim)
