"""What an extension of recon implements, and what it is handed: the whole extension contract.

A package adds to recon by registering at recon's points on matrix's registry, from its own
``matrix.extensions`` entry point (or in-process, ``recon.open(extensions=[register])``)::

    from recon.extension import POINTS

    def register(registry):
        registry.register(
            POINTS["collector"], "acme.v1.collector.sql", SqlCollector,
            config=SqlSettings, needs={"credentials"},
        )

=====================  =============================================================
:data:`POINTS` key     what an entry builds, and with what
=====================  =============================================================
``collector``          a :class:`Collector`, as ``build(**needs)`` with the needs it
                       names (:data:`NEEDS`); its ``config`` is the schema of the
                       type's own keys in a collector entry (refusing unknown keys),
                       handed to every member in :attr:`Run.settings`
``source``             a :class:`SourceKind`, as ``build()``; its ``config`` is the
                       schema of the kind's own keys in a catalog source
``converter``          a :class:`Converter`, as ``build()``: how a body's media type
                       becomes values recon finds records in
``transform``          a callable, as ``build()``: a normalize spec's ``$name``
=====================  =============================================================

A collector type gives :data:`Item` s (:class:`Body`, :class:`Record`, :class:`PageMark`,
:class:`Gap`, :class:`Dropped`, :class:`Truncated`); recon captures, converts, finds and
shapes the records, for every type, so a type never applies ``normalize:`` or writes a capture
itself. What else a type can do is an optional protocol of its own, found with ``isinstance``:
:class:`Paged` (recon drives its crawl) and :class:`Preflight` (a checked dry run asks it).

``recon.testing.CollectorConformance`` checks a type against this contract by its behaviour.

**Stability.** Provisional: the contract may change in a minor release, said in the CHANGELOG,
until three independent collector types pass the conformance kit; it freezes at recon 1.0.
"""

from recon.core.domain.capture import CaptureLogs
from recon.core.domain.clock import Clock
from recon.core.domain.collector import (
    NEEDS,
    Body,
    BodyEnd,
    Collector,
    Converter,
    Dropped,
    Effect,
    Intent,
    Item,
    Page,
    Paged,
    PageMark,
    PlainKind,
    Position,
    Preflight,
    Record,
    RecordsKind,
    Requirement,
    Run,
    SourceKind,
    Transform,
    Truncated,
    source_needed,
)
from recon.core.domain.credentials import Credentials
from recon.core.domain.deadline import Deadline
from recon.core.domain.errors import CollectionError, NoRecordsError, Problem
from recon.core.domain.http import REDACTED, HttpResponse, LiveBody, Requester, reported_url
from recon.core.domain.models import CollectorEntry, SourceEntry
from recon.core.domain.paginate import PaginateSpec
from recon.core.domain.ratelimit import RateLimiter
from recon.core.domain.shell import ShellRunner
from recon.core.domain.stream import Gap
from recon.core.domain.telemetry import Telemetry

#: recon's extension points on matrix's registry, by what an entry there is.
POINTS = {
    "collector": "recon.collector",
    "source": "recon.source",
    "converter": "recon.converter",
    "transform": "recon.transform",
}

__all__ = [
    "NEEDS",
    "POINTS",
    "REDACTED",
    "Body",
    "BodyEnd",
    "CaptureLogs",
    "Clock",
    "CollectionError",
    "Collector",
    "CollectorEntry",
    "Converter",
    "Credentials",
    "Deadline",
    "Dropped",
    "Effect",
    "Gap",
    "HttpResponse",
    "Intent",
    "Item",
    "LiveBody",
    "NoRecordsError",
    "Page",
    "PageMark",
    "PaginateSpec",
    "Paged",
    "PlainKind",
    "Position",
    "Preflight",
    "Problem",
    "RateLimiter",
    "Record",
    "RecordsKind",
    "Requester",
    "Requirement",
    "Run",
    "ShellRunner",
    "SourceEntry",
    "SourceKind",
    "Telemetry",
    "Transform",
    "Truncated",
    "reported_url",
    "source_needed",
]
