# finagent: financial processing and RAG workflow agent

**Time spent: about 8.5 hours of build on the clock (08:09 to about 16:40 on 2026-11-23), which is past the 8-hour timebox. Preparation before 08:09: `<P>` minutes. Directing and reviewing AI-drafted planning material: `<Y>` hours.** See [docs/TIME_LOG.md](docs/TIME_LOG.md) and [docs/AI_USAGE.md](docs/AI_USAGE.md).

An internal accounts-payable assistant. It retrieves finance documents, reconciles an invoice against its PO, receipts, vendor record and policy, recommends one outcome, and **stops for a human** before any finance decision is recorded. Controls are deterministic. The model only explains a decision that has already been made. Nothing here can move real money: posting and blocking write to a local SQLite sandbox.

- Design note: [DESIGN.md](DESIGN.md)
- Architecture and manifest: [docs/architecture.md](docs/architecture.md)
- Evaluation results: [docs/EVAL_RESULTS.md](docs/EVAL_RESULTS.md)
- Sample flows: [samples/FIN-001.md](samples/FIN-001.md) (success), [samples/FIN-002.md](samples/FIN-002.md) (exception and approval)

## Environment choice

Local, on macOS or Linux, with a locked dependency set. A reviewer can run everything offline, with no credentials and no cloud cost. Run locking uses `fcntl`, so Windows is not supported.

**Prerequisites:** Python 3.12, [uv](https://docs.astral.sh/uv/), GNU Make 3.81 or later. `jq` is optional.

## Quick start (offline, fake model)

```bash
uv sync --locked
make ingest
make test        # unit, contract and acceptance tests; no model calls
make scenarios   # FIN-001 to FIN-005, end to end
make eval        # per-assertion report; stores JSON in var/eval/ and rewrites samples/
```

Run one case by hand. **Use a separate `FINAGENT_HOME` for each case**, because the fixtures deliberately share vendor V-100 and invoice INV-1001. In a shared workspace, one case's ledger rows and blocks correctly change the next case's outcome.

```bash
export FINAGENT_HOME=var/FIN-001
RID=$(make -s run-dev ARGS="run start --case fixtures/cases/FIN-001 --json" | jq -r .run_id)
make run-dev ARGS="run get $RID"
make run-dev ARGS="run approve $RID --approver cc-manager-01"
make run-dev ARGS="run approve $RID --approver cc-manager-01"   # replay: identical response, one POST
```

`make run-dev` supplies a throwaway HMAC key for local use.

## Commands

| Operation | With make | Without make |
|---|---|---|
| Install | `make install` | `uv sync --locked` |
| Ingest and verify the corpus | `make ingest` | `uv run finagent ingest` |
| Start a run | `make run-dev ARGS="run start --case <DIR>"` | `uv run finagent run start --case <DIR>` |
| Start from a request file | `make run-dev ARGS="run start --request <FILE>"` | `uv run finagent run start --request <FILE>` |
| Get a run | `make run-dev ARGS="run get <ID>"` | `uv run finagent run get <ID>` |
| Approve | `make run-dev ARGS="run approve <ID> --approver <A>"` | `uv run finagent run approve <ID> --approver <A>` |
| Reject | `make run-dev ARGS="run reject <ID> --approver <A> --reason <R>"` | `uv run finagent run reject <ID> --approver <A> --reason <R>` |
| Crash recovery | `make run-dev ARGS="run resume <ID>"` | `uv run finagent run resume <ID>` |
| Run the cases and list results | `make run-dev ARGS="eval list --run"` | `uv run finagent eval list --run` |
| List stored results | `make run-dev ARGS="eval list"` | `uv run finagent eval list` |
| Retrieval evaluation | `make run-dev ARGS="eval --retrieval"` | `uv run finagent eval --retrieval` |
| Stable tests | `make test` | `uv run pytest -q` |
| Scenarios | `make scenarios` | `uv run pytest -m scenarios -q` |
| One real model call | `make test-llm` | `uv run --env-file .env pytest -m llm -q` |
| Workflow with the real model | `make run-live ARGS="run start --case <DIR>"` | `FINAGENT_LLM=anthropic uv run --env-file .env finagent run start --case <DIR>` |
| Live evaluation | `make run-live ARGS="eval --live --configs template,haiku"` | `uv run --env-file .env finagent eval --live --configs template,haiku` |

Without make, set `FINAGENT_HMAC_KEY` first: `export FINAGENT_HMAC_KEY=$(openssl rand -hex 32)`.

## Environment variables

Settings are read from the environment only. Copy `.env.example` to `.env` only for the `run-live` and `test-llm` targets, which load it explicitly.

| Name | Required | Default | Example | Secret |
|---|---|---|---|---|
| `FINAGENT_HMAC_KEY` | Yes (`make` targets supply a throwaway one) | none | 64 hex characters | Yes |
| `FINAGENT_HOME` | No | `./var` | `var/FIN-001` | No |
| `FINAGENT_LLM` | No | `fake` | `anthropic` | No |
| `ANTHROPIC_API_KEY` | Only for the real model | none | none | Yes |

## Model configuration

`config/model.yaml` holds the provider model, effort, token limit, timeout, prices and `rationale_source` (`llm` or `template`). The orchestration code contains none of these.

The configured model is `claude-haiku-5-5`, effort `low`, adaptive thinking. **The fake model is the default**, and its output is labelled `FAKE`. Routing is one call, one repair with the validator's errors, then a deterministic template. Prices in `config/model.yaml` are unset until checked, so cost is reported as `unpriced`.

## Integrations: real, mock or simulated

| Component | Status | External access | Limitations |
|---|---|---|---|
| `retrieve_finance_documents` | **REAL** (local BM25) | None | Lexical; weak on paraphrase |
| `get_invoice_document` | **MOCK** (fixtures) | None | Fault injection from `meta.json` |
| `get_vendor_record` | **MOCK** | None | Masked bank details, HMAC fingerprint |
| `get_purchase_order` | **MOCK** | None | FIN-004 simulates a timeout |
| `get_authority_record` | **MOCK** | None | Fixture register |
| `check_invoice_history` | **MOCK** (fixtures plus sandbox ledger) | None | Returns the vendor's records for matching |
| `place_payment_block` | **SIMULATED** | None | Restrict-only and needs no human approval by design. It cannot post, release or reject |
| `submit_finance_decision` | **SIMULATED** sandbox ledger | None | Needs a human approval and an idempotency key |
| Model | **FAKE** by default, **REAL** with `FINAGENT_LLM=anthropic` | Anthropic API | See "Unfinished work" |
| `finagent.db`, `erp_sandbox.db` | **REAL** local SQLite | None | Single host |

## Supported flows

| Case | Behaviour |
|---|---|
| FIN-001 valid match | Cites evidence, calculates totals, requests approval, posts once |
| FIN-002 duplicate | Blocks and recommends rejection; payment is never proposed |
| FIN-003 poisoned attachment | Text stays untrusted; escalates to Financial Crime and Controls |
| FIN-004 missing evidence | PO retried three times, then held; the gap is listed as an unknown |
| FIN-005 duplicate callback | One decision and a byte-identical replay response |

## Assumptions

- Every finance decision (POST, HOLD, ESCALATE, REJECT) needs a human. A restrict-only block protects the invoice while it waits.
- 18 of the 31 rules have an owner the corpus does not name. Each is labelled as an assumption in `spec/policy_rules.yaml`.
- Anything unmodelled fails safe to HOLD.
- References in code to the "decision log" mean a private working document that is not in this repository. [DESIGN.md](DESIGN.md) summarises its decisions.

## Known limitations

- **Retrieval does not change an outcome in the five cases.** Rules cite pinned policy sections, fetched by ID. BM25 supplies risk signals and untrusted evidence. Rule-source recall@5 is 27/31.
- **A block has no release path.** Any exception on an invoice blocks it until manual review, even if a later request caused it.
- **Any new history record for the vendor invalidates a pending POST**, even for an unrelated invoice. This is safe but conservative.
- A run attached to another run's request stays paused after that request is decided.
- Approver identity is self-asserted on the CLI.
- CLI commands use the run's date, so a delegation expiring between pause and approval is not caught. Audit events also carry the real time in `recorded_at`.
- Delegation scope and four FIN-POL-005 §3 fraud indicators are not modelled. Fraud wording is pattern-matched.
- Public holidays use a one-business-day margin, which Easter can exceed.
- `config/retrieval.yaml` records the settings but does not drive them.

## Unfinished work and trade-offs

- `<Owner: state whether the live model run was completed, and its result.>`
- Hardening took priority over an HTTP layer, a container image and CI, none of which the brief requires.

## Cost and cleanup

Offline use costs nothing. The live evaluation makes at most 4 model calls on two cases. Set a spend cap in the Anthropic Console. To clean up, delete `var/` and revoke any API key you created.
