# gnx

The catalog CLI. It lists the components in this repository and projects them into the Claude
Code plugins the marketplace serves. A contributor's tool: it runs inside a clone of this
repository.

gnx installs nothing on a user's machine. Users install plugins with Claude Code's
`/plugin install`; gnx builds what that command finds.

## Run it

From the root of a clone:

```bash
uv --project components/capabilities run gnx list            # the components, by kind and type URL
uv --project components/capabilities run gnx build --check   # exits 1 if plugins/ has drifted from components/
uv --project components/capabilities run gnx build           # write plugins/ and .claude-plugin/marketplace.json
```

`just projection` runs `gnx build --check`, and `just check` runs it before every commit.

To install the command on its own:

```bash
uv tool install "git+https://github.com/mox-labs/gnx@gnx-v0.1.0#subdirectory=components/capabilities/gnx"
```

It reads `./components` from the directory it runs in, so run it from the repository root.

## Next

- [docs/explanation/overview.md](docs/explanation/overview.md): why the projection is committed,
  and a worked run.
- [SECURITY.md](SECURITY.md): `bundles.yaml` decides what a stranger installs.
- [CHANGELOG.md](CHANGELOG.md): what changed.
