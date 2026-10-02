# 101 Ways to Use Looping Box for Fun and Profit

Looping Box does a small thing on purpose: files go into `inbox/`, anything that
reads like an outward action is **held for a signed human decision**, everything else
becomes a local draft, and every step lands in a hash-chained log. Nothing is ever sent,
deployed, or committed by it.

Every idea below is built from that, and only that.

## Reality check (read this first)

- **The gate is a keyword tripwire, not a guarantee.** It misses paraphrase and other
  languages. See [THREAT-MODEL.md](THREAT-MODEL.md). Do not sell it as security.
- **Approving does not do the thing.** There are no executors yet. "Approved" means
  "released into the local draft pipeline"; you still perform the action yourself.
- **Offline, a draft is a 500-character excerpt copy.** The drafting gets interesting
  only with a model enabled, and then excerpts go to OpenRouter.
- **The "profit" section is unvalidated.** Nobody has paid for any of it. Treat each item
  as a hypothesis to test with one real person before building anything.

Tags: **[today]** works as shipped. **[model]** needs `OPENROUTER_API_KEY` and a
`MODEL_<ROLE>` (excerpts leave your machine). **[roadmap]** needs an item from
[ROADMAP.md](../ROADMAP.md). **[you do]** the tool holds or records; a human acts.

Adding a gate keyword means editing both `config/sops/phase1_ingestion.json` and
`config/action_classes.json`; `looping-box doctor` warns if they disagree.

---

## A. Your own inbox (1–12)

1. **Brain-dump folder.** Drop notes in `inbox/`; the routes label them
   `documentation`, `task_backlog`, `context_package`, and `draft.md` is your skim. **[today]**
2. **The follow-up checklist.** `looping-box review list` is a to-do list of everything
   that sounded like an action you owe. **[today] [you do]**
3. **Morning delta.** Run `looping-box run` from cron; exit code `2` means something is waiting for you. **[today]**
4. **Meeting-notes sink.** Transcripts in, action items routed to `task_backlog`, "send the deck" held. **[today]**
5. **Email-export triage.** Save emails as `.txt`; ones asking you to pay, send, or sign get held. **[today]**
6. **Bills inbox.** `invoice` and `pay` are gate words, so bills pile up in the review list instead of in your head. **[today] [you do]**
7. **Journal with a smoke alarm.** Entries mentioning `password`, `secret`, or `api key` are held
   in the `blocked` class, so you notice when you pasted a credential into your notes. **[today]**
8. **Read-later queue.** Paste articles as `.md`; define your own routes (`ai`, `finance`, `recipes`). **[today]**
9. **Voice-memo transcripts.** Whatever you use to transcribe, point its output at `inbox/`. **[today]**
10. **One workspace per life area.** `looping-box init ~/work`, `~/home`, `~/side-project`; switch with `LOOPING_BOX_ROOT`. **[today]**
11. **Dedup by content.** Same note saved twice under two names is processed once (SHA-256). **[today]**
12. **Personal tripwires.** Add words like `cancel subscription` or `refund` to the SOP so they never slide by unnoticed. **[today]**

## B. A seatbelt for AI agents (13–27)

13. **Proposal folder.** Have an agent write what it *wants* to do into `inbox/`; a human approves before a separate script acts. **[today] [you do]**
14. **Pre-flight for coding agents.** Agent plans in markdown; `rm -rf`, `sudo`, `| sh`, `terraform apply`, `git reset --hard` hold the plan. **[today]**
15. **Prompt-injection canary.** Drop hostile documents in and see which are held; tune keywords from the misses. **[today]**
16. **Red-team corpus.** Keep 100 adversarial strings, run `match_keywords` over them in CI, track the miss rate over time. **[today]**
17. **Gate as a library.** `from looping_box.action_policy import match_keywords` and reuse the normalizer (homoglyphs, spaced-out letters, zero-width) in your own tools. **[today]**
18. **Self-approval demo.** Show a team why `approve` needs a real TTY and why an MCP `approve` tool must never exist. **[today]**
19. **Per-workspace signing keys.** Give each project its own `LOOPING_BOX_REVIEW_KEY` so a decision can't be copied between them. **[today]**
20. **Circuit breaker.** `max_files_per_cycle` and `max_payload_bytes` cap how much a runaway producer can push through one cycle. **[today]**
21. **A "forbidden" list.** Move `deploy` to the `forbidden` class during a freeze: requests can only be rejected, never approved. **[today]**
22. **Quarantine for scraped content.** Pages saved as `.md`; credential-ish text is held before any model sees it. **[today]**
23. **Cheap filter before an expensive model.** Held and oversized items never reach OpenRouter; only clean excerpts do. **[model]**
24. **Role-split models.** Cheap model for `context_builder`, strong one for `execution_engine`, a third as `verifier`. **[model]**
25. **A skeptic verifier.** Judge approvals with a different model family; remember it is advisory and promptable. **[model]**
26. **Morning review for scheduled LLM jobs.** Cron writes outputs to `inbox/`; you read the held ones over coffee. **[today] [you do]**
27. **Notification on gate trip.** Pipe a trip to Slack or `say` so a hold finds you. **[roadmap]**

## C. Audit trail (28–38)

28. **Who approved what.** `review.jsonl` records approver, note, interactivity, and payload hash, hash-chained. **[today]**
29. **Cron the chain check.** `looping-box review audit` exits 1 on a broken log; alert on it. **[today]**
30. **Shared-machine attribution.** Set `LOOPING_BOX_APPROVER` so decisions carry a name, not just a login. **[today]**
31. **AI-use evidence pack.** Threat model + signed records + audit logs for a client or auditor. It supports a story; it is not a certification. **[today]**
32. **Incident replay.** Read `phase1.jsonl` to find the first run where a risky file appeared. **[today]**
33. **"Why was this held?"** Each payload lists `risk_reasons` and an action class, an explainable gate for a policy doc. **[today]**
34. **Anchor the head hash.** Paste the latest log hash into a commit message or gist so a full rewrite becomes detectable. **[today] [you do]**
35. **Hygiene check.** `looping-box doctor` flags loose key permissions, unverifiable decisions, stale locks, config drift. **[today]**
36. **Justification trail.** `--note` is mandatory for both decisions, so "why" is always on file. **[today]**
37. **Four-eyes light.** Two people, two `LOOPING_BOX_APPROVER` names, one convention: the second reviewer re-reads the first's note. **[you do]**
38. **Quarterly "what did we hold" report.** Today: `jq` over `review.jsonl`. Later: `looping-box report`. **[today] [roadmap]**

## D. Writing and research (39–50)

39. **Draft-notes factory.** Feed outlines or scraps; get a working note per item (model optional). **[model]**
40. **Research inbox.** Abstracts in, routed by your keywords, one digest out. **[today]**
41. **Overclaim tripwire.** Add `guarantee`, `proven`, `unlimited` as gate words so claims get a human look before they ship. **[today]**
42. **Brand-voice guardrail.** Hold drafts containing words your brand never says. **[today]**
43. **Newsletter pipeline.** `publish` and `send` are gate words; you are the send button. **[today] [you do]**
44. **Social-post approval queue.** Posts as `.txt`; `post to` and `tweet` hold them; you post by hand. **[today] [you do]**
45. **Prompt-library staging.** New prompts sit in review until someone has tested them. **[today]**
46. **Release-notes collector.** Merge-request blurbs in, one draft of notes out. **[today]**
47. **Interview-transcript sorter.** Routes tag quotes by topic. **[today]**
48. **Zettelkasten sorter.** Notes arrive, routes file them, the delta shows what changed. **[today]**
49. **Counter-argument pass.** A `verifier` prompt that argues against each draft before you approve. **[model]**
50. **Long-document processing.** Today only 500-char excerpts; chunking is on the roadmap. **[roadmap]**

## E. Dev and ops (51–64)

51. **Runbook linter.** Runbooks as `.md`; destructive commands get flagged for review. **[today]**
52. **Ticket triage.** Exported tickets as text; ones mentioning `delete` or `production` are held. **[today]**
53. **Infra RFC gate.** Change proposals containing `terraform apply` or `kubectl apply` wait for a human. **[today]**
54. **Pasted-log check.** Logs with `password` or `api key` are `blocked` before you paste them to a model or a chat. **[today]**
55. **Secrets-in-docs scan.** Copy a docs folder into a scratch workspace's `inbox/` and read the review list. **[today]**
56. **Nightly shift.** `looping-box run` at 02:00, `looping-box status` at 09:00. **[today]**
57. **CI forcing function.** Run it on a fixture folder in CI and fail the pipeline on exit `2` to force a human sign-off. **[today]**
58. **Support-macro review.** Replies mentioning refunds or credits (`pay`, `invoice`, `transfer`) are held. **[today]**
59. **Data-request queue.** "Delete my data" requests hold until a person confirms. **[today] [you do]**
60. **Slack-thread triage.** Export threads as `.txt`; asks to `merge`, `upload`, or `deploy` surface. **[today]**
61. **Air-gapped workflow.** No dependencies, no network by default; it runs on a laptop with Wi-Fi off. **[today]**
62. **Throwaway workspaces.** The demo scripts build and delete one; copy that pattern for experiments. **[today]**
63. **Release checklist enforcer.** Items shaped like `push`, `publish`, `deploy` become a human checklist. **[today] [you do]**
64. **Approval-released `git_commit` / `file_move`.** One action per approved review, run exactly once, logged. **[roadmap]**

## F. Teaching and demos (65–74)

65. **Workshop: human in the loop.** Run `demo-1.sh`, `demo-2.sh`, `demo-3.sh` live. **[today]**
66. **Teach HMAC signing.** Edit a record by hand and watch the gate re-close. **[today]**
67. **Teach hash chains.** Tamper with a log line, run `review audit`, see `BROKEN at line N`. **[today]**
68. **Teach prompt injection.** Try spaced letters, homoglyphs, and zero-width characters; then try something that still gets through. **[today]**
69. **Teach honest framing.** Show a paraphrase the gate misses and why the README says "tripwire". **[today]**
70. **Threat-model exercise.** Read [THREAT-MODEL.md](THREAT-MODEL.md) and find the hole it didn't list. **[today]**
71. **Code-reading lab.** About 3,000 lines of stdlib Python; small enough to read in an evening. **[today]**
72. **Race-condition lab.** Reproduce the advisory-lock steal described in [REVIEW.md](../REVIEW.md). **[today]**
73. **Test-writing dojo.** Pick an open item from `REVIEW.md` ("no tests for `startday.sh`") and close it. **[today]**
74. **Capstone.** Add one executor behind approval and write its threat model first. **[roadmap]**

## G. Services and profit (75–90, all hypotheses)

75. **"Approval workflow" setup for a small team.** You install it, tune it, and train them. Service beats product here. **[today]**
76. **Gate tuning from real incidents.** Build a client's keywords and action classes from their near-misses. **[today]**
77. **Red-team-as-a-service.** Run an adversarial corpus against a client's agent workflow and report the misses. **[today]**
78. **AI-use disclosure pack for agencies.** Signed decisions and audit logs they can show clients. Not legal advice. **[today]**
79. **"Human in the loop" training.** A paid two-hour session built on the three demos. **[today]**
80. **Industry SOP packs.** Pre-tuned keyword/class sets for marketing ops, devops, bookkeeping. A digital product. **[today]**
81. **Real-estate intake.** `wire transfer` and `wire` are already gate words; wire-fraud emails stop at a person. **[today] [you do]**
82. **Bookkeeper's inbox.** Invoices and payment requests held, everything else summarized. **[today] [you do]**
83. **Freelancer approval ledger.** A hash-chained record of "client approved X on date Y" for scope disputes. **[today]**
84. **Content marketing.** A series on what a keyword gate catches and misses; honest posts travel. **[today]**
85. **Open-source plus support.** Needs a LICENSE decision first. **[roadmap]**
86. **PyPI package and sponsorship.** Blocked on the same LICENSE decision. **[roadmap]**
87. **One narrow paid automation.** Wrap a single executor (e.g. `file_move`) for one niche. **[roadmap]**
88. **Agent-gate audit.** Sell a half-day review of where a client's agents act without a human. The pattern, not the code. **[today]**
89. **Client sign-off portal.** A shared folder plus the review CLI as a poor-man's approval system. **[today] [you do]**
90. **Validate first.** Before building any of 75–89, show `demo-2.sh` to three people who run agents and ask what they'd pay to avoid. **[today]**

## H. For fun (91–98)

91. **Dungeon Master's table.** Players submit actions as files; risky ones are held; the DM approves with a note, and the log becomes the campaign record. **[today]**
92. **Haunted inbox.** Make `summon`, `sacrifice`, `ritual` gate words and let a "review board" rule on them. **[today]**
93. **Gate golf.** Smallest keyword list that catches a fixed test corpus. **[today]**
94. **Homoglyph fuzzer.** Generate evasions and score the normalizer. **[today]**
95. **Escape room.** The tripwire is the lock: players must phrase a request that gets past it. **[today]**
96. **Tamper-evident diary.** Use `append_audit` as a library for hash-chained entries. **[today]**
97. **Approver leaderboard.** Count decisions per approver from `review.jsonl`. **[today]**
98. **Family-chatbot guardrail.** Hold anything with `password` or an address-like keyword before it reaches a model. **[model]**

## I. Make it better (99–101)

99. **Build `looping-box report`.** The audit log plus decisions as one markdown summary. **[roadmap]**
100. **Build the lock properly.** A kernel-backed project lock and an enforced worker deadline. **[roadmap]**
101. **Finish it.** Run the test suite, choose a LICENSE, tag 0.1.0, and stop adding features until someone uses it. **[you do]**

---

## The winners, runnable

One demo per section, each in its own throwaway workspace (`./demos/run-all.sh` runs
them all; nothing touches this repo's inbox, state, or logs).

| Section | Winner | Why it wins | Demo |
|---|---|---|---|
| A. Your own inbox | #2/#4 "What do I owe people?" | Zero setup, offline, and a list you actually want | [a-owed-list.sh](../demos/a-owed-list.sh) |
| B. Agent seatbelt | #14 Pre-flight for coding agents | The real agent risk; shows the `blocked` class refusing a casual approve | [b-agent-preflight.sh](../demos/b-agent-preflight.sh) |
| C. Audit trail | #28/#67 Tamper-evident approvals | One claim you can prove in 60 seconds: edit the evidence, watch it fail | [c-tamper-evidence.sh](../demos/c-tamper-evidence.sh) |
| D. Writing | #41 Overclaim tripwire | The rule list is yours; editing the text clears the hold | [d-overclaim-tripwire.sh](../demos/d-overclaim-tripwire.sh) |
| E. Dev and ops | #57 CI forcing function | Exit code `2` is three lines of shell in any CI | [e-ci-gate.sh](../demos/e-ci-gate.sh) |
| F. Teaching | #68 Evasion lab | Teaches the honest lesson: catches tricks, misses paraphrase | [f-evasion-lab.sh](../demos/f-evasion-lab.sh) |
| G. Services | #76 Gate tuning from incidents | A measured before/after (4/10 to 10/10) is a deliverable | [g-gate-tuning.sh](../demos/g-gate-tuning.sh) |
| H. Fun | #91 Dungeon Master's table | Notes, a forbidden class, and the log as the campaign record | [h-dm-table.sh](../demos/h-dm-table.sh) |
| I. Make it better | #99 `looping-box report` | A roadmap item, now built; the audit trail as one markdown page | [i-report.sh](../demos/i-report.sh) |

Read the winners with the same skepticism as the rest: G's 10/10 is measured on the
corpus it was tuned on, and F shows five plain misses. A winner here means "demonstrates
well and needs nothing that does not exist", not "someone will pay for it".
