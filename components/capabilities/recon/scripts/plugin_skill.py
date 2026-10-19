"""Write the recon plugin's skill from what the installed ``recon`` command prints.

    python plugin_skill.py <plugin skill dir>           write SKILL.md and references/*.md
    python plugin_skill.py <plugin skill dir> --check   exit 1, listing each difference

``SKILL.md`` is ``recon --skill``; ``references/<name>.md`` is ``recon --skill -r <name>``
for every name ``recon.skill_references()`` gives. Any other file in the directory is a
difference: the copy holds what the command prints and nothing else.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import recon


def _printed(*args: str) -> str:
    command = Path(sys.executable).with_name("recon")
    result = subprocess.run([str(command), *args], capture_output=True, text=True, check=True)
    return result.stdout


def expected() -> dict[str, str]:
    files = {"SKILL.md": _printed("--skill")}
    for name in recon.skill_references():
        files[f"references/{name}.md"] = _printed("--skill", "-r", name)
    return files


def differences(directory: Path, files: dict[str, str]) -> list[str]:
    present = {p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()}
    found = [f"not printed by recon: {name}" for name in sorted(present - set(files))]
    for name, text in files.items():
        path = directory / name
        if not path.is_file():
            found.append(f"missing: {name}")
        elif path.read_text(encoding="utf-8") != text:
            found.append(f"differs from recon --skill: {name}")
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("directory", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    files = expected()
    if not args.check:
        for name, text in files.items():
            path = args.directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    found = differences(args.directory, files)
    for line in found:
        print(f"{args.directory}: {line}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
