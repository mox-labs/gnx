"""Read the agent-craft catalog from dao-corpus.

dao-corpus is DATA (its README: "This is data. Tooling lives in cix"). This adapter only
reads it — nothing here writes to the corpus, and a missing corpus degrades to an empty
catalog rather than an error, so `dao init` still works for a project that grounds its
bench some other way.

The catalog's `informs` column already records the projection edge (domain →
`project:unit`), so figure selection reads that mapping instead of re-deriving it.
"""

from __future__ import annotations

import re
from pathlib import Path

from dao.domain.models import CorpusDomain, DomainType, Grounding

DEFAULT_CORPUS = Path.home() / "mox" / "packages" / "dao-corpus"
CATALOG_REL = Path("corpora") / "agent-craft" / "CATALOG.md"

# | domain | type | status | sources | cnf | informs |
_ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|"
                  r"\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*$")
_SEPARATOR = re.compile(r"^\|[\s|:-]+\|$")


def _int_or_zero(s: str) -> int:
    try:
        return int(s.strip())
    except (ValueError, AttributeError):
        return 0


def parse_catalog(text: str) -> list[CorpusDomain]:
    """Parse the CATALOG markdown table into domains.

    Rows that are the header, the separator, or a bold totals line are skipped. A row
    whose `type` is not a known DomainType is skipped rather than guessed — the catalog is
    someone else's data and inventing a classification for it would be a lie the rest of
    the projection would then rely on.
    """
    domains: list[CorpusDomain] = []
    for line in text.splitlines():
        line = line.rstrip()
        if not line.startswith("|") or _SEPARATOR.match(line):
            continue
        m = _ROW.match(line)
        if not m:
            continue
        name, dtype, status, sources, cnf, informs = m.groups()
        if name.lower() == "domain" or name.startswith("**"):
            continue
        try:
            domain_type = DomainType(dtype.strip().lower())
        except ValueError:
            continue
        try:
            grounding = Grounding(status.strip().upper())
        except ValueError:
            grounding = Grounding.UNKNOWN
        refs = tuple(r.strip() for r in informs.split(",") if r.strip())
        domains.append(
            CorpusDomain(
                name=name.strip(),
                type=domain_type,
                grounding=grounding,
                sources=_int_or_zero(sources),
                confidence=_int_or_zero(cnf),
                informs=refs,
            )
        )
    return domains


def load_catalog(corpus_root: Path | None = None) -> list[CorpusDomain]:
    """Load the agent-craft catalog. Empty list when the corpus is absent."""
    root = corpus_root or DEFAULT_CORPUS
    catalog = root / CATALOG_REL
    if not catalog.is_file():
        return []
    return parse_catalog(catalog.read_text(encoding="utf-8"))


def projects(domains: list[CorpusDomain]) -> dict[str, int]:
    """Every project the catalog's `informs` edges point at, with a domain count.

    Useful before selecting: it shows which projects the corpus already grounds
    (guild-arch, moxlabs.studio, ziran.ink, chanakya, research, cix …).
    """
    counts: dict[str, int] = {}
    for d in domains:
        for ref in d.informs:
            if ":" in ref:
                project = ref.split(":", 1)[0]
                counts[project] = counts.get(project, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def for_project(domains: list[CorpusDomain], project: str) -> list[CorpusDomain]:
    """Domains whose `informs` edge names this project."""
    return [d for d in domains if d.informs_project(project)]
