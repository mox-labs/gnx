---
title: "Spec: the gnx CLI"
section: spec
mode: reference
status: planned
register: internal
fidelity: cobblestone
---

# Spec: the gnx CLI

**Status: revised after five-lens review** (burner · dijkstra · ace · vector · antifragile-arch; findings record in `scratch/cli-spec-panel-findings.md`). This spec makes the CLI buildable: it ratifies the open decisions, fixes the architecture, and names what gnx inherits from cix versus what it fixes at the boundary. The public command contracts live in the [CLI reference](/docs/cli-reference); this spec is the architecture underneath them. Per the intake ruling: **cix is input, not constraint** — the proven shape ports, the verified defects do not.

---

## The two walls — prerequisites, not backlog

The panel's convergent verdict: both boundaries the design depends on were asserted as in-process disciplines. They must be runtime authority.

**W1 — SDK isolation.** The Claude Agent SDK is an **outbound** dependency, not an inbound driver: `ProposerPort` (in `domain/ports/_out/`) with methods that return **inert data** — `conduct_interview(context) -> InitPlan`, `curate(context) -> Proposal`. The adapter (`adapters/_out/sdk_proposer.py`) is constructed with `CatalogReadPort` and nothing else — read-only by constructor signature. Three further layers, because a Python object graph is not a sandbox:

1. **Process boundary** — SDK sessions run out-of-process; proposals cross a narrow IPC as data; no shared writable filesystem region with the deterministic writer.
2. **Tool allowlist** — an SDK session with Bash/Write tools can mutate the registry out-of-band regardless of the object graph. Sessions run with a tool allowlist excluding filesystem writes outside their scratch dir.
3. **Import gate** — an import-linter contract in CI forbids the edge from the proposer module to any write port or write use case; `mypy --strict` is load-bearing for this boundary, not hygiene.

**W2 — The produce-authority wall.** On the author's machine, nothing distinguishes a "human mint" from agent-written state — so accreditation must be **a detached signature over the component's content hash, from a key the author's runtime cannot produce**. The CLI *verifies* accreditation; it is structurally unable to *mint* it. The ledger is registry-held, keyed by content hash + minting-authority identity; trust-bearing fields carried inside fetched artifacts are claims, never status. **The concrete authority mechanism is the owed decision** (CI signer / human hardware key / server-side registry) — build of the registry write path is blocked on it, per ground-truth §9: "build that wall first."

---

## Decisions this spec ratifies

| # | Decision | Ruling | Basis |
|---|----------|--------|-------|
| R1 | Language | **Python ≥3.12**, uv, hatchling | cix's hexagonal core ports directly; sits next to matrix; `--skill` loader ports verbatim; `uv tool install gnx`. Rust kernel stays "later, if evidence demands." Closes §10.4. |
| R2 | Architecture | Hexagonal, ports as `typing.Protocol` | Proven in cix. Structural typing means the gates carry the guarantee: import-linter layer contract + `mypy --strict` are named parts of the architecture, enforced in CI (F7). |
| R3 | Agentic boundary | **Non-deterministic sessions produce candidates; only the deterministic validator produces verdicts; only deterministic code holds write capability** | Enforced by W1's three layers. The validator is a pure function of (candidate, catalog snapshot, registration identity, gnx version) — no network, no clock, catalog-read and registry-append under one lock. |
| R4 | Output contract | **JSON is the contract; the table is a courtesy** — CLI-wide | Every command supports `--json` and emits the R7 envelope. Auto-detect (JSON when piped) stays as the human courtesy; `--json` is the documented agent path — SKILL.md teaches it. |
| R5 | Manifest parsing | Parse manifest YAML/JSON directly; **conformance-gated against slickit** | slickit 0.2.0's Python crust is broken at source. gnx parses directly — but a **golden manifest corpus** (accept + reject cases) is validated by both gnx's parser and the slickit Rust crate in CI, so the two implementations cannot drift silently (F3's disease, one level up). slick owns Manifest semantics; gnx owns the on-disk serialization convention. Parsing lives in adapters; domain receives constructed `Manifest` values. |
| R6 | Exit codes | **Four values, frozen**: `0` success (an empty search result is success) · `1` domain failure (validation failed, drift, unsatisfied ports, install failed) · `2` usage error · `3` environment error (no SDK credentials, registry unreachable, target unavailable) | The exit code says which way to branch; `errors[].code` says why. Failure kinds never grow the exit-code space — they grow the (additive) error-code namespace. CI treats any non-zero as blocking: rejection and tool-crash both fail, distinguishably. |
| R7 | JSON envelope | `{"apiVersion": "gnx.dev/v1", "ok": bool, "data": ..., "errors": [{"code", "message", "path", "hint"}]}` | The CLI's own output is a declared, versioned surface — the doctrine applied to itself. Error codes are namespaced stable strings (`manifest/missing-field`, `kind/skill-has-transport`, `ports/unsatisfied-require`) mapping 1:1 onto the validate rule table. `hint` is load-bearing: agents recover by reading it. One JSON document on stdout; diagnostics on stderr; no ANSI under `--json` (pinned by a test). |
| R8 | Bootstrap | `gnx init` writes a gnx section into the generated CLAUDE.md ("run `gnx --skill`"); gnx also ships as a CC plugin whose skill activates on intent | The hop from "gnx exists here" to "run `--skill`" was tribal knowledge — the exact failure the agent-first doctrine exists to prevent. |

---

## The port cut

The single RegistryPort conflated three governance regimes. The cut, with the wall as interface shape:

| Port | Methods (shape) | Regime |
|------|-----------------|--------|
| `CatalogReadPort` | search, get, provides-index, portability inputs | open read — the only port the SDK proposer receives |
| `CatalogWritePort` | register, lifecycle-state (F4) | deterministic code only; write under the registry lock |
| `LedgerPort` | `append(record)`, `read(...)` — **nothing else** | accreditation: append-only enforced by the method set; authority identity is a required field on the record; minting per W2 |
| `InstallStatePort` | project/user install records (`.gnx/`) | local, ungoverned — the catalog ports never see `Installation` |
| `SourcePort` | fetch, list, get, materialize | v1 sources materialize on the local filesystem — the `Path` seam to TargetPort is a **named invariant**, revisited when a registry-API source lands |
| `TargetPort` | name, is_available, install, uninstall, is_installed, `describe()`, `supported_namespaces()` | live-environment mutation; `get_config_path` dropped (a boundary hole — it invited callers to bypass the port) |
| `ProjectionPort` | `project(component_set) -> tree`, `check(...)` | authoring-time repo operation (`gnx build`); **CC's plugin.json/marketplace.json vocabulary lives only in the CC projection adapter** — the build use case never learns a vendor manifest schema |
| `ProposerPort` | interview/curate → inert data | W1 |

`supported_namespaces()` is the adapter capability negotiation ground-truth §8b requires: the projection use case filters the catalog by it, and the reference adapter (F8) declares a *different* namespace set so the filter path is a tested branch, not a dead one.

```
src/gnx/
├── domain/
│   ├── models.py          # Component, Manifest, kind consequences, Installation (provenance + pin), PortabilityClass
│   └── ports/_out/        # the eight protocols above
├── application/
│   └── use_cases.py       # Gnx facade + error hierarchy + result DTOs (the JSON contract is application-owned;
│                          #   cli.py only chooses table-vs-JSON rendering)
├── adapters/
│   ├── _in/cli.py         # rich-click; composition root; --skill flag; exit-code mapping
│   └── _out/              # claude_code_target, reference_target, claude_code_projection,
│                          #   filesystem_registry, local_source, sdk_proposer
├── assets/skills/gnx/     # SKILL.md + references/*.md — packaged in the wheel
└── skill.py               # importlib.resources loader (ports verbatim from cix)
```

---

## Fixes at the boundary (verified cix defects + panel corrections)

| # | Rule | Precise form |
|---|------|--------------|
| F1 | **Install atomicity — as an invariant, not a mechanism** | *At every observable instant, and after recovery from a crash at any instant, the destination resolves to either the complete prior version or the complete new version — never a partial tree, never absent.* Mechanism: stage to a **sibling directory under the destination's parent** (same filesystem — `$TMPDIR` staging risks EXDEV and a copy-delete fallback); verify the staged inode; per-target lock; two-rename swap (`dest → dest.old.<nonce>`, `staged → dest`) with a recovery rule (a surviving `.old.<nonce>` with missing `dest` rolls back on any invocation). Claimed crash model: **atomic under process kill** (tested by killing at every step boundary, including between the renames); power-loss atomicity is not claimed. |
| F1b | **Verify is adversarial, not just structural** | The staged tree is untrusted: reject entries containing `..`; reject symlinks whose realpath escapes the staging root; confirm the swap destination's realpath is confined under the target root; **enumerate executable surfaces** (`hooks/`, `bin/`) in the verify report instead of silently installing deferred code execution. Verify binds to the inode that gets swapped (no TOCTOU re-resolution). |
| F2 | **Provenance and pin are different facts** | `installed_commit` (provenance — always recorded, for audit) and `pin: Pinned(commit) \| Floating` (intent — set by `add --pin`/`pin`, never implicitly). `update`: Floating → latest; Pinned → no-op ("held at `<commit>`") unless `--latest`, which installs latest and re-pins only with an explicit `--pin`. Bulk `update --latest` refuses to override pins unless `--all-pins`. |
| F3 | **One source, two generated manifests** | `gnx build` generates plugin.json and the marketplace entry from one source; `gnx build --check` fails CI on drift and is a **mandatory branch-protection gate**; generated files carry a generator-emitted integrity hash so hand-edits are detectable without a rebuild. |
| F4 | **Lifecycle state is data** | Never a path convention (cix: `_radix.parked-while-tuning/`). |
| F5 | **type_url uniqueness is catalog-global, enforced at index construction** | One namespace across `api/`, `components/`, every `extensions/<target>/`. Building the index over the declared roots **fails closed** on collision. `gnx validate <dir>` additionally checks the candidate against the current index, and the check re-executes atomically with the registry append (under the registry lock — no validate-then-write TOCTOU). Uniqueness key: the full `type_url` including its version segment; a new version is a new `type_url`. |
| F6 | **Adapter tests are required — including the adversarial paths** | The port-fakes pattern stays for the facade. Adapters get real-I/O suites: the F1 kill-matrix, F1b's hostile trees (traversal, symlink escape), F2's full state table. The TargetPort contract suite is **parameterized over adapters** and runs against both `claude_code_target` and `reference_target` (F8) — a port change CC needs that the reference can't satisfy fails CI. |
| F7 | **Self-contained gates, wired** | ruff (strict select), `mypy --strict`, pytest config in gnx's own pyproject; justfile as gate runner; `.githooks` wired by `just setup` (`core.hooksPath` — cix's hooks were verified never wired). **Import-linter contracts in CI**: layer direction (`domain ← application ← adapters`), no vendor symbols in domain/application, and the W1 forbidden edge (proposer → write ports). These gates are load-bearing for R2/R3, not hygiene. |
| F8 | **A reference target adapter ships in-tree** | A deliberately non-CC target (generic directory target with a different config model and namespace set). It forces M=2 so the Boundary Test discriminates, keeps the port vocabulary vendor-neutral mechanically (degeneration-watch #2), and makes namespace-filtered projection a tested path. |
| F9 | **Validation is static** | `validate` and `build` never import or execute candidate code — kind consequences are checked by parsing (a poisoned repo must not achieve execution inside a CI gate; `build --check` runs on attacker-openable PRs). The behavioral half of the `--skill` rule (the binary actually responding) is an **assertion-tier check recorded off the registration path** — see the rule table. |
| F10 | **Namespace authorization** | The directory boundary (`components/` vs `extensions/`) is an ontological check; it is not authorization. Registration identity gates **which namespace an author may write**: `slick.dev/v1` by chartered maintainers, vendor namespaces by their vendor. F5 stops duplication; this stops impersonation. Intra-catalog references resolve to **pinned content hashes** at build — a reference bump is a reviewable diff, not a silent supply-chain propagation. |

Also inherited-with-care: sources that are git repos are cloned into a gnx-controlled location with hooks and filters disabled — git is never run against an untrusted embedded `.git` (`core.fsmonitor`/smudge filters execute code on ordinary operations).

---

## The validate rule table (tiered per the intent-hardening gradient)

| Rule | Check | Tier | Error code |
|------|-------|------|-----------|
| Manifest well-formedness | parse against the golden-corpus-conformant schema | structural | `manifest/*` |
| Kind consequences | a Skill declaring transport/topology is rejected — by parsing | structural | `kind/skill-has-transport` |
| Port satisfaction | `requires` resolve in the catalog index (local roots offline; catalog-dependent checks report **skipped-with-reason** when no registry is reachable, never silent pass) | structural | `ports/unsatisfied-require` |
| Namespace scope + authorization | vendor fields off core schemas; author may write this namespace (F10) | structural | `namespace/*` |
| type_url uniqueness | against the index, re-checked at append (F5) | structural | `type-url/duplicate` |
| Embedded skill declared | `relations["skills"]` resolves to a surface **inside the artifact**; SKILL.md parses | structural | `skill/missing` |
| `--skill` responds | executing the binary — **runs sandboxed, off the registration path**, recorded as an auditable assertion | assertion (L2.5) | `skill/unverified` |
| Per-component ACE remainder | config-over-hardcoding, hidden coupling — the named, versioned review checklist | editorial | curation review |

`validate` on pass emits a one-line summary on TTY and `{"ok": true, "data": {"checks": [...]}}` under `--json` — silence is not a contract. `gnx build` runs `validate` over every source component before projecting; that is why "`build --check` in CI" covers both.

---

## The `--skill` mechanism — with the drift guard

Ports verbatim from cix (`skill.py`, `importlib.resources`). Two panel additions: the **intent table covers the full lifecycle** (search/inspect *and* add/rm/update — the discovery loop must not dead-end one step before value), and SKILL.md gets F3's medicine: the intent table is **generated from the click command tree** (or a parity test asserts every command appears and every entry exists). SKILL.md documents `--json` as the invocation agents use, and enumerates its reference pages by name.

`gnx init` declares its real dependencies (SDK credentials, network → exit `3`, `env/sdk-unavailable`, with a hint) and offers `--defaults` — a deterministic, interview-free path. The interview is the human courtesy; the flags are the agent contract. Scaffolds narrate the loop: `component init` ends with "Next: `gnx validate`".

---

## Test discipline

- **Domain**: property tests on models and kind consequences.
- **Application**: in-memory fakes implementing the ports — the facade tested with zero I/O (cix's 19-test shape).
- **Adapters**: real-I/O suites per F6 — kill-matrix, hostile trees, the F2 state table, parameterized contract suite over both target adapters.
- **Architecture**: the import-linter contracts run as tests — the W1 boundary and the layer direction fail the build, not a review comment.

---

## Open questions

- **W2's minting authority** — CI signer / human hardware key / server-side registry. **The owed decision**; blocks the registry write path, nothing else.
- **On-disk manifest format** — `manifest.yaml` leaning; final call in the manifest spec.
- **SourcePort v1 scope** — local + git in v1; the port makes a registry-API source a new adapter.
- **Init profiles** — plain curation data (leaning) vs a new kind.
- **Plugin definitions' home** — which components each plugin bundles: `distributions/` layer (leaning — a plugin's composition becomes a reviewable authored artifact) vs marketplace entries. Ties to ground-truth §8a's open edge; the authoring loop needs this named before `build` is coded.
