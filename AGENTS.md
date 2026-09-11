# Repository guidance

- For an implementation task, first read `docs/development-status.md` and the current task card named there.
- Read only the sections of `个人图片搜索系统_V3.0_开发主架构.md` and `个人图片搜索系统_V3.1_Codex_DeepSeek_开发执行策略.md` listed by that task card, plus the directly related implementation, interfaces, and tests.
- Read both current architecture/strategy documents in full only for initial architecture alignment, a missing or repository-inconsistent task card, an architecture-wide or cross-module protocol/data-semantics decision, or an explicit architecture audit. Starting another subtask or phase does not by itself require a full reread; read the relevant phase sections instead.
- Work on one accepted task at a time; create modules only when that task needs them.
- Never scan, modify, move, or delete a real photo library without explicit user authorization.
- Keep application data outside the install directory. Do not add cloud services, Docker, Redis, or a second vector backend without a documented architecture decision.
- Use Python 3.12.10 and `uv`; update `uv.lock` whenever Python dependencies change.
- For backend changes run the validation level required by the current task card and V3.1 strategy. Prefer quiet summaries (`pytest -q`) on success; inspect detailed logs only for failures. A task cannot be marked PASS while a required validation is still pending.
- Keep detailed commands, results, and unverified items in the task report; keep only the latest concise verification summary and report link in `docs/development-status.md`.
