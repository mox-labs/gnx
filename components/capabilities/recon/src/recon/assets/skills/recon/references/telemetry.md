# Telemetry

What a survey or a query tells about itself: an event log in every survey's archive, and the same spans and two metrics through OpenTelemetry. The names below (spans, metrics, events, attribute keys) are not contract and may change without notice; what telemetry never carries is.

## The event log

Every survey writes `telemetry.jsonl` into its archive, beside `meta.yaml`: one `recon.v1.event` line when a span starts, one when it ends, and, while a table is written, a `recon.collect.progress` line at most once a second carrying `recon.records`, the records written so far. Each line is `{schema, name, time, span, parent, kind, attributes, status, message, duration_ms}`, the times from recon's clock, so a survey against a fixed clock writes the same log twice. The file is open for the whole survey and each line reaches it as it happens, so it can be read while the survey runs. It is not a table: `query` does not read it.

| Span | Attributes |
|---|---|
| `recon.survey` | `recon.mission`, `recon.archive`; at its end `recon.tables`, `recon.tables.failed`, `recon.records` |
| `recon.collect`, one per table | `recon.table`, `recon.collector`, `recon.collector.type`, `recon.source`; at its end `recon.records`; between, `recon.collect.progress` events |
| `recon.http.request`, one per attempt to send a request | the HTTP client attributes `http.request.method`, `server.address`, `server.port`, `url.full`, `http.response.status_code`, and `http.request.resend_count` on a retry |
| `recon.mcp.call`, one per call to an MCP server, a listing included | `mcp.method.name` (`tools/call`, `tools/list`, `resources/read`, ...), `gen_ai.tool.name` for a tool call, `recon.source`; never the arguments or the result |
| `recon.query` | `recon.mission`, `recon.archive`; at its end `recon.rows` |

A span that ends with `status: error` says why in `error.type` (a recon error kind such as `transient` or `auth`, a status code such as `503`, an `httpx2.<class>`, or `body_too_large`) and in a `message` in recon's own words that quotes nothing of the error. A start with no end is where a killed survey stopped. No secret and no body is logged: `url.full` has its user information, the source's `auth.param` value and the signature parameters `REDACTED`, and no header or body is an attribute. A failed table's error, its command or URL included, is in the archive's `meta.yaml`.

## OpenTelemetry

recon emits the same spans through the OpenTelemetry API (instrumentation scope `recon`, schema URL `https://opentelemetry.io/schemas/1.44.0`), and two counters from the table ends: `recon.collect.records` (by `recon.collector.type`) and `recon.collect.failures` (by `recon.collector.type` and `error.type`). Each exported span carries `recon.event.span`, its id in the event log, so a line of `telemetry.jsonl` is the span with that id in the trace whose `recon.survey` span carries the archive's `recon.archive`. While a span is open it is the current one, so a span another library opens inside recon's work nests under it, and each HTTP request sends its span as a `traceparent` header.

The `recon` command installs the OpenTelemetry SDK and its OTLP exporter, both installed with recon, when the standard variables ask for an export:

- `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318` exports traces and metrics;
- `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` or `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` exports that signal alone;
- `OTEL_TRACES_EXPORTER=none` or `OTEL_METRICS_EXPORTER=none` turns a signal off, and `OTEL_SDK_DISABLED=true` all of them.

recon exports over http/protobuf only: `OTEL_EXPORTER_OTLP_PROTOCOL=grpc` is refused with an `otel_unavailable` warning naming what to set. An install without the SDK gives an `otel_unavailable` warning in the document instead of an export. A provider the program installed is kept. A Python program that imports recon gets no SDK installed: it configures OpenTelemetry itself, or calls `recon.configure_telemetry()`, which does what the command does.

## What never slows a survey

OpenTelemetry and a program's `on_event` callback receive a survey's events through a bounded queue (4096 events) that a thread of its own drains, so neither slows the writing of the archive nor changes a byte of it; the event log alone is written as each event happens. At the survey's end the receivers get up to 10 seconds to catch up. The survey's document then says what they missed:

| Warning | Means | What to do |
|---|---|---|
| `events_dropped` | `got` events never reached OpenTelemetry or `on_event`: the queue was full, or the end could not wait | nothing for the data, which is whole; a faster exporter (a batching span processor) or callback loses none |
| `telemetry_stuck` | a receiver had not returned from an event when the survey ended: it was not closed, and its thread stays until it returns | give the exporter a timeout, or replace it |
| `telemetry_failed` | a receiver raised, and got nothing more | fix the receiver; the data is unaffected |
| `otel_unavailable` | the variables ask for an export recon cannot make | as its `fix` says |
