# Changelog — hardline

## Unreleased

### Added

- **`ModelRuntime.aclose()`, and `async with runtime`.** Either closes the connections that the backends opened in the running event loop. The runtime stays usable afterwards. A backend that holds connections defines its own async `aclose()`, which the runtime calls; a backend without one is left alone.

### Fixed

- **A runtime used from a second event loop no longer duplicates the request.** Each backend kept one provider client for the runtime's whole life, and that client's connections were bound to the first loop. The second `asyncio.run` therefore got "Connection error" after the provider had already received the request. With retries the request was sent again, and with `retries: 0` the caller was told the provider was down. Clients are now pooled within one loop and never shared across loops. A loop that ends without `aclose()` leaves its connections open, and hardline logs a warning that names the fix.
