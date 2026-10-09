"""A failed request is classified by what the caller should do: retry, re-key, or fix."""

import httpx
import pytest

from recon.adapters._out.http_requester import classify_http_failure


def _status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.test/x")
    response = httpx.Response(code, request=request)
    return httpx.HTTPStatusError(f"HTTP {code}", request=request, response=response)


@pytest.mark.parametrize(
    ("code", "kind"),
    [
        (401, "auth"),
        (403, "auth"),
        (429, "transient"),
        (500, "transient"),
        (503, "transient"),
        (400, "collection"),
        (404, "collection"),
    ],
)
def test_status_codes(code, kind):
    assert classify_http_failure(_status_error(code)) == kind


def test_transport_errors_are_transient():
    request = httpx.Request("GET", "https://example.test/x")
    assert classify_http_failure(httpx.ConnectError("refused", request=request)) == "transient"
    assert classify_http_failure(httpx.ReadTimeout("slow", request=request)) == "transient"
