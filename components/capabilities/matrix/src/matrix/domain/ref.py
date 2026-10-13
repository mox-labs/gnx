"""``matrix.v1.ref``: a small row that stands for a large or external value.

A table's rows are read whole by every component that requires the kind, and written whole
to the store. A value too large for that (a sample series, a capture, a workspace) is kept
where it is, and the row says where: a component provides a :class:`Ref`, alone or as a field
of its own record, and whoever needs the bytes resolves it.

matrix records a ref like any row. It does not fetch, copy or verify what the ref points at.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

REF = "matrix.v1.ref"


class Ref(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Where the value is: a URL, or a path relative to wherever the run is kept.
    uri: str
    media_type: str = ""
    #: Size in bytes, when known.
    size: int | None = None
    #: ``<algorithm>:<hex>`` of the value, when the provider computed one.
    digest: str | None = None
