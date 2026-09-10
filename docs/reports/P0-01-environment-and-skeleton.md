# P0-01 — Environment inventory and minimum project skeleton

**Status:** completed on 2026-09-10.

## Scope and safety boundary

This task creates only the minimum Python application skeleton and a health endpoint.
It does not download models, inspect or scan a photo library, build an index, or add
future-phase placeholder modules.

## Verified host inventory

| Item | Observed value | Evidence / note |
|---|---|---|
| Operating system | Windows 11 Home Chinese, 64-bit | CIM: version `10.0.26200`, build `26200` |
| CPU | 13th Gen Intel Core i9-13900HX | 24 cores, 32 logical processors; CIM reported max clock 2200 MHz |
| Memory | 34,038,341,632 bytes (31.70 GiB) | CIM `Win32_ComputerSystem.TotalPhysicalMemory` |
| NVIDIA GPU | NVIDIA GeForce RTX 4060 Laptop GPU | `nvidia-smi`: 8188 MiB, driver `616.56` |
| Integrated GPU | Intel UHD Graphics | CIM driver `32.0.101.6790`, dated 2025-04-28 |
| Virtual display adapter | OrayIddDriver Device | Recorded as a display adapter; not treated as a compute GPU |
| Python baseline | CPython 3.12.10, 64-bit | `C:\Users\28288\AppData\Local\Python\pythoncore-3.12-64\python.exe` |
| Other Python | CPython 3.14, 64-bit | Present but not used by this project |
| uv | 0.11.0 | `uv --version` |
| Node | v24.19.0 | Codex runtime `node.exe` |
| npm / corepack | unavailable on PATH | `Get-Command npm,corepack` returned no command |

The NVIDIA memory value comes from `nvidia-smi`. CIM reported an approximately 4 GiB
`AdapterRAM` value for the RTX adapter, so it is not used as the VRAM fact.

## Created minimum skeleton

- `pyproject.toml` fixes the interpreter baseline at Python `3.12.10`, declares the
  CPU-only web/check dependencies, and configures pytest and Ruff.
- `uv.lock` locks the actual resolved package set; `.venv` is intentionally ignored.
- `src/photo_search` contains only a FastAPI application and local-only module/script
  entry point. `GET /api/v1/health` responds with process readiness and touches no
  database, model, vector store, or photo source.
- `tests/unit/test_health.py` verifies that endpoint in-process.
- `AGENTS.md` and `docs/development-status.md` establish the project guardrails and
  hand-off record.

Resolved top-level packages: FastAPI `0.141.1`, Uvicorn `0.52.4`, HTTPX `0.28.1`,
pytest `8.4.2`, and Ruff `0.16.6`. The full transitive set contains 24 packages in
`uv.lock`.

## Commands actually run

```text
uv lock
uv sync --all-groups
uv run ruff check .
uv run pytest tests/unit
uv run python -c "from fastapi.testclient import TestClient; from photo_search.app import app; response = TestClient(app).get('/api/v1/health'); print(response.status_code, response.json())"
uv tree --depth 1
git diff --check
```

Results: dependency resolution and installation succeeded; Ruff passed; the unit test
passed; the health smoke test returned `200 {'status': 'ok', 'service': 'photo-search'}`;
and `git diff --check` reported no whitespace errors.

## Frontend decision

No frontend scaffold was created in P0-01. The available Node runtime lacks both npm
and corepack, so a `package-lock.json` cannot be truthfully generated. The task does
not require a UI, and creating an unbuildable `frontend/` placeholder would violate the
stage boundary. When a Node package manager is made available, initialize the minimal
Vue 3 + TypeScript + Vite project and validate these scripts before recording its lock:

```text
npm ci
npm run typecheck
npm run build
```

## Unverified and explicitly deferred

- P0-02: SQLite FTS5 and LanceDB persistence/restart/filter probes; LanceDB is not a
  dependency yet.
- CPU/GPU ONNX Runtime schemes, model manifests, model downloads, and all AI loading.
- AI subprocess behavior, packaging, and Chinese/space-path executable launch.
- Any frontend installation, typecheck, build, or frontend lockfile.
- All photo-library, index, database-schema, and full-build operations.

## Next task

**P0-02** — create focused Windows probes for SQLite FTS5 and LanceDB, then lock only
the dependencies demonstrated by those probes.