# AI usage and my manual work

This document separates what AI produced from what I did, phase by phase. Each claim about my work points to a prompt time, a recorded decision or a commit. Where I delegated a check to AI, or have not done it, the document says so.

## Summary

| Area | Who did it |
|---|---|
| Code, tests, scripts, configuration, specifications and document drafts | **AI** (Claude Code, model `claude-opus-5-5`) |
| Planning drafts: execution plan, build guide, decision log, requirements table | **AI**, directed and adopted by me |
| Scope, sequence, stop points and what could be committed | **Me** |
| Project safety rules for the AI: read-only paths, tests first, no secrets, no real money | **Me** |
| Decisions between options | **Me**, usually accepting AI's recommendation. Some I delegated, as marked below |
| Calling for independent reviews and deciding to fix their findings | **Me** |
| Finding and diagnosing defects | **Mostly AI reviewers and tests** |
| Running and pushing most commits | **Me**: 26 of 51 commits. AI ran the other 25 on my instruction |
| Runtime rationale in the product | **Claude Haiku 5.5** (`claude-haiku-5-5`), optional; a fake model is the default |

Across the build session I sent 73 prompts. I answered 8 recorded decision questions, and in all 8 I chose the option AI had recommended. My contribution was direction, control and acceptance. I did not write the code by hand.

## How I controlled the AI

1. **One work package at a time.** I started each package with its own prompt and let the next begin only after the previous result.
2. **Read-only ground truth.** Project rules barred the AI from changing specifications, acceptance tests, the corpus or the expectations. It had to write changes under `drafts/` for me to move into place.
3. **Tests before code.** I committed the acceptance tests before their implementation: `70b6466` before `774dde8`, and `f3a0e58` before `ab80439`.
4. **Labels before measurement.** I committed the retrieval relevance labels (`9cf7e0a`) before the evaluation ran (`b8911c1`).
5. **No secrets or live spend by default.** Rules barred the AI from reading `.env` and from running live model commands unless I told it to.
6. **Independent reviews at four points.** I asked for three separate AI reviewers each time, at 09:23, 11:17, 15:10 and 16:11.
7. **Review against the real brief.** For the final review I supplied the brief's text myself, so the build was checked against the source and not only against an AI-drafted requirements table.

## Before the build

| Item | AI | Me |
|---|---|---|
| Repository | | Created it (`4a6b2c6`) |
| Plan, build guide, decision log, requirements table | Drafted them | Directed and adopted them. These are private working documents and are not in this repository |
| Private reference key | Drafted it | Kept it outside the repository and told the AI not to read it |
| Project rules for Claude Code | | Set the rules the AI worked under |

This planning time is not included in the build time in [TIME_LOG.md](TIME_LOG.md).

## Phase by phase

### Phase 1: Foundation

| WP | Time | AI did | I did | Evidence |
|---|---|---|---|---|
| 1.1, 1.2 | 07:51–09:11 | Built the scaffold, settings, clock, logging, redaction and fingerprints | Started each package. Decided what to commit, kept docs and project rules out, and fixed the commit message policy. Resolved a failed `git add` by giving the AI the error | Prompts 07:51, 08:14, 08:54–09:07; commits `1bd0216`, `cff8023` |

### Phase 2: Specifications and fixtures

| WP | Time | AI did | I did | Evidence |
|---|---|---|---|---|
| 2.1 Specs | 08:32–09:22 | Drafted 31 rules, the state graph, permissions, registry and tools | Decided four specification questions: block timeout path, refusing POST on a blocked re-evaluation, the `UNIQUE_VIOLATION` block rule, and the vector set. Committed the specs | Answers at 09:15; commit `8ee5e0b` |
| First review | 09:23–09:53 | Three reviewers found issues; AI fixed 13 | **Called the review.** Chose the recommended fixes and required one commit per fix. Ran the check AI asked for and reported its result | Prompts 09:23, 09:39, 09:52, 09:55; commits through `e84fd8e` |
| 2.2 Models | 09:55–10:06 | Built domain models, tool schemas and the model interface | Asked which decisions were mine before committing, then accepted them | Prompts 10:03–10:05; commit `8695cfa` |
| 2.3 Fixtures | 10:09–10:39 | Built the five cases and scenario expectations | Started the package and told AI to commit | Prompts 10:09, 10:38; commit `d017a81` |

### Phase 3: RAG pipeline

| WP | Time | AI did | I did | Evidence |
|---|---|---|---|---|
| 3.1 Ingestion | 10:39–10:44 | Built ingestion, chunking, tokenizer and BM25 index | Directed and told AI to commit | Prompts 10:39, 10:43; commit `a805ed4` |
| 3.2 Retrieval | 10:50–10:56 | Built the retrieval tool, trust lanes and templates | Resolved a conflict in my documents by setting the golden set to 5 queries. Approved the commit | Answers at 10:56; commit `2839fed` |
| 3.3 Evaluation | 10:57–11:16 | Proposed relevance labels and built the evaluation | **Accepted the labels and committed them before measurement**, then ran the commit for the results | Answer at 11:03; commits `9cf7e0a`, `b8911c1` |
| Second review | 11:17–11:38 | Three reviewers raised 42 findings; AI fixed 34 | **Called the review** and required every issue to be recorded. Told AI to apply the recommended decisions | Prompts 11:17, 11:38; commits through `9faf2d4` |

### Phase 4: Tools, ledger, block and submit

| WP | Time | AI did | I did | Evidence |
|---|---|---|---|---|
| 4.A Tests | 11:40–11:49 | Wrote tool, block and submit tests first | Moved them into the read-only path and committed them before any implementation | Commits `5c8c3a9`, `70b6466` |
| 4.B Implementation | 11:49–11:55 | Built the tool runner, sandbox ledger, block and submit | Started the package and told AI to make the recommended decision and commit | Prompts 11:49, 11:54; commit `774dde8` |
| 4.3 Mocks | 11:54–11:58 | Built mock tools, contracts and audit schema | Committed the result | Commit `334a86b` |

### Phase 5: Controls engine

| WP | Time | AI did | I did | Evidence |
|---|---|---|---|---|
| 5.1 Calculator, matching | 11:58–12:05 | Wrote tests and code | Delegated the open choices to AI's recommendation. Committed | Prompt 12:03; commit `fe59c99` |
| 5.2 Duplicates, vendor, authority | 12:06–12:10 | Wrote tests and code | Delegated the open choices to AI's recommendation | Prompt 12:10; commit `abf7f32` |
| 5.3 Fraud, decision table | 12:12–12:21 | Wrote tests and code | Delegated the open choices to AI's recommendation. Committed | Prompt 12:20; commit `37d34ad` |

### Phase 6: Persistence, orchestration and approvals

| WP | Time | AI did | I did | Evidence |
|---|---|---|---|---|
| 6.A Tests | 12:21–12:37 | Wrote approval, graph and resume tests | Delegated the open choices. Moved the tests into place and committed them before the implementation | Prompt 12:29; commit `f3a0e58` |
| 6.B1 Approvals | 12:37–13:02 | Built repositories and the approval service | Started the package and committed | Prompts 12:37, 12:56; commit `ab80439` |
| 6.B2 State graph | 13:02–13:12 | Built the graph, nodes, runner and final result | Started the package and committed | Prompts 13:02, 13:10; commit `6f0455c` |
| 6.B3 Resume | 13:12–13:16 | Built checkpointing and crash-safe resume | Started the package and committed | Prompt 13:12; commit `8c22074` |
| FIN-001 and FIN-004 trace | 13:30–13:55 | **Ran the trace**, found missing assumptions, and fixed them | The plan made this trace my check. **I delegated it to AI**, then approved the fix and committed it | Prompts 13:30, 13:37; commit `afac98f` |

### Phase 7: LLM integration

| WP | Time | AI did | I did | Evidence |
|---|---|---|---|---|
| 7.1 Prompt, adapter, router | 13:55–14:05 | Built all three | Started the package and committed | Prompt 13:55; commit `b91e84b` |
| 7.2 Validator | 14:05–14:16 | Built the validator and approval evidence | Started the package and committed | Prompt 14:05; commit `e5a7e2e` |
| 7.3 Live smoke | 16:50–16:58 | Diagnosed the rejected replies, fixed the validator, and reran the test | **Ran the first real model call in my own terminal** and supplied the failure. Then told AI to finish the step, and committed | Prompts 16:50, 16:52; commit `49c9bf2` |

### Phase 8: Interface and evaluation

| WP | Time | AI did | I did | Evidence |
|---|---|---|---|---|
| 8.1 CLI | 14:16–14:26 | Built the run service and CLI | Started the package and committed | Prompt 14:16; commit `56a16f2` |
| 8.2 Evaluation | 14:26–14:33 | Built the scenario runner, report and live harness | Started the package and committed | Prompt 14:26; commit `bbbf6e2` |
| 8.3 End-to-end fix | 14:33–14:54 | Ran the scenarios, diagnosed the failure, and proposed two fixes | **Chose the recommended product fix** over a change that would only have turned the test green. Committed | Prompt 14:39; commit `9dedc1f` |
| 8.4 Live run | 16:55–16:58 | Ran FIN-001 and FIN-003 on Claude Haiku 5.5 and recorded the gates | Told AI to run it, and committed the result | Prompt 16:52; commit `eef60d3` |

### Phase 9: Documentation and final reviews

| WP | Time | AI did | I did | Evidence |
|---|---|---|---|---|
| 9.1 and first full review | 15:06–15:34 | Drafted the design note. Three reviewers checked requirements, controls and AI safety, and AI fixed the confirmed defects | **Called the review** and committed the fixes | Prompts 15:06, 15:10; commit `74be6b6` |
| Second full review and 9.2 | 16:11–16:38 | Three reviewers checked the build against the brief. AI fixed confirmed defects and drafted the README and architecture | **Called the review, supplied the brief, and told AI to apply the fixes.** Committed | Prompts 16:11, 16:30; commits `5896078`, `7314e2e` |
| Time log | After 16:41 | Rebuilt the log from the transcript and commits | Set the measurement rule: start at execution, end at commit, no breaks. Set the WP 9.1 end time to 15:34 | [TIME_LOG.md](TIME_LOG.md) |

## Manual checks my plan assigned to me

| Check | Status | What actually happened |
|---|---|---|
| Interpretation of the brief | **Partly** | I made the decisions recorded above. The decision-log sign-off was not finalised |
| Specification and fixture sign-off | **Partly** | I decided the open questions and committed the specs. I did not record a line-by-line review |
| Approving acceptance tests before code | **Partly** | I committed them before the implementation. I did not record a line-by-line review |
| Golden-set labels | **Accepted** | AI proposed them. I accepted them before measurement |
| Failure diagnosis | **Delegated** | AI diagnosed. I chose among the fixes it proposed |
| Tracing FIN-001 and FIN-004 | **Delegated** | AI ran the trace at my request |
| Verifying API facts and prices | **Not done** | `docs/prep/RESEARCH_NOTES.md` is blank and model prices are unset, so cost is reported as `unpriced` |
| Reading the live model outputs | **Not done** | AI read them against the gates. The Manual read column is empty |
| Rejection reasons in the design note | **Not done** | The "Why I rejected it" column in `DESIGN.md` is empty |

## AI reviews

All reviews were performed by Claude reviewers, not by people. They found real defects that the passing tests had missed, including overbilling paths and an approval that could be applied to a replaced request. Confirmed defects were fixed or listed as limitations in the [README](../README.md).

## Material I did not author

- **All code, tests, scripts, configuration and documents:** generated by Claude Code under my direction.
- **Policy corpus:** supplied with the brief and unmodified. Its files are hash-locked in `config/registry.yaml`.
- **Direct dependencies** (exact versions locked in `uv.lock`):

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

See [TIME_LOG.md](TIME_LOG.md). Build time is 7 h 32 min, measured from execution in Claude Code to each commit. Planning before the build is not included.
