# Security — hardline

hardline sends text to model providers and reads credentials to do it. It opens no listening
socket and executes nothing a model returns.

## Blast radius

A call sends the caller's messages to the endpoint a registry row names. The row decides
where data goes: `local: true` rows point at a server on this machine; any other row sends
the prompt off-machine. **A registry file decides where your prompts go.** Review it like one.

## Credentials

- **References only.** `api_key` accepts `env:NAME` or `file:PATH`. A literal key is rejected
  at validation (`ModelSpec._secret_is_a_reference`), so a key cannot be committed inside a
  config file by accident.
- **No ambient fallback.** The OpenAI-compatible backend passes a placeholder instead of letting
  the SDK read `OPENAI_API_KEY`; the Anthropic backend refuses to run without a reference
  rather than read `ANTHROPIC_API_KEY`. A credential is used only when a row names it.
- **Never in messages.** Provider errors are wrapped as `BackendError` after the key, and its
  first-8 and last-6 characters, are replaced with `***`
  (`adapters/_out/_scrub.py`; `test_openai_compat_error_is_wrapped_and_key_scrubbed`).
  `SecretError` names the reference, never the value.
- `Completion.raw` is the provider's response metadata with the message content excluded. It
  holds no request headers.

## Model output

Structured output is parsed with `json` and validated with pydantic — never `eval`ed or
imported. Text is returned as data. Anything a caller does with it is the caller's boundary.

## Open

- **M-1: `base_url` is not constrained.** A row can point a hosted model's key at any URL. A
  malicious or mistaken row sends that key to the named host. Mitigation today: review
  registry changes; keys are per-row, so a stray row leaks only the key it references.
- **M-2: entry-point backends run in-process.** Discovery imports every installed
  `hardline.backends` entry point at composition. Installing a package is trusting it.
