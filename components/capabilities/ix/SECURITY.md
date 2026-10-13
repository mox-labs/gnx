# Security — ix

ix validates, plans and registers an experiment. It does not run one: it starts no subject,
applies no probe, calls no model and opens no network connection. What it does is read
files, compute, and write one registration file.

Audience: anyone running ix against an experiment, a genre or a kind they did not author.

## What ix touches

| Command | Reads | Writes |
|---|---|---|
| `validate`, `plan`, `deviations` | the experiment file; genre and kind files; probe files; the registration file | nothing |
| `register` | the same | `<file-stem>.registration.json` beside the experiment |
| `genres`, `kinds`, `--skill` | genre and kind files | nothing |

## Trust boundaries

### 1. Files are data

The experiment file, genre files, kind files and probe frontmatter are parsed with
`yaml.safe_load`. A YAML tag that would construct a Python object is a parse error and the
file is refused; the test suite holds this for genre files with a tag that would create a
file if it ran. The registration is JSON.

### 2. An installed genre or kind package is imported

Genres and kinds are found through the `ix.genre` and `ix.kind` entry points. An entry point
names a package of YAML files. ix never calls the entry point, but locating the files
imports the package, so **that package's `__init__.py` runs**. This is the trust you already
gave by installing the package; ix adds nothing to it and removes nothing from it. A
`genres/` or `kinds/` directory beside an experiment holds YAML only and imports nothing.

### 3. Paths in an experiment are followed

`probes: {source: directory, path: ...}` reads every `*.md` file of the directory named,
relative to the experiment file, and the path may point outside it. ix reads those files as
the user running it. Their content goes into the probe set's digest; the plan prints probe
ids and the digest, not the content. The registration is written beside the experiment
file, under a name derived from the file's own name.

### 4. A declaration is a claim, not a control

A kind declares its effects (`process`, `network`, `spend`, `tools`, `code`, `containers`,
`filesystem`) and the plan reports them. That is a statement by whoever wrote the kind file,
and ix has no code that enforces it. Two properties limit how it can mislead:

- an experiment can add effects to what a kind declares and cannot remove one;
- two files declaring the same kind are both refused, so a lab's `kinds/` directory cannot
  quietly redeclare a built-in kind with fewer effects, or a sensor's metric as not judged.

A third-party kind package is trusted to describe itself honestly, as in boundary 2.

### 5. The registration is not tamper-proof

A registration is a plain file. Its purpose is to make a change of design visible, not to
prevent one: anyone who can edit the experiment can edit or delete the record. Keep both in
version control, where an edit to the record is itself a visible change.

## Findings

### I-8 — `ix` is a taken name on PyPI (2026-10-09)

`ix` on PyPI is an unrelated package. Observed 2026-10-09: a throwaway extension package
that declared `dependencies = ["ix"]`, installed beside ix with `uv run --with <package>`,
pulled PyPI's `ix` instead of this one, and its import failed before this ix's CLI ran.

On the git-subdirectory install path it resolves correctly: uv clones the whole repository,
finds `components/capabilities/pyproject.toml` as the workspace root, and applies its
`[tool.uv.sources]`. It resolves to the stranger whenever a third-party package names `ix`
as a bare dependency, which is what a package shipping genres or kinds would naturally
write.

Until a naming decision is made, a package extending ix should depend on it by git URL
(`ix @ git+https://github.com/mox-labs/gnx#subdirectory=components/capabilities/ix`), not by
bare name.

### Earlier findings

I-1 to I-7 concerned code this package no longer has: a sensor that executed generated code
in process, the scoring of results, a dependency on `matrix`, and an optional evaluator's
telemetry. That code was removed in 0.3.0 and the findings went with it; they are in the
repository's history.

## Not covered

- ix does not verify the provenance of an experiment, a genre or a kind. Nothing is signed.
- ix does not check that a subject's `command` or a probe's `target` is safe to use. It
  reports what the kinds declare; whatever later runs the experiment owns that decision.

## Reporting

Open an issue on the gnx repository. Pre-gen-0 component; no embargo channel.
