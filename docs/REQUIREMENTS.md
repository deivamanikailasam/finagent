# Requirements traceability (R01–R90)

Every requirement in the brief.

| ID | Brief item (short quote) |
|---|---|
| R01 | Python or TypeScript |
| R02 | Local, AWS or Google Cloud environment |
| R03 | Source repository, setup artefacts and a short design note |
| R04 | Small, production-minded, not a feature-rich demo |
| R05 | Retrieve, reconcile, identify exceptions, recommend an outcome |
| R06 | Pause for human approval before posting or changing any financial decision |
| R07 | Invoices, POs, goods receipts, vendor master, policy as sources |
| R08 | Retrieval can be incomplete, contradictory, stale or malicious |
| R09 | Ground in cited sources; identify what it does not know |
| R10 | Step 1: accept request (case ID, invoice ref, vendor, amount, currency, notes/attachments) |
| R11 | Step 2: use RAG; call other tools only when needed |
| R12 | Step 3: detect mismatches, duplicates and missing approvals |
| R13 | Step 4: cited evidence, calculations, assumptions, confidence, exceptions, next action |
| R14 | Step 5: create an approval request and stop until approval or rejection |
| R15 | Step 6: persist run state and audit events to explain, reproduce and resume |
| R16 | HTTP endpoints or equivalent CLI commands |
| R17 | Start run until completion, failure or approval required |
| R18 | Get run: status, current state, result, audit events |
| R19 | Approve/reject resumes the same run safely |
| R20 | Run test cases and report pass/fail |
| R21 | Actual API calls, mocks or a mixture |
| R22 | Tool inputs and outputs use explicit schemas |
| R23 | README states which integrations are real or simulated |
| R24 | retrieve: document ID, type, version/page, relevance, citation metadata |
| R25 | Include at least one adversarial or irrelevant document |
| R26 | vendor: status, payment details, risk flags, last updated |
| R27 | PO: lines, totals, currency, tolerances, approval status, goods receipts |
| R28 | history: references or fingerprints with stable IDs and status |
| R29 | submit: require approval, validated arguments, idempotency key |
| R30 | Added tools: purpose, bounded permissions, timeout, observable result |
| R31 | Real LLM or abstraction; configuration outside orchestration code |
| R32 | RAG: ingestion, chunking/indexing, retrieval, citations; limitations explained |
| R33 | Bounded loop or explicit state graph with step/tool budget |
| R34 | Invalid outputs retried, repaired or failed explicitly |
| R35 | Arithmetic and reconciliation deterministic in code |
| R36 | Log run, retrieval and tool events: timestamp, run ID, outcome, duration |
| R37 | Do not log credentials, bank details or unnecessary financial data |
| R38 | Handle tool timeout |
| R39 | Handle transient tool failure |
| R40 | Handle malformed model output |
| R41 | Handle duplicate approval request |
| R42 | Handle application restart/resume |
| R43 | Treat retrieved documents and case text as untrusted |
| R44 | Consequential tools deny-by-default, approval-gated, idempotent |
| R45 | Typed result: sourced facts, calculations, inferences, unknowns, findings, actions |
| R46 | Include fixtures; values may change if intent preserved |
| R47 | FIN-001: request approval, then submit exactly once |
| R48 | FIN-002: hold or recommend rejection; do not propose payment |
| R49 | FIN-003: untrusted evidence; do not bypass policy or approval |
| R50 | FIN-004: bounded retry or fail gracefully; expose missing evidence; no approval for payment |
| R51 | FIN-005: one effective decision; stable replay-safe response |
| R52 | Choose the environment that best demonstrates the approach |
| R53 | Reviewer can configure and run it from the instructions |
| R54 | Exact prerequisites, env vars, model config, ingestion, start, test/eval commands |
| R55 | Diagram + manifest: model, runtime, store/index, persistence, tools, API surface, trust boundaries |
| R56 | Reproducible scripts, containers or equivalent configuration |
| R57 | Cloud IaC where practical |
| R58 | Cost/cleanup notes; example configuration only |
| R59 | Label real integrations, mocks and anything needing external access |
| R60 | Framework: what it provides vs what code enforces |
| R61 | Runnable code and complete instructions to provision, ingest, run tests |
| R62 | README: environment, commands, model/API requirements, assumptions, flows, limitations |
| R63 | Design note ~1–2 pages covering the listed topics |
| R64 | Architecture diagram plus configuration |
| R65 | Tests for orchestration, retrieval grounding, safety; stable separate from model-dependent |
| R66 | Sample output: one successful flow, one exception/approval flow |
| R67 | No API keys, credentials, personal data or proprietary code |
| R68 | Pin or lock dependencies |
| R69 | Example environment file only if needed |
| R70 | Declare how AI coding tools were used |
| R71 | Stop after 8 hours |
| R72 | Document unfinished work or deliberate trade-offs |
| R73 | Completeness less important than sound prioritisation |
| R74 | No UI required |
| R75 | No particular framework, cloud, vector store or provider required |
| R76 | Mocks acceptable when contracts and limitations are clear |
| R77 | Simple local persistence that demonstrates restart/resume and idempotency |
| R78 | No tool capable of moving real money |
| R79 | Assessed: clear states, bounded decisions, grounded retrieval, model/tool boundaries |
| R80 | Assessed: typed contracts, persistence, idempotency, retries, timeouts, recoverable failures |
| R81 | Assessed: least-authority tools, injection resistance, approval gates, safe logging |
| R82 | Assessed: grounded citations, sensible retrieval, adversarial cases, quality measures |
| R83 | Assessed: readable code, sensible abstractions, straightforward setup |
| R84 | Extensions: at most one or two, only after the required workflow is solid |
| R85 | Optional work doesn't compensate for missing controls |
| R86 | Extension list (parallel tools, cost budgets, OTel, second model, property/fault tests) |
| R87 | Include approximate time spent |
| R88 | Declare code, libraries or generated material not authored |
| R89 | Be able to explain, modify and defend every decision |
| R90 | Explain operation at enterprise scale |