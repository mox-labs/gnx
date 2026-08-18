# radix — The Snapshot-vs-History Temporal Axis

> Context substrate for the FINAL radix. Honesty markers: **[DECIDED]** · **[LEANING]** ·
> **[OPEN]** · **[BUILT]** · **[UNBUILT]**. Authority order on conflict: this-session principal
> input > `~/mox/research/meta/decisions.md` OD rulings > 2026-06-09 proposal > 2026-06-02 common
> model > prototypes.
>
> This axis is **NEW this session** — it is not in the older common-model or proposal docs. The
> axis ruling is DECIDED; its realization as an axis is UNBUILT everywhere.

---

## 1. The ruling [axis DECIDED, principal this session]

Snapshot-vs-history is a **first-class orthogonal TEMPORAL axis OVER Base / Relations / Models —
NOT a fourth frame, NOT a peer scale.**

For the same artifact the finalized radix must produce BOTH:

- a **HISTORY frame** — how/why the artifact became what it is (evolution, timeline, rationale,
  inflection points); and
- a **SNAPSHOT frame** — what the artifact IS right now.

Two distinct outputs. Every modality has the axis; this file says how each realizes it. This is a
**comprehension** concern — distinct from ilm's downstream bi-temporal *ranking* clock (§4).

The origin of the ruling: radix-code as-built comprehended the **diachronic** artifact — the git
history, PRs, evolution, rationale (`archaeology.py`) — NOT the codebase-as-a-snapshot. The
finalized radix must produce both readings, for every modality.

---

## 2. Per-modality realization

| Modality | HISTORY (diachronic) | SNAPSHOT (synchronic) | Prior art |
|---|---|---|---|
| **CODE** | git commits/tags; PR/RFC evolution (proposed → accepted → amended → superseded), rationale, inflection commits, temporal coupling. Snapshot identity = git **tree-hash** per commit | current tree (ordinary file/crate frames) | **Strongest — BUILT.** 4 gate-passing `temporal` frames (base = snapshots@versions; relations = lineage/drivers/inflection/structural_delta; models = design_evolution / what_evolved_and_why / constraint). Tooling: `archaeology.py` + codemaat backfill |
| **IMAGE / video** | frame-sequence / boot-trajectory / iteration history; adjacency computes adjacent-pair deltas | key still / terminal settled state (narrator-preferred) | **Strongest dual-output prior art — BUILT within-sequence.** adjacency emits BOTH `symmetry_emergence` (HISTORY) AND `symmetry_terminal` (SNAPSHOT) from one pass |
| **TEXT / conversation** | turn-by-turn evolution; position drift; when a claim first settled vs got revised | settled conclusion / current model | **NONE — entirely UNBUILT.** radix-text has no diachronic frame; vaani parses a snapshot only |

The pattern generalizes: a conversation's turn-by-turn evolution vs its settled conclusion; a
video's frame-sequence vs a key still; a codebase's git-history vs its current tree. Every modality
has the axis; only text is a complete blank today.

---

## 3. The genuine departure to build (the crux) [UNBUILT as an axis in all three]

All three prototypes realize temporal as a **peer scale / sibling ArtifactKind**, NOT as an
orthogonal axis:

- **radix-code** — `scale='temporal'` sits *alongside* file/crate — a peer, and the
  least-exercised (4 temporal frames vs 266 file frames). It proved the diachronic material is
  gate-passable; it did **not** conceive snapshot + history as dual outputs of one axis.
- **radix-vis** — temporal = `ArtifactKind.SEQUENCE` + `FrameKind.SEQUENCE`; `FrameKind.TEMPORAL`
  is declared but **unmapped** in `_FRAME_KIND_BY_ARTIFACT` (unbuilt); iteration-history is
  designed only.
- **radix-text** — the axis is absent entirely.
- **common-model + proposal** — the axis is absent; the nearest hook is "temporal composition as
  the same machinery" via `arises_from`.

### The net-new design work

Lift temporal from *a scale / a frame-kind* to *an orthogonal `snapshot|history` selector over
Base/Relations/Models*. The raw material exists (radix-code's `parent-of` lineage +
`archaeology.py`; radix-vis's emergence/terminal dual); the **dual-output-per-axis design is
unspecified.**

**The decision the spec must make explicit** [OPEN — no OD covers it]: does every modality's Frame
carry a **`temporal_view ∈ {snapshot, history}`** discriminator (this realizes the principal's
"not a fourth frame") rather than a separate FrameKind / Scale? This is the *recommended framing*,
not a ruling.

**Consequence to record.** A HISTORY frame's Anchors terminate in *multiple* time-point Artifacts
linked by `parent-of` lineage, each a distinct content-hashed Artifact. This changes what a
Frame's Status / Anchor must span — a snapshot frame anchors to one Artifact; a history frame
anchors across a lineage of them.

---

## 4. Keep distinct from ilm's bi-temporal recency [boundary discipline]

ilm establishes three downstream time terms — transaction time, valid time, last-recall — but
these are **ranking modulators**, not radix's history-frame comprehension. radix produces HISTORY
+ SNAPSHOT *frames*; ilm's last-recall is a query-time fold over a `recall_events` ledger
(downstream). Do not collapse the two: the temporal axis is a *comprehension* output; recency is a
*ranking* input.

**One touchpoint: source timestamps.** Today radix-text preserves source `created` / `updated`
**only inside `artifact.meta` JSON**, not a typed queryable column; `frames.created_at` =
comprehension time (episode `started_at`), NOT source valid-time. Surfacing source valid-time as a
typed column is a schema decision **gated behind a principal scope amendment (D-ilm-1 / OD-6)** and
must not preclude the downstream ilm read. radix must not build it; radix must not preclude it.

---

## 5. Carry-forward — what the prototypes give the axis

| Capability | code | text | vis | → gnx/radix as |
|---|---|---|---|---|
| Temporal snapshot + history | ✅ peer scale (4 frames) | ❌ absent | ✅ both readings, sibling kind | **lift to orthogonal axis — net-new** |
| `parent-of` lineage machinery | ✅ (`archaeology.py`, tree-hash identity) | ❌ | partial (adjacency deltas) | history-frame Anchor substrate |
| dual-output from one pass | ✅ temporal frames | ❌ | ✅ `symmetry_emergence` + `symmetry_terminal` | closest prior art to the dual-output requirement |

**radix-code carries the lineage primitives** (`commit_at` / `tree_at` / `file_log` /
`find_revert_oscillations`; codemaat backfill; snapshot identity = git tree-hash), but modeled
temporal as a peer SCALE and operated on git commits/tags/versions only — PR-rationale entered via
recon-ingested issue/PR artifacts, not `archaeology.py`. Rebuild the driver, carry the primitives.

**radix-vis carries the dual-output shape** — one adjacency pass emitting both a HISTORY reading
(`symmetry_emergence`) and a SNAPSHOT reading (`symmetry_terminal`), narrator preferring the
terminal. This is the closest as-built evidence that one pass can yield both outputs of the axis.

**radix-text is the gap** — no diachronic frame at all. Turn-by-turn evolution, position drift, and
when-a-claim-settled-vs-revised are all UNBUILT.

---

## 6. Open questions

- **The `temporal_view ∈ {snapshot, history}` selector** — recommended framing; no OD legislates
  it. The build must not silently pick a separate FrameKind/Scale instead (that would re-introduce
  the peer-scale error the ruling corrects).
- **History-frame Anchor spanning** — a history frame's Anchors terminate across a lineage of
  content-hashed Artifacts; the Status / Anchor spanning semantics for multi-time-point frames are
  UNBUILT.
- **Text diachronic realization** — entirely unbuilt; the design for turn-by-turn evolution frames
  does not exist.
- **Source valid-time as a typed column** — gated behind OD-6 (D-ilm-1); out of radix scope, must
  not be precluded.

---

## Provenance

Consolidated 2026-07-04 from this-session principal input (the snapshot-vs-history axis is new this
session), the authoritative radix spec §2, `~/mox/research/meta/decisions.md`, and the prototypes:
radix-code (`~/radix-workspaces/rust-mastery/tools/src/radix/`, `archaeology.py` + 4 temporal
frames — the only place the axis was exercised on real artifacts), radix-vis
(`~/mox/platform/mox.studio/tools/radix-vis/`, the `symmetry_emergence` / `symmetry_terminal` dual
output), and radix-text (`~/mox/research/tools/radix-text/`, where the axis is absent). The axis is
DECIDED; its realization as an orthogonal axis is UNBUILT in all three prototypes.
