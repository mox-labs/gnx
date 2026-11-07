# gnx

Components that make agents more effective, and their work inspectable.

gnx is a set of Python command-line tools and libraries, plus skills installed as Claude Code
plugins. You, or an agent working for you, use them to evaluate an agent, collect data, compose
agents and components, and call models. The project is owned by yzavyas and built mostly by
Claude; [How gnx is built](docs/content/start/2-how-gnx-is-built.md) says how.

## Why you would use it

| The question you have | Tool | What it gives you |
|---|---|---|
| Does my skill fire on the asks it should, and stay quiet on decoys? Does an extra review round make my agent's code pass its tests more often? Is the new build no slower? | [ix](components/capabilities/ix/README.md) | A verdict, `holds`, `fails` or `inconclusive`, on a 95% interval, and an exit code (0, 10, 11) a CI job can gate a merge on. |
| I need releases from GitHub, tickets from our tracker's API and a CLI's output in one place, collected the same way every week, that my agent can query. | [recon](components/capabilities/recon/README.md) | One YAML mission. recon shows every request and command before it runs, collects with no model in the loop, keeps each run as an archive, and answers SQL: `recon query <mission> "<SQL>"`. |
| My pipeline fetches items, has an agent review each, and a model score the review. Afterwards, which agent, prompt and model produced each row? | [matrix](components/capabilities/matrix/README.md) | Components declare the kinds of data they need and provide. matrix orders them, checks the wiring before anything runs, and writes every row to disk as it is handed over, with a ledger naming the component behind it and a session row for every agent session and model call. |
| My code calls Claude. Can I try the same prompt on a local model or Gemini without rewriting the call, and know which family answered and what it cost? | [hardline](components/capabilities/hardline/README.md) | One call shape for every model family. Models are named rows in `hardline.yaml`; each answer is tagged with the family that gave it, its token usage and its latency. A `mock` backend answers offline, for tests. |
| Is my project's `.claude/` a real bench of agents, or a few thin prompts that look like one? | [dao](components/capabilities/dao/README.md) | `dao check <project>`: each of eleven parts PRESENT, PARTIAL or ABSENT with the gap named, never a score. It checks an organization; it does not write one. |

What makes them easier for an agent to drive, and their work easier to check:

- **The guide comes from the tool.** `ix --skill` (and `recon`, `matrix`, `hardline`) prints the
  guide for the version installed, so an agent never works from a stale page.
- **A plan before a run.** `ix plan`, `matrix plan` and `recon survey --dry-run` run nothing and
  list every setting with who set it: `matrix.limits.retry.attempts = 1  (matrix)`, or
  `(config matrix.yaml)` when you set it.
- **A refusal names the fix.** `ix run` on an unregistered experiment exits 15 and prints
  `fix: ix register <path>, then run it`.
- **The design is frozen before the numbers.** `ix register` fixes the experiment before a run,
  so a threshold cannot move once results are in. `ix report` recomputes a verdict from the
  run's record.

## Hand a tool to your Claude

Install the tool so it is on your `PATH` (see [Install a release](#install-a-release)), then ask
Claude Code to read its guide before it starts:

```
Run `ix --skill` and read it. Then write an ix experiment that checks whether my
code-review skill fires on these three asks and stays quiet on these two.
```

To make it standing, add a line to your project's `CLAUDE.md`:

```
To evaluate an agent or a skill, use ix: run `ix --skill` first and follow it.
```

recon's guide also ships as a Claude Code plugin, which Claude loads on its own when an ask fits
([below](#skills-for-claude-code)).

An ix run against a real agent starts Claude Agent SDK sessions, which cost what the provider
charges. `ix plan` prints the most model calls a run can make before anything runs.

## Try it from a clone

No install and no model call. Needs [uv](https://docs.astral.sh/uv/) and Python 3.12 or later.

```
git clone https://github.com/mox-labs/gnx && cd gnx
uv --project components/capabilities run ix --skill | head
uv --project components/capabilities run ix plan components/capabilities/ix/examples/harness-integrity
```

The plan runs nothing and calls no model. It shows the question, the trials a run would make,
`Model calls:  none`, and ends with the next command, `Next: ix register …`.

The first `uv` call builds the workspace's environment. After that every tool runs the same
way: `uv --project components/capabilities run <tool>`. To put one on your
`PATH` from the clone, `uv tool install ./components/capabilities/<tool>`; it runs from the
clone, so keep it.

## Install a release

Each package installs from this repository at its release tag. None is on a package index:
the names `matrix` and `ix` on PyPI belong to other projects.

```
uv tool install "git+https://github.com/mox-labs/gnx@ix-v0.9.0#subdirectory=components/capabilities/ix"
uv tool install "git+https://github.com/mox-labs/gnx@recon-v0.19.1#subdirectory=components/capabilities/recon"
uv tool install "git+https://github.com/mox-labs/gnx@matrix-v0.10.0#subdirectory=components/capabilities/matrix"
uv tool install "git+https://github.com/mox-labs/gnx@hardline-v0.3.1#subdirectory=components/capabilities/hardline"
uv tool install "git+https://github.com/mox-labs/gnx@dao-v0.1.0#subdirectory=components/capabilities/dao"
```

uv takes each package's dependencies from this repository too. Mutation testing adds
`ix-cosmic-ray` beside ix:

```
uv tool install "git+https://github.com/mox-labs/gnx@ix-v0.9.0#subdirectory=components/capabilities/ix" --with "git+https://github.com/mox-labs/gnx@ix-cosmic-ray-v0.1.0#subdirectory=components/capabilities/ix-cosmic-ray"
```

Each README has the line for using the package as a library (`uv add`). Every version is
below 1.0, so a minor release may break: pin a tag, and read the package's `CHANGELOG.md`
before you move.

## The packages

| Package | What it is | Docs |
|---|---|---|
| `ix` | Decides a question about an agent or a service by experiment: one YAML file, validated, planned and registered before it runs, reported as `holds`, `fails` or `inconclusive`. CLI and library. | [README](components/capabilities/ix/README.md) · [SECURITY](components/capabilities/ix/SECURITY.md) |
| `ix-cosmic-ray` | cosmic-ray's mutation operators as ix's mutant source, for `ix mutants sample`. Library, installed beside ix. | [README](components/capabilities/ix-cosmic-ray/README.md) · [SECURITY](components/capabilities/ix-cosmic-ray/SECURITY.md) |
| `recon` | Collects from APIs, CLIs, web pages, documents and MCP servers into JSONL, queried with SQL. CLI, library, and a Claude Code skill. | [README](components/capabilities/recon/README.md) · [SECURITY](components/capabilities/recon/SECURITY.md) |
| `matrix` | Runs components wired by the kinds of data they exchange, composes agents from a definition plus a runtime, and records every row as a run goes. CLI and library. | [README](components/capabilities/matrix/README.md) · [SECURITY](components/capabilities/matrix/SECURITY.md) |
| `hardline` | One interface to every model family (local MLX or ollama, Gemini, Claude), each answer tagged with the family that gave it. CLI and library. | [README](components/capabilities/hardline/README.md) · [SECURITY](components/capabilities/hardline/SECURITY.md) |
| `matrix-hardline` | The bridge that makes hardline a matrix model runtime. Library; ix depends on it. | [README](components/capabilities/matrix-hardline/README.md) · [SECURITY](components/capabilities/matrix-hardline/SECURITY.md) |
| `dao` | Audits a project's agent organization: each of eleven parts present, partial or absent, never a score. CLI. | [README](components/capabilities/dao/README.md) · [SECURITY](components/capabilities/dao/SECURITY.md) |
| `gnx` | Projects this repository's components into Claude Code plugins, and checks the committed projection. CLI, for contributors. | [README](components/capabilities/gnx/README.md) · [SECURITY](components/capabilities/gnx/SECURITY.md) |

Dependencies: `ix` needs `matrix` and `matrix-hardline`; `matrix-hardline` needs `matrix` and
`hardline`; `recon` needs `matrix`. `matrix` calls no model on its own.

## Skills for Claude Code

The plugins put skills in Claude Code: text Claude loads when an ask matches, with no
program behind it. The tools above are separate Python commands; only recon has both.

| Plugin | What it does for you |
|---|---|
| `rational-inquiry` | Checks whether a "therefore" holds: tests an argument against five named ways a reason fails to support its conclusion, and names the one that fired. |
| `intent-hardening` | Turns a vague ask ("make the onboarding better") into a buildable one: states the strongest other reading, asks what changes downstream, and says how settled the result is. |
| `recon` | recon's `--skill` guide as a skill, so Claude reaches for recon when an ask is about collecting data. The `recon` command installs separately ([above](#install-a-release)). |

In Claude Code:

```
/plugin marketplace add mox-labs/gnx
/plugin install rational-inquiry@gnx
```

[The catalog](https://mox-labs.github.io/gnx/catalog) lists every component, read from this
repository's source.

## How gnx is built

yzavyas makes the decisions: rulings, merges to `main`, releases. Claude does most of the
design, code and review under those rulings. The evidence is in the repository: design records
in each package's `docs/design/`, commit trailers naming the Claude session, and the `just`
gates. [How gnx is built](docs/content/start/2-how-gnx-is-built.md) has the detail, the limits,
and a correction the process caught.

## Working here

```
just              # list every gate
just check        # what a commit must pass (the pre-commit hook runs it)
just ci           # the full gate, which CI runs as it stands
```

[CONTRIBUTING.md](CONTRIBUTING.md) has the workflow. [AGENTS.md](AGENTS.md) is the short form for
an agent. Report a vulnerability privately: [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE). Copyright (c) 2026 Mox Labs.
