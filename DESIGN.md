# Design

## Overview

finagent processes supplier invoices against a governed policy corpus. It gathers records, applies 31 coded rules, recommends one outcome, protects the invoice, and pauses for a human. Controls are deterministic. The model only explains a decision already made.

## Orchestration and framework

The run is an explicit 18-state graph (`spec/graph.yaml`) with a checkpoint after every node. LangGraph would provide checkpointing and interrupt/resume. It would not provide payload-bound approvals, idempotent submit or revalidation, which this code must enforce under any framework. For one pause point, the dependency adds more than it saves.

## Where agency sits

Conditional edges fetch a PO only when one is referenced and run the receipt-evidence query only when needed. Vendor, history and authority reads always run, because every control needs them and approvers need the full exception list. The model chooses no tools and cannot set an outcome, amount or approval.

## RAG design, results and limitations

`bm25s` indexes one chunk per policy section, in three lanes: authoritative, case evidence and non-authoritative. Queries are templated from typed fields. Rule sources are fetched by ID, so a ranking miss cannot lose a citation. Retrieval never satisfies a control: a receipt must be a typed record. As a result, BM25 supplies risk signals and untrusted evidence but changes no outcome in the five cases.

Rule-source recall@5 is **27/31 (87.1%)**. Stale-in-authoritative and non-authoritative-cited are both 0. Golden query G3 is a false abstention. Limitations: BM25 misses paraphrases, lexical traps such as AUD 75 collide, and sections are the citation unit because the corpus has no page numbers.

## Model choice and routing

`claude-haiku-5-5` (effort low, adaptive thinking) drafts a schema-constrained rationale. Routing is primary, one repair, then a deterministic template. A live run on FIN-001 and FIN-003 produced no forbidden claims or truncation; FIN-003 needed its one repair. Two cases are indicative only. The 97 scenario assertions pass with a fake model, labelled `FAKE`, and show control flow only. Workflow runs call Claude only with `FINAGENT_LLM=anthropic`.

## Trust boundaries and threat model

1. Frontmatter confers no trust; a hash-locked registry does.
2. Supplier text reaches the prompt only capped, stripped and fenced in one `<data>` block. Untrusted documents appear as metadata.
3. The validator rejects unknown citations, unbound numbers, forbidden claims, contradictions of the outcome, and omitted exceptions or unknowns.
4. Only `SYSTEM_FAILSAFE` can place a block, and a block cannot post, release or reject.
5. Every submit needs an approved human record bound to the run, payload hash and action.
6. Bank details are stored as HMAC fingerprints, and logs are redacted.

Approver identity is self-asserted on the CLI, so this is not a cryptographic boundary.

## Model and tool contracts

Tools have Pydantic input and output schemas, deadlines and bounded retries. Reads retry; writes run once. Budgets are 40 steps and 20 tool calls. One retrieval step spends one call however many templates it runs. If retrieval still fails, the run ends `FAILED`, because risk signals come from it.

## Persistence, idempotency and resume

SQLite (WAL) holds runs, checkpoints, audit and approvals, plus a sandbox ledger. Ledger, blocks and audit are append-only by trigger. Idempotency keys derive from run, authorisation and action. The ledger row and idempotency record commit together. Resume reuses recorded results before the pause and runs `REVALIDATE`, `PLACE_BLOCK` and `SUBMIT` live. An interrupted POST or REJECT with no committed row revalidates first. A per-run lock stops concurrent commands, and checkpoints omit case-document text.

## Failure handling

A failed read becomes a recorded unknown and the case holds. A write timeout enters `SUBMIT_UNKNOWN`, which looks for the committed row before retrying with the same key. A UNIQUE violation retires the approval and requires a new human decision. Unexpected errors end in `FAILED`.

## Interpretations of the brief

- "pause for human approval before posting or changing any financial decision" and "require approval": POST, HOLD, ESCALATE and REJECT all need a human (I1). The immediate protection is a restrict-only block.
- FIN-002 recommends rejection, and a human approves it (I2).
- FIN-004 never requests a POST; a HOLD request is allowed (I3).
- The fake model is the default and the live model is opt-in (I4).
- "Please stop after 8 hours": the README reports both active and elapsed time (I5).
- Every pause and terminal state after `EVALUATE` produces a typed final result (I6). A run failing earlier ends `FAILED` with its audit trail.

## Key decisions and alternatives rejected

| Decision | Choice | Alternatives | Why I rejected it |
|---|---|---|---|
| Orchestration | Explicit state graph | LangGraph, ADK, Strands, Pydantic AI | |
| Model role | Explains only | Model decides or plans | |
| Retrieval | BM25 with lanes | Dense, hybrid | |
| Rules | Coded, with pinned sources | Derived from retrieved text | |
| Persistence | SQLite | Postgres, files | |
| Protection | Block now, decide later | Unapproved protective submits | |
| Interface | CLI over `RunService` | HTTP | |

## Key interpretations and assumptions

18 of the 31 rules have an owner marked as an assumption, because the corpus does not name one. Each is labelled in [spec/policy_rules.yaml](spec/policy_rules.yaml), with its source and basis. Anything unmodelled fails safe to HOLD.

Declared gaps:

- A block has no release path, and any new vendor history invalidates a pending POST. Both are safe but conservative.
- Delegation scope is not built.
- Four FIN-POL-005 §3 fraud indicators are not modelled, and fraud wording is pattern-matched.
- Public holidays use a one-business-day margin, which Easter can exceed.
- CLI commands keep the run's `as_of`, so a date passing between pause and approval is not detected.

## Production view

| Seam in this build | Production |
|---|---|
| Inline execution in `RunService` | Durable workflow engine, one activity per node |
| SQLite repositories | Postgres with row-level security, PITR and multi-AZ |
| CLI | API adapter with idempotent approval callbacks |
| Mock tools | ERP, vendor-master and authority APIs with service identities |
| `bm25s` | Hybrid retrieval with a reranker, same evaluation method |
| Sandbox ledger and blocks | ERP posting and payment-block APIs through an outbox, with reconciliation |
| `LLMClient` and router | Approved AU-region endpoint with no-training terms |
| Approval records | SSO, MFA, signed records, expiry, dual approval |
| Settings and `Clock` | Secrets manager, real clock, per-entity calendars |
| JSON logs | Traces, metrics and SIEM events |

Production also needs AU data residency, 7-year WORM retention (FIN-POL-010), tested restores, shadow mode and a global kill switch.
