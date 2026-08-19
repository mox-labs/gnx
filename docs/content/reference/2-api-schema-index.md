---
title: The api/ schema index
section: reference
status: proposed
register: public
---

# The api/ schema index

`api/` is the designed home for every typed surface's schema in the catalog. The join key throughout is `type_url`. **This directory does not exist yet** — no schemas are populated today, so every config in the catalog is currently *opaque* (below). This page describes the shape it will take.

---

## What lives in `api/`

Three categories of schema share one directory, all keyed by `type_url`:

```
api/
└── gnx.dev/v1/
    ├── <data-type>.schema          # the shape of a typed payload
    ├── <component-config>.schema   # a component's config surface
    └── <transport-config>.schema   # how a Capability is reached
```

The directory mirrors the namespace — the same invariant the rest of the catalog tree follows. Where a schema file lives on disk *is* its scope: core schemas under `gnx.dev`, anything runtime-specific under that runtime's namespace.

---

## Defined vs opaque

A config is either **defined** or **opaque**, and the difference is whether gnx can validate it.

| | Has a schema in `api/`? | Can gnx validate it? |
|---|---|---|
| **defined** | yes | yes — checked at `gnx validate` |
| **opaque** | no | no — travels as an unvalidated blob |

Opaque is the onboarding ramp; defined is the destination. A config rides as an opaque typed value until a schema for its `type_url` lands in `api/`, at which point validation switches on with no change to the identity. Neither state blocks the other — a composition of opaque configs still compiles from its ports; only the config *contents* go unchecked. Today, with `api/` unpopulated, everything is opaque.

---

## The `type_url` convention

`type_url` follows a fully-qualified, package-tree convention — one string standing for one type across every surface: the manifest at authoring time, the runtime type at execution, and the schema file in `api/`. There is no drift between them because they share a key, not a copy. Look up a component's `type_url` and you have found its schema.

---

## Open edges

- **Transport location.** Whether a Capability's transport config is a top-level field or lives inside its implementation config is unresolved. The index holds either way: a transport config schema is `type_url`'d and lives in `api/` regardless of where the manifest points at it.
- **Schema format.** The concrete schema language and how normalization is defined (so a hash of a schema is stable) are unspecified.

---

## See also

- **[Grammar reference](/docs/grammar-reference)** — the manifest fields the schemas back.
- **[How components work](/docs/how-components-work)** — `type_url` as the join key, in prose.
- **[What's real vs planned](/docs/status)** — where the `api/` layer sits on the shipped/designed line.
