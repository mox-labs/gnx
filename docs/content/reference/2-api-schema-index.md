---
title: The api/ schema index
section: reference
mode: reference
status: planned
register: public
fidelity: tarmac
---

# The api/ schema index

**designed.** No schemas exist yet — this is the planned shape, recorded so authoring and tooling can be built against it. The join key throughout is `type_url`.

---

## One directory, one join key

Everything with a declared type lives under `api/`, keyed by `type_url`: data types, component config schemas, and protocol config schemas all sit in one place, joined by the same key the Manifest uses.

```
api/
└── slick.dev/v1/
    ├── <DataType>.schema          # the shape of a typed payload
    ├── <ComponentConfig>.schema   # a component's config surface
    └── <ProtocolConfig>.schema    # e.g. an MCP or HTTP transport config
```

The directory mirrors the apiVersion namespace, so where a schema lives on disk is its vendor scope — the same invariant that governs `components/` and `extensions/`.

---

## Defined vs opaque

A config is either **defined** or **opaque**, and the difference is whether gnx can validate it.

| | Has a schema in `api/`? | gnx can validate it? |
|---|---|---|
| **defined** | yes | yes — checked at `gnx validate` |
| **opaque** | no | no — travels as an unvalidated blob |

A generated component produces its own schema, so **defined** is the natural default: the config surface is mechanics (it belongs in `api/`), distinct from the component's semantics. Opaque is the escape hatch for a config gnx has no schema for yet — it still travels, it just isn't checked.

---

## The type-url convention

`type_url` follows the same `type.googleapis.com`-style fully-qualified convention used across the stack — one identity that names the type in the Manifest, names the runtime type, and names the schema in `api/`. A single key, three surfaces, no drift between them.

---

## Open edges

- **Protocol as a Manifest field vs implementation config.** Whether transport (`Protocol`) is a top-level Manifest field or lives inside a component's implementation config is genuinely unresolved. The schema index holds either way — protocol config schemas are `type_url`'d and live in `api/` regardless.
- **MCP as a fifth adapter vs riding HTTP/CLI.** MCP may ride the existing transports (session JSON-RPC over stdio/SSE/HTTP) rather than being its own adapter. Affects how its config schema is organized, not whether it lives in `api/`.

---

## See also

- **[Grammar reference](/docs/grammar-reference)** — the Manifest fields the schemas back.
- **[How components work](/docs/how-components-work)** — `type_url` as the join key, in prose.
