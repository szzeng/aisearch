# Repository guidance

- For an implementation task, first read `docs/development-status.md` and the current task card named there.
- Read only the sections of `个人图片搜索系统_V3.0_开发主架构.md` and `个人图片搜索系统_V3.1_Codex_DeepSeek_开发执行策略.md` listed by that task card, plus the directly related implementation, interfaces, and tests.
- Read both current architecture/strategy documents in full only for initial architecture alignment, a missing or repository-inconsistent task card, an architecture-wide or cross-module protocol/data-semantics decision, or an explicit architecture audit. Starting another subtask or phase does not by itself require a full reread; read the relevant phase sections instead.
- Model routing is advisory only. The user manually switches GPT/DeepSeek models and reasoning levels inside Codex. Never attempt or claim an automatic model switch, never require a separate agent/client for DeepSeek, and when the next task recommends a different model, stop after generating the next task card and remind the user to switch manually.
- For remaining-task review routing, follow V3.1 §2.4 and the current task card: high-risk contract/state/data work gets an independent **DS-H full affected-surface engineering review**; measurement-only, display-only, and packaging-only work may use the specifically bounded independent review defined there. A bounded review must still verify the complete changed surface and required evidence, and must expand to full affected-surface review when its stated trigger occurs. Escalate DS-H to **DeepSeek max** only when a V3.1 §2.2 named condition is evidenced. Use **GPT-5.6 Sol / high** only for focused final review of high-risk invariants, ADR alignment, and unresolved review findings, not to repeat the full diff walkthrough.
- Default session boundary: one independently accepted task per new Codex conversation. Within that task, keep the same conversation and model through implementation, debugging, tests, fixes, and re-tests whenever possible. Only recommend a mid-task model switch when a V3.1 escalation condition is actually met; otherwise do not reopen conversations or rotate models just for cache, speed, or experimentation.
- At the end of a task, update the concise final report, `docs/development-status.md`, and the current/next task card. Reuse these documents within the same change; do not create a new report or card for each repair, rerun, or review. A new conversation should recover context in the stable order `AGENTS.md` → `docs/development-status.md` → current task card.
- Work on one accepted task at a time; create modules only when that task needs them.
- Before implementing any task, complete its short performance section using [docs/performance.md](docs/performance.md) and [the task template](docs/tasks/TASK-TEMPLATE.md). Identify the affected user flow, work bounds, budget source, evidence and stop condition before coding; pure documentation may record a justified `PERF-N/A`. Existing cards acquire this section when next activated, without rewriting historical acceptance. Review must check the affected execution path and result/state invariants; a required performance check left pending blocks PASS. Do not claim latency guarantees from small fixtures, SQL counts or API timings alone, or require a new full benchmark for every task.
- For this personal library, default to three short performance lines (flow/risk, approach/bounds, validation/stop), reusing existing budgets. The eight-item checklist is an expansion aid, not mandatory paperwork. Query changes alone do not require a 300k benchmark; expand only for evidenced scale risk or explicit capacity acceptance. Unclaimed capacity/release checks may remain unverified without blocking a scoped feature PASS; existing required checks, data integrity and truthful evidence remain mandatory.
- For every task, select validation by risk and the complete affected surface under V3.1 §3.2/3.4 and [docs/testing.md](docs/testing.md). Reuse verifiable evidence for unchanged code and dependencies; documentation, resource cache versions, test fixtures/expectations and local UI fixes do not automatically require another full suite. Shared fixtures and behavior changes require all affected consumers; data/state changes also require the relevant fault and recovery checks. Broad merges, dependency/runtime or migration-semantic changes, unbounded shared changes and formal releases require full regression, with the reason recorded. A semantics-preserving schema calculation cache still needs affected migration/compatibility/refusal/invalidation checks; the filename alone does not require full regression. The historical personal-album grouping in §9.4 remains useful, but evidence reuse is not restricted to that task list.
- Baseline plus incremental validation may establish current node coverage when code/environment identity and every affected node are reconciled. Label it as combined evidence, never as a single final full-suite pass. Preserve original failures and actual exits/JUnit; close each affected failure with an explained fix and rerun. Do not hide failures by deleting coverage, weakening assertions or adding skip/xfail. Independent review and identity/manual-state/undo/migration/recovery invariants remain required where applicable.
- Never scan, modify, move, or delete a real photo library without explicit user authorization.
- Keep application data outside the install directory. Do not add cloud services, Docker, Redis, or a second vector backend without a documented architecture decision.
- Use Python 3.12.10 and `uv`; update `uv.lock` whenever Python dependencies change.
- For backend changes, record the required affected validation and any full-suite trigger in the current task card using V3.1 §3.2. Older blanket B/D rerun wording does not override this risk-based rule. Prefer quiet summaries (`pytest -q`) on success; inspect detailed logs only for failures. A task cannot be marked PASS while a required validation is pending or a failure within its acceptance scope remains unresolved.
- Keep purpose, implementation, reproducible commands, final results, and material unverified items in a concise task report. Store raw JUnit/JSON, screenshots, intermediate failures, per-run hashes, and review transcripts in ignored `test-results/` or `.tmp/` (CI artifacts when available); preserve them for local evidence reuse without adding them to Git by default. Keep regression tests and reusable benchmark code in Git. Keep only the latest verification summary and report link in `docs/development-status.md`.

## Windows Codex Environment Rules

### Project Location

- All project files must remain under:
  `D:\SZ\projects\aisearch` (the current repository location, explicitly authorized by the user on 2026-10-18)

- Do not create or modify project files outside the project directory unless explicitly requested.

### Filesystem Rules

- Do not use junctions or symbolic links for `.codex` or project management directories.

- Do not use user-level cache directories for project dependencies, builds, or temporary data.

- Prefer project-local temporary directories.

### Python / uv Environment

- uv cache must use:
  `.uv-cache`

- pytest temporary files must use:
  `.pytest-tmp`

- Avoid using `uv run` for simple single-file syntax validation.
  Prefer direct interpreter checks when no project environment execution is required.

### UI Browser Acceptance (default for UI tasks)

- Use the Codex built-in browser on the real app bound to `127.0.0.1`, with project-local isolated synthetic data; reuse existing fixtures. Do not access the user's real photo library or require external browsers/drivers.
- Start without `--open-browser`. Open the launcher's fresh, single-use session link once in that same browser and confirm authenticated API access before testing. Never reuse the link, expose its credential in reports, or bypass browser/session/CSRF protections.
- Check only the affected pages and essential click, selection, navigation, and return flows. Distinguish product failures from browser-tool restrictions and missing fixture states; record a blocker instead of repeatedly attempting a prohibited action.
- Save genuine browser screenshots to ignored `test-results/screenshots/` when export is supported. Summarize routes, actions, observations, and interaction/visual/performance limits in the final report; do not add per-step screenshots or logs to Git. Untested/tool-limited items remain PENDING; narrow viewports are not proof of native 200% zoom.
- Follow the task card's test scope. API/static tests and mockups do not replace real browser evidence, and pending required checks cannot be marked PASS.
- Minimize browser tool/token use: reuse fixtures and verified setup; for a small UI fix usually check 1 key flow, for a routine UI task 2–3, with only essential screenshots (typically 0–1 or 1–2 respectively). Avoid step-by-step screenshots, rechecking unaffected paths, repetitive blocked attempts, and dumping full logs into the conversation; use short summaries and inspect details only on failure. These are defaults, not caps: expand for real defects, affected regressions, or any required task-card acceptance.

### Testing Rules

- Test reports must be generated using JUnit XML format.

- Never infer test success or failure from missing terminal output.

- `pytest --collect-only` results only indicate test collection status.
  They do not indicate tests passed.

### Windows Permission Safety

- Do not modify Windows ACL permissions automatically.

- If permission changes are required, explicitly request confirmation before applying changes.

### Codex Tool and Sandbox Facts

- The `apply_patch` tool can hang in some sessions regardless of the patch content.
  After two consecutive no-result calls, stop retrying: use an equivalent explicit
  write (`Set-Content` / `Add-Content`), then read the file back or compile it to
  verify what actually landed.

- Sandboxed sessions can deny pytest's temporary directories (`WinError 5` on
  `listdir` / `rmtree`), reproduced for `.pytest-tmp` and for `--basetemp` under
  both `tmp/` and `%TEMP%`, at temp-dir creation and at session-finish cleanup.
  This is an environment failure, not a product or test failure: record it as
  such and re-run the required suite through the normal permission route.
  Never change ACLs to work around it.

### AI Model and Data Rules

- Do not store AI models inside source code directories.

- AI model files must use project-defined data directories.

- Do not download large model files without explicit confirmation.

- Generated indexes, databases, and embeddings must remain separated from source code.
