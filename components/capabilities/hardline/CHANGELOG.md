# Changelog — hardline

## Unreleased

### Added

- **`ModelRuntime.aclose()`, and `async with runtime`.** Either closes the connections that the backends opened in the running event loop. The runtime stays usable afterwards. A backend that holds connections defines its own async `aclose()`, which the runtime calls; a backend without one is left alone.

### Changed (breaking)

- **hardline's own backends register through its own `hardline.backends` entry points**, like a third party's, and the hard-coded table is gone. `discover_backends()` no longer takes `include_entry_points`; it returns exactly the installed entry points.
- **A broken backend plugin fails only the rows that name it.** An entry point that cannot be loaded, does not produce a `ModelBackend`, or shares its name with another used to abort composition for every row, the first two with an exception that was not a `HardlineError`. Each is now loaded on its own and kept as a stand-in that names the fault: a row naming it fails composition with a `ConfigError`, and a warning is logged.
- **Backend options are checked when the runtime is composed.** A backend may define `check(spec)`, returning what is wrong with a row; the runtime calls it for every row. Each built-in backend reads its `options` through a model of its own and refuses any key it does not read, so `hardline check` fails a row that would fail its first call. A mock `script` naming an unknown error reason is refused there too.
- **A keyless `anthropic` row is a config problem.** It used to pass `hardline check` and then fail its call as `auth` ("the provider rejected the key", exit 6) when no provider had been asked. It now fails composition, naming `models.<name>.api_key` (exit 3).
- **Each discovered tier is named with how it was found**: `user <path>`, `cwd <path>`, `$HARDLINE_CONFIG <path>`, in errors and in `hardline check`.

### Fixed

- **A tier that cannot be read is a `ConfigError`.** `$HARDLINE_CONFIG` naming a directory passed discovery and raised `IsADirectoryError`; a tier that is not UTF-8 raised `UnicodeDecodeError`. Discovery now requires `$HARDLINE_CONFIG` to name a file, and a tier that cannot be read or decoded names itself in a `ConfigError`.

- **A runtime used from a second event loop no longer duplicates the request.** Each backend kept one provider client for the runtime's whole life, and that client's connections were bound to the first loop. The second `asyncio.run` therefore got "Connection error" after the provider had already received the request. With retries the request was sent again, and with `retries: 0` the caller was told the provider was down. Clients are now pooled within one loop and never shared across loops. A loop that ends without `aclose()` leaves its connections open, and hardline logs a warning that names the fix.
- **Everything the library raises on purpose is a `HardlineError`.** An empty message list, `max_tokens` of 0 or less, a negative or `nan` temperature, and a negative `schema_retries` were a bare `ValueError` or a pydantic `ValidationError`, and a negative temperature was sent to the provider. Each is now a `ConfigError` raised before anything is sent.
- **A registry row may not name the same fallback twice.** `fallbacks: [b, b]` asked `b` again in full after it had just failed. It is refused at validation, naming the row and the repeated name.
- **`extract` finds the answer after bracketed prose.** A reply such as `Answer [1] below: {...}` had `[1]` taken as the answer and the valid object discarded, which cost a repair turn. Every balanced value that parses as JSON is now tried against the schema in turn.
