"""One loading mechanism: a source reads plain data, the loader validates it as the caller's
type, and every problem comes back as a record with its key path and its file."""

from __future__ import annotations

import io
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Any, Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field

from matrix import (
    Accumulate,
    AgentDefinition,
    BesideFile,
    ConfigError,
    Document,
    DocumentSource,
    Loader,
    NotFoundError,
    Problem,
    load,
    load_each,
)
from matrix.adapters._out.documents.files import FileSource


def _write(folder: Path, name: str, text: str) -> Path:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class Probe(BaseModel):
    """A shape ix will read: frontmatter id/kind/options/truth, the body as the prompt."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    kind: str = "prompt"
    options: dict[str, Any] = {}
    truth: dict[str, Any] = {}
    prompt: str = Field("", alias="body")


class Experiment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    repeats: int = Field(1, ge=1)
    probes: Annotated[str, BesideFile()] = "probes"


# --- one file ---------------------------------------------------------------------------


def test_one_yaml_file_is_one_model(tmp_path: Path) -> None:
    path = _write(tmp_path, "lab/experiment.yaml", "name: soak\nrepeats: 3\n")
    experiment = load(Experiment, path)
    assert (experiment.name, experiment.repeats) == ("soak", 3)


def test_a_relative_path_marked_beside_file_reads_from_the_file_s_folder(tmp_path: Path) -> None:
    path = _write(tmp_path, "lab/experiment.yaml", "name: soak\nprobes: cases\n")
    assert load(Experiment, path).probes == str((tmp_path / "lab" / "cases").resolve())
    absolute = _write(tmp_path, "abs.yaml", "name: x\nprobes: /srv/cases\n")
    assert load(Experiment, absolute).probes == "/srv/cases"


def test_every_problem_in_a_file_is_reported_with_its_path_kind_and_file(tmp_path: Path) -> None:
    path = _write(tmp_path, "experiment.yaml", "repeats: 0\nrepets: 2\n")
    with pytest.raises(ConfigError) as raised:
        load(Experiment, path)
    found = {(p.path, p.kind) for p in raised.value.problems}
    assert found == {
        ("name", "missing_key"),
        ("repeats", "invalid_value"),
        ("repets", "unknown_key"),
    }
    assert all(p.source == str(path) for p in raised.value.problems)
    assert raised.value.complete and raised.value.exit_code == 3


def test_a_value_of_the_wrong_type_is_wrong_shape_and_keeps_what_was_written(
    tmp_path: Path,
) -> None:
    path = _write(tmp_path, "e.yaml", "name: [a, b]\nrepeats: many\n")
    with pytest.raises(ConfigError) as raised:
        load(Experiment, path)
    by_path = {p.path: p for p in raised.value.problems}
    assert by_path["name"].kind == "wrong_shape" and by_path["name"].got is None
    assert by_path["repeats"].kind == "wrong_shape" and by_path["repeats"].got == "many"


def test_a_closed_set_lists_what_is_allowed(tmp_path: Path) -> None:
    class Strict(BaseModel):
        schema_: Literal["acme.v1.x"] = Field("acme.v1.x", alias="schema")
        mode: Literal["fast", "slow"] = "fast"

    path = _write(tmp_path, "s.yaml", "schema: acme.v2.x\nmode: medium\n")
    with pytest.raises(ConfigError) as raised:
        load(Strict, path)
    by_path = {p.path: p for p in raised.value.problems}
    assert by_path["schema"].kind == "schema_unsupported"
    assert by_path["schema"].allowed == ("acme.v1.x",)
    assert by_path["mode"].allowed == ("fast", "slow") and by_path["mode"].got == "medium"


def test_yaml_that_does_not_parse_is_one_yaml_syntax_problem(tmp_path: Path) -> None:
    path = _write(tmp_path, "broken.yaml", "name: [unclosed\n")
    with pytest.raises(ConfigError) as raised:
        load(Experiment, path)
    (found,) = raised.value.problems
    assert (found.kind, found.source) == ("yaml_syntax", str(path))
    assert not raised.value.complete  # nothing else could be checked


def test_a_top_level_that_is_not_a_mapping_is_wrong_shape(tmp_path: Path) -> None:
    path = _write(tmp_path, "scalar.yaml", "just a string\n")
    with pytest.raises(ConfigError) as raised:
        load(Experiment, path)
    assert [p.kind for p in raised.value.problems] == ["wrong_shape"]


def test_nothing_at_the_location_is_not_found(tmp_path: Path) -> None:
    with pytest.raises(NotFoundError) as raised:
        load(Experiment, tmp_path / "missing.yaml")
    assert raised.value.exit_code == 4


def test_a_folder_is_not_one_document(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="is a folder"):
        load(Experiment, tmp_path)


def test_a_stream_is_read_as_yaml() -> None:
    stream = io.StringIO("name: piped\n")
    stream.name = "<stdin>"  # type: ignore[misc]
    assert load(Experiment, stream).name == "piped"


def test_a_stream_reports_problems_against_its_name() -> None:
    stream = io.StringIO("repeats: 2\n")
    stream.name = "<stdin>"  # type: ignore[misc]
    with pytest.raises(ConfigError) as raised:
        load(Experiment, stream)
    assert raised.value.problems[0].source == "<stdin>"


# --- markdown ---------------------------------------------------------------------------


def test_markdown_is_frontmatter_plus_its_body_and_the_type_says_what_body_means(
    tmp_path: Path,
) -> None:
    path = _write(
        tmp_path,
        "probes/greet.md",
        "---\nid: greet\nkind: prompt\ntruth: {intent: hello}\n---\nSay hello.\n",
    )
    probe = load(Probe, path)
    assert (probe.id, probe.prompt, probe.truth) == ("greet", "Say hello.\n", {"intent": "hello"})


def test_markdown_without_frontmatter_is_all_body(tmp_path: Path) -> None:
    path = _write(tmp_path, "plain.md", "Just a prompt.")
    with pytest.raises(ConfigError) as raised:  # Probe needs an id the file does not give
        load(Probe, path)
    assert [p.path for p in raised.value.problems] == ["id"]


def test_unclosed_frontmatter_names_the_file(tmp_path: Path) -> None:
    path = _write(tmp_path, "bad.md", "---\nid: x\nno close")
    with pytest.raises(ConfigError) as raised:
        load(Probe, path)
    (found,) = raised.value.problems
    assert found.kind == "yaml_syntax" and "never closes" in found.message
    assert found.source == str(path)


# --- folders ----------------------------------------------------------------------------


def test_a_folder_is_one_model_per_file_in_name_order(tmp_path: Path) -> None:
    _write(tmp_path, "probes/b.md", "---\nid: second\n---\nB")
    _write(tmp_path, "probes/a.md", "---\nid: first\n---\nA")
    _write(tmp_path, "probes/notes.txt", "not a probe")
    probes = load_each(Probe, tmp_path / "probes", key="id", suffixes=(".md",))
    assert [p.id for p in probes] == ["first", "second"]


def test_the_key_can_default_to_the_file_s_stem(tmp_path: Path) -> None:
    _write(tmp_path, "probes/greet.md", "Say hello.")
    (probe,) = load_each(Probe, tmp_path / "probes", key="id", name_from_file=True)
    assert (probe.id, probe.prompt) == ("greet", "Say hello.")


def test_a_name_defined_twice_is_refused_naming_both_files(tmp_path: Path) -> None:
    _write(tmp_path, "one/x.md", "---\nid: same\n---\nA")
    second = _write(tmp_path, "two/y.md", "---\nid: same\n---\nB")
    with pytest.raises(ConfigError) as raised:
        load_each(Probe, tmp_path / "one", tmp_path / "two", key="id")
    (found,) = raised.value.problems
    assert (found.kind, found.path, found.source, found.got) == (
        "name_taken",
        "id",
        str(second),
        "same",
    )
    assert str(tmp_path / "one" / "x.md") in found.message


def test_every_document_in_a_folder_is_checked_one_bad_file_hides_nothing(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "kinds/a.yaml", "name: [broken\n")
    _write(tmp_path, "kinds/b.yaml", "repeats: 2\n")
    _write(tmp_path, "kinds/c.yaml", "name: fine\n")

    with pytest.raises(ConfigError) as raised:
        load_each(Experiment, tmp_path / "kinds")
    found = [(Path(p.source).name, p.kind) for p in raised.value.problems]
    assert found == [("a.yaml", "yaml_syntax"), ("b.yaml", "missing_key")]


def test_a_file_is_a_folder_of_one(tmp_path: Path) -> None:
    path = _write(tmp_path, "one.yaml", "name: only\n")
    assert [e.name for e in load_each(Experiment, path)] == ["only"]


def test_a_missing_folder_is_not_found(tmp_path: Path) -> None:
    with pytest.raises(NotFoundError, match="no folder or file"):
        load_each(Experiment, tmp_path / "nope")


def test_a_folder_inside_an_installed_package_is_read_too() -> None:
    """ix's genres and kinds ship as package data: a Traversable is a location."""
    folder = files("matrix") / "domain"
    found = FileSource().documents(folder, (".py",))
    assert found and all(isinstance(d, Document) for d in found)


# --- agent definitions are a type like any other ----------------------------------------


def test_a_claude_code_agent_file_is_an_agent_definition_as_it_stands(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "agents/x.md",
        "---\nname: rev\ndescription: d\ntools: Read, Grep\nmodel: opus\n"
        "maxTurns: 4\ncolor: red\n---\nBody here.\n",
    )
    d = load(AgentDefinition, path)
    assert (d.name, d.description, d.system_prompt) == ("rev", "d", "Body here.")
    assert (d.tools, d.model, d.max_turns, d.metadata) == (
        ("Read", "Grep"),
        "opus",
        4,
        {"color": "red"},
    )


def test_agent_tools_as_a_list_and_absent_and_the_name_from_the_file(tmp_path: Path) -> None:
    _write(tmp_path, "agents/a.md", "---\ntools: [Read]\n---\nx")
    _write(tmp_path, "agents/b.md", "---\ndescription: y\n---\nx")
    listed, absent = load_each(AgentDefinition, tmp_path / "agents", name_from_file=True)
    assert listed.tools == ("Read",) and absent.tools is None
    assert (listed.name, absent.name) == ("a", "b")


def test_an_invalid_agent_field_is_a_problem_at_its_key(tmp_path: Path) -> None:
    path = _write(tmp_path, "m.md", "---\nname: m\nmax_turns: 0\n---\nx")
    with pytest.raises(ConfigError) as raised:
        load(AgentDefinition, path)
    assert [(p.path, p.kind) for p in raised.value.problems] == [("max_turns", "invalid_value")]


def test_reads_every_real_agent_file_in_the_repo() -> None:
    """Every agent file gnx holds loads as a definition: written by hand for Claude Code, in
    the wild (block-scalar descriptions, comma and list tool forms, extra keys like color)."""
    root = Path(__file__).resolve().parents[4]
    agents = sorted(root.glob("incubator/agents/*/agent.md")) + sorted(
        root.glob("plugins/*/agents/*.md")
    )
    if not agents:
        pytest.skip("no agent files in this checkout")
    for path in agents:
        d = load_each(AgentDefinition, path, name_from_file=True)[0]
        assert d.system_prompt and d.name, path


# --- merged tiers -----------------------------------------------------------------------


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: int = 1
    paths: Annotated[tuple[str, ...], Accumulate(), BesideFile()] = ()
    options: dict[str, Any] = {}


def test_tiers_merge_later_wins_mappings_by_key_and_marked_lists_accumulate(
    tmp_path: Path,
) -> None:
    low = _write(tmp_path, "home/c.yaml", "acme: {level: 2, paths: [a], options: {x: 1}}\n")
    high = _write(tmp_path, "proj/c.yaml", "acme: {paths: [b], options: {y: 2}}\n")
    merged = Loader(FileSource()).merge({"acme": Settings}, [low, tmp_path / "absent", high])
    settings = merged.sections["acme"]
    assert isinstance(settings, Settings)
    assert settings.level == 2 and settings.options == {"x": 1, "y": 2}
    assert settings.paths == (
        str((tmp_path / "home" / "a").resolve()),
        str((tmp_path / "proj" / "b").resolve()),
    )
    assert merged.sources == (str(low), f"{tmp_path / 'absent'} (absent)", str(high))


def test_a_merged_problem_names_the_file_that_set_the_value(tmp_path: Path) -> None:
    low = _write(tmp_path, "low.yaml", "acme: {level: 2}\n")
    high = _write(tmp_path, "high.yaml", "acme: {level: high, colour: red}\n")
    with pytest.raises(ConfigError) as raised:
        Loader(FileSource()).merge({"acme": Settings}, [low, high])
    by_path = {p.path: p for p in raised.value.problems}
    assert by_path["acme.level"].source == str(high)
    assert by_path["acme.colour"].kind == "unknown_key"
    assert by_path["acme.colour"].source == str(high)
    assert raised.value.details["sources"] == [str(low), str(high)]


def test_an_unreadable_tier_stops_the_merge_and_says_the_check_is_incomplete(
    tmp_path: Path,
) -> None:
    good = _write(tmp_path, "good.yaml", "acme: {level: 3}\n")
    bad = _write(tmp_path, "bad.yaml", "acme: [\n")
    with pytest.raises(ConfigError) as raised:
        Loader(FileSource()).merge({"acme": Settings}, [good, bad])
    assert [p.kind for p in raised.value.problems] == ["yaml_syntax"]
    assert raised.value.complete is False


# --- a third party's source ---------------------------------------------------------------


class _Inline:
    """A source that is not files: what a test root, or a remote store, would provide."""

    def __init__(self, documents: dict[str, dict[str, Any]]) -> None:
        self._documents = documents

    def document(self, location: Any) -> Document | None:
        data = self._documents.get(str(location))
        return None if data is None else Document(source=f"inline:{location}", stem="", data=data)

    def documents(self, location: Any, suffixes: Any) -> tuple[Document, ...] | None:
        found = self.document(location)
        return None if found is None else (found,)

    def beside(self, document: Document, path: str) -> str:
        return path


def test_any_source_that_meets_the_port_can_stand_behind_the_loader() -> None:
    source = _Inline({"team": {"matrix": {"definitions": ["/opt/team-agents"]}}})
    assert isinstance(source, DocumentSource)
    from matrix import load_config

    config = load_config(sources=["team"], loader=Loader(source))
    assert config.matrix.definitions == ("/opt/team-agents",)
    assert config.sources == ("inline:team",)


def test_problem_records_are_the_shared_envelope_shape() -> None:
    record = Problem(path="a.b", kind="unknown_key", message="m").document()
    assert set(record) == {"path", "kind", "message", "fix", "allowed", "got", "source"}
