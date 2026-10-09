"""Planning: every problem, with a path; every run, with its effects; no effects of its own."""

import pytest

from recon.application.plan import BrokenPlugin, build_plan
from recon.composition import load_registry


@pytest.fixture(scope="module")
def registry():
    return load_registry()


def _plan(raw, registry, **kwargs):
    return build_plan(
        raw,
        collector_types=registry.collector_types,
        transforms=registry.transforms,
        broken=kwargs.pop("broken", registry.broken),
        env=kwargs.pop("env", {}),
    )


def _paths(plan):
    return {p.path: p.message for p in plan.problems}


class TestValidPlans:
    def test_fan_out_resolves_every_run_with_effects(self, registry):
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
                        "params": {"q": "attention"},
                    },
                ],
            },
            registry,
        )
        assert plan.valid, plan.problems
        runs = {r.output: r.as_dict() for r in plan.runs}
        assert set(runs) == {"scan-a", "scan-s2", "q"}
        assert runs["scan-a"]["effects"] == ["subprocess"]
        assert runs["scan-a"]["commands"] == ["rg x", "rg y"]
        assert runs["q"]["effects"] == ["network"]
        assert runs["q"]["url"] == "https://api.example.test/search/attention"
        assert runs["q"]["type_url"] == "recon.v1.records"
        assert plan.effects() == ["network", "subprocess"]

    def test_undeclared_effects_are_unknown_never_none(self, registry):
        from dataclasses import replace

        types = dict(registry.collector_types)
        types["mystery"] = replace(types["cli"], effects=None, check=lambda e: [])
        plan = build_plan(
            {"collectors": [{"name": "m", "type": "mystery"}]},
            collector_types=types,
            transforms=registry.transforms,
        )
        assert plan.runs[0].as_dict()["effects"] == "unknown"
        assert plan.effects() == ["unknown"]

    def test_unset_auth_env_is_a_warning_not_a_problem(self, registry):
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
        assert any("GH_TOKEN_FOR_TEST is not set" in w for w in plan.warnings)
        assert not _plan(raw, registry, env={"GH_TOKEN_FOR_TEST": "t"}).warnings


class TestEveryProblemWithAPath:
    def test_reports_all_problems_not_the_first(self, registry):
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
        assert "unknown collector type 'ftp'" in paths["collectors[0].type"]
        assert "api, capture, cli, web" in paths["collectors[0].type"], "names what is installed"
        assert "source 'nowhere' not in catalog" in paths["collectors[1].source"]
        assert "unknown transform $nope" in paths["collectors[2].normalize.t"]
        assert "collectors[3].method" in paths
        assert "slash form" in paths["collectors[4].type_url"]
        assert "endpoint" in paths["collectors[5].endpoint"]
        assert "duplicate source name" in paths["catalog[1].name"]
        assert "unknown key" in paths["preserve-raw"]

    def test_old_type_names_are_unknown_types(self, registry):
        plan = _plan({"collectors": [{"name": "o", "type": "command", "run": "x"}]}, registry)
        assert "unknown collector type 'command'" in _paths(plan)["collectors[0].type"]

    def test_misspelt_field_is_located(self, registry):
        plan = _plan(
            {"collectors": [{"name": "c", "type": "cli", "run": "true", "normalise": {}}]},
            registry,
        )
        assert "collectors[0].normalise" in _paths(plan)

    def test_output_collision_is_a_problem(self, registry):
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
        assert "also produced by collectors[0]" in _paths(plan)["collectors[1].name"]

    def test_capture_log_name_is_reserved(self, registry):
        plan = _plan({"collectors": [{"name": "captures", "type": "cli", "run": "true"}]}, registry)
        assert "reserved" in _paths(plan)["collectors[0].name"]

    def test_api_without_a_source_or_catalog(self, registry):
        plan = _plan({"collectors": [{"name": "a", "type": "api", "endpoint": "/x"}]}, registry)
        assert "needs a source" in _paths(plan)["collectors[0].source"]

    def test_unresolved_endpoint_placeholder_is_caught(self, registry):
        plan = _plan(
            {
                "catalog": [{"name": "gh", "url": "https://api.github.com"}],
                "collectors": [
                    {
                        "name": "i",
                        "type": "api",
                        "endpoint": "/repos/{owner}/{repo}/issues",
                        "params": {"owner": "{owner}", "repo": "gnx"},
                    }
                ],
            },
            registry,
        )
        message = _paths(plan)["collectors[0].endpoint"]
        assert "{owner}" in message and "{repo}" not in message

    def test_unresolved_body_placeholder_is_caught_before_a_request(self, registry):
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
        assert "{topic}" in _paths(plan)["collectors[0].body"]

    @pytest.mark.parametrize("raw", [None, [], "collectors: x"])
    def test_not_a_mapping(self, registry, raw):
        plan = _plan(raw, registry)
        assert not plan.valid and plan.problems[0].path == ""

    def test_missing_and_empty_collectors(self, registry):
        assert "collectors" in _paths(_plan({"catalog": []}, registry))
        assert "empty" in _paths(_plan({"collectors": []}, registry))["collectors"]


class TestBrokenPlugins:
    def test_a_broken_type_is_named_as_broken_not_unknown(self, registry):
        broken = [BrokenPlugin("recon.collectors", "sql", "pkg.mod:t", "ImportError: no db")]
        plan = _plan({"collectors": [{"name": "q", "type": "sql"}]}, registry, broken=broken)
        message = _paths(plan)["collectors[0].type"]
        assert "installed but failed to load" in message and "no db" in message
        assert any("pkg.mod:t" in w for w in plan.warnings)

    def test_a_broken_plugin_does_not_affect_other_collectors(self, registry):
        broken = [BrokenPlugin("recon.collectors", "sql", "pkg.mod:t", "ImportError: no db")]
        plan = _plan(
            {"collectors": [{"name": "c", "type": "cli", "run": "true"}]}, registry, broken=broken
        )
        assert plan.valid
