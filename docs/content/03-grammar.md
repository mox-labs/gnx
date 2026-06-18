# The grammar gnx inherits

**Source**: §4 of ground-truth.md. Established; slick's call; gnx does not reopen.

---

## The 4 kinds — and one stale list to bury

The taxonomy compressed to **Capability / Agent / Skill / Flow** in April 2026 and was
reaffirmed 2026-06-10. Both the 7-kind list (October 2025) and the 8-kind list (early
2026) did not survive that compression.

Flag: a June 5 doc re-introduced the 8-kind list. That is stale sourcing against an
explicit "don't reopen" ruling. Any content, scaffold template, or validation rule that
references 8 kinds needs correction before it lands in the catalog.

`kind` is an open string, not an enum. Enforcement is at the type-consequence level
(below), not by exhaustive enumeration.

---

## Vendor extensions isolate via apiVersion namespaces

The namespace split follows the Kubernetes CRD pattern:

| Scope | apiVersion example |
|---|---|
| Core (slick) | `slick.dev/v1` |
| Claude Code hooks | `hooks.claude.anthropic.com/v1` |
| Claude Code commands | `commands.claude.anthropic.com/v1` |

Portability class (Universal / Specialized / Vendor-Specific / Multi-Vendor) is
**registry-computed**, not declared. gnx derives it from the apiVersions present in the
manifest and its relations.

This is degeneration-watch #1 in structural form: the core namespace cannot accrete
vendor semantics because `slick.dev/v1` and `hooks.claude.anthropic.com/v1` are distinct
strings. Vendor vocabulary lives in vendor namespaces by construction.

aboot is the first real test of this split — its continuity-artifact core belongs in
`slick.dev/v1`; its Claude Code hook bindings (SessionStart, SessionEnd, PreCompact,
Stop) belong in `hooks.claude.anthropic.com/v1`.

---

## provides is discovery; produces/consumes is topology

Settled semantically 2026-04-21. The proto field names may change (§10.5), but the
semantic distinction is fixed.

| Field | Consumer | What it encodes |
|---|---|---|
| `provides` | Dagstra, `gnx search`, agents | Semantic discovery tags — what this component offers to proof-search |
| `produces` | matrix at build time | DAG topology — what this component emits |
| `consumes` | matrix at build time | DAG topology — what this component requires from the graph |

A component whose `provides` tags match a composition query may still fail DAG validation
if `produces`/`consumes` don't align. Search and wiring are independent layers.

A component with neither `provides` nor `produces`/`consumes` is invisible to both
layers and should not exist in the catalog.

---

## Skills are provides-only axioms

Skills carry no `produces` or `consumes`. They are axioms in the proof-search space:
they enter composition through `provides`-matching only, invisible to DAG validation by
design.

The reasoning: a Skill is activated by being read, not by being wired. It has no
port-level outputs that matrix needs to route. Giving a Skill DAG topology fields would
be a representable type error.

---

## Kind carries type-level consequences (2026-06-02)

A kind label that carries no structural enforcement re-opens the Kubernetes homonym
problem: `kind: Pod` means something in Kubernetes only because the controller enforces
it.

The rule: every kind implies a set of fields that are valid, required, or forbidden.
Violation is a type error at registration, not a warning.

Concrete corollary: a Skill declaring `produces` or `consumes` is a type error. The
kind forbids those fields. gnx's validator will enforce this at registration, not leave it to lint.

Before any new kind enters the catalog, the question is not "what shall we call it?" but
"what field-level consequences does it carry, and can gnx enforce them?"

---

## Cycles: condensation + leash, not flat DAG rejection

DAG-only Flow validation is stale doctrine as of 2026-06-02.

The current design classifies strongly connected components (SCCs) in a Flow graph
rather than rejecting any cycle. A Flow with cycles is valid if every SCC carries a
declared **leash** — a termination argument. Two leash forms are named:

- **fuel + convergence**: a decreasing measure that bounds iteration count
- **guardedness**: a productivity condition (coinductive; the computation produces output
  before it consumes input again)

Exact rule is pending Mission C / S9. Open position, not settled doctrine.
What is settled: flat DAG rejection is wrong. The condensation step (collapsing each SCC
to a node) is the right frame for reasoning about Flow topology.

---

## Strict, not Postel-liberal

At the grammar boundary, gnx is strict. Malformed manifests fail at `gnx validate`;
they do not get silently coerced into something plausible.

This composes with "calibration not correctness" without contradiction. gnx's structural
validator is strict (is this manifest well-formed? does the kind's type contract hold?)
while gnx never promises semantic correctness (does this composition do what you
intend?). Postel-liberalism at the grammar boundary would make the structural claim
meaningless.

---

## gnx defines the on-disk manifest format

Shipped slickit v0.2.0 Manifest has 5 fields:

```
type_url   string
source     string
requires   []string
provides   []string
relations  map[string][]string
```

JSON, in-memory only. **No `kind`, no `apiVersion`, no `metadata.name` on disk.** No
on-disk file convention exists in slickit.

gnx defines the on-disk format. Current lean: `manifest.yaml` (human-authoring ergonomics)
with JSON-aligned semantics underneath. Not hard-decided. See §10.3.

A scaffolded component from `gnx component init` will emit this file. The shape gnx
writes is the shape the ecosystem reads. Get it wrong once and the migration cost is paid
by every component author.

Sketch of what the on-disk manifest adds over the shipped Manifest:

```yaml
apiVersion: slick.dev/v1
kind: Capability
metadata:
  name: memex
  namespace: slick.dev
spec:
  provides:
    - episodic-memory
    - session-recall
  relations:
    skills: "bin/memex --skill"   # gnx-enforced: every Capability ships --skill
```

The `spec.*` fields map to the in-memory Manifest fields. `apiVersion`, `kind`, and
`metadata` are the gnx additions that slickit currently lacks.

---

## The intent-hardening gradient bounds catalog promises

What the catalog can promise depends on the tier of intent:

| Tier | Hardens to | How | Catalog shows |
|---|---|---|---|
| Structural | Full machine validation | `gnx validate` at registration; kind field contracts | "validated" |
| Behavioral | Assertion (L2.5 envelope) | Recorded and auditable; never proof | "asserted" |
| Semantic | Discovery surface only | `provides` tags; never a guarantee | "tagged" |

The gradient is not a quality ladder — it describes what kind of claim is being made.
A component's `provides: ["episodic-memory"]` is a tag, not a proof that the component
correctly implements episodic memory. The registry makes the tag searchable; geist.sh /
x.uma enforce behavioral envelopes above it.

Catalog surfaces should expose which tier each claim sits in. A component card that
presents a `provides` tag with the same visual weight as a validation badge is
misleading.

---

## Open questions

**§10.3 — On-disk manifest format**: `manifest.yaml` is the lean; `manifest.json` is the
alternative. The decision has real consequences: yaml tooling vs json tooling across
every scaffold template, every CI validator, every editor extension. This is gnx's call
to make, not slick's, but it should be made once and frozen.

**§10.5 — The proto seam**: `provides`/`produces` vs `requires`/`provides` — field names
are blocked on slick's next proto pass (Mission C / S9 unrun). gnx's semantic distinction
(provides = discovery, produces/consumes = topology) holds regardless of what the proto
fields are eventually named. The risk is that gnx builds vocabulary around current field
names and the rename creates a migration. Worth tracking whether gnx can alias cleanly or
needs to wait.

**Kind field-contract specification**: The type-consequence rule is settled in principle
(2026-06-02). Where does the normative field contract for each kind live? In slickit, in
a gnx schema file, or in the ground truth doc itself? The answer determines who owns a
future kind addition and what the amendment process looks like.

**Leash declaration syntax**: The condensation + leash design is settled in direction;
the exact syntax for declaring a leash in a Flow manifest is pending. `gnx component
init flow` will need to scaffold this. What does a leash declaration look like on disk?

