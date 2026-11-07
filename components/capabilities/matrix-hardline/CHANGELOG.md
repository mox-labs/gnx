# Changelog — matrix-hardline

## 0.1.0 — 2026-11-06

The first release: the hardline model runtime moves out of matrix, which depends on no model runtime
from matrix 0.10 (owner ruling HO1, 2026-11-06). Compared with matrix 0.9's `hardline` model runtime:

### Changed (breaking)

- **The type URL is `matrix-hardline.v1.model-runtime`**, under this distribution's own namespace,
  where it was `matrix.v1.model-runtime.hardline` (short name `hardline`). A config names it in full.
- **The registry is the runtime's own `models`**, or hardline's tiers; matrix's top-level
  `matrix.models` is gone, and nothing is handed to the runtime from it.
- **hardline's record is one document**, `provenance["hardline.v1.call"]` (hardline's `CallRecord`,
  whole), where matrix 0.9 copied its fields one by one onto `ModelCall` and the session row (`backend`,
  `attempts`, `retries`, `fallback_from`, `params`, `attempt_log`, `turns_digest`, `row`). A field
  hardline adds to its record now reaches the session row with no edit here.
- **A failed call's attempts** are `provenance["matrix-hardline.v1.failed-call"]` (`attempts`,
  `attempt_log`, `limit`); its usage is the call's `usage`.
- **The pydantic-ai `Model` is not this runtime's**: `matrix.pydantic_ai_model(runtime, model)` builds
  one over any model runtime, this one included.

### Added

- **One mapping of hardline's failures** to the port's reasons (`reason`), where matrix 0.9 had two
  that disagreed on hardline's `ConfigError` (`failed` in one, `incapable` in the other): it is
  `incapable` here.
- **hardline's warnings are the runtime's `warnings`**, so `matrix plan` shows a fallback that shares
  its primary's fate, where matrix 0.9 only logged it.
- **`check(model, uses=...)` refuses tools and an output structure together**, and tools on a row whose
  backend cannot call them, at binding.
- **A request carries turns, tools and the `max_tokens` and `temperature` overrides**: the whole call
  hardline's `complete` takes, as matrix 0.10's widened port does.
- **Its entry points name its points**, `model-runtime` and `payload-type`, as matrix 0.10 reads them:
  a session that reads neither (a recon survey) does not import the bridge or hardline.
