"""Capture logs on disk: writing and reading ``captures.jsonl`` (format: recon.v1.capture).

A capture log is a directory holding::

    captures.jsonl          append-only, one capture per line
    raw/<sha256>            each body, content-addressed (identical bodies stored once)

An archive with ``preserve_raw: true`` is one (``archive/<ts>/``); a mission's external
log, fed by ``recon capture``, is another (``.recon/<mission>/captures/``). The line
format is specified by `recon --skill -r capture-format` and is written by tools other
than recon, so the reader here is strict about what it trusts (the body path) and lenient
about what it does not need (unknown fields are ignored).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import uuid
from typing import TYPE_CHECKING, Any, BinaryIO

from recon.adapters._out.private_files import make_dirs, open_file, write_file
from recon.domain.capture import CAPTURE_LOG_NAME, CAPTURE_SCHEMA, CaptureKind, redact_headers
from recon.domain.documents import Capture
from recon.domain.errors import CollectionError

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from recon.domain.clock import Clock

_BODY_PATH = re.compile(r"^raw/[0-9a-f]{64}$")
_SCHEMA = re.compile(r"^recon\.v([0-9]+)\.capture$")

#: The string fields a line needs to be read, besides ``schema`` and ``body``.
_REQUIRED_TO_READ = ("id", "collector", "kind", "sha256")


class FilesystemCaptureLog:
    """Appends captures under ``root`` (``root/captures.jsonl`` + ``root/raw/``).

    Appends take an exclusive ``flock`` on POSIX and write each line with one ``write``
    call, so concurrent ``recon capture`` processes (parallel tool calls firing hooks)
    never interleave lines.
    """

    def __init__(self, root: Path, clock: Clock) -> None:
        self.root = root
        self._clock = clock
        self.log_path = root / CAPTURE_LOG_NAME
        self._streams: dict[str, Path] = {}
        #: The open file each ``tee`` appends to, by collector, until its body is finalized.
        self._tees: dict[str, BinaryIO] = {}

    # --- writing ---

    def record(
        self,
        *,
        collector: str,
        source: str | None,
        kind: CaptureKind,
        request: dict[str, Any],
        status: int | None,
        content_type: str,
        headers: dict[str, str],
        body: bytes,
    ) -> Capture:
        sha = hashlib.sha256(body).hexdigest()
        body_path = self._raw_dir() / sha
        if not body_path.exists():
            tmp = body_path.with_name(f".{sha}.{uuid.uuid4().hex}.tmp")
            write_file(tmp, body)
            os.replace(tmp, body_path)
        return self._append(
            collector=collector,
            source=source,
            kind=kind,
            request=request,
            status=status,
            content_type=content_type,
            headers=headers,
            sha=sha,
            size=len(body),
        )

    def stream_path(self, collector: str) -> Path:
        path = self._streams.get(collector)
        if path is None:
            path = self._raw_dir() / f".stream-{uuid.uuid4().hex}.tmp"
            self._streams[collector] = path
        return path

    def tee(self, collector: str, data: bytes) -> None:
        tee = self._tees.get(collector)
        if tee is None:
            descriptor = open_file(self.stream_path(collector), os.O_WRONLY | os.O_APPEND)
            tee = self._tees[collector] = os.fdopen(descriptor, "ab")
        tee.write(data)

    def finalize_stream(
        self,
        collector: str,
        *,
        source: str | None,
        kind: CaptureKind,
        request: dict[str, Any],
        status: int | None,
        content_type: str,
    ) -> Capture | None:
        tee = self._tees.pop(collector, None)
        if tee is not None:
            tee.close()
        path = self._streams.pop(collector, None)
        if path is None:
            return None
        if not path.exists():
            write_file(path, b"")  # the command printed nothing: that is still evidence
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
        sha = digest.hexdigest()
        size = path.stat().st_size
        target = self._raw_dir() / sha
        if target.exists():
            path.unlink()
        else:
            os.replace(path, target)
        return self._append(
            collector=collector,
            source=source,
            kind=kind,
            request=request,
            status=status,
            content_type=content_type,
            headers={},
            sha=sha,
            size=size,
        )

    def _raw_dir(self) -> Path:
        raw = self.root / "raw"
        make_dirs(raw)
        return raw

    def _append(
        self,
        *,
        collector: str,
        source: str | None,
        kind: CaptureKind,
        request: dict[str, Any],
        status: int | None,
        content_type: str,
        headers: dict[str, str],
        sha: str,
        size: int,
    ) -> Capture:
        line = Capture(
            id=uuid.uuid4().hex,
            source=source,
            collector=collector,
            kind=kind,
            request=request,
            status=status,
            content_type=content_type,
            headers=redact_headers(headers),
            body=f"raw/{sha}",
            sha256=sha,
            size=size,
            captured_at=self._clock.now().isoformat(),
        )
        data = (json.dumps(line.document(), default=str, ensure_ascii=False) + "\n").encode()
        make_dirs(self.root)
        fd = open_file(self.log_path, os.O_WRONLY | os.O_APPEND)
        try:
            _lock(fd)
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        return line


def _lock(fd: int) -> None:
    with contextlib.suppress(ImportError, OSError):
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX)  # released by close()


# --- reading ---


def read_captures(log_path: Path) -> Iterator[tuple[dict[str, Any], bytes]]:
    """Yield (capture line, body bytes) for each capture in ``log_path``, in order.

    Raises CollectionError naming the file and line for anything that cannot be trusted:
    unparseable JSON, a schema of another major version, a field the format requires to
    read that is missing or not a string, a body path outside ``raw/``, a missing body, or
    a body whose sha256 does not match. Unknown fields are ignored.
    """
    root = log_path.parent
    fix = f"remove or repair the line named in {log_path}; recon writes only valid lines"
    with open(log_path, encoding="utf-8") as f:
        for number, text in enumerate(f, start=1):
            if not text.strip():
                continue
            where = f"{log_path}:{number}"
            try:
                line = json.loads(text)
            except json.JSONDecodeError as exc:
                raise CollectionError(f"{where}: not JSON ({exc})", fix=fix) from exc
            if not isinstance(line, dict):
                raise CollectionError(f"{where}: a capture must be a JSON object", fix=fix)
            schema = line.get("schema")
            match = _SCHEMA.match(schema) if isinstance(schema, str) else None
            if match is None:
                msg = f"{where}: schema {schema!r} is not {CAPTURE_SCHEMA}"
                raise CollectionError(msg, fix=fix)
            if match.group(1) != "1":
                msg = f"{where}: schema {schema} is a major version this recon cannot read"
                raise CollectionError(msg, fix="upgrade recon to read this log")
            absent = [name for name in _REQUIRED_TO_READ if not isinstance(line.get(name), str)]
            if absent:
                names = ", ".join(repr(name) for name in absent)
                msg = f"{where}: a capture needs {names} as a string (recon.v1.capture)"
                raise CollectionError(msg, fix=fix)
            body_ref = line.get("body")
            if not isinstance(body_ref, str) or not _BODY_PATH.match(body_ref):
                msg = f"{where}: body must be 'raw/<sha256>', got {body_ref!r}"
                raise CollectionError(msg, fix=fix)
            body_path = root / body_ref
            try:
                body = body_path.read_bytes()
            except OSError as exc:
                msg = f"{where}: body {body_ref} unreadable ({exc})"
                raise CollectionError(msg, fix=fix) from exc
            actual = hashlib.sha256(body).hexdigest()
            if actual != body_ref.removeprefix("raw/") or actual != line["sha256"]:
                msg = f"{where}: body {body_ref} does not match its sha256"
                raise CollectionError(msg, fix=fix)
            yield line, body
