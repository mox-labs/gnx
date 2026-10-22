# Security — hardline

hardline sends text to model providers and reads credentials to do it. It opens no listening
socket and executes nothing a model returns.

## Blast radius

A call sends the caller's messages to the endpoint a registry row names. The row decides
where data goes: `local: true` rows point at a server on this machine; any other row sends
the prompt off-machine. **A registry file decides where your prompts go.** Review it like one.

## Locations

- **From the row, or the backend's declared default, and nothing else.** hardline speaks to
  providers over HTTP itself, with no provider SDK, so no SDK fills a missing `base_url` from
  `OPENAI_BASE_URL` or `ANTHROPIC_BASE_URL`, or sends `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID`
  as headers. A row with no `base_url` is given its backend's default when the runtime is
  composed, so the row, `hardline models` and each call's record show where it sends
  (`test_a_row_without_base_url_goes_to_its_backends_default_whatever_the_environment`).
- **No proxy or certificate variable.** HTTP clients are built with `trust_env=False`:
  `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `SSL_CERT_FILE` and `SSL_CERT_DIR` change neither
  where a request goes nor what it trusts. A proxy is used only when a row names one
  (`proxy:`), and one carrying credentials is refused, since they would sit in a config file
  (`test_a_row_names_its_proxy`, `test_a_proxy_the_row_cannot_use_fails_composition`). TLS is verified against the operating system's trust
  store (truststore).
- **Redirects are not followed.** A 3xx is a config error whose fix names where to point the
  row's `base_url`; following it would send the row's key to wherever it points
  (`test_a_redirect_is_reported_not_followed`).
- **Which registry files are read is ambient.** `./hardline.yaml` is found through the working
  directory and `$HARDLINE_CONFIG` through the environment. Each tier is named with how it was
  found (`cwd`, `$HARDLINE_CONFIG`) in errors, in `hardline check`, and in the record of every
  call, against each field it set. A host that hands hardline its own section
  (`build_runtime(config=...)`) reads no file at all.

## Credentials

- **References only.** `api_key` accepts `env:NAME` or `file:PATH`. A literal key is rejected
  at validation (`ModelSpec._secret_is_a_reference`), so a key cannot be committed inside a
  config file by accident.
- **No ambient fallback.** A credential is used only when a row names it. An `openai-compat`
  row with no `api_key` sends no `Authorization` header, whatever `OPENAI_API_KEY` holds; an
  `anthropic` row with no `api_key` fails composition rather than read `ANTHROPIC_API_KEY`.
- **Per request, never cached by value.** HTTP clients are kept per event loop and keyed by
  location and timeout; the key is added to each request's headers.
- **Never in messages.** A provider's error is wrapped as `BackendError` after the key, and its
  first-8 and last-6 characters, are replaced with `***`
  (`adapters/_out/_scrub.py`; `test_openai_compat_error_is_wrapped_and_key_scrubbed`).
  `SecretError` names the reference, never the value.
- `Completion.raw` is the provider's response metadata with the message content excluded: no
  `content` blocks, and of each OpenAI-compatible choice only its index and finish reason. It
  holds no request headers.
- A call's record holds digests, never content: `turns_digest` of the turns sent, and the row's
  digest, which covers the row's `api_key` *reference*, not the key.

## Telemetry

hardline records OpenTelemetry spans and metrics through the API, which sends nothing until the
host application installs an SDK and an exporter. What it records carries no content: no
prompt, system prompt, reply, refusal or error message is an attribute, a span event or a
span status, and a failed call's exception is not recorded on its span
(`test_no_content_leaves_in_a_span`). What it does record says where calls went: the model id,
the row name, the provider's host and port, token counts, reasons and durations.

## Model output

Structured output is parsed with `json` and validated with pydantic — never `eval`ed or
imported. Text is returned as data. Anything a caller does with it is the caller's boundary.

## Open

- **M-1: `base_url` is not constrained.** A row can point a hosted model's key at any http(s)
  URL. A malicious or mistaken row sends that key to the named host. Mitigation today: review
  registry changes; keys are per-row, so a stray row leaks only the key it references; the
  record of every call names the location and the tier that set it.
- **M-2: entry-point backends run in-process.** Discovery imports every installed
  `hardline.backends` entry point at composition, each on its own: one that fails to load fails
  only the rows that name it. Installing a package is trusting it.
