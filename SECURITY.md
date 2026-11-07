<!--
The route below is GitHub private vulnerability reporting. It must be enabled in the
repository's settings (Settings → Code security → Private vulnerability reporting); without
it the Security tab has no "Report a vulnerability" button. It read as enabled on 2026-11-07
(`gh api repos/mox-labs/gnx/private-vulnerability-reporting`).
-->

# Security

## Report a vulnerability

Report it privately: on [github.com/mox-labs/gnx](https://github.com/mox-labs/gnx), open the
**Security** tab and choose **Report a vulnerability**. The report is visible only to you and
the maintainers until a fix is published.

Do not open a public issue for a vulnerability. Say which package and version, what an attacker
can do, and how to reproduce it.

One person maintains gnx, so a report is answered on a best-effort basis; there is no
guaranteed response time. If the Security tab shows no **Report a vulnerability** button, open
an issue that asks for a private contact, with no details of the vulnerability in it.

## Supported versions

The latest release of each package. Every package is below 1.0, and a fix ships in that
package's next release, recorded in its `CHANGELOG.md`.

## Each package's trust boundaries

Each package's `SECURITY.md` says what it trusts, what it executes or sends, and the findings
already fixed.

| Package | Trust boundaries |
|---|---|
| `ix` | [components/capabilities/ix/SECURITY.md](components/capabilities/ix/SECURITY.md) |
| `ix-cosmic-ray` | [components/capabilities/ix-cosmic-ray/SECURITY.md](components/capabilities/ix-cosmic-ray/SECURITY.md) |
| `recon` | [components/capabilities/recon/SECURITY.md](components/capabilities/recon/SECURITY.md) |
| `matrix` | [components/capabilities/matrix/SECURITY.md](components/capabilities/matrix/SECURITY.md) |
| `hardline` | [components/capabilities/hardline/SECURITY.md](components/capabilities/hardline/SECURITY.md) |
| `matrix-hardline` | [components/capabilities/matrix-hardline/SECURITY.md](components/capabilities/matrix-hardline/SECURITY.md) |
| `dao` | [components/capabilities/dao/SECURITY.md](components/capabilities/dao/SECURITY.md) |
| `gnx` | [components/capabilities/gnx/SECURITY.md](components/capabilities/gnx/SECURITY.md) |
