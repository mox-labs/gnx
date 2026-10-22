"""``ix --skill``: the text an agent learns ix from, and the references it lists.

An agent acts on this prose, so every claim in it is held to the code: the frontmatter is a
valid skill, the version is the installed one, each command and flag it names exists, the
exit table is the one table, every example validates, each genre and kind is taught from its
record, and what an agent needs to act sits within the first 5,000 tokens.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import Any, get_args

import pytest
import yaml
from click.testing import CliRunner, Result

import ix
from ix.adapters._in.cli import main
from ix.adapters._out.k6_load import K6Load
from ix.adapters._out.sensors import group_rss_bytes
from ix.application.skill import (
    AGENT_EXAMPLES,
    COMMANDS,
    EXAMPLES,
    PREREQUISITES,
    QUICK_START,
    REFERENCES,
    SHAPE_MARK,
    VALUE_FORMS,
)
from ix.domain.errors import ERROR_KINDS, EXIT_CODES, EXIT_MEANINGS, EXIT_NEXT
from ix.domain.experiment import Experiment, Margin
from ix.domain.kind import arrivals, seconds
from ix.domain.problem import PROBLEM_KINDS

Write = Any  # the write_experiment fixture

#: Claude Code keeps the first 5,000 tokens of an invoked skill after compaction. Tokens are
#: estimated here at 4 characters each, which is an estimate, not a tokenizer's count.
FIRST_TOKENS_AS_CHARS = 5_000 * 4

#: Every complete experiment the skill and its references show.
EVERY_EXAMPLE = {**EXAMPLES, "quick-start": QUICK_START}


def _run(*args: str) -> Result:
    return CliRunner(env={"COLUMNS": "80"}).invoke(main, list(args))


def _skill() -> str:
    return ix.skill()


def _reference(name: str) -> str:
    return ix.skill(name)


def _everything() -> str:
    return "\n".join([_skill(), *(_reference(name) for name in REFERENCES)])


def _frontmatter(text: str) -> tuple[dict[str, Any], str]:
    assert text.startswith("---\n"), text[:40]
    head, body = text[4:].split("\n---\n", 1)
    data = yaml.safe_load(head)
    assert isinstance(data, dict)
    return data, body


def _yaml_blocks(text: str) -> list[str]:
    return re.findall(r"```yaml\n(.*?)```", text, re.S)


class TestTheSkillIsASkill:
    def test_the_frontmatter_parses_with_the_portable_keys_only(self) -> None:
        """A top-level key outside the portable set fails a skill upload, so the version
        sits under ``metadata``."""
        data, _ = _frontmatter(_skill())
        assert list(data) == ["name", "description", "metadata"]
        assert data["name"] == "ix" and re.fullmatch(r"[a-z0-9-]{1,64}", data["name"])
        assert "version" not in data

    def test_the_description_is_a_trigger_contract(self) -> None:
        description = _frontmatter(_skill())[0]["description"]
        assert 0 < len(description) <= 1024
        assert "<" not in description and ">" not in description
        assert not description.lower().startswith(("you", "i ", "use "))  # third person
        assert "This skill should be used when the user asks to" in description
        assert len(re.findall(r'"[^"]+"', description)) >= 5  # trigger phrases

    def test_the_version_is_the_installed_one_in_the_frontmatter_and_the_first_line(
        self,
    ) -> None:
        data, body = _frontmatter(_skill())
        assert data["metadata"] == {"version": ix.__version__}
        first = body.lstrip("\n").splitlines()[0]
        assert first.startswith(f"# ix {ix.__version__}:")
        for name in REFERENCES:
            assert _reference(name).startswith(f"# ix {ix.__version__} reference: {name}\n")

    def test_skill_prints_the_text_and_exits_0(self) -> None:
        result = _run("--skill")
        assert result.exit_code == 0 and result.stdout == _skill()

    def test_every_install_line_pins_this_release_s_tag(self) -> None:
        """ix is installed from its repository, never a package index: every install line, in
        the skill, the README, the how-to and the repository's README, names the tag of the
        release they describe, so a version bump without the tag fails here."""
        source = (
            f"git+https://github.com/mox-labs/gnx@ix-v{ix.__version__}"
            "#subdirectory=components/capabilities/ix"
        )
        package = Path(ix.__file__).resolve().parents[2]
        texts = {"skill": _skill()}
        for file in (
            package / "README.md",
            package / "docs" / "how-to" / "evaluate-an-agent.md",
            package.parents[2] / "README.md",
        ):
            if file.is_file():  # run from the repository
                texts[str(file)] = file.read_text(encoding="utf-8")
        assert len(texts) in (1, 4), list(texts)
        for name, text in texts.items():
            lines = [
                line
                for line in text.splitlines()
                if "subdirectory=components/capabilities/ix" in line
            ]
            assert lines and all(source in line for line in lines), (name, lines)
            assert "pip install" not in text, name

    def test_the_extras_it_names_are_the_package_s(self) -> None:
        from importlib.metadata import metadata

        declared = set(metadata("ix").get_all("Provides-Extra") or []) - {"dev"}
        install = _skill().split("## Install and check", 1)[1].split("## Quick start", 1)[0]
        (sentence,) = [line for line in install.splitlines() if "The extras:" in line]
        assert set(re.findall(r"`([\w-]+)` \(", sentence)) == declared

    def test_what_an_agent_needs_to_act_is_within_the_first_5000_tokens(self) -> None:
        skill = _skill()
        for heading in (
            "## When to use ix",
            "## Install and check",
            "## Quick start",
            "## Commands, in the order they are used",
            "## Errors and exit codes",
            "## References",
        ):
            assert heading in skill, heading
            assert skill.index(heading) < FIRST_TOKENS_AS_CHARS, heading
        last_row = f"| {EXIT_CODES['interrupted']} | `interrupted` |"
        assert skill.index(last_row) < FIRST_TOKENS_AS_CHARS
        order = [skill.index(h) for h in ("## When", "## Install", "## Quick", "## Commands",
                                          "## Errors")]  # fmt: skip
        assert order == sorted(order)


class TestReferences:
    @pytest.mark.parametrize("name", list(REFERENCES))
    def test_each_reference_prints_and_exits_0(self, name: str) -> None:
        result = _run("--skill", "-r", name)
        assert result.exit_code == 0 and result.stdout == _reference(name)

    def test_the_skill_lists_every_reference(self) -> None:
        skill = _skill()
        for name, what in REFERENCES.items():
            assert f"- `{name}`: {what}" in skill

    def test_an_unknown_reference_is_a_usage_error_naming_the_ones_there_are(self) -> None:
        result = _run("--skill", "-r", "nope")
        assert result.exit_code == EXIT_CODES["usage"] == 2 and result.stdout == ""
        said = " ".join(result.stderr.split())
        assert said.startswith("error (usage): no skill reference named 'nope'")
        assert all(name in said for name in REFERENCES) and "fix:" in said
        with pytest.raises(ix.UsageError) as raised:
            ix.skill("nope")
        assert raised.value.details == {"path": "--reference", "value": "nope"}

    def test_a_reference_without_skill_is_a_usage_error(self) -> None:
        result = _run("-r", "kinds")
        assert result.exit_code == 2 and "--skill" in result.stderr


class TestEveryClaimHolds:
    def _named_commands(self, text: str) -> set[str]:
        """Every ``ix <word>`` in a code span or a code block."""
        spans = re.findall(r"`([^`\n]+)`", text)
        blocks = [line for block in re.findall(r"```\w*\n(.*?)```", text, re.S)
                  for line in block.splitlines()]  # fmt: skip
        return {
            match.group(1)
            for snippet in [*spans, *blocks]
            if (match := re.match(r"ix ([a-z-][a-z-]*)", snippet.strip()))
        }

    def test_every_command_and_flag_it_names_exists(self) -> None:
        named = self._named_commands(_everything())
        commands = {name for name in named if not name.startswith("-")}
        flags = {name for name in named if name.startswith("-")}
        assert {"validate", "plan", "register", "run", "report"} <= commands
        assert commands <= set(main.commands), commands - set(main.commands)
        top = {option for param in main.params for option in param.opts}
        assert flags and flags <= top, flags - top
        assert {"--skill", "-r", "--version"} <= top
        assert {command.split()[1] for command, _ in COMMANDS} == set(main.commands)

    def test_the_exit_table_is_the_one_table(self) -> None:
        rows = re.findall(r"^\| (\d+) \| `([a-z_]+)` \| (.*?) \| (.*?) \|$", _skill(), re.M)
        assert [(kind, int(code)) for code, kind, _, _ in rows] == list(EXIT_CODES.items())
        for _, kind, meaning, next_step in rows:
            assert (meaning, next_step) == (EXIT_MEANINGS[kind], EXIT_NEXT[kind])
        assert set(EXIT_NEXT) == set(EXIT_CODES) == set(EXIT_MEANINGS)
        assert "`error.kind` is one of " + ", ".join(f"`{k}`" for k in ERROR_KINDS) in _skill()

    def test_every_exit_code_named_in_the_prose_is_the_tables(self) -> None:
        quick = _skill().split("## Quick start", 1)[1].split("## Commands", 1)[0]
        assert f"{EXIT_CODES['config']}: fix every problem" in quick
        for kind in ("fails", "inconclusive"):
            assert f"{EXIT_CODES[kind]} {kind}" in quick
        assert f"an unknown name exits {EXIT_CODES['usage']} (`usage`)" in _skill()

    def test_every_yaml_example_validates_and_the_shape_says_it_is_one(
        self, tmp_path: Path
    ) -> None:
        blocks = _yaml_blocks(_everything())
        shapes = [block for block in blocks if block.startswith(SHAPE_MARK)]
        assert len(shapes) == 1
        examples = [block for block in blocks if not block.startswith(SHAPE_MARK)]
        assert len(examples) == 1 + len(EXAMPLES) + len(AGENT_EXAMPLES)
        for index, block in enumerate(examples):
            file = tmp_path / f"example-{index}.yaml"
            file.write_text(block)
            result = ix.validate(file)
            assert result.valid, (block, result.problems)
            # An agent copies the examples, so each names only kinds this ix can run.
            assert result.warnings == (), block
            plan = ix.plan(file)
            assert plan.trials.total > 0 and plan.unimplemented == (), block

    def test_the_quick_start_can_run_here_as_written(self, tmp_path: Path) -> None:
        """It names programs that exist (``python3``) and kinds with adapters. Whether k6 is
        installed is the host's business; the skill says how to check."""
        assert f"```yaml\n{QUICK_START.rstrip()}\n```" in _skill()
        directory = tmp_path / "static"
        directory.mkdir()
        (directory / "experiment.yaml").write_text(QUICK_START)
        plan = ix.plan(directory)
        assert plan.genre == "evaluate" and plan.unstartable == () and plan.unimplemented == ()
        assert set(yaml.safe_load(QUICK_START)) == {"name", "question", "genre", "subjects",
                                                    "probes", "primary", "threshold"}  # fmt: skip

    def test_the_registration_file_it_names_is_the_one_register_writes(
        self, tmp_path: Path
    ) -> None:
        directory = tmp_path / "static"
        directory.mkdir()
        (directory / "experiment.yaml").write_text(QUICK_START)
        ix.register(directory)
        assert (directory / "experiment.registration.json").is_file()
        assert "lab/static/experiment.registration.json" in _skill()

    def test_the_always_required_keys_are_the_models(self) -> None:
        required = [f.alias or n for n, f in Experiment.model_fields.items() if f.is_required()]
        assert required == ["name", "question", "genre", "subjects"]  # probes may be files
        assert "Always set " + ", ".join(f"`{k}`" for k in required) + "." in _skill()

    def test_the_shape_names_every_key_the_model_accepts(self) -> None:
        (shape,) = [b for b in _yaml_blocks(_reference("experiment")) if b.startswith(SHAPE_MARK)]
        for name, field in Experiment.model_fields.items():
            assert f"\n{field.alias or name}:" in shape, name

    def test_every_value_form_it_shows_parses(self) -> None:
        for example in VALUE_FORMS["duration"][1]:
            assert seconds(float(example) if example.isdigit() else example) is not None
        for example in VALUE_FORMS["rate"][1]:
            assert arrivals(example) is not None, example
        for example in VALUE_FORMS["margin"][1]:
            Margin.model_validate(float(example) if "%" not in example and "pp" not in example
                                  else example)  # fmt: skip

    def test_the_prerequisites_are_of_runnable_kinds_and_name_what_the_adapters_run(
        self,
    ) -> None:
        runnable = {name.rsplit(".", 1)[1] for name in ix.kinds().runnable}
        assert set(PREREQUISITES) <= runnable
        assert inspect.signature(K6Load).parameters["binary"].default == "k6"
        assert '"ps"' in inspect.getsource(group_rss_bytes)
        install = _skill().split("## Install and check", 1)[1].split("## Quick start", 1)[0]
        assert "`k6 version`" in install and "`ps`" in install

    def test_what_it_says_cannot_run_is_what_has_no_adapter(self) -> None:
        kinds = ix.kinds()
        declared = sorted(
            {name.rsplit(".", 1)[1] for name in set(kinds.kinds) - set(kinds.runnable)}
        )
        assert declared  # agent evaluations are declared only in this build
        when = _skill().split("## When to use ix", 1)[1].split("## Install", 1)[0]
        assert ", ".join(f"`{label}`" for label in declared) in when


class TestTheReferencesTeachFromTheRecords:
    def test_running_and_what_cannot_be_run(self) -> None:
        running, kinds = _reference("running"), _reference("kinds")
        for said in (
            "An unregistered experiment is refused (exit\n  15)",
            "`--exploratory`",
            "**The exit code is the verdict**: 0 holds",
            ".ix/runs/<run id>/",
            "an intersection-union test",
            "two subjects that do not differ are **inconclusive**",
            "--amend --reason",
        ):
            assert said in running, said
        # A kind with no adapter says so where it is taught; one with an adapter does not.
        marked = [line for line in kinds.splitlines() if "*(declared only: no adapter" in line]
        assert {line.split("`")[1] for line in marked} == {"inspect-task", "compose"}

    def test_every_genre_from_its_record(self) -> None:
        genres, skill = _reference("genres"), _skill()
        for genre in ix.genres().genres.values():
            assert f"### {genre.label}" in genres
            assert genre.guidance.strip() in genres and genre.question in genres
            assert f"- Must set: {', '.join(genre.author_must_set)}." in genres
            assert f"| `{genre.label}` | {genre.question} |" in skill
            for name, form in genre.comparisons.items():
                assert f"  - `{name}`: {form}" in genres

    def test_what_it_says_must_be_set_is_what_validate_enforces(self, tmp_path: Path) -> None:
        """Reviewed defect: the skill said regress needs ``rule`` while its example left it
        out, and never said compare needs a margin. For every genre: with all the fields it
        lists, the example is valid; with any one of them removed, it is not."""
        for label, text in EXAMPLES.items():
            genre = ix.genres().get(label)
            assert genre is not None
            data = yaml.safe_load(text)
            comparison = data.get("comparison") or genre.default_comparison
            listed = [field.split(" ")[0] for field in genre.author_must_set]
            for field in listed:
                if field == "margin" and not genre.needs_margin(comparison):
                    continue
                assert field in data, (label, field)
                without = {key: value for key, value in data.items() if key != field}
                file = tmp_path / f"{label}-{field}.yaml"
                file.write_text(yaml.safe_dump(without, sort_keys=False))
                kinds = {p.kind for p in ix.validate(file).problems}
                assert kinds & {"must_set", "margin_required"}, (label, field, kinds)
        assert "- Must set: primary, margin." in _reference("genres")  # regress: no comparison
        assert "| `regress` | May this change ship against the baseline? | primary, margin |" in (
            _skill()
        )

    def test_every_kind_from_its_declaration(self) -> None:
        text = _reference("kinds")
        for kind in ix.kinds().kinds.values():
            assert f"**`{kind.label}`**: {kind.summary}" in text
            for name, option in kind.options.items():
                assert f"`{name}`{'*' if option.required else ''}" in text, (kind.label, name)
            for metric in kind.metrics:
                assert f"metric `{metric.name}`" in text
        judged = [m for k in ix.kinds().kinds.values() for m in k.metrics if m.judged]
        assert text.count("JUDGED by a model") == len(judged) == 4

    def test_what_it_says_about_unknown_keys_is_true(
        self, compare_data: dict[str, Any], write_experiment: Write
    ) -> None:
        """Reviewed defect: the skill said "an unknown key is refused" while a typo beside
        ``kind`` passed. The sentence stays; the behaviour was made to match it."""
        assert "An unknown key is refused, at the top of the file and beside" in _reference(
            "experiment"
        )
        assert "an unknown key is refused, at the top of the file and beside every" in _skill()
        compare_data["tyop"] = 1
        compare_data["subjects"]["buffered"]["comand"] = ["x"]
        compare_data["probes"][0]["durration"] = "1s"
        compare_data["environment"] = {"kind": "none", "servces": {}}
        top = ix.validate(write_experiment(compare_data, "top"))
        assert [(p.path, p.kind) for p in top.problems] == [("tyop", "unknown_key")]
        del compare_data["tyop"]
        beside = ix.validate(write_experiment(compare_data, "beside"))
        assert {(p.path, p.kind) for p in beside.problems} == {
            ("subjects.buffered.comand", "option_unknown"),
            ("probes.0.durration", "option_unknown"),
            ("environment.servces", "option_unknown"),
        }

    def test_the_documents_the_problem_shape_and_the_problem_kinds(self) -> None:
        skill = _skill()
        for schema in ("ix.v1.validation", "ix.v1.plan", "ix.v1.registered", "ix.v1.deviations",
                       "ix.v1.report", "ix.v1.runs", "ix.v1.genres", "ix.v1.kinds",
                       "ix.v1.error"):  # fmt: skip
            assert schema in skill
        for word in ('"complete": false', "allowed", "got", "--amend --reason"):
            assert word in skill
        problems = _reference("problems")
        for kind in PROBLEM_KINDS:
            assert f"`{kind}`" in problems
        assert "ix.v2" not in _everything() and "--format" not in _everything()

    @pytest.mark.parametrize("example", sorted(EVERY_EXAMPLE))
    def test_no_example_restates_a_default(self, example: str, tmp_path: Path) -> None:
        """An example shows the minimum: every value it sets differs from what it would get
        by leaving the key out."""
        text = EVERY_EXAMPLE[example]
        data = yaml.safe_load(text)
        file = tmp_path / "experiment.yaml"
        file.write_text(text)
        full = ix.resolve(file)
        for key in ("repeats", "seed", "control", "comparison", "environment", "rationale"):
            if key not in data:
                continue
            file.write_text(yaml.safe_dump({k: v for k, v in data.items() if k != key}))
            without = ix.validate(file)
            assert not without.valid or getattr(ix.resolve(file), key, None) != getattr(
                full, key, None
            ), (example, key)
        for ref in [data.get("primary", {}), *data.get("guardrails", [])]:
            for reduction in ("statistic", "estimator"):
                if reduction in ref:
                    trimmed = yaml.safe_load(text)
                    target = (
                        trimmed["primary"]
                        if ref is data.get("primary")
                        else trimmed["guardrails"][data["guardrails"].index(ref)]
                    )
                    del target[reduction]
                    file.write_text(yaml.safe_dump(trimmed))
                    assert ix.resolve(file) != full, (example, reduction)

    def test_the_question_guide_names_genres_and_comparisons_that_exist(self) -> None:
        from ix.application.skill import QUESTION_TO_GENRE

        genres = {g.label: g for g in ix.genres().genres.values()}
        text = _skill()
        for question, answer in QUESTION_TO_GENRE:
            assert f"- {question}: {answer}." in text
            genre = genres[re.findall(r"`([a-z-]+)`", answer)[0]]
            for comparison in re.findall(r"`comparison: ([a-z-]+)`", answer):
                assert comparison in genre.comparisons, (genre.label, comparison)
        regress = genres["regress"]
        assert "non-inferiority" in regress.comparisons and "margin" in regress.author_must_set

    def test_there_is_an_example_for_each_built_in_genre(self) -> None:
        assert set(EXAMPLES) == {g.label for g in ix.genres().genres.values() if "." not in g.label}
        genres = _reference("genres")
        for example in EXAMPLES.values():
            assert f"```yaml\n{example.rstrip()}\n```" in genres


class TestTheAgentsReference:
    """What the agents reference teaches is what the kinds declare and the adapters do."""

    def test_it_names_every_option_and_metric_of_the_agent_kinds(self) -> None:
        text = " ".join(_reference("agents").split())
        kinds = ix.kinds()
        for family, name in [("subject", "agent-session"), ("probe", "prompt")]:
            kind = kinds.get(family, name)
            assert kind is not None
            for option in kind.options:
                assert f"`{option}`" in text, (name, option)
        for name in (
            "activation", "tool-usage", "function-test", "outcome", "text-match", "usage", "judge",
        ):  # fmt: skip
            sensor = kinds.get("sensor", name)
            assert sensor is not None and f"(`{name}`" in text, name
            for metric in sensor.metrics:
                if not metric.name.startswith("judge_"):
                    assert f"`{metric.name}`" in text, (name, metric.name)
            for key in sensor.truth:
                assert f"`{key}`" in text, (name, key)

    def test_its_numbers_and_words_are_the_codes(self) -> None:
        from ix.adapters._out.matrix_agents import (
            SESSION_TIMEOUT_S,
            VERIFIED,
            Loop,
        )
        from ix.domain.summaries import NOISY

        text = " ".join(_reference("agents").split())
        # No session runtime is the default: each calls a model or not, and the reference
        # says which.
        assert "It is required, with no default" in text and "`option_missing`" in text
        for runtime, calls in [("claude-sdk", "calls a model provider)"),
                               ("model", "calls a model provider)"),
                               ("mock", "calls no model")]:  # fmt: skip
            assert re.search(rf"`{runtime}` \([^)]*{re.escape(calls)}", text), runtime
        assert "`judge` asks a model (`runtime`, required with no default" in text
        assert f"`timeout` ({SESSION_TIMEOUT_S:g}s)" in text and f"`{VERIFIED}`" in text
        for strategy in get_args(Loop.model_fields["strategy"].annotation):
            assert f"strategy: {strategy}" in text, strategy
        assert f"more than {NOISY:g} of the range" in text

    @pytest.mark.parametrize("example", ["skill", "routing", "control"])
    def test_its_dry_run_runs_here_as_written_and_calls_no_model(
        self, example: str, tmp_path: Path
    ) -> None:
        """The order of work's second step, done to the reference's own examples with no
        edit: the dry run sets aside what only the real runtime takes."""
        lab = tmp_path / example
        lab.mkdir()
        (lab / "experiment.yaml").write_text(AGENT_EXAMPLES[example])
        report = ix.run(lab, dry_run=True)
        assert report.dry_run and report.simulated == "all" and report.stood_in
        assert report.verdict is None and report.indication is None
        assert report.exit_code == 0

    def test_its_probe_file_puts_options_under_options_and_runs(self, tmp_path: Path) -> None:
        """Regression: the reference showed no probe file, and an agent wrote
        ``mock_response`` beside the truth, where the probe never sees it."""
        from ix.application.skill import PROBE_FILE

        assert f"```markdown\n{PROBE_FILE}\n```" in _reference("agents")
        front, body = _frontmatter(PROBE_FILE + "\n")
        assert set(front["options"]) == {"mock_response"} and body.strip()
        data = yaml.safe_load(AGENT_EXAMPLES["skill"])
        data.pop("probes")
        lab = tmp_path / "review-skill"
        (lab / "probes").mkdir(parents=True)
        (lab / "experiment.yaml").write_text(yaml.safe_dump(data))
        (lab / "probes" / f"{front['id']}.md").write_text(PROBE_FILE + "\n")
        assert ix.validate(lab).valid
        report = ix.run(lab, dry_run=True)
        interactions = Path(report.tables["ix.v1.interaction"]).read_text()
        assert front["options"]["mock_response"] in interactions

    def test_every_flag_it_gives_a_command_is_one_of_that_command(self) -> None:
        """``ix run <path> --exploratory --repeats N``: each flag after a command is the
        command's own."""
        text = _everything()
        snippets = [*re.findall(r"`([^`\n]+)`", text), *re.findall(r"^(ix .*)$", text, re.M)]
        checked = set()
        for snippet in snippets:
            words = snippet.split()
            if len(words) < 2 or words[0] != "ix" or words[1] not in main.commands:
                continue
            command = main.commands[words[1]]
            own = {option for param in command.params for option in param.opts}
            for flag in (word for word in words[2:] if word.startswith("--")):
                assert flag in own, (snippet, flag)
                checked.add((words[1], flag))
        assert ("run", "--repeats") in checked and ("run", "--exploratory") in checked

    def test_its_skill_example_is_a_skill_and_the_one_the_experiment_names(
        self, tmp_path: Path
    ) -> None:
        """The SKILL.md it shows has the two keys a skill needs, its name is its folder's and
        the one the experiment expects, and the experiment beside it validates."""
        from ix.application.skill import SKILL_FILE, SKILL_LAYOUT

        text = _reference("agents")
        assert f"```markdown\n{SKILL_FILE}\n```" in text and SKILL_LAYOUT in text
        front, _ = _frontmatter(SKILL_FILE + "\n")
        assert set(front) == {"name", "description"} and front["description"]
        assert re.fullmatch(r"[a-z0-9-]+", front["name"])
        data = yaml.safe_load(AGENT_EXAMPLES["skill"])
        (folder,) = data["subjects"]["agent"]["skills"]
        assert folder.rsplit("/", 1)[-1] == front["name"]
        assert f"skills/{front['name']}/SKILL.md" in SKILL_LAYOUT
        assert {p["truth"]["expected_skill"] for p in data["probes"]} == {front["name"]}
        assert "Skill" in data["subjects"]["agent"]["tools"]
        lab = tmp_path / "review-skill"
        (lab / "skills" / front["name"]).mkdir(parents=True)
        (lab / "skills" / front["name"] / "SKILL.md").write_text(SKILL_FILE + "\n")
        (lab / "experiment.yaml").write_text(AGENT_EXAMPLES["skill"])
        assert ix.validate(lab).valid
