"""Recon domain models — pure, frozen Pydantic types.

No infrastructure dependencies. These flow through the hexagon.

Every model forbids unknown keys: a misspelt ``normalise:`` or ``preserve-raw:`` is a config
error with a path, not a silently ignored line. The one exception is made by the planner
before a config reaches these models: a top-level key beginning with ``_`` is dropped.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from recon.domain.type_url import DEFAULT_RECORDS_TYPE_URL, type_url_problem

#: The largest response body a source may return unless its entry says otherwise. Large
#: enough for a long PDF or a full page of API results; small enough that one bad URL
#: cannot fill memory.
DEFAULT_MAX_BYTES = 50_000_000

# --- Value Objects ---


class AuthConfig(BaseModel):
    """How to authenticate with a source.

    `env` names the environment variable holding the secret; the secret itself never
    appears in the config.

    - `header`: send it as a request header (e.g. `x-api-key`, `Authorization`)
    - `param`: send it as a query-string parameter (e.g. OpenAlex `api_key`)

    Both may be set. `prefix` is prepended to the resolved value in either place (e.g.
    "Bearer " for `Authorization: Bearer <token>`).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    header: str = ""
    param: str = ""
    env: str = ""
    prefix: str = ""


class RateLimitConfig(BaseModel):
    """Per-source rate limiting via token bucket."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rps: float = 10.0
    burst: int = 3


class CaptureMatch(BaseModel):
    """Filters a `capture` collector applies to capture-log lines.

    Each field is a shell-style glob (`fnmatch`) matched against the capture's field of the
    same name; an unset field matches everything.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str | None = None
    collector: str | None = None
    kind: str | None = None


# --- Source Entry ---


class SourceEntry(BaseModel):
    """A source in the catalog — where to look.

    API sources provide URL + auth + rate limits.
    Web sources provide URL + rate limits for page fetching.
    Local sources provide a filesystem path (used as cwd by CLI collectors).

    ``max_bytes`` bounds one HTTP response body from this source, counted after any
    content-encoding is undone. A larger body fails the table instead of being buffered.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    type: Literal["api", "web", "local"] = "api"
    url: str
    auth: AuthConfig = AuthConfig()
    rate_limit: RateLimitConfig = RateLimitConfig()
    user_agent: str | None = None
    timeout: float = 60.0
    max_bytes: int = Field(default=DEFAULT_MAX_BYTES, gt=0)


# --- Collector Entry ---


class CollectorEntry(BaseModel):
    """A collection step — what to do.

    `type` names an installed collector type (entry-point group `recon.collectors`). It is
    an open string here; whether it is installed is checked at plan time against what is
    actually registered. The built-in types use these fields:

    - cli: `run`, `patterns`
    - api: `endpoint`, `params`, `method`, `body`, `response_format`, `extract`
    - web: `endpoint` (optional, appended to source URL)
    - capture: `path` (capture log path or glob), `match`, `response_format`, `extract`

    `normalize` applies to every type. `type_url` declares what the output table's records
    are; it is recorded in the archive's meta.yaml, never injected into the records.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    type: str
    source: str | None = None
    type_url: str = DEFAULT_RECORDS_TYPE_URL

    # cli fields
    run: str | None = None
    patterns: list[str] | None = None

    # api fields
    endpoint: str | None = None
    params: dict[str, str] | None = None
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    body: dict[str, Any] | None = None
    response_format: Literal["json", "xml", "html", "text"] = "json"
    extract: str | None = None

    # capture fields
    path: str | None = None
    match: CaptureMatch | None = None

    # shared: field mapping spec (output_col: "path.to.field|$transform")
    normalize: dict[str, str] | None = None

    @field_validator("type_url")
    @classmethod
    def _type_url_grammar(cls, value: str) -> str:
        problem = type_url_problem(value)
        if problem:
            raise ValueError(f"{value!r} is not a type_url: {problem}")
        return value


# --- Top-level Config ---


class ReconConfig(BaseModel):
    """One YAML file per mission. Catalog + collectors.

    `preserve_raw: true` appends every fetched response (HTTP body, CLI stdout) to the
    archive's capture log, `archive/<ts>/captures.jsonl`, with the bodies content-addressed
    under `archive/<ts>/raw/<sha256>`. See `recon --skill -r capture-format`. It enables:
      - Re-normalization without re-fetching (a `capture` collector reads the log)
      - Audit: prove what the server actually said
      - Experimentation: try a different normalize spec on the same snapshot
    Default is off because raw captures roughly double disk usage per mission.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    catalog: list[SourceEntry] = []
    collectors: list[CollectorEntry]
    preserve_raw: bool = False
