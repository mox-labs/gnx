# gnx

Generative noetic extensions for agents.

## What

gnx is the **extensions catalog** in the mox stack — the vocabulary of typed, manifested capabilities that agents compose to do real work.

## Stack placement

| Layer | Repo | Role |
|---|---|---|
| Grammar | [slick](https://github.com/mox-labs/slick) | What extensions look like — `Manifest`, `TypedStruct`, `TypedRegistry`. *Semantic, LLM-Interpretable Component Kit.* |
| **Vocabulary** | **gnx** *(this repo)* | The actual extensions, conforming to slick manifests |
| Execution | [geist.sh](https://github.com/mox-labs/geist.sh) | Governed runtime that hosts extensions and intercepts agent tool calls |

slick gives the grammar. gnx supplies the vocabulary. geist.sh executes.

Extensions in gnx are platform-agnostic at the architecture level — any runtime that consumes slick manifests can run them. Current primary distribution surface is Claude Code.

## License

[MIT](LICENSE) — Copyright (c) 2025 Mox Labs.
