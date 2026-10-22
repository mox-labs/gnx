"""HTTP for the provider adapters: clients per event loop, one request, its failure classified.

hardline speaks to providers over HTTP itself, on httpx2, with no provider SDK between. So
nothing reads the environment behind a row's back: an SDK filled an omitted ``base_url`` from
``OPENAI_BASE_URL`` or ``ANTHROPIC_BASE_URL`` and sent ``OPENAI_ORG_ID`` as a header. A
request goes where its row says, with the key its row names, and nothing else.

How httpx2 (2.10) is used, each choice checked against its source and recorded in
``docs/design/2026-10-22-record.md``:

* **No ambient configuration.** Clients and the transport are built with ``trust_env=False``,
  so no proxy variable (``HTTP_PROXY``, ``HTTPS_PROXY``, ``ALL_PROXY``) reroutes a request and
  neither ``SSL_CERT_FILE`` nor ``SSL_CERT_DIR`` changes what it trusts.
* **TLS** is verified against the operating system's trust store, through ``truststore``,
  which httpx2 uses by default where httpx used certifi's bundle.
* **Redirects are not followed** (httpx2's default, kept). A 3xx is a ``ConfigError`` whose fix
  names where to point the row's ``base_url``: a moved endpoint is a row to correct, and
  following it would send the row's key to wherever it points.
* **A proxy only when the row names one** (``proxy:``), on the transport; never one from the
  environment, and never one carrying credentials, which would sit in a config file.
* **One timeout**, the row's ``timeout_s``, bounds each phase (connect, write, read, waiting
  for a pooled connection). httpx2's own default is 5 seconds, far below a model's reply time.
* **No retries below the runtime.** The transport is built with ``retries=0`` and httpx2 never
  retries a status, so a row's ``retries`` is the only retry policy.

A client's connection pool belongs to the event loop of its first request, so clients are kept
per loop (``LoopClients``), keyed by location and timeout; the key travels per request, never in
a client.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx2

from hardline.adapters._out._classify import classify
from hardline.adapters._out._clients import LoopClients
from hardline.adapters._out._scrub import scrub
from hardline.domain.errors import BackendError, ConfigError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from hardline.domain.types import ModelSpec

#: What carries a request: the network, or a test's ``httpx2.MockTransport``. A backend names it
#: by this name, so httpx2 is named in this file alone.
Transport = httpx2.AsyncBaseTransport

#: The most of a provider's error body quoted in a BackendError.
_DETAIL_CHARS = 500


def network_transport(proxy: str | None = None) -> httpx2.AsyncBaseTransport:
    """The real network, through ``proxy`` when a row names one. The workspace's test guard
    refuses this call, so no test reaches a provider; a test hands a backend an
    ``httpx2.MockTransport`` instead."""
    return httpx2.AsyncHTTPTransport(trust_env=False, retries=0, proxy=proxy)


def client_timeout(spec: ModelSpec) -> httpx2.Timeout:
    return httpx2.Timeout(spec.timeout_s)


def location_problems(spec: ModelSpec) -> list[str]:
    """``base_url: ...`` when the row's location is missing or not an http(s) URL."""
    if not spec.base_url:
        return [
            "base_url: not set. build_runtime writes each backend's default into its rows; "
            "a runtime built another way needs one named"
        ]
    try:
        url = httpx2.URL(spec.base_url)
    except httpx2.InvalidURL as e:
        return [f"base_url: {spec.base_url!r} is not a URL: {e}"]
    if url.scheme not in ("http", "https") or not url.host:
        return [f"base_url: {spec.base_url!r} must be an http:// or https:// URL"]
    return _proxy_problems(spec.proxy)


def _proxy_problems(proxy: str | None) -> list[str]:
    """``proxy: ...`` when the row names a proxy hardline cannot use. The value is never
    echoed, since a proxy URL can carry a password."""
    if proxy is None:
        return []
    try:
        url = httpx2.URL(proxy)
    except httpx2.InvalidURL:
        return ["proxy: not a URL"]
    if url.scheme not in ("http", "https") or not url.host:
        return ["proxy: must be an http:// or https:// URL"]
    if url.userinfo:
        return [
            "proxy: carries credentials, which would sit in a config file; use a proxy that "
            "needs none, or one that authenticates this machine"
        ]
    return []


class Http:
    """One provider adapter's HTTP. ``transport`` replaces the network, as a test does."""

    def __init__(self, transport: httpx2.AsyncBaseTransport | None = None) -> None:
        self._transport = transport
        self._clients: LoopClients[httpx2.AsyncClient] = LoopClients(lambda client: client.aclose())

    async def aclose(self) -> None:
        await self._clients.aclose()

    def _client(self, spec: ModelSpec, base_url: str) -> httpx2.AsyncClient:
        return self._clients.get(
            (base_url, spec.timeout_s, spec.proxy),
            lambda: httpx2.AsyncClient(
                base_url=base_url,
                timeout=client_timeout(spec),
                follow_redirects=False,
                trust_env=False,
                transport=(
                    self._transport
                    if self._transport is not None
                    else network_transport(spec.proxy)
                ),
            ),
        )

    async def post(
        self,
        spec: ModelSpec,
        path: str,
        body: dict[str, Any],
        headers: Mapping[str, str],
        api_key: str | None,
    ) -> dict[str, Any]:
        """POST ``body`` as JSON to ``path`` under the row's location; the reply's JSON object.

        Every failure is a BackendError naming the row and where it sent, with the key
        scrubbed: a transport failure is transient, a status is classified, and a reply that is
        not a JSON object is ``unknown``.
        """
        problems = location_problems(spec)
        if problems or spec.base_url is None:
            raise ConfigError("\n".join(f"models.{spec.name}.{problem}" for problem in problems))
        where = f"model {spec.name!r} ({spec.backend} → {spec.base_url})"
        try:
            response = await self._client(spec, spec.base_url).post(
                path, json=body, headers=dict(headers)
            )
        except httpx2.TimeoutException as e:
            raise BackendError(
                f"{where}: no reply within timeout_s={spec.timeout_s:g} ({type(e).__name__})",
                reason="timeout",
            ) from None
        except httpx2.TransportError as e:
            raise BackendError(
                f"{where}: {type(e).__name__}: {scrub(str(e), api_key)}", reason="unavailable"
            ) from None
        except httpx2.HTTPError as e:
            raise BackendError(
                f"{where}: {type(e).__name__}: {scrub(str(e), api_key)}", reason="unknown"
            ) from None

        status = response.status_code
        if response.is_redirect:
            raise _redirected(spec, path, response)
        if not response.is_success:
            reason, retry_after = classify(status, response.headers)
            detail = scrub(_error_detail(response), api_key)
            raise BackendError(
                f"{where}: HTTP {status}: {detail}", reason=reason, retry_after=retry_after
            )
        try:
            data = response.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise BackendError(
                f"{where}: HTTP {status}, but the body is not a JSON object: "
                f"{scrub(response.text[:_DETAIL_CHARS], api_key)!r}",
                reason="unknown",
            )
        return data


def _redirected(spec: ModelSpec, path: str, response: httpx2.Response) -> ConfigError:
    """A 3xx: the row's location is wrong, so a config error whose fix says where to point it.
    When the new location ends with the path asked for, the base it implies is the fix."""
    key = f"models.{spec.name}.base_url"
    location = response.headers.get("location")
    if not location:
        return ConfigError(
            f"{key}: {spec.base_url} answered HTTP {response.status_code}, a redirect with no "
            "Location; hardline does not follow redirects",
            fix=f"set {key} to where the endpoint is now",
        )
    moved = str(response.request.url.join(location))
    suffix = "/" + path
    base = moved[: -len(suffix)] if moved.endswith(suffix) else None
    return ConfigError(
        f"{key}: {spec.base_url} answered HTTP {response.status_code}, redirecting to "
        f"{moved!r}. hardline does not follow redirects, which would send the key along",
        fix=(
            f"set {key} to {base!r}"
            if base
            else f"set {key} to where the endpoint is now; the server pointed at {moved!r}"
        ),
    )


def _error_detail(response: httpx2.Response) -> str:
    """The provider's own message: ``error.message`` in both protocols, else the body's text."""
    try:
        message = response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        message = None
    if isinstance(message, str) and message:
        return message[:_DETAIL_CHARS]
    text = response.text.strip()
    return json.dumps(text[:_DETAIL_CHARS]) if text else "(empty body)"
