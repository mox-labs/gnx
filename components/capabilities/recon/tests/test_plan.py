"""Planning: every problem, with a path; every run, with its effects; no effects of its own."""

from pathlib import Path
from typing import Any, ClassVar

import pytest
import yaml

from recon.application.plan import Plan, build_plan
from recon.composition import load_plugins
from recon.domain.collector import BrokenPlugin, Collector, CollectorContext, Plugins


@pytest.fixture(scope="module")
def registry() -> Plugins:
    return load_plugins()


def _plan(raw: Any, registry: Plugins, **kwargs: Any) -> Plan:
    plugins = Plugins(
        registry.collector_types, registry.transforms, kwargs.pop("broken", registry.broken)
    )
    return build_plan(raw, plugins, kwargs.pop("env", {}))


def _never_built(ctx: CollectorContext) -> Collector:
    raise AssertionError("planning builds no collector")


def _paths(plan: Plan) -> dict[str, str]:
    return {p.path: p.message for p in plan.problems}


class TestValidPlans:
    def test_fan_out_resolves_every_run_with_effects(self, registry: Plugins) -> None:
        plan = _plan(
            {
                "catalog": [
                    {"name": "a", "type": "local", "url": "/tmp"},
                    {"name": "s2", "url": "https://api.example.test"},
                ],
                "collectors": [
                    {"name": "scan", "type": "cli", "run": "rg {pattern}", "patterns": ["x", "y"]},
                    {
                        "name": "q",
                        "type": "api",
                        "source": "s2",
                        "endpoint": "/search/{q}",
                        "vars": {"q": "attention"},
                        "params": {"limit": "5"},
                    },
                ],
            },
            registry,
        )
        assert plan.valid, plan.problems
        runs = {r.output: r.document() for r in plan.runs}
        # The cli collector fans out over the local source alone (owner ruling 2026-10-22).
        assert set(runs) == {"scan-a", "q"}
        assert runs["scan-a"]["effects"] == ["subprocess"]
        assert runs["scan-a"]["commands"] == ["rg x", "rg y"]
        assert runs["q"]["effects"] == ["network"]
        # vars: fill the endpoint and go nowhere else; params: are the query.
        assert runs["q"]["url"] == "https://api.example.test/search/attention?limit=5"
        assert runs["q"]["type_url"] == "recon.v1.records"
        assert plan.effects() == ["network", "subprocess"]

    def test_undeclared_effects_are_unknown_never_none(self, registry: Plugins) -> None:
        from dataclasses import replace

        types = dict(registry.collector_types)
        types["mystery"] = replace(types["cli"], effects=None, check=lambda e: [])
        plan = build_plan(
            {"collectors": [{"name": "m", "type": "mystery"}]},
            Plugins(types, registry.transforms, []),
            {},
        )
        assert plan.runs[0].document()["effects"] == "unknown"
        assert plan.effects() == ["unknown"]

    def test_unset_auth_env_is_a_warning_not_a_problem(self, registry: Plugins) -> None:
        raw = {
            "catalog": [
                {
                    "name": "gh",
                    "url": "https://api.github.com",
                    "auth": {"header": "Authorization", "env": "GH_TOKEN_FOR_TEST"},
                }
            ],
            "collectors": [{"name": "r", "type": "api", "endpoint": "/x"}],
        }
        plan = _plan(raw, registry, env={})
        assert plan.valid
        [warning] = plan.warnings
        assert warning.kind == "env_unset" and warning.path == "catalog.0.auth.env"
        assert "GH_TOKEN_FOR_TEST is not set" in warning.message
        assert not _plan(raw, registry, env={"GH_TOKEN_FOR_TEST": "t"}).warnings


class TestEveryProblemWithAPath:
    def test_reports_all_problems_not_the_first(self, registry: Plugins) -> None:
        plan = _plan(
            {
                "catalog": [{"name": "s", "url": "https://x"}, {"name": "s", "url": "https://y"}],
                "collectors": [
                    {"name": "a", "type": "ftp"},
                    {"name": "b", "type": "api", "source": "nowhere", "endpoint": "/x"},
                    {"name": "c", "type": "cli", "run": "true", "normalize": {"t": "x|$nope"}},
                    {"name": "d", "type": "cli", "run": "true", "method": "FETCH"},
                    {"name": "e", "type": "cli", "run": "true", "type_url": "recon/v1/x"},
                    {"name": "f", "type": "api", "source": "s"},
                ],
                "preserve-raw": True,
            },
            registry,
        )
        paths = _paths(plan)
        assert not plan.valid
        assert "unknown collector type 'ftp'" in paths["collectors.0.type"]
        assert "api, capture, cli, mcp, sse, tail, web" in paths["collectors.0.type"], (
            "names what is installed"
        )
        assert "source 'nowhere' not in catalog" in paths["collectors.1.source"]
        assert "unknown transform $nope" in paths["collectors.2.normalize.t"]
        assert "collectors.3.method" in paths
        assert "slash form" in paths["collectors.4.type_url"]
        assert "endpoint" in paths["collectors.5.endpoint"]
        assert "duplicate source name" in paths["catalog.1.name"]
        assert "unknown key" in paths["preserve-raw"]

    def test_old_type_names_are_unknown_types(self, registry: Plugins) -> None:
        plan = _plan({"collectors": [{"name": "o", "type": "command", "run": "x"}]}, registry)
        assert "unknown collector type 'command'" in _paths(plan)["collectors.0.type"]

    def test_misspelt_field_is_located(self, registry: Plugins) -> None:
        plan = _plan(
            {"collectors": [{"name": "c", "type": "cli", "run": "true", "normalise": {}}]},
            registry,
        )
        assert "collectors.0.normalise" in _paths(plan)

    def test_output_collision_is_a_problem(self, registry: Plugins) -> None:
        plan = _plan(
            {
                "catalog": [{"name": "arxiv", "type": "local", "url": "/tmp"}],
                "collectors": [
                    {"name": "foo", "type": "cli", "run": "true"},
                    {"name": "foo-arxiv", "type": "cli", "source": "arxiv", "run": "true"},
                ],
            },
            registry,
        )
        assert "also produced by collectors.0" in _paths(plan)["collectors.1.name"]

    @pytest.mark.parametrize(
        ("first", "second", "view"),
        [("a-b", "a_b", "a_b"), ("Foo", "foo", "Foo"), ("oa.works", "OA-works", "oa_works")],
    )
    def test_outputs_that_would_be_one_table_are_a_problem(
        self, registry: Plugins, first: str, second: str, view: str
    ) -> None:
        """Regression: ``a-b`` and ``a_b`` are one view to SQL, which turns every character
        but a letter, digit or ``_`` into ``_``; ``Foo`` and ``foo`` are one view, since SQL
        ignores case, and one file on macOS. Both pairs planned valid, then one table's rows
        were read as the other's, or overwrote them, and the survey said ok."""
        plan = _plan(
            {
                "collectors": [
                    {"name": first, "type": "cli", "run": "true"},
                    {"name": second, "type": "cli", "run": "true"},
                ]
            },
            registry,
        )
        [problem] = plan.problems
        assert (problem.path, problem.kind) == ("collectors.1.name", "name_taken")
        assert problem.got == second
        assert f"collectors.0 ({first!r})" in problem.message
        assert view.lower() in problem.message.lower()

    def test_a_fanned_out_output_that_would_be_one_table_with_another_is_a_problem(
        self, registry: Plugins
    ) -> None:
        plan = _plan(
            {
                "catalog": [{"name": "Repo", "type": "local", "url": "/tmp"}],
                "collectors": [
                    {"name": "scan", "type": "cli", "run": "true"},
                    {"name": "scan-repo", "type": "cli", "source": "Repo", "run": "true"},
                ],
            },
            registry,
        )
        assert "collectors.0 ('scan-Repo')" in _paths(plan)["collectors.1.name"]

    @pytest.mark.parametrize("name", ["bad/name", "../x", "..", "a..b", "back\\slash"])
    def test_an_output_name_that_is_not_one_file_name_is_a_problem(
        self, registry: Plugins, name: str
    ) -> None:
        """Regression: ``bad/name`` planned valid and its table failed as ``unknown`` for
        want of a directory; ``../x`` wrote outside the archive."""
        plan = _plan({"collectors": [{"name": name, "type": "cli", "run": "true"}]}, registry)
        [problem] = plan.problems
        assert (problem.path, problem.kind) == ("collectors.0.name", "invalid_value")
        assert problem.got == name

    @pytest.mark.parametrize("name", ["", ".", ".hidden", "-x"])
    def test_an_output_name_must_start_with_a_letter_a_digit_or_underscore(
        self, registry: Plugins, name: str
    ) -> None:
        """Regression: ``name: ""`` planned valid and wrote ``.jsonl``, a hidden file no
        query lists, without a word."""
        plan = _plan({"collectors": [{"name": name, "type": "cli", "run": "true"}]}, registry)
        [problem] = plan.problems
        assert (problem.path, problem.kind, problem.got) == (
            "collectors.0.name",
            "invalid_value",
            name,
        )
        assert "start with a letter, a digit or _" in problem.message

    def test_a_source_name_that_makes_an_output_not_one_file_name_is_a_problem(
        self, registry: Plugins
    ) -> None:
        plan = _plan(
            {
                "catalog": [{"name": "x/y", "type": "local", "url": "/tmp"}],
                "collectors": [{"name": "scan", "type": "cli", "run": "true"}],
            },
            registry,
        )
        [problem] = plan.problems
        assert problem.path == "collectors.0.name" and problem.got == "scan-x/y"

    @pytest.mark.parametrize("name", ["captures", "telemetry", "Captures", "TELEMETRY"])
    def test_the_names_of_the_archive_s_logs_are_reserved(
        self, registry: Plugins, name: str
    ) -> None:
        plan = _plan({"collectors": [{"name": name, "type": "cli", "run": "true"}]}, registry)
        assert "reserved" in _paths(plan)["collectors.0.name"]

    def test_api_without_a_source_or_catalog(self, registry: Plugins) -> None:
        plan = _plan({"collectors": [{"name": "a", "type": "api", "endpoint": "/x"}]}, registry)
        assert "needs a source" in _paths(plan)["collectors.0.source"]

    def test_unresolved_endpoint_placeholder_is_caught(self, registry: Plugins) -> None:
        plan = _plan(
            {
                "catalog": [{"name": "gh", "url": "https://api.github.com"}],
                "collectors": [
                    {
                        "name": "i",
                        "type": "api",
                        "endpoint": "/repos/{owner}/{repo}/issues",
                        "vars": {"owner": "{owner}", "repo": "gnx"},
                    }
                ],
            },
            registry,
        )
        message = _paths(plan)["collectors.0.endpoint"]
        assert "{owner}" in message and "{repo}" not in message

    def test_unresolved_params_placeholder_is_a_problem_with_its_path(
        self, registry: Plugins
    ) -> None:
        """Nothing fills a ``{placeholder}`` in a params value; it used to be dropped from
        the query string at run time, and the plan said nothing."""
        plan = _plan(
            {
                "catalog": [{"name": "s2", "url": "https://api.example.test"}],
                "collectors": [
                    {
                        "name": "s",
                        "type": "api",
                        "endpoint": "/search",
                        "params": {"query": "{topic}", "limit": "20", "fields": "{a},{b}"},
                    }
                ],
            },
            registry,
        )
        paths = _paths(plan)
        assert set(paths) == {"collectors.0.params.query", "collectors.0.params.fields"}
        assert "unresolved placeholder(s) {topic}" in paths["collectors.0.params.query"]
        assert "{a}, {b}" in paths["collectors.0.params.fields"]

    def test_a_params_value_with_braces_that_is_not_a_placeholder_is_fine(
        self, registry: Plugins
    ) -> None:
        plan = _plan(
            {
                "catalog": [{"name": "wd", "url": "https://query.example.test"}],
                "collectors": [
                    {
                        "name": "q",
                        "type": "api",
                        "endpoint": "/sparql",
                        "params": {"query": "SELECT ?p WHERE { ?p wdt:P106 wd:Q82594 . }"},
                    }
                ],
            },
            registry,
        )
        assert plan.valid, plan.problems

    def test_a_web_endpoint_placeholder_is_a_problem(self, registry: Plugins) -> None:
        """Regression: a web collector fills no placeholder, and ``/page/{slug}`` was
        fetched literally with a valid plan."""
        plan = _plan(
            {
                "catalog": [{"name": "docs", "type": "web", "url": "https://docs.example.test"}],
                "collectors": [{"name": "p", "type": "web", "endpoint": "/page/{slug}"}],
            },
            registry,
        )
        [problem] = plan.problems
        assert (problem.path, problem.kind) == ("collectors.0.endpoint", "unresolved")
        assert "{slug}" in problem.message

    @pytest.mark.parametrize(
        "collector",
        [
            {"type": "web", "endpoint": "/search?q=\\w{3}-\\d{2,4}"},
            {"type": "api", "endpoint": "/search", "params": {"q": "\\w{3}\\d{2,}"}},
            {"type": "cli", "run": "rg --json '\\w{3}' .", "source": "here"},
        ],
        ids=["web", "api", "cli"],
    )
    def test_a_regex_quantifier_is_not_a_placeholder(
        self, registry: Plugins, collector: dict[str, Any]
    ) -> None:
        plan = _plan(
            {
                "catalog": [
                    {"name": "s", "type": "web", "url": "https://s.example.test"},
                    {"name": "here", "type": "local", "url": "/tmp"},
                ],
                "collectors": [{"name": "c", "source": "s", **collector}],
            },
            registry,
        )
        assert plan.valid, plan.problems

    def test_url_in_a_command_with_no_source_to_fill_it_is_a_plan_problem(
        self, registry: Plugins
    ) -> None:
        """Regression: ``{url}`` in a ``cli`` run with no source planned valid and failed only
        when surveyed, though the plan knows the run has no source. A type checks each run
        against its source (``CollectorType.check_run``)."""
        plan = _plan({"collectors": [{"name": "c", "type": "cli", "run": "echo {url}"}]}, registry)
        [problem] = plan.problems
        assert (problem.path, problem.kind) == ("collectors.0.run", "unresolved")
        assert "source:" in (problem.fix or "")

    def test_braces_a_command_owns_are_passed_as_written(self, registry: Plugins) -> None:
        """In ``run:`` recon fills ``{url}`` and ``{pattern}``; any other ``{...}`` is the
        command's own (cargo's ``--format '{p} {f}'``, a jq object) and is not checked."""
        plan = _plan(
            {"collectors": [{"name": "c", "type": "cli", "run": "cargo tree --format '{p} {f}'"}]},
            registry,
        )
        assert plan.valid, plan.problems
        assert plan.runs[0].detail["commands"] == ["cargo tree --format '{p} {f}'"]

    def test_unresolved_body_placeholder_is_caught_before_a_request(
        self, registry: Plugins
    ) -> None:
        plan = _plan(
            {
                "catalog": [{"name": "exa", "url": "https://api.exa.ai"}],
                "collectors": [
                    {
                        "name": "s",
                        "type": "api",
                        "endpoint": "/search",
                        "method": "POST",
                        "body": {"query": "{topic}"},
                    }
                ],
            },
            registry,
        )
        assert "{topic}" in _paths(plan)["collectors.0.body"]

    @pytest.mark.parametrize("raw", [None, [], "collectors: x"])
    def test_not_a_mapping(self, registry: Plugins, raw: list[Any] | str | None) -> None:
        plan = _plan(raw, registry)
        assert not plan.valid and plan.problems[0].path == ""

    def test_missing_and_empty_collectors(self, registry: Plugins) -> None:
        assert "collectors" in _paths(_plan({"catalog": []}, registry))
        assert "empty" in _paths(_plan({"collectors": []}, registry))["collectors"]


class TestUnderscoreKeys:
    """Top-level keys beginning with ``_`` are the config's own notebook: recon ignores them.

    Missions written before 0.9 hold a shared normalize spec under such a key and alias it
    from every collector; 0.9's strict keys rejected the holder.
    """

    def test_a_mission_written_before_strict_keys_still_plans(self, registry: Plugins) -> None:
        fixture = Path(__file__).parent / "fixtures" / "pre-0.9-anchor-mission.yaml"
        raw = yaml.safe_load(fixture.read_text())
        assert "_normalize" in raw, "the fixture must keep the anchor holder"

        plan = _plan(raw, registry)

        assert plan.valid, plan.problems
        assert [r.output for r in plan.runs] == ["attention_mechanisms", "retrieval_augmentation"]
        assert plan.config is not None
        assert all(c.normalize == raw["_normalize"] for c in plan.config.collectors)

    def test_ignored_whatever_it_holds(self, registry: Plugins) -> None:
        raw = {
            "_notes": "why this mission exists",
            "_scratch": [{"anything": 1}],
            "collectors": [{"name": "c", "type": "cli", "run": "true"}],
        }
        assert _plan(raw, registry).valid

    def test_every_other_unknown_key_is_still_a_problem(self, registry: Plugins) -> None:
        raw = {
            "notes": "no underscore",
            "_notes": "underscore",
            "collectors": [{"name": "c", "type": "cli", "run": "true", "_note": "nested"}],
        }
        paths = _paths(_plan(raw, registry))
        assert "unknown key" in paths["notes"] and "beginning with _" in paths["notes"]
        assert "collectors.0._note" in paths, "the rule is for top-level keys only"
        assert "_notes" not in paths


class TestBrokenPlugins:
    def test_a_broken_type_is_named_as_broken_not_unknown(self, registry: Plugins) -> None:
        broken = [BrokenPlugin("recon.collectors", "sql", "pkg.mod:t", "ImportError: no db")]
        plan = _plan({"collectors": [{"name": "q", "type": "sql"}]}, registry, broken=broken)
        message = _paths(plan)["collectors.0.type"]
        assert "installed but failed to load" in message and "no db" in message
        assert [(w.kind, w.got) for w in plan.warnings] == [
            ("plugin_broken", "recon.collectors:sql")
        ]
        assert "pkg.mod:t" in plan.warnings[0].message

    def test_a_broken_plugin_does_not_affect_other_collectors(self, registry: Plugins) -> None:
        broken = [BrokenPlugin("recon.collectors", "sql", "pkg.mod:t", "ImportError: no db")]
        plan = _plan(
            {"collectors": [{"name": "c", "type": "cli", "run": "true"}]}, registry, broken=broken
        )
        assert plan.valid


@pytest.mark.parametrize("fmt", ["html", "text"])
def test_an_api_collector_that_would_parse_no_records_is_a_problem(
    registry: Plugins, fmt: str
) -> None:
    """Regression: an api collector with response_format html or text wrote an empty table
    and reported success; the body is not JSON or XML, so it held no records to keep."""
    plan = _plan(
        {
            "catalog": [{"name": "s", "url": "https://api.example.test"}],
            "collectors": [{"name": "c", "type": "api", "endpoint": "/x", "response_format": fmt}],
        },
        registry,
    )
    [problem] = [p for p in plan.problems if p.path == "collectors.0.response_format"]
    assert problem.got == fmt and "web collector" in problem.fix


class TestCheckingDoesNotStopEarly:
    """Every problem at once, and a plan that says when it could not check everything."""

    def test_an_unknown_key_does_not_mask_the_rest_of_its_collector(
        self, registry: Plugins
    ) -> None:
        """Regression: a collector with a misspelt key reported only the key, so its other
        problems surfaced one fix later."""
        plan = _plan({"collectors": [{"name": "c", "type": "cli", "normalise": {}}]}, registry)
        kinds = {p.path: p.kind for p in plan.problems}
        assert kinds == {"collectors.0.normalise": "unknown_key", "collectors.0.run": "missing_key"}
        assert plan.complete

    def test_the_keys_of_a_type_not_installed_are_left_unjudged(self, registry: Plugins) -> None:
        """Changed requirement: which keys a collector may hold is its type's to say, so with
        the type unknown its other keys cannot be judged; the plan says it is incomplete."""
        plan = _plan({"collectors": [{"name": "c", "type": "ftp", "normalise": {}}]}, registry)
        assert {p.path: p.kind for p in plan.problems} == {"collectors.0.type": "name_unknown"}
        assert not plan.complete

    def test_an_unknown_key_nested_in_a_source_does_not_mask_its_collectors(
        self, registry: Plugins
    ) -> None:
        plan = _plan(
            {
                "catalog": [{"name": "s", "url": "https://x", "auth": {"hedaer": "X-Key"}}],
                "collectors": [{"name": "c", "type": "api", "source": "s"}],
            },
            registry,
        )
        assert set(_paths(plan)) == {"catalog.0.auth.hedaer", "collectors.0.endpoint"}
        assert plan.complete

    def test_an_entry_that_does_not_parse_makes_the_plan_incomplete(
        self, registry: Plugins
    ) -> None:
        plan = _plan({"collectors": [{"name": "c", "type": "cli", "patterns": "x"}]}, registry)
        assert not plan.valid and not plan.complete
        assert _paths(plan).keys() == {"collectors.0.patterns"}

    def test_a_collector_naming_a_source_that_did_not_parse_is_not_told_it_is_missing(
        self, registry: Plugins
    ) -> None:
        plan = _plan(
            {
                "catalog": [{"name": "s", "type": "api", "url": ["https://x"]}],
                "collectors": [{"name": "c", "type": "api", "source": "s", "endpoint": "/x"}],
            },
            registry,
        )
        assert set(_paths(plan)) == {"catalog.0.url"}
        assert not plan.complete

    @pytest.mark.parametrize("raw", [None, [], {"collectors": "x"}])
    def test_a_config_of_the_wrong_shape_is_incomplete(
        self, registry: Plugins, raw: dict[str, Any] | list[Any] | None
    ) -> None:
        plan = _plan(raw, registry)
        assert not plan.complete and plan.problems[0].kind == "wrong_shape"


class TestProblemRecords:
    """The shared problem record: a kind from the agreed vocabulary, and the legal values
    where the set is closed."""

    def test_kinds_and_allowed_values(self, registry: Plugins) -> None:
        plan = _plan(
            {
                "catalog": [{"name": "s", "url": "https://x"}, {"name": "s", "url": "https://y"}],
                "collectors": [
                    {"name": "a", "type": "ftp", "source": "nowhere"},
                    {"name": "b", "type": "api", "endpoint": "/x", "method": "FETCH"},
                    {"name": "c", "type": "cli", "run": "true", "normalize": {"t": "x|$nope"}},
                    {"name": "d", "type": "cli", "normalise": {}},
                ],
                "preserve_raw": "yes",
            },
            registry,
        )
        by_path = {p.path: p for p in plan.problems}
        assert by_path["collectors.0.type"].kind == "name_unknown"
        assert {"api", "cli", "capture", "web"} <= set(by_path["collectors.0.type"].allowed)
        assert by_path["collectors.0.source"].allowed == ("s",)
        assert by_path["collectors.1.method"].kind == "invalid_value"
        assert by_path["collectors.1.method"].allowed == ("GET", "POST", "PUT", "PATCH", "DELETE")
        assert by_path["collectors.2.normalize.t"].kind == "name_unknown"
        assert "$html2text" in by_path["collectors.2.normalize.t"].allowed
        assert by_path["collectors.3.normalise"].kind == "unknown_key"
        assert "normalize" in by_path["collectors.3.normalise"].allowed
        assert by_path["collectors.3.run"].kind == "missing_key"
        assert by_path["catalog.1.name"].kind == "name_taken"
        assert by_path["preserve_raw"].kind == "wrong_shape"

    def test_every_kind_is_in_the_vocabulary(self, registry: Plugins) -> None:
        import recon

        plan = _plan(
            {
                "catalog": [{"name": "gh", "url": "https://x", "auth": {"env": "UNSET_FOR_TEST"}}],
                "collectors": [{"name": "c", "type": "api", "endpoint": "/{x}", "extra": 1}],
            },
            registry,
        )
        assert plan.problems and plan.warnings
        assert {p.kind for p in plan.problems + plan.warnings} <= set(recon.PROBLEM_KINDS)


class TestLocalSourcesAreRelativeToTheProject:
    def test_a_relative_url_is_resolved_against_the_base(
        self, registry: Plugins, tmp_path: Path
    ) -> None:
        raw = {
            "catalog": [
                {"name": "here", "type": "local", "url": "."},
                {"name": "sub", "type": "local", "url": "src"},
                {"name": "abs", "type": "local", "url": "/opt"},
                {"name": "api", "url": "https://x"},
            ],
            "collectors": [{"name": "ls", "type": "cli", "run": "ls"}],
        }
        plan = build_plan(raw, registry, {}, base=tmp_path)
        assert plan.config is not None
        urls = {s.name: s.url for s in plan.config.catalog}
        assert urls == {
            "here": str(tmp_path),
            "sub": str(tmp_path / "src"),
            "abs": "/opt",
            "api": "https://x",
        }
        assert {r.output: r.detail["cwd"] for r in plan.runs}["ls-sub"] == str(tmp_path / "src")

    def test_a_local_source_a_run_uses_must_be_a_directory(
        self, registry: Plugins, tmp_path: Path
    ) -> None:
        """Regression: a ``cli`` collector whose ``local`` source named a directory that did
        not exist ran in recon's working directory, while the plan showed the missing one as
        its ``cwd`` and the survey said ok: a mistyped path mined whatever tree recon was
        started in."""
        (tmp_path / "here").mkdir()
        raw = {
            "catalog": [
                {"name": "here", "type": "local", "url": "here"},
                {"name": "gone", "type": "local", "url": "does-not-exist"},
                {"name": "unused", "type": "local", "url": "also-missing"},
            ],
            "collectors": [
                {"name": "a", "type": "cli", "source": "here", "run": "pwd"},
                {"name": "b", "type": "cli", "source": "gone", "run": "pwd"},
            ],
        }
        plan = build_plan(raw, registry, {}, base=tmp_path, is_directory=Path.is_dir)
        [problem] = plan.problems
        assert (problem.path, problem.kind) == ("catalog.1.url", "invalid_value")
        assert problem.got == str(tmp_path / "does-not-exist")
        assert str(tmp_path) in problem.fix, "says what a relative url is relative to"
        assert {r.output: r.detail["cwd"] for r in plan.runs}["a"] == str(tmp_path / "here")


class TestATypeOwnsItsSettings:
    """Regression: every key an entry may hold was a field of ``CollectorEntry``, which
    forbade the rest, so a collector type that needed a setting of its own needed an edit to
    recon. A type declares the schema of its own keys; the plan validates them by it."""

    @pytest.fixture
    def plugins(self, registry: Plugins) -> Plugins:
        from pydantic import BaseModel, ConfigDict

        from recon.domain.collector import CollectorType

        class SqlSettings(BaseModel):
            model_config = ConfigDict(frozen=True, extra="forbid")
            database: str
            sql: str
            timeout_ms: int = 1000

        types = dict(registry.collector_types)
        types["sql"] = CollectorType(
            create=_never_built, effects=None, settings=SqlSettings,
            source_kinds=frozenset({"sqlite"}),
        )  # fmt: skip
        return Plugins(types, registry.transforms, [])

    def test_a_type_s_own_keys_plan_valid(self, plugins: Plugins) -> None:
        raw = {"collectors": [{"name": "q", "type": "sql", "database": "d.db", "sql": "SELECT 1"}]}
        plan = build_plan(raw, plugins, {})
        assert plan.valid, plan.problems
        assert plan.config is not None
        assert plan.config.collectors[0].settings == {"database": "d.db", "sql": "SELECT 1"}

    def test_its_schema_says_what_is_wrong_with_them_each_at_its_path(
        self, plugins: Plugins
    ) -> None:
        raw = {"collectors": [{"name": "q", "type": "sql", "sql": "x", "timeout_ms": "soon",
                               "databse": "d.db"}]}  # fmt: skip
        plan = build_plan(raw, plugins, {})
        kinds = {p.path: p.kind for p in plan.problems}
        assert kinds == {
            "collectors.0.database": "missing_key",
            "collectors.0.timeout_ms": "wrong_shape",
            "collectors.0.databse": "unknown_key",
        }
        [unknown] = [p for p in plan.problems if p.kind == "unknown_key"]
        assert {"database", "sql", "normalize", "type_url"} <= set(unknown.allowed)

    def test_one_type_s_keys_are_not_another_s(self, plugins: Plugins) -> None:
        """``run`` is the cli type's, not the api type's: it is unknown on an api collector,
        where it used to be accepted and ignored."""
        raw = {
            "catalog": [{"name": "s", "url": "https://x"}],
            "collectors": [{"name": "a", "type": "api", "endpoint": "/x", "run": "true"}],
        }
        plan = build_plan(raw, plugins, {})
        [problem] = plan.problems
        assert (problem.path, problem.kind) == ("collectors.0.run", "unknown_key")
        assert "endpoint" in problem.allowed and "run" not in problem.allowed

    def test_extract_is_for_a_type_that_reads_a_body(self, plugins: Plugins) -> None:
        plan = build_plan(
            {"collectors": [{"name": "c", "type": "cli", "run": "true", "extract": "data"}]},
            plugins,
            {},
        )
        [problem] = plan.problems
        assert (problem.path, problem.kind) == ("collectors.0.extract", "unknown_key")


class TestFanOutByKind:
    """Owner ruling 2026-10-22: an unpinned collector fans out only over the catalog sources of
    kinds its type accepts. The plan names each source it skipped, by name and kind, and a
    collector with no source of a kind it accepts is a plan problem."""

    MIXED: ClassVar[list[dict[str, str]]] = [
        {"name": "repo", "type": "local", "url": "/tmp"},
        {"name": "docs", "type": "web", "url": "https://docs.example.test"},
        {"name": "srv", "type": "mcp", "command": "server"},
    ]

    def test_an_unpinned_collector_runs_against_the_kinds_its_type_accepts(
        self, registry: Plugins
    ) -> None:
        """Regression: each ran against every source, so the ``cli`` command ran in recon's
        working directory for ``docs`` and ``srv``, and the ``web`` collector asked a
        directory and an MCP server's command for a page."""
        raw = {"catalog": self.MIXED, "collectors": [
            {"name": "todos", "type": "cli", "run": "rg TODO ."},
            {"name": "page", "type": "web"},
        ]}  # fmt: skip
        plan = _plan(raw, registry)
        assert plan.valid, plan.problems
        assert [run.output for run in plan.runs] == ["todos-repo", "page-docs"]
        assert [s.document() for s in plan.skipped] == [
            {"collector": "todos", "source": "docs", "kind": "web"},
            {"collector": "todos", "source": "srv", "kind": "mcp"},
            {"collector": "page", "source": "repo", "kind": "local"},
            {"collector": "page", "source": "srv", "kind": "mcp"},
        ]
        assert plan.warnings == [], "fan_out_mixed is gone: what it warned of no longer runs"

    def test_a_type_that_takes_any_kind_skips_nothing(self, registry: Plugins) -> None:
        raw = {"catalog": self.MIXED, "collectors": [{"name": "c", "type": "capture"}]}
        plan = _plan(raw, registry)
        assert plan.valid, plan.problems
        assert [run.output for run in plan.runs] == ["c-repo", "c-docs", "c-srv"]
        assert plan.skipped == []

    def test_a_collector_with_no_source_of_a_kind_it_accepts_is_a_problem(
        self, registry: Plugins
    ) -> None:
        raw = {"catalog": self.MIXED[1:], "collectors": [
            {"name": "todos", "type": "cli", "run": "pwd"},
        ]}  # fmt: skip
        plan = _plan(raw, registry)
        [problem] = plan.problems
        assert (problem.path, problem.kind) == ("collectors.0.source", "missing_key")
        assert "local" in problem.message
        assert "docs (web)" in problem.message and "srv (mcp)" in problem.message
        assert problem.fix is not None and "catalog:" in problem.fix
        assert plan.runs == [] and not plan.valid

    def test_a_source_of_a_kind_nothing_adds_is_not_also_a_missing_source(
        self, registry: Plugins
    ) -> None:
        raw = {
            "catalog": [{"name": "repo", "type": "git", "url": "https://github.com/x/y"}],
            "collectors": [{"name": "c", "type": "cli", "run": "true"}],
        }
        [problem] = _plan(raw, registry).problems
        assert (problem.path, problem.kind) == ("catalog.0.type", "name_unknown")

    def test_a_collector_is_given_only_the_variables_of_the_sources_it_runs_against(
        self, registry: Plugins
    ) -> None:
        """Regression: the ``cli`` type was declared the API key's variable of a source it
        fanned out over, so its commands could read it."""
        from recon.application.collectors import declared_env

        raw = {
            "catalog": [
                {"name": "repo", "type": "local", "url": "/tmp"},
                {"name": "keyed", "url": "https://api.example.test",
                 "auth": {"env": "API_KEY", "header": "x-api-key"}},
            ],
            "collectors": [
                {"name": "todos", "type": "cli", "run": "rg TODO ."},
                {"name": "q", "type": "api", "endpoint": "/x"},
            ],
        }  # fmt: skip
        config = _plan(raw, registry, env={"API_KEY": "k"}).config
        assert config is not None
        assert declared_env(config, registry.collector_types) == {
            "cli": set(),
            "api": {"API_KEY"},
        }


class TestSourceKinds:
    """The source kinds are open: a collector type adds the kinds it needs."""

    def test_an_unknown_kind_is_a_plan_problem_naming_the_installed_kinds(
        self, registry: Plugins
    ) -> None:
        """Rewritten from a test that pinned ``git`` being refused by the model: what is
        required is that a kind nothing installed adds is a plan problem that names the
        kinds there are."""
        raw = {
            "catalog": [{"name": "repo", "type": "git", "url": "https://github.com/x/y"}],
            "collectors": [{"name": "c", "type": "cli", "run": "true", "source": "repo"}],
        }
        plan = _plan(raw, registry)
        [problem] = plan.problems
        assert (problem.path, problem.kind, problem.got) == (
            "catalog.0.type",
            "name_unknown",
            "git",
        )
        assert problem.allowed == ("api", "file", "local", "mcp", "web")
        assert "api, file, local, mcp, web" in problem.message

    @pytest.mark.parametrize(
        ("source", "collector", "kinds"),
        [
            # cli would run in recon's working directory, and survey `ok`
            ({"type": "web", "url": "https://x.test"}, {"type": "cli", "run": "pwd"}, "local"),
            # the API's URL would be used as an MCP server's
            ({"type": "api", "url": "https://x.test"}, {"type": "mcp", "tool": "t"}, "mcp"),
            ({"type": "local", "url": "/tmp"}, {"type": "api", "endpoint": "/x"}, "api, web"),
            ({"type": "mcp", "command": "srv"}, {"type": "api", "endpoint": "/x"}, "api, web"),
            ({"type": "mcp", "command": "srv"}, {"type": "web"}, "api, web"),
        ],
    )
    def test_a_collector_pinned_to_a_source_of_a_kind_its_type_cannot_use_is_a_problem(
        self, registry: Plugins, source: dict[str, Any], collector: dict[str, Any], kinds: str
    ) -> None:
        """Regression: nothing tied a collector type to the kinds of source it can use, so
        each of these planned valid, and a ``cli`` pinned to a ``web`` source ran in recon's
        working directory and surveyed ``ok``. Each type declares the kinds it accepts."""
        raw = {
            "catalog": [{"name": "s", **source}],
            "collectors": [{"name": "c", "source": "s", **collector}],
        }
        [problem] = _plan(raw, registry).problems
        assert (problem.path, problem.kind, problem.got) == (
            "collectors.0.source",
            "invalid_value",
            "s",
        )
        assert f"uses a source of kind {kinds}" in problem.message
        assert f"'s' is a {source['type']} source" in problem.message

    def test_a_type_that_declares_no_kinds_takes_a_source_of_any(self, registry: Plugins) -> None:
        """``capture`` keeps the captures a source's name filters, whatever its kind; a
        plugin type that declares nothing is not judged."""
        raw = {
            "catalog": [{"name": "s", "type": "web", "url": "https://x.test"}],
            "collectors": [{"name": "c", "type": "capture", "source": "s"}],
        }
        assert _plan(raw, registry).valid

    def test_a_kind_an_installed_type_adds_plans(self, registry: Plugins) -> None:
        from recon.domain.collector import CollectorType

        types = dict(registry.collector_types)
        types["sqlite"] = CollectorType(
            create=_never_built, effects=None, source_kinds=frozenset({"sqlite"})
        )
        raw = {
            "catalog": [{"name": "db", "type": "sqlite", "url": "data.db"}],
            "collectors": [{"name": "c", "type": "sqlite", "source": "db"}],
        }
        plan = build_plan(raw, Plugins(types, registry.transforms, []), {})
        assert plan.valid, plan.problems

    def test_a_key_no_kind_takes_is_unknown_and_a_url_is_needed(self, registry: Plugins) -> None:
        """A kind with no settings of its own takes the shared keys alone, and needs url:, as
        before the source's own keys were opened."""
        raw = {
            "catalog": [{"name": "s", "type": "api", "command": "x"}],
            "collectors": [{"name": "c", "type": "api", "source": "s", "endpoint": "/x"}],
        }
        problems = {p.path: p for p in _plan(raw, registry).problems}
        assert set(problems) == {"catalog.0.command", "catalog.0.url"}
        assert problems["catalog.0.command"].kind == "unknown_key"
        assert "rate_limit" in problems["catalog.0.command"].allowed
        assert problems["catalog.0.url"].kind == "missing_key"

    def test_a_kind_with_settings_of_its_own_is_judged_by_its_type(self, registry: Plugins) -> None:
        """A type that adds a kind can give it keys of its own (``source_settings``), say what
        it needs of the shared ones (``check_source``) and name the variables it reads
        (``source_env``): the plan validates, checks and warns through each."""
        from pydantic import BaseModel, ConfigDict

        from recon.application.collectors import declared_env
        from recon.domain.collector import CollectorType
        from recon.domain.errors import Problem

        class DbSource(BaseModel):
            model_config = ConfigDict(extra="forbid")
            path: str
            password: str = ""

        types = dict(registry.collector_types)
        types["sqlite"] = CollectorType(
            create=_never_built,
            effects=None,
            source_kinds=frozenset({"sqlite"}),
            source_settings=DbSource,
            check_source=lambda src: (
                []
                if src.settings["path"].endswith(".db")
                else [Problem("path", "not a database file", fix="name a .db file")]
            ),
            source_env=lambda src: (
                {src.settings["password"].removeprefix("env:"): "password"}
                if src.settings.get("password", "").startswith("env:")
                else {}
            ),
        )
        plugins = Plugins(types, registry.transforms, [])
        raw = {
            "catalog": [
                {"name": "ok", "type": "sqlite", "path": "a.db", "password": "env:DB_PW"},
                {"name": "bad", "type": "sqlite", "path": "a.txt", "pasword": "x"},
                {"name": "odd", "type": "sqlite", "path": "b.txt"},
            ],
            "collectors": [{"name": "c", "type": "sqlite"}],
        }
        plan = build_plan(raw, plugins, {})
        problems = {p.path: p for p in plan.problems}
        assert set(problems) == {"catalog.1.pasword", "catalog.2.path"}
        assert problems["catalog.1.pasword"].kind == "unknown_key"
        assert {"path", "password", "url"} <= set(problems["catalog.1.pasword"].allowed)
        assert problems["catalog.2.path"].message == "not a database file"
        assert not plan.complete, "a source its schema refused may hide more"
        [warning] = [w for w in plan.warnings if w.kind == "env_unset"]
        assert (warning.path, warning.got) == ("catalog.0.password", "DB_PW")

        ok = {"catalog": raw["catalog"][:1], "collectors": raw["collectors"]}
        config = build_plan(ok, plugins, {"DB_PW": "x"}).config
        assert config is not None
        assert declared_env(config, types) == {"sqlite": {"DB_PW"}}
