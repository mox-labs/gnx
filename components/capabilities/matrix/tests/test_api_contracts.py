"""The API contracts the 2026-10 audit added: errors, type URLs, scheduling, shared context.

Each class pins one requirement from the audit (research/drafts/api-design-canon/AUDIT-*.md).
"""

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from matrix import (
    AgentDefinition,
    AgentRuntimeError,
    ComponentError,
    ComponentRegistry,
    ConfigError,
    MatrixConfig,
    MatrixError,
    NotFoundError,
    Orchestrator,
    TypedStruct,
    compose,
    config_env_var,
    default_registry,
    discover_sources,
    parse_type_url,
    runtime_type_url,
    type_url,
)
from matrix.adapters._out.runtime.model import ModelAgentRuntime

# --- errors ------------------------------------------------------------------------


class TestErrorHierarchy:
    def test_every_deliberate_error_is_a_matrix_error(self):
        for cls in (ConfigError, NotFoundError, ComponentError, AgentRuntimeError):
            assert issubclass(cls, MatrixError)

    def test_lookup_miss_is_a_readable_key_error(self):
        registry = ComponentRegistry()
        with pytest.raises(KeyError) as e:
            registry.create("t.v1/missing")
        assert isinstance(e.value, NotFoundError)
        # KeyError would repr-quote the message; NotFoundError keeps it a sentence.
        assert str(e.value).startswith("Unknown component type: 't.v1/missing'")

    def test_config_error_is_a_value_error(self):
        assert issubclass(ConfigError, ValueError)


# --- type URLs ---------------------------------------------------------------------


class TestTypeUrl:
    @pytest.mark.parametrize(
        "url",
        ["matrix.v1/runtime.claude-sdk", "ix.v1/sensor.function-test", "acme.tools.v2beta1/x"],
    )
    def test_valid(self, url):
        assert str(parse_type_url(url)) == url

    @pytest.mark.parametrize(
        "url", ["test.probe", "matrix/runtime.x", "Matrix.v1/x", "m.v0/x", "m.v1/", "m.v1/X"]
    )
    def test_invalid_names_the_expected_shape(self, url):
        with pytest.raises(ConfigError, match=r"<namespace>\.v<version>/<resource>"):
            parse_type_url(url)

    def test_builder(self):
        assert type_url("ix", 1, "sensor.activation") == "ix.v1/sensor.activation"
        assert runtime_type_url("strands") == "matrix.v1/runtime.strands"

    def test_registry_refuses_a_malformed_url_at_registration(self):
        with pytest.raises(ConfigError, match="not a type URL"):
            ComponentRegistry().register("strands", dict)

    def test_duplicate_registration_is_a_config_error(self):
        registry = ComponentRegistry().register("t.v1/a", dict)
        with pytest.raises(ConfigError, match="Duplicate registration"):
            registry.register("t.v1/a", dict)


# --- scheduling --------------------------------------------------------------------


class Sleeper:
    """Records when it ran; sleeps so overlap is observable."""

    def __init__(self, name, log, *, delay=0.05, fail=False, requires=frozenset()):
        self.name = name
        self.requires = requires
        self.provides = f"t.v1/{name}"
        self._log = log
        self._delay = delay
        self._fail = fail

    async def run(self, construct):
        self._log.append(("start", self.name))
        await asyncio.sleep(self._delay)
        if self._fail:
            raise RuntimeError(f"{self.name} broke")
        self._log.append(("end", self.name))
        return TypedStruct(self.provides, self.name)


class TestConcurrency:
    async def test_default_runs_one_at_a_time(self):
        log = []
        await Orchestrator([Sleeper("a", log), Sleeper("b", log)]).run()
        assert log[1] == ("end", log[0][1])  # first finished before the second started

    async def test_independent_members_overlap(self):
        log = []
        await Orchestrator([Sleeper("a", log), Sleeper("b", log)], concurrency=2).run()
        assert [event for event, _ in log[:2]] == ["start", "start"]

    async def test_ledger_order_is_batch_order_however_scheduled(self):
        log = []
        slow, fast = Sleeper("a", log, delay=0.05), Sleeper("b", log, delay=0.0)
        sequential = await Orchestrator([Sleeper("a", []), Sleeper("b", [])]).run()
        concurrent = await Orchestrator([slow, fast], concurrency=2).run()
        assert [a.producer for a in concurrent.ledger] == [a.producer for a in sequential.ledger]

    async def test_a_failure_keeps_siblings_that_finished(self):
        log = []
        ok = Sleeper("a", log, delay=0.0)
        bad = Sleeper("b", log, delay=0.02, fail=True)
        with pytest.raises(ComponentError) as e:
            await Orchestrator([ok, bad], concurrency=2).run()
        assert e.value.component == "b"
        assert "t.v1/a" in e.value.construct

    async def test_timeout_names_the_component(self):
        with pytest.raises(ComponentError, match=r"'a' failed: timed out after 0\.01s"):
            await Orchestrator([Sleeper("a", [], delay=1.0)], timeout_s=0.01).run()

    def test_bad_settings_fail_at_construction(self):
        with pytest.raises(ConfigError):
            Orchestrator([], concurrency=0)
        with pytest.raises(ConfigError):
            Orchestrator([], timeout_s=0)


# --- shared context ----------------------------------------------------------------


class ThirdPartyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    models: dict[str, Any] | None = None
    cwd: str | None = None


class ThirdPartyRuntime:
    def __init__(self, config: ThirdPartyConfig) -> None:
        self.config = config

    async def run(self, definition, task):  # pragma: no cover - not called
        raise NotImplementedError


class TestSharedContext:
    MODELS = {"models": {"m": {"backend": "mock", "model": "m", "family": "mock"}}}

    def _registry(self):
        return default_registry(discover=False).register_typed(
            runtime_type_url("thirdparty"), ThirdPartyConfig, ThirdPartyRuntime
        )

    def test_a_third_party_runtime_receives_models_and_cwd_without_being_named(self):
        config = MatrixConfig.model_validate(
            {"models": self.MODELS, "runtimes": {"x": {"type": "thirdparty"}}}
        )
        container = compose(config, registry=self._registry(), context={"cwd": "/work"})
        runtime = container.runtime("x")
        assert runtime.config.models == self.MODELS
        assert runtime.config.cwd == "/work"

    def test_explicit_options_win_over_context(self):
        config = MatrixConfig.model_validate(
            {
                "models": self.MODELS,
                "runtimes": {"x": {"type": "thirdparty", "cwd": "/mine"}},
            }
        )
        runtime = compose(config, registry=self._registry(), context={"cwd": "/work"}).runtime("x")
        assert runtime.config.cwd == "/mine"

    def test_context_is_not_forced_on_runtimes_that_do_not_declare_it(self):
        # The mock runtime's config forbids unknown keys; offering it cwd must not break it.
        config = MatrixConfig.model_validate({"runtimes": {"m": {"type": "mock"}}})
        compose(config, registry=default_registry(discover=False), context={"cwd": "/work"})


# --- discovery ---------------------------------------------------------------------


class TestDiscovery:
    def test_env_var_name(self):
        assert config_env_var("ix") == "IX_CONFIG"
        assert config_env_var("my-tool") == "MY_TOOL_CONFIG"

    def test_explicit_file_is_the_highest_tier(self, tmp_path, monkeypatch):
        explicit = tmp_path / "x.yaml"
        explicit.write_text("{}")
        monkeypatch.setenv("MYTOOL_CONFIG", str(explicit))
        sources = discover_sources("mytool", project_root=tmp_path)
        assert sources[-1] == explicit
        assert sources[1] == tmp_path / "mytool.yaml"

    def test_explicit_file_that_does_not_exist_is_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MYTOOL_CONFIG", str(tmp_path / "nope.yaml"))
        with pytest.raises(ConfigError, match=r"\$MYTOOL_CONFIG points at"):
            discover_sources("mytool")


# --- runtime errors ----------------------------------------------------------------


class TestRuntimeErrors:
    async def test_a_provider_failure_is_an_agent_runtime_error(self):
        from hardline import build_runtime

        models = build_runtime(
            config={
                "models": {
                    "m": {
                        "backend": "mock",
                        "model": "m",
                        "family": "mock",
                        "retries": 0,
                        "options": {"script": [{"error": "unavailable"}]},
                    }
                }
            }
        )
        runtime = ModelAgentRuntime(models)
        definition = AgentDefinition(name="a", tools=(), model="m")
        with pytest.raises(AgentRuntimeError, match="agent 'a'") as e:
            await runtime.run(definition, "hi")
        assert e.value.__cause__ is not None
