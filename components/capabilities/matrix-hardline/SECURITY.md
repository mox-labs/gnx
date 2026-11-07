# Security — matrix-hardline

matrix-hardline is an adapter: it adds no network client, reads no credential and executes
nothing of its own. Every call it makes is a hardline call, and hardline's
[security notes](../hardline/SECURITY.md) cover where a prompt goes and how a key is read.

## Blast radius

- **A model runtime's config decides where prompts go.** `models` in a
  `matrix-hardline.v1.model-runtime` config is a hardline registry: each row's `base_url`
  receives every prompt a model step, a `pydantic-ai` session or a judge sends on it, tool
  results included. A config that reaches matrix from a directory you did not write (matrix's
  config tiers, its [SECURITY.md](../matrix/SECURITY.md)) can name one. Review it like one.
- **Unset, hardline reads its own tiers** (`~/.hardline/config.yaml`, `./hardline.yaml`,
  `$HARDLINE_CONFIG`): the working directory's `hardline.yaml` is read unasked, as matrix's
  `./matrix.yaml` is.
- **Keys are references, never values.** A row's `api_key` is `env:` or `file:`, resolved by
  hardline at the call. `resolved` (in the reuse fingerprint) leaves it out; the plan shows the
  reference as the config wrote it, never what it resolves to.

## What it records

hardline's record of a call (`hardline.v1.call`) and what a failed call carried
(`matrix-hardline.v1.failed-call`) go into the run's session table: attempts, the row's digest
and where each of its values came from, the turns' digest. No prompt or reply text is in
either document; the session row keeps a failed call's prompt as matrix keeps it, in the local
record only.
