---
title: Grammar reference
section: reference
mode: reference
status: mixed
register: public
fidelity: tarmac
---

# Grammar reference

Lookup for the component grammar. Each entry carries a maturity mark: **shipped** (in slick v0.2.0), **ruled** (a settled convention whose enforcing validator is not yet built), **proposed** (a design tracked in the GEP register, not yet ratified or built), or **designed** (gnx's to define — decided in shape, not built).

---

## The shipped Manifest — five fields

slick v0.2.0 ships a `Manifest` of exactly five fields — JSON, in memory, behind the crate's `manifest` feature. **shipped.**

| Field | Type | Meaning |
|-------|------|---------|
| `type_url` | `string` | Globally unique identity; the single join key. Format: `<namespace>.<version>.<resource>`. |
| `source` | `string` | Where the implementation lives — a path or repository URL. |
| `requires` | `[]string` | Input ports — the typed surfaces this component depends on. |
| `provides` | `[]string` | What the component offers — typed output ports *and* discovery tags, told apart by shape (below). |
| `relations` | `map[string][]string` | Named pointers to associated surfaces; each key maps to one or more targets. |

There is no `kind`, no `apiVersion`, no `metadata`, and no transport field in shipped slick. The crate stores the manifest and round-trips it; it does not reject unknown fields or enforce the grammar below — that is the designed validator's job.

---

## `type_url` format

Dotted, version-medial, kebab-case resource. **ruled** (identity grammar).

```
<namespace>.<version>.<resource>
gnx.dev.v1.intent-hardening
```

- `<namespace>` — one or more dotted lowercase segments (`gnx.dev`). The catalog is the sole minting authority for `gnx.dev.*`; components do not self-mint.
- `<version>` — matches `v[0-9]+`, and is **version-medial** (in the middle, not the tail).
- `<resource>` — the final segment, the component name, **kebab-case** (`intent-hardening`, not `IntentHardening`).

`type_url` is the single join key across the system — authoring, discovery, and runtime all identify a component by this string, matched by equality. It is dots throughout; there is no slash form.

**Namespace extraction.** The version segment is the first segment matching `v[0-9]+`; everything before it is the namespace, everything after is the resource. Portability is computed by reading the namespace — this rule is what makes that computation well-defined instead of improvised.

**`kind` is never a `type_url` segment.** The kind lives in the overlay, never in the identity — so a component can be reclassified without an identity-breaking rename.

---

## The kinds

Every component declares one kind. `kind` is an **open literal, not a closed enum**. Type-level consequences are **ruled**; enforcement is a **designed** registration rule.

| Kind | What it is | Ports | Skill |
|------|------------|-------|-------|
| `Capability` | A runnable capability. | input + output ports | must expose `--skill` |
| `Agent` | A named reasoning role with a distinct method. | not yet specified | — |
| `Skill` | A `provides`-only axiom; read, not invoked. | none (declaring a transport is a type error) | is the skill |
| `Flow` | Composes other components as members. | derived from its members | — |

`Capability / Agent / Skill / Flow` are the current blessed set, but the literal is open: a component may declare a kind outside the four when its behaviour genuinely differs — one real component declares `kind: Processor` (a comprehension processor — its typed topology is design-intent; the shipped manifest declares discovery tags only). The overlay contract survives any answer to the kind count precisely because the kind never enters the `type_url`.

Because `Flow` is itself a kind, a Flow is a registrable component like any other — it carries its own `type_url` and is declared through the same manifest. A composition is therefore itself a catalog entry.

---

## `provides` — the shape rule

An entry in `provides` is a **port** or a **tag**, decided by shape. **ruled.**

- An entry that **parses as a `type_url`** (namespace + `v[0-9]+` + resource) is a **typed port** — it joins topology, is what `requires` matches against, and feeds capability closure.
- An entry that is **dotted-lowercase with no version segment** is a **discovery tag** — it feeds search and the projector's keywords, and nothing ever joins on it.

```yaml
provides:
  - gnx.dev.v1.recon-report   # parses as a type_url → a typed PORT
  - recon.search              # dotted-lowercase, no version → a TAG
```

No third case; a near-miss (an entry *almost* a `type_url`) is a linter warning, more likely a typo'd port than an exotic tag. Skills legitimately carry tags alone — for the skill class, ports do no work, and the rule makes that legible. Whether topology ports ever get their own separate fields is **deferred** to the first component the shape rule cannot serve; the rule holds until then.

| Role | Field | Consumer |
|------|-------|----------|
| discovery ("what can do X?") | `provides` tags | agents, `gnx search` |
| input port | `requires` | a runtime, at wiring |
| output port | `provides` ports | a runtime, at wiring |

A Skill declaring an input or output port is a type error.

---

## `relations` — typed edges

`relations` is a `map[string][]string` of named, convention-keyed pointers. **shipped** (the field); key vocabulary **designed**. Keys seen in real components on disk: `uses` (a soft dependency — one component uses another). Well-known by convention, from the format's own documentation but not yet used by any shipped component: `skills` (where a Capability's embedded skill travels), `tested_with`, `replaces`, `depends_on`. slick stores relations; it never interprets them.

For a Capability, `relations["skills"]` must point at a surface that travels inside the artifact.

---

## The gnx overlay

gnx writes the five slick fields plus a small overlay it owns, as flat YAML. **proposed** as a contract (GEP-0009); `kind` and `maturity` are written in every real manifest today.

| Field | Status | What it carries |
|-------|--------|-----------------|
| `kind` | on disk today | The component kind; an open literal, provisional pending the kind count |
| `maturity` | on disk today | Honesty and gate: `shipped` projects to an installable plugin; a lower value (e.g. `design`) does not |
| `version` | proposed | Content revision; flows into `plugin.json` as Claude Code's install cache key |

The overlay is invisible to the slick crate — the crate cannot validate it, so gnx's own validator carries the entire burden. A misspelled overlay key vanishes silently everywhere except that validator.

---

## Namespace and portability

The namespace encodes scope. **ruled** (principle); classification **designed.**

Core, vendor-neutral components live under `gnx.dev`; anything specific to one runtime lives under that runtime's namespace. Portability — how far a component travels — is **computed by the registry** from the namespaces present in the manifest, never self-declared. The full classification is not yet specified.

---

## Cycles

Cycles are **rejected** today, in the derived topology. **shipped semantics** (via the runtime the shape derives from). A rule accepting a loop only with a *declared bound* — a run budget or convergence condition — is **proposed**, not settled.

---

## On-disk format

slick ships JSON in memory with no on-disk file convention. gnx defines one: flat YAML — the five slick fields plus the overlay, no `apiVersion` / `metadata` / `spec` envelope. **designed.** `manifest.yaml` is the file the scaffold emits and the registry reads.

---

## See also

- **[How components work](/docs/how-components-work)** — the same grammar, explained rather than tabulated.
- **[API & schema index](/docs/api-schema-index)** — the typed configs and schemas a `type_url` keys into.
- **[gnx CLI reference](/docs/cli-reference)** — `gnx validate` enforces these rules at registration (designed).
- **[What's real vs planned](/docs/status)** — shipped vs designed across the whole catalog.
