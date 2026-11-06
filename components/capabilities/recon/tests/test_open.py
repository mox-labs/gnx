"""Values in, discovery out (step M4, ruling R15): ``recon.open`` takes a project, extensions
given in-process and a landing; a config may be a value, never written as the mission's, its
bytes kept in each archive it lands; the module functions sit over one default session per
project; the command line takes ``--root`` and ``RECON_ROOT``; and what a mission may name is
discoverable as data (``recon types``, ``recon schema``)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
import yaml
from click.testing import CliRunner

import recon
from recon.adapters._in.cli import main
from recon.composition import default
from recon.core.domain.collector import CollectorType
from recon.core.domain.errors import UsageError
from recon.core.domain.models import ReconConfig

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from recon.core.domain.capture import CaptureLog
    from recon.core.domain.collector import Collector, CollectorContext
    from recon.core.domain.models import CollectorEntry, SourceEntry


class _Numbers:
    """A collector a program defines for itself: the numbers 1 to 3."""

    def collect(
        self,
        entry: CollectorEntry,
        source: SourceEntry | None,
        *,
        captures: CaptureLog | None = None,
    ) -> Iterator[dict[str, Any]]:
        return iter([{"n": n} for n in (1, 2, 3)])


def _numbers(ctx: CollectorContext) -> Collector:
    return _Numbers()


NUMBERS = CollectorType(create=_numbers, effects=frozenset())
URL = "acme.v1.collector.numbers"


def INLINE(registry: Any) -> None:  # noqa: N802 - an extension, named as one
    """A program's own extension, registered in-process at recon's collector point."""
    registry.register("recon.collector", URL, lambda: NUMBERS)


CONFIG = {"collectors": [{"name": "n", "type": URL}]}


def test_an_in_process_collector_and_a_config_value_run_and_write_no_config(
    tmp_path: Path,
) -> None:
    """The gate of M4: a harness gives recon a collector type of its own and a config as a
    value; the survey runs it, and no ``config.yaml`` is written anywhere."""
    session = recon.open(tmp_path, extensions=[INLINE])
    document = session.observation.survey("m", config=CONFIG)
    assert document.status == "complete" and [t.records for t in document.tables] == [3]
    assert not list(tmp_path.rglob("config.yaml")), "a value is never written as the mission's"
    rows = session.collection.query("m", "SELECT n FROM n ORDER BY n")
    assert [row["n"] for row in rows.rows] == [1, 2, 3]


def test_a_config_value_is_kept_in_its_archive_beside_its_digest(
    tmp_path: Path,
) -> None:
    session = recon.open(tmp_path, extensions=[INLINE])
    first = session.observation.survey("m", config=ReconConfig.model_validate(CONFIG))
    meta = yaml.safe_load(
        next((tmp_path / ".recon" / "m" / "archive").glob(f"{first.archive}/meta.yaml")).read_text()
    )
    assert meta["config"] == CONFIG
    again = session.observation.plan("m", config=CONFIG)
    assert again.valid, "a model and the mapping it holds are one config"


def test_a_config_value_s_credentials_never_reach_its_archive(tmp_path: Path) -> None:
    """Regression: an archive's meta never holds a URL's password (test_telemetry's guarantee),
    so a value config is kept as recon reports a URL, its user information redacted."""
    config = {**CONFIG, "catalog": [{"name": "x", "url": "https://me:PASSWORD-TOKEN@x.test/"}]}
    session = recon.open(tmp_path, extensions=[INLINE])
    document = session.observation.survey("m", config=config)
    meta = (tmp_path / ".recon" / "m" / "archive" / document.archive / "meta.yaml").read_text()
    assert "PASSWORD-TOKEN" not in meta and "REDACTED:REDACTED@x.test" in meta


def test_an_extension_may_not_take_an_installed_type_url(tmp_path: Path) -> None:
    def taking(registry: Any) -> None:
        registry.register("recon.collector", "recon.v1.collector.api", lambda: NUMBERS)

    with pytest.raises(UsageError) as refused:
        recon.open(tmp_path, extensions=[taking])
    assert "recon.v1.collector.api" in str(refused.value)


def test_types_says_whose_each_type_is_and_its_config_schema(tmp_path: Path) -> None:
    document = recon.open(tmp_path, extensions=[INLINE]).collection.types()
    found = {item.type: item for item in document.collectors}
    assert found[URL].origin == "in_process" and found[URL].records_kind == "finite"
    assert found["api"].origin == "recon" and "paginate" in found["api"].capabilities
    assert found["api"].config is not None and "endpoint" in found["api"].config["properties"]
    assert found["tail"].records_kind == "unbounded_recon_paced"
    assert found["sse"].records_kind == "unbounded_source_paced"
    assert "$markitdown" in document.transforms
    assert {s.kind for s in document.sources} >= {"mcp"}


def test_schema_is_the_mission_config_s_json_schema() -> None:
    document = recon.schema().document()
    assert document["schema"] == "recon.v1.mission-config"
    assert {"catalog", "collectors"} <= set(document["json_schema"]["properties"])


def test_the_plan_reports_every_default_it_filled_in(tmp_path: Path) -> None:
    """F8: the older defaults were not reported; each effective value now is, with its
    origin."""
    config = {"catalog": [{"name": "x", "url": "https://x.test"}],
              "collectors": [{"name": "a", "type": "api", "endpoint": "/"}]}  # fmt: skip
    plan = recon.open(tmp_path).observation.plan("m", config=config)
    said = {value.path: (value.value, value.origin) for value in plan.provenance}
    assert said["catalog.0.timeout"] == (60.0, "recon")
    assert said["catalog.0.max_bytes"] == (50_000_000, "recon")
    assert said["catalog.0.url"] == ("https://x.test", "mission")
    assert said["collectors.0.type_url"] == ("recon.v1.records", "recon")
    assert said["preserve_raw"] == (False, "recon")


def test_the_module_functions_share_one_session_per_project(tmp_path: Path) -> None:
    assert default(tmp_path) is default(tmp_path)
    assert default(tmp_path) is not default(tmp_path / "other")


def test_a_landing_recon_cannot_query_is_refused(tmp_path: Path) -> None:
    class Elsewhere:
        """A store whose tables are kept where recon's query engine does not read."""

        def hold(self, mission: str, *, stream: str | None = None) -> Any:
            raise AssertionError("never held")

    with pytest.raises(UsageError):
        recon.open(tmp_path, landing=Elsewhere())  # type: ignore[arg-type]


@pytest.mark.parametrize("how", ["flag", "env"])
def test_the_command_line_takes_its_project_from_root_or_recon_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    project = tmp_path / "project"
    (project / ".recon" / "m").mkdir(parents=True)
    (project / ".recon" / "m" / "config.yaml").write_text(
        yaml.safe_dump({"collectors": [{"name": "t", "type": "cli", "run": "true"}]})
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    args = ["--root", str(project), "status", "--json"] if how == "flag" else ["status", "--json"]
    env = {"RECON_ROOT": str(project)} if how == "env" else {}
    result = CliRunner().invoke(main, args, env=env, catch_exceptions=False)
    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document["project"] == str(project.resolve())
    assert [m["name"] for m in document["missions"]] == ["m"]


def test_types_and_schema_print_their_documents(tmp_path: Path) -> None:
    types = CliRunner().invoke(main, ["--root", str(tmp_path), "types", "--json"])
    assert types.exit_code == 0, types.output
    assert json.loads(types.stdout)["schema"] == "recon.v1.types"
    schema = CliRunner().invoke(main, ["schema"])
    assert json.loads(schema.stdout)["schema"] == "recon.v1.mission-config"
