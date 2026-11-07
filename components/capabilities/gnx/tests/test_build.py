"""`gnx build` projects a skill's payload and leaves its catalog-side files behind."""

from __future__ import annotations

from pathlib import Path

from gnx.cli import build_plugin


def _skill(root: Path, slug: str) -> None:
    d = root / "components" / "skills" / slug
    (d / "references").mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {slug}\ndescription: A skill.\n---\n\nBody.\n")
    (d / "references" / "more.md").write_text("More.\n")
    (d / "manifest.yaml").write_text(f"type_url: gnx.dev.v1.{slug}\nkind: Skill\n")
    (d / "overview.md").write_text(f"---\ntitle: {slug}\n---\n\n# {slug}\n")


def test_a_skills_manifest_and_overview_stay_out_of_its_plugin(tmp_path: Path) -> None:
    _skill(tmp_path, "demo")
    out = tmp_path / "plugins"
    out.mkdir()

    entry, problems = build_plugin(
        tmp_path, {"name": "demo", "version": "0.1.0", "skills": ["demo"]}, {}, out
    )

    assert problems == []
    assert entry["name"] == "demo"
    shipped = out / "demo" / "skills" / "demo"
    assert (shipped / "SKILL.md").exists()
    assert (shipped / "references" / "more.md").exists()
    assert not (shipped / "manifest.yaml").exists()
    assert not (shipped / "overview.md").exists()
