# Time log

| Block | Start | End | Active minutes | Notes |
|---|---|---|---:|---|
| Pre-clock (approximate, reconstructed) | — | — | <Y> | directing and reviewing AI-drafted plan, guide, decision log, requirements |
| Phase 1: WP 1.1, 1.2 | 07:51 | 08:21 | 29 | planned 28 |
| Phase 2: WP 2.1 specs | 08:32 | 09:19 | 31 | planned 22; includes committing Phase 1 |
| Phase 2: independent review and 13 fixes | 09:23 | 09:53 | 28 | unplanned; covers Phase 1 and WP 2.1 |
| Phase 2: WP 2.2 models and schemas | 09:55 | 10:08 | 10 | planned 9; commit `8695cfa` |
| Phase 2: WP 2.3 fixtures and expectations | 10:09 | 10:39 | 15 | planned 13; commit `d017a81` |
| Phase 3: WP 3.1 ingestion and index | 10:39 | 10:44 | 4 | planned 14; commit `a805ed4` |
| Phase 3: WP 3.2 retrieval and templates | 10:50 | 10:56 | 7 | planned 11; commit `2839fed` |
| Phase 3: WP 3.3 labels and evaluation | 10:57 | 11:07 | 9 | planned 9; commits `9cf7e0a`, `b8911c1` |
| Independent review of WP 2.2–3.3 and fixes | 11:17 | 11:39 | 19 | unplanned; 34 of 42 findings fixed; committed through `9faf2d4` |
| Phase 4: WP 4.A tool and submit tests | 11:40 | 11:44 | 4 | planned 15; commit `70b6466` |
| Phase 4: WP 4.B runner, ledger, block, submit | 11:49 | 11:54 | 4 | planned 24; commit `774dde8` |
| Phase 4: WP 4.3 mocks and contracts | 11:54 | 11:57 | 3 | planned 9; commit `334a86b` |
| Phase 5: WP 5.1 calculator and matching | 11:58 | 12:04 | 4 | planned 17; commit `fe59c99` |
| Phase 5: WP 5.2 duplicates, vendor, authority | 12:06 | 12:10 | 4 | planned 17; commit `abf7f32` |
| Phase 5: WP 5.3 fraud and decision table | 12:12 | 12:20 | 5 | planned 18; commit `37d34ad` |
| Phase 6: WP 6.A approval, graph, resume tests | 12:21 | 12:30 | 5 | planned 16; commit `f3a0e58` |
| Phase 6: WP 6.B1 repositories and approvals | 12:37 | 12:56 | 3 | planned 11; commit `ab80439` |
| Phase 6: WP 6.B2 state graph and FinalResult | 13:02 | 13:11 | 9 | planned 22; commit `6f0455c` |
| Phase 6: WP 6.B3 checkpoint and resume | 13:12 | 13:15 | 3 | planned 10; commit `8c22074` |
| Phase 6: WP 6.F checkpoint | 13:16 | 13:17 | 1 | planned 2 |
| Trace of FIN-001 and FIN-004, and fixes | 13:30 | 13:38 | 3 | unplanned; commit `afac98f` |
| Phase 7: WP 7.1 prompt, adapter, router | 13:55 | 14:00 | 5 | planned 14; commit `b91e84b` |
| Phase 7: WP 7.2 validator | 14:05 | 14:09 | 4 | planned 9; commit `e5a7e2e` |
| Phase 7: WP 7.3 live smoke and validator fixes | 16:50 | 16:55 | 5 | planned 4; passed after fixing line labels, status wording and repair detail |
| Phase 8: WP 8.1 run service and CLI | 14:16 | 14:21 | 6 | planned 12; commit `56a16f2` |
| Phase 8: WP 8.2 scenarios, report, live harness | 14:26 | 14:31 | 5 | planned 9; commit `bbbf6e2` |
| Phase 8: WP 8.3 end-to-end fix-up | 14:33 | 14:40 | 2 | planned 20; commit `9dedc1f` |
| Phase 8: WP 8.4 live run and recording | 16:55 | 16:57 | 2 | planned 5; run after the review rows below; owner's manual read still open |
| Phase 9: WP 9.1 design note draft | 15:06 | 15:08 | 2 | planned 12 |
| First independent review of the full build, and fixes | 15:10 | 15:31 | 21 | unplanned; commit `74be6b6` |
| Second independent review, against the brief | 16:11 | 16:29 | 18 | unplanned |
| Second-review fixes and WP 9.2 drafts | 16:30 | 16:37 | 7 | 9.2 planned 15 |

Times are for 2026-11-23, taken from the Claude Code session transcript. **Start** is when the block's first prompt was sent. **End** is Claude's last activity on the block's last prompt. **Active minutes** add up only the time from each prompt to Claude's last activity on it, so gaps between prompts are not counted, including gaps inside a block.

**Total active time: 277 minutes (4 h 37 min).** The elapsed window was 07:51 to 16:57 (9 h 06 min), so 4 h 29 min fell between prompts. That gap may include the owner reading output, deciding and committing, which the transcript cannot separate from breaks. Preparation before 07:51 is not in this total.

| Phase | Active minutes | Planned |
|---|---:|---:|
| 1 | 29 | 28 |
| 2, including its review | 84 | 46 |
| 3 | 20 | 36 |
| Review of WP 2.2–3.3 | 19 | unplanned |
| 4 | 11 | 50 |
| 5 | 13 | 54 |
| 6 | 21 | 61 |
| FIN-001 and FIN-004 trace | 3 | unplanned |
| 7 | 14 | 29 |
| 8 | 15 | 46 |
| 9.1 design note | 2 | 12 |
| Full-build reviews, fixes and 9.2 drafts | 46 | 15 for 9.2 |
