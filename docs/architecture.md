# Architecture

Numbered markers are the six trust boundaries listed below the diagram.

```mermaid
flowchart TB
    Human["Human approver"] --> CLI["Typer CLI"]
    Request["Processing request (untrusted)"] -->|"1 validated schema"| CLI
    CLI --> Service["RunService"]
    Service --> Graph["Explicit state graph: 18 states, 40 steps, 20 tool calls"]

    subgraph Runtime["Agent runtime"]
        Graph --> Controls["Deterministic controls: 31 rules, Decimal, decision table"]
        Graph --> Runner["ToolRunner: schemas, deadlines, retries, audit"]
        Graph --> Router["Router: call, one repair, template"]
        Router -->|"3 validator"| Validator["Output validator"]
        Graph --> Approvals["Approval service"]
    end

    Router -->|"2 fenced, redacted prompt"| Model["Model: FAKE by default, Claude Haiku 5.5 opt-in"]

    subgraph Tools["Tools"]
        T1["retrieve_finance_documents: REAL"]
        T2["get_invoice_document: MOCK"]
        T3["get_vendor_record: MOCK"]
        T4["get_purchase_order: MOCK"]
        T5["get_authority_record: MOCK"]
        T6["check_invoice_history: MOCK"]
        T7["place_payment_block: SIMULATED"]
        T8["submit_finance_decision: SIMULATED"]
    end

    Runner --> T1 & T2 & T3 & T4 & T5 & T6
    Runner -->|"4 system failsafe, restrict-only"| T7
    Runner -->|"5 human approval required"| T8

    T1 --> Index["bm25s index, hash-locked registry, three trust lanes"]
    Corpus["Corpus and attachments (untrusted)"] -->|"1 registry decides trust"| Index
    T7 & T8 --> Sandbox[("erp_sandbox.db: ledger, blocks, idempotency")]
    Graph --> AppDB[("finagent.db: runs, checkpoints, audit, approvals")]
    Approvals --> AppDB
    Graph -->|"6 redacted logs and audit"| Logs["JSON logs on stderr"]
```

## Trust boundaries

1. **Input and documents.** Requests are schema-validated. Frontmatter confers no trust, and only the hash-locked registry does.
2. **Prompt.** Supplier text is capped, stripped and fenced in one `<data>` block. Untrusted documents appear as metadata only.
3. **Model output.** The validator rejects unknown citations, unbound numbers, forbidden claims, contradictions of the outcome, and omitted exceptions or unknowns.
4. **Block tool.** Only `SYSTEM_FAILSAFE` can call it. It can restrict payment and nothing else.
5. **Decision tool.** Deny by default. It needs an approved human record bound to the run, payload hash and action, plus an idempotency key.
6. **Persistence and logs.** Bank details are stored as HMAC fingerprints. Logs, audit, notes and raw model replies are redacted. Checkpoints hold no case-document text.

## Component and configuration manifest

| Component | Implementation | Configuration | Status |
|---|---|---|---|
| Model | `LLMClient`, `AnthropicLLM`, `FakeLLM` | `config/model.yaml`, `FINAGENT_LLM`, `ANTHROPIC_API_KEY` | Fake by default, real opt-in |
| Agent runtime | Framework-free state graph in `src/finagent/orchestration/` | `spec/graph.yaml` | Real |
| Budgets, timeouts, retries | `ToolRunner` | `config/tools.yaml` | Real |
| Controls | `src/finagent/controls/` | `spec/policy_rules.yaml`, `config/fraud_indicators.yaml` | Real |
| Document store and index | `bm25s` over 15 registered documents | `config/registry.yaml`, `corpus/` | Real, local |
| Persistence | SQLite (WAL) with versioned migrations | `FINAGENT_HOME` | Real, local |
| Finance system | Sandbox ledger and blocks | `spec/submit_permissions.yaml` | Simulated |
| Records | Fixture-backed tools with fault injection | `fixtures/cases/*/meta.json` | Mock |
| Output validation | `src/finagent/llm/validator.py` | `config/forbidden_claims.yaml` | Real |
| API surface | Typer CLI: `ingest`, `run start/get/approve/reject/resume`, `eval`, `eval list` | None | Real |
| Secrets | Environment variables only | `.env.example` | Example only |
| Environment | `uv.lock`, `.python-version`, `Makefile` | None | Local |
