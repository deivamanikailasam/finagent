# AI usage

> **Owner: complete every `<...>` field before submitting. State only what you personally did.**

## Tools and models

| Tool | Used for | Model |
|---|---|---|
| Claude Code | Planning drafts, code, tests, scripts, configuration, documents, reviews | `<owner: confirm the model IDs used>` |
| Claude Haiku 5.5 | The product's optional runtime rationale | `claude-haiku-5-5` |

## What AI generated

- All source code, tests, scripts and configuration in this repository.
- Drafts of the execution plan, build guide, decision log and requirements table. These are private working documents and are not in the repository.
- A private reference key, drafted before the clock started: `<owner: describe what it was and how it was used>`.
- Drafts of `README.md`, `DESIGN.md`, `docs/architecture.md`, this file, and the samples, which are rendered from evaluation output.

## What the owner decided and reviewed

`<owner: list, in your own words, the decisions you made and the code, tests and outputs you personally reviewed.>`

The golden labels for the retrieval evaluation were proposed by Claude from the policy text, without running retrieval, and accepted by the owner before measurement.

## AI reviews

Claude ran two rounds of three independent AI reviewers over the finished build: one against a requirements table and one against the brief. Their findings were fixed or listed as limitations. **These were AI reviews, not human reviews.** The commit history has no review trailers, so it does not by itself show who reviewed each change.

## Material not authored

- **Corpus:** supplied with the brief and unmodified. Its files are hash-locked in `config/registry.yaml`.
- **Adapted snippets:** `<owner: none, unless you know otherwise>`.
- **Direct dependencies** (exact versions are locked in `uv.lock`):

| Library | Version | Licence |
|---|---|---|
| anthropic | 2.12.0 | Apache-2.0 |
| bm25s | 0.3.16 | MIT |
| numpy | 2.5.5 | BSD-3-Clause AND 0BSD AND MIT |
| pydantic | 2.14.4 | MIT |
| pydantic-settings | 2.16.3 | MIT |
| pyyaml | 6.0.4 | MIT |
| typer | 0.29.2 | MIT |
| tzdata | 2026.6 | Apache-2.0 |

## Time

See [TIME_LOG.md](TIME_LOG.md). Each block runs from its first prompt to Claude's last activity on it, and gaps between prompts are not counted. The log states both active time and the elapsed window. Pre-clock preparation is `<owner: minutes>`.
