# Review log

What was reviewed and fixed in each phase, and why.

**Who did what.** Claude Code generated the code, specs and tests. Three independent AI reviewers (Claude subagents) reviewed Phase 1 and WP 2.1, and Claude reproduced or checked their findings against the plan, decision log and corpus. The project owner made the decisions listed below and directed the work. Space is left in each phase for the owner to record their own line-by-line checks.

## Phase 1: Foundation (WP 1.1, 1.2)

**Commits:** `1bd0216` (scaffold), `cff8023` (settings, clock, logging, fingerprints, redaction).

### Found and fixed during the build

| Problem | Fix | Why it mattered |
|---|---|---|
| The gitleaks allowlist as specified skipped everything in `tests/` and `fixtures/`, and allowed 64-character hex strings everywhere | Scoped it to the `generic-api-key` rule, with path and pattern both required | A real token in a test file would have gone undetected. Checked with planted fake secrets |
| Redaction missed `Acc 12345678`, `account number is ...`, `BSB is ...` and an account number right after a BSB | Added these formats and regression tests | Bank details could have reached logs or prompts |

### Found by the independent review and fixed

| Commit | Problem | Fix | Why it mattered |
|---|---|---|---|
| `86243c9` | A logging format error printed the raw value to stderr | Fixed "record dropped" line | Account numbers could leak through the error path |
| `97bc641` | Unusual payloads crashed the logger | Catch the error and continue | A logging fault must not change a run's outcome |
| `9a51964` | Free-text fields bypassed minimum-data logging | Allow-list of IDs, counts, status, hashes and amount | Decision log XIII, FIN-POL-010 s2 |
| `025029f` | Common bank, identity and credential formats passed unmasked, and redaction was not idempotent | Normalise look-alike characters, widen patterns, mask keys and nested values | Decision log XIII |
| `ce6f304` | Different account values could share a fingerprint, and an empty key was accepted | Reject these inputs | A false match could pass a bank-detail check |

### Owner's own checks

_To be completed by the owner._

## Phase 2: Specifications and fixtures (WP 2.1, 2.2, 2.3)

### WP 2.1: Executable specs

**Commit:** `8ee5e0b`.

Issues caught while drafting:

- Shrinking a typed receipt without changing the goods-receipt note would have tripped TYPED-RECORD-PRECEDENCE and turned the APPROVE vectors into HOLD. The vectors now override both.
- The plan's "committed row ⇒ COMPLETE" would have let a timed-out block end a run with no human approval. Reconciliation now returns to `PLACE_BLOCK`.

### Independent review of the specs

The reviewers traced 44 of 44 vectors through all 31 rules using their own evaluator, recomputed all 15 registry hashes and walked every approval, block and failure path. All 44 agreed once freight was exempted from the quantity rules.

| Commit | Problem | Fix | Why it mattered |
|---|---|---|---|
| `030b544` | An overstated invoice gross could be approved | Gross must equal lines plus tax | FIN-POL-002 s1 document-total match |
| `ecf120a` | An AUD invoice on a USD PO could be approved | Hold any invoice/PO currency mismatch | FIN-POL-009 s1 |
| `99f8d10` | Fraud escalation could go to the wrong owner, Financial Control could count as the posting role, and a legal-name mismatch alone could post | Tightened the three rules | FIN-POL-007 s3, decision log V, FIN-POL-001 s1 |
| `b79c6a8` | Freight failed the quantity rules as written, and the 0.61% vector used inconsistent PO data | Exempted freight, used a different PO | Decision log III: freight needs no receipt |
| `e4378aa` | A new run could post an invoice another run had blocked or an approver had rejected | Blocks apply per invoice, from any run | Plan D10: no path from a blocked invoice to a posting |
| `d2b4ed7` | After a UNIQUE violation the old approved POST stayed active | Retire it, hold, require a new approval | Plan section 3 |
| `12e8cc4` | A denied submit had no exit, and a run could be FAILED with a committed row | Added the edge, reconcile before the budget check | FAILED must mean no row exists |
| `e84fd8e` | Removing key safety rules from the specs still passed the integrity tests | Pinned those values | Protects the signed-off specs from silent change |

### WP 2.2: Domain models, tool schemas, LLM interface

**Commit:** `8695cfa`.

- `invoice` and `vendor` are optional on `Case`, so a failed read becomes an unknown and a HOLD (plan section 3). The prompt had made them required, which left that path with no safe result.
- A test checks every vector override path against the real schemas, so fixtures cannot drift from the spec.

### WP 2.3: Fixtures and scenario expectations

**Commit:** `d017a81`.

- All 46 vectors build on the real fixtures, and the error vector raises its reason code.
- FIN-002's PO quantities show as already invoiced, so the fixture agrees with its paid history. REJECT_DUPLICATE still outranks the resulting quantity findings.
- FIN-005 has its own invoice and PO, so a shared sandbox cannot turn it into a duplicate of FIN-001.
- Claude chose the approvers, the FIN-002 history date and the citation entries where the plan was silent. These still need checking against plan section 8 and the reference key.

### Owner decisions

| Decision | Choice | Reason |
|---|---|---|
| Block timeout with a committed row | Return to `PLACE_BLOCK` | Never end a run without a human decision |
| APPROVE on a blocked run | Refuse POST, hold for manual review | A block has no release path |
| Block `rule_id` after a UNIQUE violation | `UNIQUE_VIOLATION` | The audit shows exactly why the block was placed |
| Blocks across runs | Bar posting on any blocked invoice | Closes the new-run bypass |
| Vector count | 46 instead of 44 | Adds gross and currency coverage without removing any |
| `invoice` and `vendor` on `Case` | Optional | Failed reads fail safe |
| `DocType` values, block `rule_id` field, `evidence.documents`, float relevance score | Accepted | The prompt left these open, and none changes a control outcome |

### Owner's own checks

_To be completed by the owner, including the mechanical diff against the reference key._

## Phase 3: RAG pipeline (WP 3.1, 3.2, 3.3)

**Commits:** `a805ed4` (ingestion, chunker, tokenizer, index), `2839fed` (retrieval tool and templates), `9cf7e0a` (golden labels). The evaluation code and results are awaiting review.

### Found and fixed during the build

| Problem | Fix | Why it mattered |
|---|---|---|
| A hostile heading such as `## 01. x` would have crashed ingestion | The chunker accepts only section numbers the hash can locate; anything else becomes one whole chunk | An untrusted file must not be able to stop the pipeline |
| `fetch_by_id` as specified had no role, so it would have bypassed the restricted-document check | It now takes a role and applies the same permissions as search | FIN-POL-004 is restricted (FIN-POL-010 s3) |

### Owner decisions

| Decision | Choice | Reason |
|---|---|---|
| Golden set size | 5 queries, per the plan | The log said 12 and the guide said 8; the plan records the deliberate cut |
| Golden labels | Accepted as proposed by Claude | Proposed from the policy text without running retrieval, and committed before measurement |

### First evaluation run (before the review fixes)

- **Rule-source recall@5: 26/31 (83.9%).** Misses: REQ-AMOUNT-POSITIVE, REQ-MATCHES-INVOICE, PO-REQUIRED, SERVICE-CONFIRMED and NEGATIVE-LINE.
- **Golden set:** G1 and G2 hit at rank 1, and G5 correctly abstained. G3 and G4 were false abstentions from the coverage rule, although FIN-POL-004#s2 ranked 2nd for G3 and FIN-POL-002#s2 ranked 1st for G4.
- **Invariants:** stale in authoritative 0, non-authoritative cited 0.
- For G3, the injection document ranked above the target on the unfiltered ranking. It stayed out of the authoritative lane.

### Evaluation after the review fixes

The fixes changed scoring scope, tokenisation and tie-breaking for security and correctness. Templates, the 0.5 threshold and the golden labels did not change. This second run is disclosed in `docs/EVAL_RESULTS.md`.

- **Rule-source recall@5: 27/31 (87.1%).** With every pinned section required: 26/31 (83.9%). Misses: REQ-AMOUNT-POSITIVE, REQ-MATCHES-INVOICE, PO-REQUIRED and NEGATIVE-LINE.
- **Golden set:** G1, G2 and G4 hit at rank 1, and G5 correctly abstained. G3 is still a false abstention: FIN-POL-004#s2 is retrieved at rank 4, then suppressed by the coverage rule.
- **Invariants:** stale in authoritative 0, non-authoritative cited 0, now also checked on both sides of the FIN-POL-003 effective date.

### Owner's own checks

_To be completed by the owner._

## Independent review of WP 2.2 to 3.3

Three independent AI reviewers (Claude subagents) reviewed the models and fixtures, ingestion and index, and retrieval and evaluation. Claude reproduced the main findings by running the code, then fixed them. Each fix has a test in `tests/unit/test_review_regressions.py`. The fixes are in commits `6a449f8`, `c3fa1a5`, `0f0609f`, `5e965e7` and `9faf2d4`.

**42 findings: 34 fixed, 4 partly fixed, 3 decided by the owner, 1 scheduled for WP 8.1.**

### Ingestion and index (WP 3.1)

| ID | Severity | Problem | Status and fix |
|---|---|---|---|
| ING-B1 | Blocker | One malformed or unreadable corpus file could crash or stall ingestion | Fixed: recorded as a finding, containers are never stringified, heading numbers are bounded |
| ING-B2 | Blocker | `verify_index` was never called, and the manifest omitted version, dates, ACL and the registry hash | Fixed: all trust inputs are in the manifest, a damaged corpus cannot become the baseline, and retrieval verifies on every load |
| ING-M1 | Major | IDs were split or collided, such as `INV-1001A` with `INV-1001` | Fixed |
| ING-M2 | Major | Numbers were mis-tokenised, such as `.5%`, `AUD10,000` and non-ASCII digits | Fixed |
| ING-M3 | Major | The index returned masked chunks at score 0 | Fixed: only positive scores are returned |
| ING-M4 | Major | Unregistered files could suppress the authoritative lane through shared IDF | Fixed: statistics are per lane and per visible set |
| ING-M5 | Major | An edited file kept its approved version and citation | Partly fixed: the version becomes `modified:<hash>` and trust is removed. Phases 5 and 7 must only cite AUTHORITATIVE sections |
| ING-m1 | Minor | A file name could forge a row in the trust report | Fixed |
| ING-m2 | Minor | Text before the first section was dropped | Fixed: the file becomes one whole chunk |
| ING-m3 | Minor | Tied scores at the cutoff were non-deterministic | Fixed |
| ING-m4 | Minor | Empty-token crash and double-counted repeated terms | Fixed |
| ING-m5 | Minor | Tests enshrined defects or missed boundaries | Fixed |

### Retrieval and evaluation (WP 3.2, 3.3)

| ID | Severity | Problem | Status and fix |
|---|---|---|---|
| RET-B1 | Blocker | Hidden documents and other cases affected abstention through shared statistics | Fixed: one index per lane and visible set. This replaces the guide's `weight_mask` approach because a mask cannot hide statistics |
| RET-B2 | Blocker | `fetch_by_id` ignored effective dates | Fixed: `as_of` is required, and out-of-date policies are not returned or offered |
| RET-B3 | Blocker | Role and case access failed open | Partly fixed: role is required, unknown roles are denied, and case documents have an ACL. WP 4.B must inject `case_id` from the run context |
| RET-M1 | Major | Case citations collided across sections and differed from `<DOC>#<sN>` | Partly fixed: multi-section citations are unique and fetchable. `CASE:<ref>` stays because the signed-off expectations require it |
| RET-M2 | Major | The stale invariant could not detect date errors | Fixed: checked per item, including around 2026-08-01 |
| RET-M3 | Major | Comparing coverage with the top chunk is an unrecorded interpretation | Decided: keep the top-chunk reading and record it. Changing it after measurement would be tuning, so G3 stays a reported false abstention |
| RET-M4 | Major | `fetch_by_id` calls were not logged | Fixed |
| RET-M5 | Major | The ACL-before-scoring test was vacuous | Fixed: results must equal an index built without the hidden document |
| RET-m1 | Minor | Headline recall ignored additional pinned sections | Fixed: both figures are reported |
| RET-m2 | Minor | The report omitted the "Not evaluated" list and golden misses | Fixed |
| RET-m3 | Minor | The evaluation depended on the working directory | Fixed: the root is passed explicitly |
| RET-m4 | Minor | Audit events vanish until logging is configured | Scheduled: wire `configure_logging` at startup in WP 8.1 |
| RET-m5 | Minor | The template safety test was narrow | Fixed: every supplier-controlled string is poisoned |

### Models and fixtures (WP 2.2, 2.3)

| ID | Severity | Problem | Status and fix |
|---|---|---|---|
| MOD-B1 | Blocker | Submit and block payloads were unvalidated and could carry bank details | Fixed: typed payloads, and the hash must match |
| MOD-B2 | Blocker | The planned ledger cannot supply history fields, which breaks sticky outcomes and cumulative quantities | Decided: add prior outcome, hold category, invoice date, PO reference and per-line quantities to the WP 4.B ledger design before the WP 4.A tests. ESCALATE maps to HELD with its prior outcome |
| MOD-M1 | Major | Money was not canonical or ASCII-only | Fixed |
| MOD-M2 | Major | Decimals did not survive JSON | Fixed |
| MOD-M3 | Major | Frozen models could be mutated or bypassed | Partly fixed: tolerances are immutable and instances are revalidated. `Calculation.inputs` and retrieval `lanes` are still plain dicts |
| MOD-M4 | Major | The builder claimed a DFA source before it took effect | Fixed |
| MOD-M5 | Major | FIN-001 and FIN-005 must cite FIN-POL-001 s3, but no rule pins it | Decided: the WP 5.3 decision table always cites FIN-POL-001 s3 for the outcome, so the 31 rules stay unchanged |
| MOD-M6 | Major | The vector test could pass vacuously | Fixed: every override is checked in the built case, and expectations are validated |
| MOD-m1 | Minor | The last tax code won on a shared PO line | Fixed: the highest rate is used |
| MOD-m2 | Minor | Explicit values in lists or odd indexes were overwritten | Fixed |
| MOD-m3 | Minor | FIN-004 silently discarded PO overrides | Fixed: now an error |
| MOD-m4 | Minor | Bad overrides raised undocumented errors | Fixed |
| MOD-m5 | Minor | A comment wrongly said FIN-005 cannot be a duplicate | Fixed |
| MOD-m6 | Minor | `risk_flags` was free text | Fixed: closed list |
| MOD-m7 | Minor | Weak bounds on attachments, blanks, quantities, scores and delegations | Fixed |
| MOD-m8 | Minor | A HOLD with no category or an APPROVE with one was valid | Fixed |
| MOD-m9 | Minor | No `response_id`, and an empty rationale parsed as valid | Fixed |

## Phase 5: Controls (WP 5.1 and 5.2 committed, WP 5.3 awaiting commit)

Claude recommended these interpretations where the prompt was silent, and the owner asked for the recommended choice.

| Choice | Decision | Why |
|---|---|---|
| Invoice lines sharing a PO line | Add them together for quantity checks | Two lines of 40 must not both pass against 40 received |
| Freight undercharge | Fails beyond AUD 75 | FIN-POL-002 s2 says freight may "vary" by up to that amount |
| Quantity or confirmation failure | Reported by its own rule, with no extra price failure | Keeps exceptions and confidence accurate |
| Bill-to entity | Northstar Group Pty Ltd only | The corpus has no entity list, so anything else fails safe |

WP 5.2 choices, also recommended by Claude and accepted when the owner asked for the best choice:

| Choice | Decision | Why |
|---|---|---|
| Prior escalation on the same invoice | Escalates again through `PRIOR-ESCALATION` | Resubmitting must not close a control review |
| Overseas remit-to country | High risk even without a vendor flag | FIN-POL-009 s4 |
| Register limit above the DFA matrix | Capped at the matrix limit | The register cannot widen authority |
| Probable-duplicate amount band | Same currency only | A percentage across currencies has no meaning; number and attachment matches still flag |
| Segregation on non-AUD invoices | Reported as unknown | The AUD amount is unknown, and FX-HOLD holds the case |

Found during WP 5.2: `HIGH-RISK` reported PASS when the invoice was unavailable. It now reports UNKNOWN.

WP 5.3 choices, also recommended by Claude and accepted when the owner asked for the best choice:

| Choice | Decision | Why |
|---|---|---|
| Source unavailable and no rule failed | HOLD, with the unknown checks as exceptions | Missing evidence must never approve a case (FIN-POL-001 s5) |
| Prior BANK_CHANGE or VENDOR_BLOCK hold | Stays in force through `PRIOR-HOLD` | Resubmitting must not clear a vendor or bank control |
| Supplier claims new bank details | Adds a Vendor Governance task to the next action | Decision log X; the claim never changes bank details |

All 46 policy vectors passed through the real decision engine on the first full run. The guide's line-by-line review of `test_decision.py` against decision log IV is the owner's to record below.

### Owner's own checks

_To be completed by the owner._

## Phase 6: Persistence, orchestration, approvals (committed through `8c22074`)

Claude wrote 192 tests-first acceptance tests. They load but have not been run, because the code they test arrives in WP 6.B. Claude recommended these decisions, and the owner asked for the best choice:

| Choice | Decision | Why |
|---|---|---|
| Recent-modifier window, delegation scope, delegation-midnight test | Left out and declared | Plan section 14 item 8 and the WP 6.A prompt. **Known gap:** a non-bank vendor change by the approver is not detected |
| Second run on an invoice with a pending request | Attaches to the same request | Decision log V; the invoice still gets at most one POST |
| WP 6.B interfaces | Fixed by the WP 6.A support files | The implementation must pass the tests without editing them |

Claude fixed three defects in its own drafts before handover: a shared `conftest.py` that would have broken the WP 4.A tests, a wrong ledger count, and a dependency on a private attribute.

WP 6.B1 (repositories and approval service) passes all 49 approval acceptance tests without editing them. It is committed at `ab80439`. Claude recommended these choices, and the owner asked for the best choice:

| Choice | Decision | Why |
|---|---|---|
| Authority register or vendor record unreadable | Nobody can approve a POST or REJECT | Errors fail safe |
| Facts not captured at request time | Revalidation invalidates the approval | A change cannot be ruled out |
| Repeat decision on an invalidated approval | Returns `ALREADY_DECIDED` | An old "approved" response must not be replayed |
| Repeated ineligible attempts | One escalation per approver and reason, every attempt audited | Callbacks cannot flood Financial Control |

WP 6.B2 (state graph, nodes, runner and `FinalResult`) passes the graph and invariant acceptance tests without editing them. It is committed at `6f0455c`. Claude recommended these choices, and the owner asked for the best choice:

| Choice | Decision | Why |
|---|---|---|
| Run attached to another run's approval | Stays paused and never submits | Only the owning run may act on an approval. **Known limitation:** the attached run does not finish by itself |
| Model rationale before WP 7.2 | Model is called, template text is reported | Unvalidated model text must not reach `FinalResult` |
| Rule source cannot be fetched | Run ends `FAILED` with nothing written | Errors fail safe |
| Authority read | Done in `RECONCILE` | The signed-off graph has no authority node |

Claude's own check of WP 6.B2 found and fixed three defects, each now covered by `tests/unit/test_orchestration_failsafe.py`:

- A block confirmed by reconciliation was missing from `actions_taken`.
- A denied rule citation was silently skipped.
- The block authorisation ID was not checkpointed before the write, as decision log VI requires.

WP 6.B3 (checkpoint and resume) passes the resume acceptance tests without editing them and is committed at `8c22074`. Claude also ran a one-off crash sweep: all 5 cases, stopped after each node's work and before its checkpoint, 66 crash points, with no difference from an uninterrupted run. The sweep does not cover a kill in the middle of a database write.

### Claude's trace of FIN-001 and FIN-004 (at the owner's request, not the owner's sign-off)

Claude ran both cases at `8c22074` and compared them with `spec/graph.yaml`, `tests/scenarios/expectations.yaml` and the database rows.

| Check | FIN-001 | FIN-004 |
|---|---|---|
| Path | 13 steps, every edge declared | 13 steps, every edge declared |
| Outcome and owner | APPROVE_FOR_POSTING, AP Operations | HOLD_FOR_INFORMATION, MISSING_RECEIPT, AP Operations |
| Rows before the decision | No block, no ledger row | 1 block, no ledger row |
| Rows after the decision | 1 POST | 1 block, 1 HOLD |
| Revalidation | Ran | Skipped, correct for HOLD |
| Unknowns | None | `get_purchase_order`, after 3 timeouts |
| Receipt query | Not needed | Abstained and audited |
| Required and forbidden citations | Pass | Pass |
| Model text in `FinalResult` | None | None |
| Assumptions | Failed at first (0 of 1), now pass | Failed at first (0 of 2), now pass |

The owner asked Claude to fix both findings. After the fix, all five cases report exactly the assumptions in the signed-off expectations, and the 66-point crash sweep still shows no differences.

- **TRACE-1 (defect, fixed):** `FinalResult.assumptions` was empty in both cases. `evaluate_case` only recorded failing rules whose owner basis is `assumption`, so it missed the AP Operations owner on an approval and the unknown-receipter fallback in FIN-004. The decision table now states how the owner was chosen. `test_final_result_assumptions.py` checks the exact contents, which `test_invariants.py` did not.
- **TRACE-2 (wording, fixed):** FIN-004's next action said to ask "the designated receipter", although the PO was unavailable. With no PO it now says "AP Operations identifies the receipter and requests confirmation".
- **TRACE-3 (wording, open):** other next actions can still name a person when the owner has fallen back to AP Operations. For example, MISSING_PO says "Requester supplies an approved PO".
- **Corpus report:** both runs log a `unlinked_risk_text` security event for ADV-001. It is treated as non-authoritative and did not change either outcome.

### Owner's own checks

_To be completed by the owner, including the guide's review of the crash-test row counts and the plan's WP 6.B2 trace of FIN-001 and FIN-004._

## Independent review of the full build (2026-11-23)

At the owner's request, three independent Claude reviewers checked requirements and submission, finance controls, and AI, retrieval and security. They are AI reviewers, not the owner. They did not read the brief PDF, so `docs/REQUIREMENTS.md` was the record of the brief. Requirements traced at review time: 63 met, 21 partly met, 6 missing, mostly the unfinished README, architecture and AI-usage documents.

| Area | Defect found | Fix |
|---|---|---|
| Money | Line totals were never checked, so a 10,450.00 invoice could post against a 9,900.00 PO | Line total must equal quantity x price |
| Money | Split variances, uncapped freight, quantity above the PO and future-dated receipts could post | Each now holds |
| Money | Two invoices could together exceed the received quantity | Rechecked inside the POST transaction |
| Approval | Limits ignored the PO commitment | Limit and 25k check use the commitment |
| Approval | Limit cuts, risk flags, PO cancellation and remit changes after approval were missed | Revalidation covers them and re-runs eligibility |
| Approval | A recent vendor modifier or service confirmer could approve, and a conflicted approver could dismiss an escalation | Conflict checks extended |
| Approval | A stricter run could be stranded behind an undecided request | The stricter request replaces it |
| Workflow | Concurrent approvals could produce POST plus REJECT | Per-run lock. 80 concurrent approvals produced one POST |
| Workflow | A crash at SUBMIT skipped revalidation, and a stalled write could commit late | Revalidates first. Late writes roll back |
| Workflow | A refused command could drive the run | Only an applied decision resumes it |
| Evidence | Approval rows could be edited, the wrong limit was stored, and audit detail was thin | Triggers, applied limit, full audit fields |
| Model | A rationale contradicting the outcome passed validation | Outcome, exception and unknown checks |
| Model | Number and forbidden-claim checks could be bypassed, and true wording was over-blocked | Both reworked |
| Model | A supplier reference could forge a prompt header, and an untrusted attachment could be cited | Fenced IDs, trusted citations only |
| Privacy | Checkpoints stored raw supplier text, and redaction missed common formats | Text is read from source; patterns added |
| Honesty | Fake output was labelled `LLM`, and live gates could read clean after a provider failure | `FAKE` label; `not measured` |
| Setup | `make ingest` and `make eval` failed from a clean clone, three settings did nothing, and the real model could not run a workflow | Fixed; `FINAGENT_LLM=anthropic` |

### Owner's own checks

_To be completed by the owner. Decision note: `drafts/decision_log_XXVI.md`._

## Second independent review, against the brief (2026-11-23)

The owner supplied the brief's text in the chat, and three Claude reviewers checked the plans and build against it. They are AI reviewers, not the owner. Traceability at review time: 40 of 64 requirements met, 15 partly met and 9 missing, mostly undelivered documents.

| Finding | Result |
|---|---|
| README, architecture, AI usage and samples missing | Drafted; owner fields remain in the README and AI usage |
| Retrieval bypassed its tool contract, and its events had no run ID | Fixed |
| Audit timestamps showed only the case date | `recorded_at` added |
| Raw notes and rejection reasons were stored | Redacted |
| One transient read could block an invoice | Approval-path reads now retry |
| Two re-evaluations could exhaust the budget | Fixed |
| The validator rejected ordinary faithful wording, and repair lacked detail | Fixed |
| The real model had no documented workflow path | `make run-live` added |
| `eval list` did not run the cases | `eval list --run` added |
| A random all-digit ID could fail the bank-masking scenario | Fixed, then 125 case runs with no failure |
| An unrelated invoice invalidates a pending POST | **Not changed.** The approved test `test_revalidation_change_or_read_failure_invalidates[add_history]` requires it |
| A block has no release path, and an attached run stays paused | Declared; a fix needs a spec change |
| Retrieval changes no outcome in the five cases | Declared in `DESIGN.md` and the README |

### Owner's own checks

_To be completed by the owner. Decision note: `drafts/decision_log_XXVII.md`._

## Verification status

- Latest, with the second-review fixes on top of `74be6b6` (uncommitted): 1,537 offline tests and 5 scenarios (97 assertions) pass, lint is clean, and the 66-point crash sweep shows no differences. The live model has not been called. The entries below are earlier snapshots.
- 564 unit tests pass and lint is clean at `9faf2d4`.
- The 46 expected vector outcomes have not been run against the real engine, which arrives in Phase 5. The two added vectors were not part of the reviewers' 44-vector trace, although the models reviewer's throwaway evaluator covered all 46.
- The rules for blocked invoices, UNIQUE violations and reconciliation exist in the specs only until WP 4.B and 6.B enforce them.

## Open items

- WP 7.3 (live smoke) has not been run, and `docs/prep/RESEARCH_NOTES.md` and the model price are still blank. These are owner steps.
- REV-1 (owner decision): CLI decisions use the run's date, so a delegation expiring between pause and approval is not caught. A real clock would need fixture dates or a demo mode.
- REV-2: a write's audit row can be lost if the application database is busy after commit. A durable outbox is a production change.
- REV-3: Easter can exceed the one-day holiday margin, and fraud wording is pattern-matched, so paraphrases can escape it.
- REV-4 (owner): choose one reading of the 8-hour limit. `docs/TIME_LOG.md` has no preparation rows, and the guide's README line "within 8 h" may be false.
- REV-5 (owner): the AI-usage statement must separate what the owner reviewed from what AI reviewers checked. No commit has review trailers.
- REV-6: the final commit must name `DESIGN.md` and the deliverables in `docs/` explicitly, and decide whether `review.md` and `CLAUDE.md` ship, because they name private `_inputs/` files.
- CLI-1: all five fixture cases use invoice V-100 / INV-1001, so they must run in separate `FINAGENT_HOME` workspaces. In a shared workspace, earlier rows correctly change later outcomes.
- TRACE-3: decide the next-action wording when a requester or receipter owner falls back to AP Operations (see the Phase 6 trace).
- MOD-B2: build the decided history fields and per-line quantities into WP 4.A and 4.B.
- MOD-M5: make the WP 5.3 decision table cite FIN-POL-001 s3.
- RET-m4: wire logging at startup in WP 8.1.
- Finish the partly fixed items ING-M5, RET-B3, RET-M1 and MOD-M3 in the work packages named above.
- The remaining retrieval misses and the G3 false abstention are reported as measured.

- Owner tie-break when rules in the winning category have different owners (fix before Phase 4).
- A submit retry skips revalidation (fix before Phase 4).
- Some over-masking remains, such as `qty 100 200 300`.
- Tool declarations still accept unsafe values, such as an infinite timeout.
- The guide's 4.A and 6.A prompts need tests for the new block rules, and the plan and guide still say 44 vectors.
- Four FIN-POL-005 s3 fraud indicators are not modelled. This is declared in plan section 14 and must appear in the README.

## Process incidents

- During WP 1.1, a repo-wide gitleaks scan opened `.env` and, almost certainly, `_inputs/`, which the project rules forbid. Its report was redacted, showed only a file name and count, and was deleted.
- During WP 1.1, a search over `_inputs/*.md` included the reference key file. It returned no lines from that file.
