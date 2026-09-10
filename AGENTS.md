# Repository guidance

- Read `docs/development-status.md` and the two V3.0 architecture documents before changing a phase.
- Work on one accepted task at a time; create modules only when that task needs them.
- Never scan, modify, move, or delete a real photo library without explicit user authorization.
- Keep application data outside the install directory. Do not add cloud services, Docker, Redis, or a second vector backend without a documented architecture decision.
- Use Python 3.12.10 and `uv`; update `uv.lock` whenever Python dependencies change.
- For backend changes run `uv run ruff check .` and the relevant `uv run pytest` targets. Record commands, results, and unverified items in `docs/development-status.md`.
