# Time log

| Block | Start | End | Minutes | Notes |
|---|---|---|---|---|
| Pre-clock (approximate, reconstructed) | — | — | <Y> | directing and reviewing AI-drafted plan, guide, decision log, requirements |
| Phase 1: WP 1.1, 1.2 | 08:09 | 08:48 | 39 | planned 28; committed at 09:10–09:11, after 2.1 drafting began |
| Phase 2: WP 2.1 specs | 08:48 | 09:22 | 34 | planned 22; ends at spec commit `8ee5e0b` |
| Phase 2: independent review and 13 fixes | 09:22 | 09:53 | 31 | unplanned; covers Phase 1 and WP 2.1; ends at fix commits |
| Phase 2: WP 2.2 models and schemas | 09:53 | 10:06 | 13 | planned 9; ends at commit `8695cfa` |
| Phase 2: WP 2.3 fixtures and expectations | 10:06 | 10:39 | 33 | planned 13; ends at commit `d017a81` |
| Phase 3: WP 3.1 ingestion and index | 10:39 | 10:44 | 5 | planned 14; ends at commit `a805ed4` |
| Phase 3: WP 3.2 retrieval and templates | 10:44 | 10:56 | 12 | planned 11; ends at commit `2839fed` |
| Phase 3: WP 3.3 labels and evaluation | 10:56 | 11:07 | 11 | planned 9; labels committed at `9cf7e0a`; evaluation awaiting review |
| Independent review of WP 2.2–3.3 and fixes | 11:07 | 11:34 | 27 | unplanned; 34 of 42 findings fixed; committed through `9faf2d4` |
| Phase 4: WP 4.A tool and submit tests | 11:34 | 11:49 | 15 | planned 15; ends at commit `70b6466` |
| Phase 4: WP 4.B runner, ledger, block, submit | 11:49 | 11:55 | 6 | planned 24; ends at commit `774dde8` |
| Phase 4: WP 4.3 mocks and contracts | 11:55 | 11:58 | 3 | planned 9; ends at commit `334a86b` |
| Phase 5: WP 5.1 calculator and matching | 11:58 | 12:05 | 7 | planned 17; ends at commit `fe59c99` |
| Phase 5: WP 5.2 duplicates, vendor, authority | 12:05 | 12:10 | 5 | planned 17; ends at commit `abf7f32` |
| Phase 5: WP 5.3 fraud and decision table | 12:10 | 12:21 | 11 | planned 18; ends at commit `37d34ad` |
| Phase 6: WP 6.A approval, graph, resume tests | 12:21 | 12:37 | 16 | planned 16; ends at commit `f3a0e58` |
| Phase 6: WP 6.B1 repositories and approvals | 12:37 | 13:02 | 25 | planned 11; ends at commit `ab80439` |
| Phase 6: WP 6.B2 state graph and FinalResult | 13:02 | 13:12 | 10 | planned 22; ends at commit `6f0455c` |
| Phase 6: WP 6.B3 checkpoint and resume | 13:12 | 13:16 | 4 | planned 10; ends at commit `8c22074` |
| Trace of FIN-001 and FIN-004, and fixes | 13:16 | 13:55 | 39 | unplanned; ends at commit `afac98f` |
| Phase 7: WP 7.1 prompt, adapter, router | 13:55 | 14:05 | 10 | planned 14; ends at commit `b91e84b` |
| Phase 7: WP 7.2 validator | 14:05 | 14:16 | 11 | planned 9; ends at commit `e5a7e2e` |
| Phase 7: WP 7.3 live smoke | — | — | — | planned 4; **not run**, owner step |
| Phase 8: WP 8.1 run service and CLI | 14:16 | 14:26 | 10 | planned 12; ends at commit `56a16f2` |
| Phase 8: WP 8.2 scenarios, report, live harness | 14:26 | 14:33 | 7 | planned 9; ends at commit `bbbf6e2` |
| Phase 8: WP 8.3 end-to-end fix-up | 14:33 | 14:54 | 21 | planned 20; ends at commit `9dedc1f` |
| Phase 8: WP 8.4 live run | — | — | — | planned 5; **not run**, owner step |
| WP 9.1 draft, independent review of the full build, and fixes | 14:54 | 15:30 | 36 | 9.1 planned 12; review unplanned; committed at `74be6b6` |
| Second review against the brief, fixes, and WP 9.2 drafts | 15:30 | 16:40 | 70 | past the 8-hour mark; review unplanned; 9.2 planned 15; end time approximate |

Times are for 2026-11-23, reconstructed from file-creation and commit timestamps, so they include waiting between steps. Each row from Phase 4 on starts at the previous commit, so work done before a commit can fall in the earlier row. Total so far: about 511 minutes (08:09 to about 16:40) against 377 planned for Phases 1 to 9, with WP 7.3 and 8.4 still to run. Preparation before 08:09 is not in this total. Phase 1: 39 minutes against 28 planned. Phase 2: 111 minutes against 46 planned. Phase 3: 28 minutes against 36 planned, plus the 27-minute review. Phase 4: 24 minutes against 50 planned. Phase 5: 23 minutes against 54 planned. Phase 6: 55 minutes against 61 planned.
