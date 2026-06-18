---
title: Grammar reference
section: reference
mode: reference
status: mixed
register: public
fidelity: tarmac
---

# Grammar reference

Lookup for the component grammar. Each entry is marked **shipped** (in slick v0.2.0), **established** (decided doctrine, not yet built), **designed** (gnx's to define), or **open seam** (the distinction is settled but the field names are not). Verify against the shipped source before relying on a field; doctrine and shipped code are marked apart.

---

## The shipped Manifest — 5 fields

slick v0.2.0 ships a Manifest of exactly five fields, JSON, in memory. **shipped.**

| Field | Type | Meaning |
|-------|------|---------|
| `type_url` | `string` | Globally unique identity; the schema key. Format `<namespace>.<version>.<Resource>`. |
| `source` | `string` | Where the component comes from. |
| `requires` | `[]string` | Input ports — `type_url`s this component depends on. |
| `provides` | `[]string` | Output ports — `type_url`s this component offers. |
| `relations` | `map[string][]string` | Named pointers to associated surfaces; each key maps to one or more targets. |

There is no `kind`, no `apiVersion`, no `metadata.name`, and no `protocols` in shipped slick. Those are designed additions (below).

---

## `type_url` format

Dot-delimited, PascalCase resource. **shipped** (the convention is live in slick).

```
<namespace>.<version>.<Resource>
cix.commands.v1.Recon
cix.skills.v1.RustMastery
slick.dev.v1.Aboot
```

`type_url` bridges the Manifest to the runtime type and, in the designed model, to the schema in `api/`. One join key across the system.

---

## The four kinds

Every component is one of four kinds. Open string, not an enum — the constraint is type-level consequences, not an exhaustive list. **established** (reaffirmed 2026-06-10); enforced as a **designed** gnx registration rule.

| Kind | What it is | Ports | Skill |
|------|------------|-------|-------|
| `Capability` | A runnable capability. | input + output ports | must expose `--skill` |
| `Agent` | A named reasoning role with a distinct method. | — | — |
| `Skill` | A `provides`-only axiom; read, not invoked. | none (declaring a transport is a type error) | is the skill |
| `Flow` | A declared composition of other components. | ports of its members | — |

The 7-kind and 8-kind taxonomies from 2025/early 2026 did not survive; four is the settled set.

---

## `apiVersion` and namespace — designed

The namespace encodes vendor scope (Kubernetes-CRD style). A gnx on-disk addition. **designed.**

| Scope | apiVersion |
|-------|------------|
| Core (vendor-neutral) | `slick.dev/v1` |
| Claude Code hooks | `hooks.claude.anthropic.com/v1` |
| Claude Code commands | `commands.claude.anthropic.com/v1` |

Portability class — Universal / Specialized / Vendor-Specific / Multi-Vendor (the last still future) — is **computed by the registry** from the namespaces in the manifest. Never self-declared. **designed.**

---

## Discovery vs topology — the open seam

In shipped slick, `requires` and `provides` are the ports. The designed model splits the roles: `provides` becomes the semantic discovery surface, and typed topology moves to `produces` / `consumes`. **open seam** — what is settled is the distinction (discovery is matched before composition; topology is matched at build); the field names are not.

| Role | shipped field | designed field |
|------|---------------|----------------|
| discovery ("what can do X?") | `provides` | `provides` |
| input port | `requires` | `consumes` |
| output port | `provides` | `produces` |

---

## On-disk format — designed

slick ships JSON in memory with no on-disk file convention. gnx defines the on-disk format. The lean is `manifest.yaml`, JSON-aligned underneath; not yet fixed. **designed (open).** The designed on-disk manifest adds `apiVersion`, `kind`, and `metadata`; `spec.*` carries the in-memory fields.

---

## See also

- **[How components work](/docs/how-components-work)** — the same grammar, explained rather than tabulated.
- **[gnx CLI reference](/docs/cli-reference)** — `gnx validate` enforces these rules at registration (designed).
- **[Status](/docs/status)** — what's shipped vs planned across the whole catalog.
