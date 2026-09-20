# Repository guidance

- For an implementation task, first read `docs/development-status.md` and the current task card named there.
- Read only the sections of `个人图片搜索系统_V3.0_开发主架构.md` and `个人图片搜索系统_V3.1_Codex_DeepSeek_开发执行策略.md` listed by that task card, plus the directly related implementation, interfaces, and tests.
- Read both current architecture/strategy documents in full only for initial architecture alignment, a missing or repository-inconsistent task card, an architecture-wide or cross-module protocol/data-semantics decision, or an explicit architecture audit. Starting another subtask or phase does not by itself require a full reread; read the relevant phase sections instead.
- Model routing is advisory only. The user manually switches GPT/DeepSeek models and reasoning levels inside Codex. Never attempt or claim an automatic model switch, never require a separate agent/client for DeepSeek, and when the next task recommends a different model, stop after generating the next task card and remind the user to switch manually.
- For remaining-task review routing, use an independent **DS-H** session for the full engineering primary review. Escalate that review to **DeepSeek max** only when a V3.1 named condition is evidenced; do not make max a default task tier. Use **GPT-5.6 Sol / high** only for the focused final review of high-risk invariants, ADR alignment, and unresolved primary-review findings, not to repeat the full diff walkthrough.
- Default session boundary: one independently accepted task per new Codex conversation. Within that task, keep the same conversation and model through implementation, debugging, tests, fixes, and re-tests whenever possible. Only recommend a mid-task model switch when a V3.1 escalation condition is actually met; otherwise do not reopen conversations or rotate models just for cache, speed, or experimentation.
- At the end of a task, persist cross-task context in the task report, `docs/development-status.md`, and the next task card. Do not require copying long chat history into the next conversation. A new conversation should recover context in the stable order `AGENTS.md` → `docs/development-status.md` → current task card.
- Work on one accepted task at a time; create modules only when that task needs them.
- Never scan, modify, move, or delete a real photo library without explicit user authorization.
- Keep application data outside the install directory. Do not add cloud services, Docker, Redis, or a second vector backend without a documented architecture decision.
- Use Python 3.12.10 and `uv`; update `uv.lock` whenever Python dependencies change.
- For backend changes run the validation level required by the current task card and V3.1 strategy. Prefer quiet summaries (`pytest -q`) on success; inspect detailed logs only for failures. A task cannot be marked PASS while a required validation is still pending.
- Keep detailed commands, results, and unverified items in the task report; keep only the latest concise verification summary and report link in `docs/development-status.md`.

## Windows Codex Environment Rules

### Project Location

- All project files must remain under:
  `D:\SZ\project`

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

### Testing Rules

- Test reports must be generated using JUnit XML format.

- Never infer test success or failure from missing terminal output.

- `pytest --collect-only` results only indicate test collection status.
  They do not indicate tests passed.

### Windows Permission Safety

- Do not modify Windows ACL permissions automatically.

- If permission changes are required, explicitly request confirmation before applying changes.

### AI Model and Data Rules

- Do not store AI models inside source code directories.

- AI model files must use project-defined data directories.

- Do not download large model files without explicit confirmation.

- Generated indexes, databases, and embeddings must remain separated from source code.
