# Research note

Status key:
- ✅ verified (date and URL filled in);
- ✎ changed after checking (the correct value is written in);
- ❌ not confirmed (it must not be used until it is resolved).

| # | Fact | Expected value (to confirm) | Status | Checked on | Source URL |
|---|---|---|---|---|---|
| 1 | Primary model ID | `claude-haiku-5-5` | | | docs.claude.com → Models |
| 2 | Fallback model ID | `claude-sonnet-5-5` | | | docs.claude.com → Models |
| 3 | Effort parameter | `output_config.effort = "low"` accepted by both models | | | docs.claude.com → Effort |
| 4 | Thinking | `thinking = {"type": "adaptive"}` accepted; budget not required | | | docs.claude.com → Extended thinking |
| 5 | Parameters rejected or not to send | temperature, top_p, top_k, forced `tool_choice`, assistant prefill | | | docs.claude.com → Migration guide |
| 6 | Structured output | `messages.create(output_config={"format": {"type": "json_schema", "schema": …}})`; the response text is JSON; our code validates it (`messages.parse` raises on failure and loses usage and stop_reason) | | | docs.claude.com → Structured outputs |
| 7 | Stop reasons to handle | `max_tokens`, `refusal` ⇒ treated as malformed | | | docs.claude.com → Messages API |
| 8 | Region / request ID in the response | which field carries the request ID; whether a region is reported | | | docs.claude.com → API errors / headers |
| 9 | `bm25s` version and API | `bm25s==0.3.16`; `BM25().index(tokens)`; `retrieve(query_tokens, k=…, weight_mask=…, show_progress=False)`; k ≤ number of documents | | | pypi.org/project/bm25s (and the installed signatures in 3.1) |
| 10 | LangGraph interrupt semantics (for the framework decision D2) | `interrupt()` re-runs its node from the start on resume | | | docs.langchain.com → LangGraph → Interrupts |
| 11 | Claude Code authentication precedence | `ANTHROPIC_API_KEY` in the environment overrides the subscription login | | | code.claude.com → Authentication |
| 12 | Claude Code permission rules | `Read(path)`, `Edit(path)`, `Bash(cmd:*)`, `*` wildcards; `Edit` also covers writes; settings hot-reload | | | code.claude.com → Permissions |
| 13 | CLAUDE.md loading | auto-loaded from the repo root; `/memory` lists it | | | code.claude.com → Memory |
| 14 | Pricing (input / output per MTok) | Haiku 5.5: ___ / ___ USD · Sonnet 5.5: ___ / ___ USD | | | docs.claude.com → Pricing |

## Notes and deviations
- (Write anything that differs from the expected value, and what you changed in the plan or decision log because of it.)