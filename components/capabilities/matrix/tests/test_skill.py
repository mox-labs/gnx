"""``matrix --skill``: the text an agent learns the component protocol and config from.

Every claim in it is held to the code: the frontmatter is a valid skill with the installed
version, each command it names exists, the exit table is the one table, the tiers are the
ones read, the config keys are the model's, every example runs or composes, and the whole
of it sits within the first 5,000 tokens.
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import tomllib
from importlib.metadata import metadata
from pathlib import Path
from typing import Any

import pytest
import yaml

import matrix
from matrix import (
    EXIT_CODES,
    EXIT_MEANINGS,
    EXIT_NEXT,
    ConfigError,
    MatrixConfig,
    Registry,
    compose,
    discover_sources,
    load_config,
)
from matrix.adapters._in.cli import cli, main
from matrix.adapters._in.skill import (
    COMMANDS,
    COMPONENT_EXAMPLE,
    CONFIG_EXAMPLE,
    CONFIG_KEYS,
    ENTRY_POINT,
    EXTENSION_EXAMPLE,
    EXTRAS,
    NEEDS,
    REFERENCES,
    TIERS,
)

#: Claude Code keeps the first 5,000 tokens of an invoked skill after compaction. Tokens are
#: estimated here at 4 characters each, which is an estimate, not a tokenizer's count.
FIRST_TOKENS_AS_CHARS = 5_000 * 4


def _frontmatter(text: str) -> tuple[dict[str, Any], str]:
    assert text.startswith("---\n"), text[:40]
    head, body = text[4:].split("\n---\n", 1)
    data = yaml.safe_load(head)
    assert isinstance(data, dict)
    return data, body


def _fenced(text: str, language: str) -> list[str]:
    return re.findall(rf"```{language}\n(.*?)```", text, re.S)


def test_skill_prints_the_text_and_exits_0(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--skill"]) == 0
    assert capsys.readouterr().out == matrix.skill()


def test_the_frontmatter_parses_with_the_portable_keys_and_the_installed_version() -> None:
    data, body = _frontmatter(matrix.skill())
    assert list(data) == ["name", "description", "metadata"] and "version" not in data
    assert data["name"] == "matrix"
    assert data["metadata"] == {"version": matrix.__version__}
    assert body.lstrip("\n").splitlines()[0].startswith(f"# matrix {matrix.__version__}:")
    description = data["description"]
    assert 0 < len(description) <= 1024 and "<" not in description and ">" not in description
    assert not description.lower().startswith(("you", "i ", "use "))  # third person
    assert "This skill should be used when the user asks to" in description
    assert len(re.findall(r'"[^"]+"', description)) >= 5  # trigger phrases


def test_all_of_it_is_within_the_first_5000_tokens_in_this_order() -> None:
    skill = matrix.skill()
    assert len(skill) < FIRST_TOKENS_AS_CHARS
    headings = [
        "## When to use matrix",
        "## Install and check",
        "## The component protocol",
        "## Write and register a component",
        "## Config",
        "## Commands, in the order they are used",
        "## Errors and exit codes",
        "## References",
    ]
    positions = [skill.index(heading) for heading in headings]
    assert positions == sorted(positions)


def test_every_command_it_names_exists() -> None:
    skill = matrix.skill()
    named = set(re.findall(r"^matrix ([a-z-]+)", "\n".join(_fenced(skill, "bash")), re.M))
    named |= set(re.findall(r"`matrix ([a-z-]+)", skill))
    flags = {name for name in named if name.startswith("-")}
    commands = named - flags
    assert commands == set(cli.commands), commands ^ set(cli.commands)
    assert {command.split()[1] for command, _ in COMMANDS} == set(cli.commands)
    top = {option for param in cli.params for option in param.opts}
    assert flags <= top and "--skill" in top
    for command in cli.commands.values():
        assert "--json" in {o for p in command.params for o in p.opts}, command.name


def test_the_exit_table_is_the_one_table() -> None:
    rows = re.findall(r"^\| (\d+) \| `([a-z_]+)` \| (.*?) \| (.*?) \|$", matrix.skill(), re.M)
    assert [(kind, int(code)) for code, kind, _, _ in rows] == list(EXIT_CODES.items())
    for _, kind, meaning, next_step in rows:
        assert (meaning, next_step) == (EXIT_MEANINGS[kind], EXIT_NEXT[kind])
    assert set(EXIT_NEXT) == set(EXIT_CODES) == set(EXIT_MEANINGS)
    assert matrix.ConfigError("x").payload()["schema"] == "matrix.v1.error"
    assert '{"schema": "matrix.v1.error"' in matrix.skill()


def test_the_tiers_are_the_ones_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, root = tmp_path / "home", tmp_path / "project"
    monkeypatch.setattr(Path, "home", lambda: home)
    for variable, name in (("MATRIX_CONFIG", "shared.yaml"), ("ACME_CONFIG", "acme.yaml")):
        (tmp_path / name).write_text("{}\n")
        monkeypatch.setenv(variable, str(tmp_path / name))
    found = discover_sources("acme", project_root=root)
    expected = [
        home / ".matrix" / "config.yaml",
        home / ".acme" / "config.yaml",
        root / "matrix.yaml",
        root / "acme.yaml",
        tmp_path / "shared.yaml",
        tmp_path / "acme.yaml",
    ]
    assert found == expected
    shown = [
        path.replace("~", str(home)).replace("./", f"{root}/").replace("<tool>", "acme")
        for path, _ in TIERS
    ]
    assert shown[:4] == [str(path) for path in expected[:4]]
    assert [path for path, _ in TIERS[4:]] == ["$MATRIX_CONFIG", "$<TOOL>_CONFIG"]
    for tier, (path, _) in enumerate(TIERS, start=1):
        assert f"| {tier} | `{path}` |" in matrix.skill()


def test_the_config_keys_are_the_models_and_unknown_ones_are_refused() -> None:
    assert list(CONFIG_KEYS) == list(MatrixConfig.model_fields)
    for key in CONFIG_KEYS:
        assert f"- `{key}`: " in matrix.skill()
    with pytest.raises(ConfigError):
        load_config(sources=[io.StringIO("matrix: {runtimez: {}}\n")])


def test_the_extras_are_the_packages() -> None:
    declared = set(metadata("matrix").get_all("Provides-Extra") or []) - {"dev"}
    assert set(EXTRAS) == declared
    for name in EXTRAS:
        assert f"- `{name}`: " in matrix.skill()


def test_the_install_lines_name_this_release_by_its_tag() -> None:
    """matrix is installed from the repository, never a package index: every install line
    pins the tag of the release it belongs to, so a reader gets the matrix the skill
    describes. A version bump without the tag fails here."""
    tag = f"matrix-v{matrix.__version__}"
    source = (
        f"git+https://github.com/mox-labs/gnx@{tag}#subdirectory=components/capabilities/matrix"
    )
    text = matrix.skill()
    assert f'uv add "matrix @ {source}"' in text
    assert f'uv tool install "{source}"' in text
    assert f'uv run --with "matrix @ {source}"' in text
    assert "pip install" not in text


def test_the_points_and_needs_are_what_composition_hands_over() -> None:
    assert Registry().points == {
        "agent-runtime",
        "component",
        "model-runtime",
        "observer",
        "payload-type",
        "store",
    }
    container = compose(MatrixConfig())
    assert set(NEEDS) == set(container.needs()) | {"name"}


def test_the_component_example_runs_as_written() -> None:
    assert f"```python\n{COMPONENT_EXAMPLE.rstrip()}\n```" in matrix.skill()
    exec(compile(COMPONENT_EXAMPLE, "<skill>", "exec"), {})  # its asserts are the check


def test_the_extension_example_registers_wires_runs_and_refuses_an_unknown_key() -> None:
    skill = matrix.skill()
    assert f"```python\n{EXTENSION_EXAMPLE.rstrip()}\n```" in skill
    assert f"```toml\n{ENTRY_POINT.rstrip()}\n```" in skill
    group = tomllib.loads(ENTRY_POINT)["project"]["entry-points"]["matrix.extensions"]
    assert group == {"acme": "acme.matrix_extension:register"}
    namespace: dict[str, Any] = {}
    exec(compile(EXTENSION_EXAMPLE, "<skill>", "exec"), namespace)
    registry = matrix.default_registry(discover=False)
    namespace["register"](registry)
    container = compose(MatrixConfig(), registry=registry)
    wiring = container.wire(
        [{"name": "score", "type": "acme.v1.scorer", "config": {"weight": 2}}],
        inputs=["acme.v1.diff"],
    )
    run = asyncio.run(container.executor().run(wiring, {"acme.v1.diff": ["abc"]}))
    assert run.rows("acme.v1.score") == (6.0,)
    with pytest.raises(ConfigError, match=r"components\.score\.config\.wieght"):
        container.wire(
            [{"name": "score", "type": "acme.v1.scorer", "config": {"wieght": 2}}],
            inputs=["acme.v1.diff"],
        )


def test_the_config_example_composes_and_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (block,) = _fenced(matrix.skill(), "yaml")
    assert block == CONFIG_EXAMPLE
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.delenv("MATRIX_CONFIG", raising=False)
    (tmp_path / "matrix.yaml").write_text(block)
    assert main(["check", "--json"]) == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked["components"] == ["claim", "rate"]
    assert checked["agents"] == ["claimer"]
    assert checked["agent_runtimes"] == checked["model_runtimes"] == ["offline"]
    assert main(["plan", "--json"]) == 0
    planned = json.loads(capsys.readouterr().out)
    assert planned["agent_calls_min"] == planned["agent_calls_max"] == 1
    assert planned["model_calls_min"] == planned["model_calls_max"] == 1
    assert [r["stand_in"] for r in planned["agent_runtimes"] + planned["model_runtimes"]] == [
        True,
        True,
    ]
    (tmp_path / "diffs.jsonl").write_text('"- a\\n+ b"\n')
    assert main(["run", "--input", "acme.v1.diff=diffs.jsonl", "--json"]) == 0
    ran = json.loads(capsys.readouterr().out)
    assert ran["status"] == "completed" and Path(ran["location"]).is_dir()
    record = Path(ran["location"])
    assert json.loads((record / "acme.v1.rating.jsonl").read_text()) == {"score": 4}
    claim = json.loads((record / "acme.v1.claim-reply.jsonl").read_text())
    assert claim["task"] == "Claims in:\n- a\n+ b"
    call = json.loads((record / "matrix.v1.model-call.jsonl").read_text())
    assert call["prompt"].startswith("Rate:\n") and call["text"] == '{"score": 4}'


def test_the_agents_reference_steps_are_valid_steps() -> None:
    agent_block, model_block = _fenced(matrix.skill("agents"), "yaml")
    (step,) = yaml.safe_load(agent_block)
    config = matrix.AgentStepConfig.model_validate(step["config"])
    assert (config.response, config.output) == ("acme.v1.claim-reply", "acme.v1.claims")
    (step,) = yaml.safe_load(model_block)
    assert step["type"] == matrix.MODEL_STEP
    model = matrix.ModelStepConfig.model_validate(step["config"])
    assert (model.runtime, model.response, model.output) == (
        "fast",
        "acme.v1.rate-call",
        "acme.v1.rating",
    )


def test_every_reference_the_skill_lists_prints(capsys: pytest.CaptureFixture[str]) -> None:
    listed = re.findall(r"^- `([a-z]+)`: ", matrix.skill().split("## References", 1)[1], re.M)
    assert listed == list(REFERENCES)
    for name in REFERENCES:
        assert main(["--skill", "-r", name]) == 0
        text = capsys.readouterr().out
        assert text == matrix.skill(name) and text.startswith("# ")


def test_an_unknown_reference_is_a_usage_error_listing_the_references(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--skill", "-r", "nope"]) == 2
    assert "References: config, agents, running, telemetry, loading" in capsys.readouterr().err
    with pytest.raises(matrix.UsageError) as raised:
        matrix.skill("nope")
    assert raised.value.details["allowed"] == list(REFERENCES)


def test_a_reference_without_skill_is_a_usage_error() -> None:
    assert main(["-r", "config"]) == 2


def test_the_retry_example_is_valid_config() -> None:
    from matrix.adapters._in.skill import RETRY_EXAMPLE

    limits = load_config(sources=[io.StringIO(RETRY_EXAMPLE)]).matrix.limits
    assert limits.retry_of("review").attempts == 5 and limits.retry_of("other").attempts == 3
    assert "`errors`" in matrix.skill("config")


def test_every_max_option_the_skill_names_exists() -> None:
    """Moved from the spend tests when spend was removed: each ``max_*`` option the config and
    agents references name is a field of a runtime, an agent, the limits or a retry."""
    from matrix.adapters._out.runtime.claude_sdk import ClaudeSdkRuntimeConfig, SubagentConfig
    from matrix.adapters._out.runtime.model import HardlineModelRuntimeConfig
    from matrix.domain.config import AgentConfig

    text = matrix.skill("config") + matrix.skill("agents")
    named = set(re.findall(r"\b(max_[a-z_]+)\b", text))
    fields = {
        *ClaudeSdkRuntimeConfig.model_fields,
        *SubagentConfig.model_fields,
        *HardlineModelRuntimeConfig.model_fields,
        *AgentConfig.model_fields,
        *matrix.Limits.model_fields,
        *matrix.Retry.model_fields,
    }
    assert {"max_budget_usd", "max_turns", "max_tokens"} <= named
    assert named <= fields, named - fields


def test_the_loading_reference_names_only_exported_api() -> None:
    named = set(re.findall(r"matrix\.([A-Za-z_]+)\(", matrix.skill("loading")))
    assert named and named <= set(matrix.__all__), named - set(matrix.__all__)


def test_the_running_reference_python_runs_as_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: it showed ``plan_config()`` alone, and an agent passing a path found that
    the first argument is the tool. The block names the signature and runs on a config of
    its own, with nothing of the tiers."""
    (block,) = _fenced(matrix.skill("running"), "python")
    assert 'plan_config(tool="matrix", config=None, *, only=False)' in block
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    (tmp_path / "live.yaml").write_text(CONFIG_EXAMPLE)
    exec(compile(block, "running-reference", "exec"), {})  # noqa: S102
    (record,) = (tmp_path / ".matrix" / "runs").iterdir()
    assert json.loads((record / "run.json").read_text())["status"] == "completed"


def test_the_only_example_names_where_the_record_goes() -> None:
    text = " ".join(matrix.skill().split())
    assert "`matrix run live.yaml --only --input KIND=PATH`" in text
    assert "default `.matrix/runs` under the working directory" in text
    assert "matrix run live.yaml --only --input" in matrix.skill("config")


def test_nothing_sends_a_reader_to_the_events_for_a_fact() -> None:
    """Events are lossy: the skill and ``matrix run`` sent readers to ``events.jsonl`` for a
    failed step's task (W5.2). No recipe reads them now, and each says they are lossy."""
    from matrix.adapters._in.cli import _files

    text = "\n".join([matrix.skill(), *(matrix.skill(name) for name in REFERENCES)])
    assert not re.search(r"^jq .*events\.jsonl", text, re.M)
    assert "lossy" in matrix.skill("running") and "lossy" in matrix.skill("telemetry")
    (events,) = [
        line for line in _files({"acme.v1.x": {"file": "acme.v1.x.jsonl"}}) if "events" in line
    ]
    assert "lossy" in events and "task" not in events
