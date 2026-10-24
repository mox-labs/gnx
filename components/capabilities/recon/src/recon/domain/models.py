"""Recon domain models — pure, frozen Pydantic types.

No infrastructure dependencies. These flow through the hexagon.

Every model forbids unknown keys: a misspelt ``normalise:`` or ``preserve-raw:`` is a config
error with a path, not a silently ignored line. Three exceptions: a top-level key beginning
with ``_`` is dropped by the planner before a config reaches these models; a collector entry
keeps the keys that are its type's own settings, which the planner validates against that
type's schema (``CollectorType.settings``); and a catalog source keeps the keys that are its
kind's own, which the planner validates against the schema of the type that adds the kind
(``CollectorType.source_settings``). An unknown one is still a config error.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from recon.domain.clock import LONGEST_WAIT
from recon.domain.ratelimit import wait_for_token

# Pydantic reads the field types when the models are built, so they are imported to run.
from recon.domain.stream import BufferSpec, Retain, WindowSpec  # noqa: TC001
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
    """Per-source rate limiting via token bucket: ``rps`` tokens a second, at most ``burst``
    held. Each is positive: a bucket that never refills, or holds no token, never lets a
    request go. ``rps`` is finite (no rate is written ``.inf``), and an empty bucket's wait
    for a token, ``1 / rps``, is one the clock can sleep (:data:`LONGEST_WAIT`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rps: float = Field(default=10.0, gt=0, allow_inf_nan=False)
    burst: int = Field(default=3, ge=1)

    @field_validator("rps")
    @classmethod
    def _a_wait_the_clock_can_sleep(cls, value: float) -> float:
        wait = wait_for_token(0.0, value)
        if wait > LONGEST_WAIT:
            raise ValueError(
                f"a wait for a token of 1/rps = {wait:g}s is longer than this platform's "
                f"timers take ({LONGEST_WAIT:g}s): raise rps"
            )
        return value


# --- Source Entry ---


class SourceEntry(BaseModel):
    """A source in the catalog — where to look.

    API sources provide URL + auth + rate limits.
    Web sources provide URL + rate limits for page fetching.
    Local sources provide a filesystem path (used as cwd by CLI collectors).

    ``type`` is a source kind: ``api``, ``web``, ``local``, or one an installed collector type
    adds (``CollectorType.source_kinds``). It is an open string here; whether it is installed
    is checked at plan time.

    ``url`` is where the source is. A source of a kind with no settings of its own needs one,
    which the plan checks; a kind with settings of its own (``mcp``: a stdio server's
    ``command``, or a remote's ``url``) says through its type what it needs. Every other key
    is the kind's own setting, kept in :attr:`settings`.

    ``max_bytes`` bounds one HTTP response body from this source, counted after any
    content-encoding is undone. A larger body fails the table instead of being buffered.
    ``timeout`` bounds, in seconds of wall time, one send of a request (from connecting to
    its body's last byte, every redirect it follows included) and a ``cli`` command; it is
    finite, as no send is unbounded, and no longer than the platform's timers take
    (:data:`LONGEST_WAIT`).
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    name: str
    type: str = "api"
    url: str = ""
    auth: AuthConfig = AuthConfig()
    rate_limit: RateLimitConfig = RateLimitConfig()
    user_agent: str | None = None
    timeout: float = Field(default=60.0, gt=0, allow_inf_nan=False)
    max_bytes: int = Field(default=DEFAULT_MAX_BYTES, gt=0)

    @property
    def settings(self) -> dict[str, Any]:
        """The keys that are the source kind's own settings, as written."""
        return dict(self.model_extra or {})

    @field_validator("timeout")
    @classmethod
    def _a_timeout_the_platform_can_time(cls, value: float) -> float:
        if value > LONGEST_WAIT:
            raise ValueError(
                f"{value:g}s is longer than this platform's timers take ({LONGEST_WAIT:g}s)"
            )
        return value


# --- Collector Entry ---


class CollectorEntry(BaseModel):
    """A collection step — what to do.

    `type` names an installed collector type (entry-point group `recon.collectors`). It is
    an open string here; whether it is installed is checked at plan time against what is
    actually registered.

    The fields here are the ones recon itself reads, for every type: `name`, `type`,
    `source`, `type_url` (what the output table's records are; recorded in the archive's
    meta.yaml, never injected into the records), `normalize` (output column to
    ``path.to.field|$transform``), `extract` (where the records are in a body, for a type
    that declares ``extracts``) and `deadline` (the seconds each of its tables may take).
    `window` makes the entry a stream, collected into windows that each end at the first of
    its bounds; `buffer` and `retain` are a stream's too (:mod:`recon.domain.stream`); left
    out, the plan fills in their defaults and says so.
    Every other key is the type's own setting (`run:` for `cli`, `endpoint:` for `api`),
    kept in :attr:`settings` and validated by the type's own schema at plan time.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    name: str
    type: str
    source: str | None = None
    type_url: str = DEFAULT_RECORDS_TYPE_URL
    extract: str | None = None
    normalize: dict[str, str] | None = None
    deadline: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    window: WindowSpec | None = None
    buffer: BufferSpec | None = None
    retain: Retain | None = None

    @property
    def settings(self) -> dict[str, Any]:
        """The keys that are the type's own settings, as written."""
        return dict(self.model_extra or {})

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

    `deadline` is the seconds the whole survey may take; a table it cuts, and every table
    not started by then, fails as ``transient``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    catalog: list[SourceEntry] = []
    collectors: list[CollectorEntry]
    preserve_raw: bool = False
    deadline: float | None = Field(default=None, gt=0, allow_inf_nan=False)
