"""Tests for ApiCollector — helpers + injected-Requester behavior.

No monkeypatching. Dependencies (Requester, env) are constructor args,
so tests pass fakes directly.
"""

import pytest

from recon.adapters._out.api_collector import ApiCollector
from recon.adapters._out.capture_files import FilesystemCaptureLog
from recon.adapters._out.system_clock import SystemClock
from recon.composition import DEFAULT_USER_AGENT
from recon.domain.capture import REDACTED
from recon.domain.errors import NoRecordsError
from recon.domain.http import HttpResponse
from recon.domain.models import AuthConfig, CollectorEntry, SourceEntry
from recon.domain.normalize import resolve_path, to_records
from recon.domain.substitution import find_unresolved, substitute


class _FakeRequester:
    """Minimal Requester double — records calls, returns canned HttpResponse."""

    def __init__(
        self, response: HttpResponse | None = None, raise_on_call: Exception | None = None
    ):
        self.response = response
        self.calls: list[dict] = []
        self.raise_on_call = raise_on_call

    def request(self, source, method, url, *, params=None, headers=None, json_body=None):
        self.calls.append(
            {
                "source": source,
                "method": method,
                "url": url,
                "params": params or {},
                "headers": headers or {},
                "json_body": json_body,
            }
        )
        if self.raise_on_call:
            raise self.raise_on_call
        return self.response

    def get(self, source, url, *, headers=None):
        return self.request(source, "GET", url, params={}, headers=headers)


def _ok_response(body_text: str, content_type: str = "application/json") -> HttpResponse:
    return HttpResponse(
        status_code=200,
        headers={"content-type": content_type},
        body=body_text.encode(),
        text=body_text,
        url="https://fake.test/",
        content_type=content_type,
    )


class TestExtract:
    def test_dotted_path(self):
        data = {"feed": {"entry": [{"title": "test"}]}}
        assert resolve_path(data, "feed.entry") == [{"title": "test"}]

    def test_single_level(self):
        assert resolve_path({"data": [1, 2]}, "data") == [1, 2]

    def test_missing(self):
        assert resolve_path({"a": 1}, "b") is None

    def test_deep_missing(self):
        assert resolve_path({"a": {"b": 1}}, "a.c") is None


class TestEnsureList:
    def test_list_of_dicts(self):
        assert to_records([{"a": 1}, {"b": 2}], None) == [{"a": 1}, {"b": 2}]

    def test_filters_non_dicts(self):
        assert to_records([{"a": 1}, "skip", 42], None) == [{"a": 1}]

    def test_single_dict(self):
        assert to_records({"a": 1}, None) == [{"a": 1}]

    def test_none(self):
        assert to_records(None, None) == []

    @pytest.mark.parametrize(
        ("data", "extract"), [({"data": []}, "data"), ([], None), ({}, "data"), (None, "data")]
    )
    def test_an_empty_body_or_an_empty_list_has_no_records_and_nothing_to_say(self, data, extract):
        assert to_records(data, extract) == []


class TestNoPlausibleEmptyTable:
    """Regression: a body whose records recon could not find or keep gave an empty table and
    "ok", indistinguishable from a source that had nothing: a 200 carrying an error object,
    a changed response shape, a list of ids. Each now says why, as ``NoRecordsError``, which the
    survey turns into a warning."""

    def test_an_extract_that_resolves_to_nothing_in_a_non_empty_body(self):
        with pytest.raises(NoRecordsError) as excinfo:
            to_records({"error": {"message": "quota"}}, "data.items")
        found = excinfo.value
        assert (found.problem_kind, found.field) == ("extract_unresolved", "extract")
        assert "data.items" in str(found) and "error" in str(found)
        assert "extract:" in found.fix

    def test_an_extract_that_names_a_null(self):
        with pytest.raises(NoRecordsError, match="data"):
            to_records({"data": None, "total": 0}, "data")

    @pytest.mark.parametrize(
        ("data", "extract"), [({"ids": ["a", "b"]}, "ids"), (["a", "b"], None), ({"n": 3}, "n")]
    )
    def test_items_that_are_not_objects(self, data, extract):
        with pytest.raises(NoRecordsError) as excinfo:
            to_records(data, extract)
        found = excinfo.value
        assert (found.problem_kind, found.field) == ("records_not_objects", "extract")
        assert "not objects" in str(found) or "not an object" in str(found)

    def test_a_text_body_is_not_records(self):
        with pytest.raises(NoRecordsError, match="str"):
            to_records("text", None)


class TestResolveAuth:
    """Auth is observed where it lands: the headers and query params sent to the Requester."""

    @staticmethod
    def _sent(auth: AuthConfig, env: dict) -> dict:
        req = _FakeRequester(_ok_response('{"data": []}'))
        src = SourceEntry(name="s", url="https://x", auth=auth)
        entry = CollectorEntry(name="t", type="api", source="s", endpoint="/q", params={"q": "a"})
        list(ApiCollector(req, env=env, user_agent=DEFAULT_USER_AGENT).collect(entry, src))
        return req.calls[0]

    def test_header_no_prefix(self):
        sent = self._sent(AuthConfig(header="x-api-key", env="FAKE_KEY"), {"FAKE_KEY": "abc123"})
        assert sent["headers"]["x-api-key"] == "abc123"

    def test_header_with_prefix(self):
        auth = AuthConfig(header="Authorization", env="FAKE_KEY", prefix="Bearer ")
        sent = self._sent(auth, {"FAKE_KEY": "fc-xyz"})
        assert sent["headers"]["Authorization"] == "Bearer fc-xyz"

    def test_empty_env_returns_no_headers(self):
        auth = AuthConfig(header="Authorization", env="FAKE_KEY", prefix="Bearer ")
        sent = self._sent(auth, {})  # FAKE_KEY absent
        assert "Authorization" not in sent["headers"]

    def test_param_sends_the_key_as_a_query_parameter(self):
        """auth.param was declared and ignored before 0.9.0: OpenAlex-style api_key."""
        sent = self._sent(AuthConfig(param="api_key", env="FAKE_KEY"), {"FAKE_KEY": "k-123"})
        assert sent["params"] == {"q": "a", "api_key": "k-123"}
        assert "api_key" not in sent["headers"]

    def test_param_with_prefix_and_header_together(self):
        auth = AuthConfig(header="x-api-key", param="key", env="FAKE_KEY", prefix="p-")
        sent = self._sent(auth, {"FAKE_KEY": "v"})
        assert sent["params"]["key"] == "p-v"
        assert sent["headers"]["x-api-key"] == "p-v"

    def test_param_unset_env_sends_no_parameter(self):
        sent = self._sent(AuthConfig(param="api_key", env="FAKE_KEY"), {})
        assert "api_key" not in sent["params"]

    def test_param_key_never_reaches_the_capture_log(self, tmp_path):
        """The response URL carries the query string — and with auth.param, the key."""
        resp = HttpResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=b"{}",
            text="{}",
            url="https://x/q?q=a&api_key=k-SECRET",
            content_type="application/json",
        )
        req = _FakeRequester(resp)
        src = SourceEntry(name="s", url="https://x", auth=AuthConfig(param="api_key", env="K"))
        entry = CollectorEntry(name="t", type="api", source="s", endpoint="/q")
        log = FilesystemCaptureLog(tmp_path, SystemClock())
        list(
            ApiCollector(req, env={"K": "k-SECRET"}, user_agent=DEFAULT_USER_AGENT).collect(
                entry, src, captures=log
            )
        )

        import json
        from urllib.parse import parse_qs, urlsplit

        text = log.log_path.read_text()
        assert "k-SECRET" not in text
        logged = urlsplit(json.loads(text)["request"]["url"])
        assert parse_qs(logged.query) == {"q": ["a"], "api_key": [REDACTED]}


class TestCollectThroughRequester:
    def test_basic_get_yields_records(self):
        resp = _ok_response('{"data": [{"title": "A"}, {"title": "B"}]}')
        req = _FakeRequester(resp)
        col = ApiCollector(req, env={}, user_agent=DEFAULT_USER_AGENT)

        source = SourceEntry(name="s", url="https://x")
        entry = CollectorEntry(name="t", type="api", source="s", endpoint="/q", extract="data")
        records = list(col.collect(entry, source))
        assert [r["title"] for r in records] == ["A", "B"]

        # Requester was called exactly once with GET
        assert len(req.calls) == 1
        assert req.calls[0]["method"] == "GET"
        assert req.calls[0]["url"] == "https://x/q"

    def test_post_with_body(self):
        resp = _ok_response('{"results": [{"ok": true}]}')
        req = _FakeRequester(resp)
        col = ApiCollector(req, env={}, user_agent=DEFAULT_USER_AGENT)

        source = SourceEntry(name="exa", url="https://api.exa.ai")
        entry = CollectorEntry(
            name="search",
            type="api",
            source="exa",
            endpoint="/search",
            method="POST",
            body={"query": "transformer attention", "numResults": 5},
            extract="results",
        )
        list(col.collect(entry, source))
        assert req.calls[0]["method"] == "POST"
        assert req.calls[0]["json_body"] == {"query": "transformer attention", "numResults": 5}

    def test_get_without_body(self):
        resp = _ok_response('{"data": [{"x": 1}]}')
        req = _FakeRequester(resp)
        col = ApiCollector(req, env={}, user_agent=DEFAULT_USER_AGENT)

        source = SourceEntry(name="s", url="https://x")
        entry = CollectorEntry(name="g", type="api", source="s", endpoint="/q", extract="data")
        list(col.collect(entry, source))
        assert req.calls[0]["json_body"] is None

    def test_templated_body_end_to_end(self):
        resp = _ok_response('{"results": [{"ok": true}]}')
        req = _FakeRequester(resp)
        col = ApiCollector(req, env={}, user_agent=DEFAULT_USER_AGENT)

        source = SourceEntry(name="exa", url="https://api.exa.ai")
        entry = CollectorEntry(
            name="search",
            type="api",
            source="exa",
            endpoint="/search",
            method="POST",
            vars={"topic": "state space models"},
            body={"query": "{topic}", "numResults": 10},
            extract="results",
        )
        list(col.collect(entry, source))
        assert req.calls[0]["json_body"] == {"query": "state space models", "numResults": 10}
        assert req.calls[0]["params"] == {}, "a value that fills the body is not a query too"


class TestUserAgent:
    """Unless the mission says otherwise, recon says who it is."""

    def _sent(self, **source_fields):
        req = _FakeRequester(_ok_response("[]"))
        source = SourceEntry(name="s", url="https://x", **source_fields)
        entry = CollectorEntry(name="c", type="api", source="s", endpoint="/q")
        list(ApiCollector(req, env={}, user_agent=DEFAULT_USER_AGENT).collect(entry, source))
        return req.calls[0]["headers"]["User-Agent"]

    def test_the_default_names_recon_its_version_and_where_to_find_it(self):
        from recon import __version__

        ua = self._sent()
        assert ua.startswith(f"recon/{__version__} ")
        assert "github.com/mox-labs/gnx" in ua
        assert "Mozilla" not in ua and "Chrome" not in ua

    def test_a_mission_s_own_user_agent_is_sent_as_written(self):
        assert self._sent(user_agent="my-survey/2 (mailto:me@example.org)") == (
            "my-survey/2 (mailto:me@example.org)"
        )


class TestParams:
    """Every ``params`` value is sent as written, or the collector refuses to run.

    A value still holding a ``{placeholder}`` used to be left out of the query string
    without a word, and so was any value that merely contained a brace.
    """

    SPARQL = "SELECT ?p WHERE { ?p wdt:P106 wd:Q82594 . }"

    def _collect(self, params):
        req = _FakeRequester(_ok_response("[]"))
        source = SourceEntry(name="s", url="https://x")
        entry = CollectorEntry(name="c", type="api", source="s", endpoint="/q", params=params)
        list(ApiCollector(req, env={}, user_agent=DEFAULT_USER_AGENT).collect(entry, source))
        return req

    def test_a_value_containing_braces_is_sent(self):
        req = self._collect({"format": "json", "query": self.SPARQL})
        assert req.calls[0]["params"] == {"format": "json", "query": self.SPARQL}

    def test_an_unresolved_placeholder_is_a_plan_problem(self):
        """Changed requirement: the collector refused at run time what the plan already
        refuses, so the survey never reaches it."""
        from recon.application.plan import build_plan
        from recon.composition import load_plugins

        raw = {
            "catalog": [{"name": "s", "url": "https://x"}],
            "collectors": [{"name": "c", "type": "api", "endpoint": "/q",
                            "params": {"q": "{topic}", "n": "5"}}],
        }  # fmt: skip
        [problem] = build_plan(raw, load_plugins(), {}).problems
        assert (problem.path, problem.kind, problem.got) == ("collectors.0.params.q",
                                                             "unresolved", "{topic}")  # fmt: skip


class TestSubstituteUnified:
    def test_string_branch(self):
        assert substitute("hello {name}", {"name": "world"}) == "hello world"

    def test_flat_string(self):
        assert substitute({"q": "{topic}"}, {"topic": "attention"}) == {"q": "attention"}

    def test_preserves_non_strings(self):
        body = {"q": "{topic}", "n": 10, "flag": True, "nothing": None}
        out = substitute(body, {"topic": "x"})
        assert out == {"q": "x", "n": 10, "flag": True, "nothing": None}

    def test_nested_dict(self):
        assert substitute(
            {"contents": {"text": True, "filter": "{domain}"}}, {"domain": "arxiv.org"}
        ) == {"contents": {"text": True, "filter": "arxiv.org"}}

    def test_nested_list(self):
        assert substitute(
            {"includeDomains": ["{primary}", "secondary.com"]}, {"primary": "example.com"}
        ) == {"includeDomains": ["example.com", "secondary.com"]}

    def test_unresolved_placeholder_stays_literal(self):
        assert substitute({"q": "{missing}"}, {}) == {"q": "{missing}"}


class TestFindUnresolved:
    def test_empty_when_resolved(self):
        assert find_unresolved({"q": "hello"}) == []

    def test_finds_flat(self):
        assert find_unresolved({"q": "{topic}"}) == ["topic"]

    def test_finds_nested(self):
        assert sorted(find_unresolved({"a": {"b": "{x}"}, "c": ["{y}", "z"]})) == ["x", "y"]

    def test_ignores_non_strings(self):
        assert find_unresolved({"n": 10, "ok": True, "none": None}) == []


@pytest.mark.parametrize(
    "method,expected",
    [("GET", "GET"), ("POST", "POST"), ("PUT", "PUT"), ("PATCH", "PATCH"), ("DELETE", "DELETE")],
)
def test_method_expanded(method, expected):
    entry = CollectorEntry(name="x", type="api", endpoint="/y", method=method)
    assert entry.method == expected


class TestParamsAndVars:
    """Regression: ``params:`` was both the query string and the values for an endpoint's
    and a body's placeholders, so a value meant for a path or a POST body (a search topic, a
    target URL) also went out as a query parameter, into the server's access log."""

    def _sent(self, **settings):
        req = _FakeRequester(_ok_response("[]"))
        source = SourceEntry(name="s", url="https://x.test")
        entry = CollectorEntry(name="c", type="api", source="s", **settings)
        list(ApiCollector(req, env={}, user_agent=DEFAULT_USER_AGENT).collect(entry, source))
        return req.calls[0]

    def test_vars_fill_the_endpoint_and_the_body_and_are_sent_nowhere_else(self):
        sent = self._sent(
            endpoint="/repos/{owner}/{repo}",
            method="POST",
            body={"url": "{target}"},
            vars={"owner": "o", "repo": "r", "target": "https://t.test/p"},
            params={"per_page": "5"},
        )
        assert sent["url"] == "https://x.test/repos/o/r"
        assert sent["json_body"] == {"url": "https://t.test/p"}
        assert sent["params"] == {"per_page": "5"}

    def test_a_placeholder_vars_does_not_fill_is_a_plan_problem(self):
        from recon.application.plan import build_plan
        from recon.composition import load_plugins

        raw = {
            "catalog": [{"name": "s", "url": "https://x.test"}],
            "collectors": [{"name": "c", "type": "api", "endpoint": "/r/{owner}",
                            "params": {"owner": "o"}}],
        }  # fmt: skip
        [problem] = build_plan(raw, load_plugins(), {}).problems
        assert (problem.path, problem.kind) == ("collectors.0.endpoint", "unresolved")
        assert "vars:" in problem.fix
