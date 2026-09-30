"""P4-02W -- current-build 300k personal-trial performance driver (measurement only).

This driver measures the *current* product end to end through the real HTTP entry
(``python -m photo_search``), the real query encoder and the current CUDA GPU, against a
throw-away copy of the frozen 300k "wave" fixture. It never writes to the product source
tree and never touches the historical evidence directory it copies from.

Scenarios (the task card fixes the order A -> D -> B -> C and stops at the first failure):

  A  warm-text   30 warm plain-text searches                        P95 <= 5 s
  D  background  real semantic build + plain-text search together   P95 <= 8 s, 0 refusals
  C  upload      upload -> decode -> image tower -> search          P95 <= 5 s

Nothing here is an acceptance test by itself. The JSON it writes is the raw evidence; the
P95 is the sample P95 ``sorted_samples[ceil(0.95 * N) - 1]`` the card defines, recomputable
with the ``stats`` subcommand. All failures are kept, never replaced by a later success.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_DIR = Path(r"D:\SZ\project\PhotoSearchData\models\fg-clip2-base")
DEFAULT_MANIFEST = REPO_ROOT / "src" / "photo_search" / "models" / "embedding" / "fg-clip2-base.json"
VENV_PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"

#: The fixture's own canonical Chinese query (capacity_validation.DEFAULT_QUERY_TEXT).
QUERY_TEXT = "西湖 sunset 全家福"

SERVER_READY_TIMEOUT_S = 180.0
HEALTH_TIMEOUT_S = 120.0
SEARCH_TIMEOUT_S = 1200.0
CLI_TIMEOUT_S = 6 * 3600.0

LISTEN_RE = re.compile(r"listening on http://([0-9.]+):(\d+)")
TOKEN_RE = re.compile(r"[?&]token=([0-9A-Za-z\-_]+)")
ENCODER_RE = re.compile(r"semantic channel (\S+)")


# --------------------------------------------------------------------------- basics
def python_exe() -> str:
    return str(VENV_PYTHON if VENV_PYTHON.is_file() else Path(sys.executable))


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def sample_stats(values: list[float]) -> dict[str, Any]:
    """Nearest-rank P50/P95 the card fixes for P95: ``sorted[ceil(q*N)-1]``."""
    ordered = sorted(float(v) for v in values)
    n = len(ordered)
    if n == 0:
        return {"n": 0}
    return {
        "n": n,
        "min": round(ordered[0], 1),
        "p50": round(ordered[math.ceil(0.50 * n) - 1], 1),
        "p95": round(ordered[math.ceil(0.95 * n) - 1], 1),
        "max": round(ordered[-1], 1),
        "mean": round(sum(ordered) / n, 1),
    }


def gpu_facts() -> dict[str, Any]:
    try:
        done = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version,memory.total,compute_cap",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return {"query": done.stdout.strip(), "returncode": int(done.returncode)}
    except Exception as error:  # noqa: BLE001 - a missing tool is a fact, not a crash
        return {"error": type(error).__name__, "detail": str(error)}


def binding_of(manifest: Path) -> dict[str, Any]:
    """The fixture's frozen space/processor/dimension/generation, from the real manifest."""
    from photo_search.artifacts.store import generation_id_for
    from photo_search.workers.query_encoder import pinned_embedding_spec

    spec = pinned_embedding_spec(Path(manifest))
    space_id = str(spec.envelope["space_id"])
    processor_id = str(spec.processor_id)
    return {
        "space_id": space_id,
        "processor_id": processor_id,
        "dimension": int(spec.envelope["dimension"]),
        "generation_id": generation_id_for(space_id, processor_id),
        "supported_devices": [str(item) for item in spec.envelope["supported_devices"]],
    }


def read_fixture(data_root: Path, binding: dict[str, Any]) -> dict[str, Any]:
    """Read-only facts of the copied fixture catalog (never writes)."""
    db = Path(data_root) / "catalog" / "catalog.db"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    def scalar(query: str, params: tuple[Any, ...] = ()) -> Any:
        row = cur.execute(query, params).fetchone()
        return None if row is None else row[0]

    gen = str(binding["generation_id"])
    try:
        facts = {
            "catalog_db_bytes": db.stat().st_size,
            "schema_version": scalar("SELECT value FROM metadata WHERE key = 'schema_version'"),
            "assets": int(scalar("SELECT COUNT(*) FROM assets")),
            "file_locations": int(scalar("SELECT COUNT(*) FROM file_locations")),
            "index_entries_confirmed": int(
                scalar(
                    "SELECT COUNT(*) FROM index_entries WHERE generation_id = ? AND state = 'CONFIRMED'",
                    (gen,),
                )
            ),
            "index_generation": dict(
                cur.execute(
                    "SELECT generation_id, space_id, processor_id, dimension, state, is_active "
                    "FROM index_generations WHERE generation_id = ?",
                    (gen,),
                ).fetchone()
                or {}
            ),
            "search_documents": int(scalar("SELECT COUNT(*) FROM search_documents")),
            "search_projection_queue": int(scalar("SELECT COUNT(*) FROM search_projection_queue")),
            "active_profile": scalar("SELECT profile_id FROM active_search_profile WHERE state_id = 1"),
            "outbox_by_state": {
                str(row["state"]): int(row["total"])
                for row in cur.execute(
                    "SELECT state, COUNT(*) AS total FROM index_outbox GROUP BY state"
                )
            },
        }
    finally:
        con.close()
    return facts


def progress_snapshot(data_root: Path, binding: dict[str, Any]) -> dict[str, Any]:
    """One read-only snapshot of the background build's real progress."""
    db = Path(data_root) / "catalog" / "catalog.db"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    gen = str(binding["generation_id"])
    proc = str(binding["processor_id"])

    def scalar(query: str, params: tuple[Any, ...] = ()) -> int:
        row = cur.execute(query, params).fetchone()
        return 0 if row is None else int(row[0])

    try:
        snapshot = {
            "jobs": {
                str(row["state"]): int(row["total"])
                for row in cur.execute(
                    "SELECT state, COUNT(*) AS total FROM jobs WHERE processor_id = ? "
                    "GROUP BY state",
                    (proc,),
                )
            },
            "outbox": {
                str(row["state"]): int(row["total"])
                for row in cur.execute(
                    "SELECT state, COUNT(*) AS total FROM index_outbox WHERE generation_id = ? "
                    "GROUP BY state",
                    (gen,),
                )
            },
            "entries": {
                str(row["state"]): int(row["total"])
                for row in cur.execute(
                    "SELECT state, COUNT(*) AS total FROM index_entries WHERE generation_id = ? "
                    "GROUP BY state",
                    (gen,),
                )
            },
            "artifacts_published": scalar(
                "SELECT COUNT(*) FROM artifacts WHERE lifecycle_state = 'PUBLISHED'"
            ),
            "search_projection_queue": scalar("SELECT COUNT(*) FROM search_projection_queue"),
            "search_documents": scalar("SELECT COUNT(*) FROM search_documents"),
            "ts": time.time(),
        }
    finally:
        con.close()
    return snapshot


def _count(mapping: dict[str, int], key: str) -> int:
    return int(mapping.get(key, 0))


def progress_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """How much real background work happened between two snapshots."""
    delta = {
        "jobs_succeeded": _count(after["jobs"], "SUCCEEDED") - _count(before["jobs"], "SUCCEEDED"),
        "jobs_running": _count(after["jobs"], "RUNNING") - _count(before["jobs"], "RUNNING"),
        "jobs_queued": _count(after["jobs"], "QUEUED") - _count(before["jobs"], "QUEUED"),
        "outbox_pending": _count(after["outbox"], "PENDING") - _count(before["outbox"], "PENDING"),
        "outbox_confirmed": _count(after["outbox"], "CONFIRMED")
        - _count(before["outbox"], "CONFIRMED"),
        "entries_confirmed": _count(after["entries"], "CONFIRMED")
        - _count(before["entries"], "CONFIRMED"),
        "artifacts_published": int(after["artifacts_published"])
        - int(before["artifacts_published"]),
    }
    delta["advanced"] = any(
        delta[key] > 0
        for key in (
            "jobs_succeeded",
            "outbox_pending",
            "outbox_confirmed",
            "entries_confirmed",
            "artifacts_published",
        )
    )
    return delta


# --------------------------------------------------------------------------- server
class ApiServer:
    """The real ``python -m photo_search`` process, driven over real HTTP on loopback."""

    def __init__(
        self,
        *,
        data_root: Path,
        model_dir: Path,
        manifest: Path,
        device: str,
        log_path: Path,
        extra: tuple[str, ...] = (),
    ) -> None:
        self.data_root = Path(data_root)
        self.model_dir = Path(model_dir)
        self.manifest = Path(manifest)
        self.device = str(device)
        self.log_path = Path(log_path)
        self.extra = tuple(extra)
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.proc: subprocess.Popen[bytes] | None = None
        self.token: str | None = None
        self.launcher_lines: list[str] = []
        self.client: httpx.Client | None = None
        self.headers: dict[str, str] = {}

    def start(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            python_exe(),
            "-m",
            "photo_search",
            "--data-root",
            str(self.data_root),
            "--model-dir",
            str(self.model_dir),
            "--model-manifest",
            str(self.manifest),
            "--model-device",
            self.device,
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            *self.extra,
        ]
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        env.pop("PHOTO_SEARCH_DATA_ROOT", None)
        self._log = open(self.log_path, "w", encoding="utf-8", newline="\n")
        self.proc = subprocess.Popen(
            command, cwd=str(REPO_ROOT), stdout=self._log, stderr=subprocess.STDOUT, env=env
        )
        deadline = time.time() + SERVER_READY_TIMEOUT_S
        while time.time() < deadline:
            text = self._log_text()
            listen = LISTEN_RE.search(text)
            token = TOKEN_RE.search(text)
            if listen and token:
                self.token = token.group(1)
                self.launcher_lines = [line for line in text.splitlines() if line.strip()]
                self._await_health()
                return
            if self.proc.poll() is not None:
                raise RuntimeError(f"server exited early (code {self.proc.returncode}):\n{text}")
            time.sleep(0.5)
        raise TimeoutError(f"server did not start within {SERVER_READY_TIMEOUT_S:.0f}s")

    def _log_text(self) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return ""

    def _await_health(self) -> None:
        deadline = time.time() + HEALTH_TIMEOUT_S
        while time.time() < deadline:
            try:
                response = httpx.get(f"{self.base}/api/v1/health", timeout=5.0)
                if response.status_code == 200:
                    return
            except Exception:  # noqa: BLE001 - not listening yet
                pass
            time.sleep(0.4)
        raise TimeoutError("the API never answered /api/v1/health")

    def semantic_channel(self) -> dict[str, Any]:
        for line in self.launcher_lines:
            match = ENCODER_RE.search(line)
            if match:
                return {"reason": match.group(1), "available": match.group(1) == "READY", "line": line.strip()}
        return {"available": None, "line": None}

    def open_session(self) -> dict[str, Any]:
        self.client = httpx.Client(
            base_url=self.base,
            timeout=httpx.Timeout(connect=30.0, read=SEARCH_TIMEOUT_S, write=120.0, pool=30.0),
            follow_redirects=False,
        )
        response = self.client.get("/api/v1/session", params={"token": self.token})
        response.raise_for_status()
        csrf = str(response.json()["csrf_token"])
        self.headers = {"X-CSRF-Token": csrf, "Origin": self.base}
        return {"status": int(response.status_code), "csrf": bool(csrf)}

    def stop(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=30)
        try:
            self._log.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self) -> "ApiServer":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()


def post_search(server: ApiServer, body: dict[str, Any]) -> dict[str, Any]:
    """One timed ``POST /api/v1/search``; keeps the status, the latency and any error body."""
    assert server.client is not None
    started = time.perf_counter()
    try:
        response = server.client.post("/api/v1/search", json=body, headers=server.headers)
    except Exception as error:  # noqa: BLE001 - keep the failure, never retry silently
        return {
            "ok": False,
            "status": None,
            "http_ms": round((time.perf_counter() - started) * 1000, 1),
            "transport_error": f"{type(error).__name__}: {error}",
        }
    http_ms = round((time.perf_counter() - started) * 1000, 1)
    record: dict[str, Any] = {"status": int(response.status_code), "http_ms": http_ms}
    if response.status_code == 200:
        payload = response.json()
        record.update(
            ok=True,
            state=payload.get("state"),
            hits=len(payload.get("hits") or []),
            total_candidates=payload.get("total_candidates"),
            channels=payload.get("channels"),
            search_projection=payload.get("search_projection"),
        )
    else:
        record.update(ok=False, error_body=response.text[:1200])
    return record


def text_body(binding: dict[str, Any], request_id: str, top_k: int, page_size: int) -> dict[str, Any]:
    return {
        "contract_version": "p2-contracts.v1",
        "request_id": request_id,
        "text": QUERY_TEXT,
        "space_id": str(binding["space_id"]),
        "image_generation_id": str(binding["generation_id"]),
        "dimension": int(binding["dimension"]),
        "top_k": int(top_k),
        "page_size": int(page_size),
    }


def emit(output: Path | None, payload: dict[str, Any], summary: str) -> None:
    text = json.dumps(payload, ensure_ascii=True, indent=2)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8", newline="\n")
    print(summary)
    if output is not None:
        print(f"wrote {output}")

# --------------------------------------------------------------------------- fixtures
def ensure_backlog_library(path: Path, count: int) -> dict[str, Any]:
    """A seeded library of ``count`` distinct, valid JPEGs for the real build backlog."""
    from PIL import Image

    path = Path(path)
    existing = sorted(path.glob("backlog-*.jpg")) if path.is_dir() else []
    if len(existing) == count:
        return {"reused": True, "count": count, "dir": str(path)}
    if path.exists():
        import shutil

        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    stamp = time.time() - 3600.0
    for index in range(count):
        colour = (index % 256, (index // 256) % 256, (index * 7) % 256)
        image = Image.new("RGB", (320, 240), colour)
        image.putpixel((0, 0), (index % 256, (index // 256) % 256, (index // 65536) % 256))
        target = path / f"backlog-{index:06d}.jpg"
        image.save(target, "JPEG", quality=80)
        os.utime(target, (stamp, stamp))
    return {"reused": False, "count": count, "dir": str(path)}


def run_cli(arguments: list[str], *, timeout: float = CLI_TIMEOUT_S) -> dict[str, Any]:
    command = [python_exe(), "-m", "photo_search", *arguments]
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env.pop("PHOTO_SEARCH_DATA_ROOT", None)
    done = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
    )
    payload = None
    for line in reversed(done.stdout.splitlines()):
        stripped = line.strip()
        if stripped.startswith("{"):
            try:
                payload = json.loads(stripped)
                break
            except json.JSONDecodeError:
                continue
    return {
        "command": command,
        "returncode": int(done.returncode),
        "payload": payload,
        "stdout_tail": done.stdout[-3000:],
        "stderr_tail": done.stderr[-3000:],
    }


def ensure_backlog_source(data_root: Path, library: Path, name: str) -> dict[str, Any]:
    """Register the backlog source root through the product CLI (idempotent by path)."""
    listed = run_cli(["catalog", "list-sources", "--json", "--data-root", str(data_root)])
    sources = ((listed.get("payload") or {}).get("sources")) or []
    for source in sources:
        if str(source.get("root_path")) == str(library):
            return {"source_id": int(source["source_id"]), "registered": False, "list": listed}
    added = run_cli(
        [
            "catalog",
            "add-source",
            "--name",
            name,
            "--path",
            str(library),
            "--json",
            "--data-root",
            str(data_root),
        ]
    )
    source = (added.get("payload") or {}).get("source") or {}
    return {"source_id": int(source["source_id"]), "registered": True, "add": added}


def _server_for(args: argparse.Namespace, log_name: str) -> ApiServer:
    return ApiServer(
        data_root=Path(args.data_root),
        model_dir=Path(args.model_dir),
        manifest=Path(args.manifest),
        device=args.device,
        log_path=Path(args.work_dir) / "logs" / log_name,
    )


# --------------------------------------------------------------------------- scenario A
def scenario_warm_text(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    root = Path(args.data_root).resolve()
    binding = binding_of(args.manifest)
    report: dict[str, Any] = {
        "task": "P4-02W",
        "scenario": "A_warm_text",
        "generated_at": now_iso(),
        "git_head": args.git_head,
        "data_root": str(root),
        "model_dir": str(args.model_dir),
        "manifest": str(args.manifest),
        "device": args.device,
        "query_text": QUERY_TEXT,
        "gpu": gpu_facts(),
        "binding": binding,
        "fixture_before": read_fixture(root, binding),
        "threshold_ms": float(args.threshold_ms),
        "method": {
            "entry": "python -m photo_search (real HTTP on 127.0.0.1)",
            "encoder": "resolve_query_encoder(model_dir, manifest, device=cuda)",
            "samples": int(args.samples),
            "smoke": int(args.smoke),
            "p95_rule": "sorted_samples[ceil(0.95*N)-1]",
            "observable_components": ["httpx total request time"],
            "not_observable_components": [
                "query encoding",
                "vector retrieval",
                "text/OCR channel",
                "fusion",
                "projection catch-up",
                "metadata/response assembly",
            ],
        },
        "samples": [],
    }
    server = _server_for(args, "warm-text-server.log")
    try:
        server.start()
        report["semantic_channel"] = server.semantic_channel()
        report["launcher_lines"] = server.launcher_lines
        report["session"] = server.open_session()
        assert server.client is not None
        capabilities = server.client.get("/api/v1/capabilities")
        report["capabilities"] = {
            "status": int(capabilities.status_code),
            "search": capabilities.json().get("search") if capabilities.status_code == 200 else None,
        }
        report["cold_first_search"] = post_search(
            server, text_body(binding, "p4-02w-a-cold", args.top_k, args.page_size)
        )
        smoke = [
            post_search(server, text_body(binding, f"p4-02w-a-smoke-{i}", args.top_k, args.page_size))
            for i in range(int(args.smoke))
        ]
        report["smoke"] = smoke
        samples = []
        for index in range(int(args.samples)):
            record = post_search(
                server, text_body(binding, f"p4-02w-a-{index:03d}", args.top_k, args.page_size)
            )
            record["index"] = index
            samples.append(record)
        report["samples"] = samples
        report["fixture_after"] = read_fixture(root, binding)
    finally:
        server.stop()
    latencies = [float(sample["http_ms"]) for sample in samples]
    failures = [sample for sample in samples if not sample.get("ok")]
    stats = sample_stats(latencies)
    report["stats"] = stats
    report["failure_count"] = len(failures)
    report["failures"] = failures
    report["verdict"] = (
        "PASS" if not failures and stats.get("p95", 1e9) <= float(args.threshold_ms) else "FAIL"
    )
    report["p95_excess_ms"] = round(float(stats.get("p95", 0.0)) - float(args.threshold_ms), 1)
    _summary = (
        f"A warm-text: n={stats.get('n')} p50={stats.get('p50')} "
        f"p95={stats.get('p95')} max={stats.get('max')} failures={len(failures)} "
        f"verdict={report['verdict']}"
    )
    return report, 0 if report["verdict"] == "PASS" else 1


# --------------------------------------------------------------------------- scenario D
def scenario_background(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    root = Path(args.data_root).resolve()
    binding = binding_of(args.manifest)
    backlog_dir = Path(args.backlog_library).resolve()
    report: dict[str, Any] = {
        "task": "P4-02W",
        "scenario": "D_background_build_and_text",
        "generated_at": now_iso(),
        "git_head": args.git_head,
        "data_root": str(root),
        "model_dir": str(args.model_dir),
        "manifest": str(args.manifest),
        "device": args.device,
        "query_text": QUERY_TEXT,
        "gpu": gpu_facts(),
        "binding": binding,
        "threshold_ms": float(args.threshold_ms),
        "backlog_target": int(args.backlog),
        "fixture_before": read_fixture(root, binding),
        "samples": [],
    }
    backlog = ensure_backlog_library(backlog_dir, int(args.backlog))
    report["backlog"] = backlog
    source = ensure_backlog_source(root, backlog_dir, "p4-02w-backlog")
    report["source"] = source
    if source["source_id"]:
        report["scan"] = run_cli(
            [
                "catalog",
                "scan",
                "--source-id",
                str(source["source_id"]),
                "--json",
                "--data-root",
                str(root),
            ]
        )
    report["fixture_after_scan"] = read_fixture(root, binding)
    server = _server_for(args, "background-server.log")
    background_log = Path(args.work_dir) / "logs" / "background-build.log"
    background_log.parent.mkdir(parents=True, exist_ok=True)
    background_proc: subprocess.Popen[bytes] | None = None
    background_handle = None
    try:
        server.start()
        report["semantic_channel"] = server.semantic_channel()
        report["launcher_lines"] = server.launcher_lines
        report["session"] = server.open_session()
        report["cold_first_search"] = post_search(
            server, text_body(binding, "p4-02w-d-cold", args.top_k, args.page_size)
        )
        report["smoke"] = [
            post_search(server, text_body(binding, f"p4-02w-d-smoke-{i}", args.top_k, args.page_size))
            for i in range(int(args.smoke))
        ]
        # Real product maintenance entry: the semantic build of the scanned backlog.
        background_command = [
            python_exe(),
            "-m",
            "photo_search.indexing.cli",
            "build",
            "--data-root",
            str(root),
            "--model-dir",
            str(args.model_dir),
            "--manifest",
            str(args.manifest),
            "--device",
            args.device,
            "--source-id",
            str(source["source_id"]),
            "--limit",
            str(int(args.backlog)),
            "--skip-profile",
            "--json",
        ]
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        env.pop("PHOTO_SEARCH_DATA_ROOT", None)
        background_handle = open(background_log, "w", encoding="utf-8", newline="\n")
        background_proc = subprocess.Popen(
            background_command,
            cwd=str(REPO_ROOT),
            stdout=background_handle,
            stderr=subprocess.STDOUT,
            env=env,
        )
        report["background_command"] = background_command
        # Wait until the background really produces work before timing starts.
        wait_started = time.time()
        started_snapshot = progress_snapshot(root, binding)
        while time.time() - wait_started < 600.0:
            snapshot = progress_snapshot(root, binding)
            if progress_delta(started_snapshot, snapshot)["advanced"]:
                break
            if background_proc.poll() is not None:
                break
            time.sleep(1.0)
        report["background_started_snapshot"] = progress_snapshot(root, binding)
        samples = []
        for index in range(int(args.samples)):
            before = progress_snapshot(root, binding)
            record = post_search(
                server, text_body(binding, f"p4-02w-d-{index:03d}", args.top_k, args.page_size)
            )
            after = progress_snapshot(root, binding)
            record["index"] = index
            record["background_before"] = before
            record["background_after"] = after
            record["background_delta"] = progress_delta(before, after)
            record["background_overlap"] = bool(record["background_delta"]["advanced"])
            record["background_alive"] = background_proc.poll() is None
            samples.append(record)
        report["samples"] = samples
        report["background_alive_after_samples"] = background_proc.poll() is None
        # Let the background finish so its own drain report is available.
        remaining = 0.0
        if background_proc.poll() is None:
            remaining = max(0.0, float(args.background_grace_s))
        try:
            background_proc.wait(timeout=remaining if remaining else 0.001)
        except subprocess.TimeoutExpired:
            background_proc.terminate()
            try:
                background_proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                background_proc.kill()
                background_proc.wait(timeout=60)
        background_handle.flush()
        background_handle.close()
        background_handle = None
        text = background_log.read_text(encoding="utf-8", errors="replace")
        parsed = None
        for line in reversed(text.splitlines()):
            stripped = line.strip()
            if stripped.startswith("{"):
                try:
                    parsed = json.loads(stripped)
                    break
                except json.JSONDecodeError:
                    continue
        report["background_result"] = parsed
        report["background_log_tail"] = text[-3000:]
        report["fixture_after"] = read_fixture(root, binding)
    finally:
        if background_handle is not None:
            background_handle.close()
        if background_proc is not None and background_proc.poll() is None:
            background_proc.terminate()
            try:
                background_proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                background_proc.kill()
        server.stop()
    latencies = [float(sample["http_ms"]) for sample in samples]
    failures = [sample for sample in samples if not sample.get("ok")]
    overlaps = [sample for sample in samples if sample.get("background_overlap")]
    stats = sample_stats(latencies)
    report["stats"] = stats
    report["failure_count"] = len(failures)
    report["failures"] = failures
    report["overlap_count"] = len(overlaps)
    report["required_overlap"] = int(args.samples)
    progressed = report.get("background_result") is not None
    report["verdict"] = (
        "PASS"
        if (
            not failures
            and len(overlaps) >= int(args.samples)
            and stats.get("p95", 1e9) <= float(args.threshold_ms)
            and progressed
        )
        else "FAIL"
    )
    report["p95_excess_ms"] = round(float(stats.get("p95", 0.0)) - float(args.threshold_ms), 1)
    _summary = (
        f"D background: n={stats.get('n')} p50={stats.get('p50')} p95={stats.get('p95')} "
        f"max={stats.get('max')} failures={len(failures)} overlaps={len(overlaps)} "
        f"background_result={'yes' if progressed else 'no'} verdict={report['verdict']}"
    )
    return report, 0 if report["verdict"] == "PASS" else 1


# --------------------------------------------------------------------------- scenario C
def scenario_upload(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    root = Path(args.data_root).resolve()
    binding = binding_of(args.manifest)
    images = [Path(item).resolve() for item in args.image]
    report: dict[str, Any] = {
        "task": "P4-02W",
        "scenario": "C_cuda_upload_search",
        "generated_at": now_iso(),
        "git_head": args.git_head,
        "data_root": str(root),
        "model_dir": str(args.model_dir),
        "manifest": str(args.manifest),
        "device": args.device,
        "gpu": gpu_facts(),
        "binding": binding,
        "fixture_before": read_fixture(root, binding),
        "threshold_ms": float(args.threshold_ms),
        "images": [str(item) for item in images],
        "method": {
            "entry": "python -m photo_search (real HTTP POST /api/v1/search/image)",
            "samples": int(args.samples),
            "p95_rule": "sorted_samples[ceil(0.95*N)-1]",
        },
        "samples": [],
    }
    server = _server_for(args, "upload-server.log")
    try:
        server.start()
        report["semantic_channel"] = server.semantic_channel()
        report["session"] = server.open_session()
        assert server.client is not None

        def one(index: int) -> dict[str, Any]:
            image = images[index % len(images)]
            fields = {
                "contract_version": "p2-contracts.v1",
                "request_id": f"p4-02w-c-{index:03d}",
                "space_id": str(binding["space_id"]),
                "image_generation_id": str(binding["generation_id"]),
                "dimension": str(binding["dimension"]),
                "top_k": str(args.top_k),
                "page_size": str(args.page_size),
            }
            started = time.perf_counter()
            try:
                response = server.client.post(
                    "/api/v1/search/image",
                    data=fields,
                    files={"image": (image.name, image.read_bytes(), "image/jpeg")},
                    headers=server.headers,
                )
            except Exception as error:  # noqa: BLE001
                return {
                    "ok": False,
                    "status": None,
                    "http_ms": round((time.perf_counter() - started) * 1000, 1),
                    "transport_error": f"{type(error).__name__}: {error}",
                    "image": str(image),
                }
            http_ms = round((time.perf_counter() - started) * 1000, 1)
            record: dict[str, Any] = {
                "status": int(response.status_code),
                "http_ms": http_ms,
                "image": str(image),
            }
            if response.status_code == 200:
                payload = response.json()
                record.update(
                    ok=True,
                    state=payload.get("state"),
                    hits=len(payload.get("hits") or []),
                    channels=payload.get("channels"),
                    image_query=payload.get("image_query"),
                )
            else:
                record.update(ok=False, error_body=response.text[:1200])
            return record

        report["cold_first_upload"] = one(0)
        report["smoke"] = [one(i) for i in range(1, 1 + int(args.smoke))]
        samples = []
        for index in range(int(args.samples)):
            record = one(100 + index)
            record["index"] = index
            samples.append(record)
        report["samples"] = samples
        report["fixture_after"] = read_fixture(root, binding)
    finally:
        server.stop()
    latencies = [float(sample["http_ms"]) for sample in samples]
    failures = [sample for sample in samples if not sample.get("ok")]
    stats = sample_stats(latencies)
    report["stats"] = stats
    report["failure_count"] = len(failures)
    report["failures"] = failures
    report["verdict"] = (
        "PASS" if not failures and stats.get("p95", 1e9) <= float(args.threshold_ms) else "FAIL"
    )
    report["p95_excess_ms"] = round(float(stats.get("p95", 0.0)) - float(args.threshold_ms), 1)
    _summary = (
        f"C upload: n={stats.get('n')} p50={stats.get('p50')} p95={stats.get('p95')} "
        f"max={stats.get('max')} failures={len(failures)} verdict={report['verdict']}"
    )
    return report, 0 if report["verdict"] == "PASS" else 1


# --------------------------------------------------------------------------- stats
def scenario_stats(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    latencies = [float(sample["http_ms"]) for sample in payload.get("samples", [])]
    stats = sample_stats(latencies)
    failures = [s for s in payload.get("samples", []) if not s.get("ok")]
    recomputed = {
        "input": str(args.input),
        "scenario": payload.get("scenario"),
        "stats": stats,
        "failure_count": len(failures),
        "threshold_ms": payload.get("threshold_ms"),
        "reported_stats": payload.get("stats"),
        "reported_verdict": payload.get("verdict"),
        "matches": stats == payload.get("stats"),
    }
    print(json.dumps(recomputed, ensure_ascii=True, indent=2))
    return recomputed, 0



# --------------------------------------------------------------------------- attribution
class _BoundedCallLedger:
    """Count the product's two candidate entries without changing either of them.

    ``vector_channel`` asks for ``search_candidates_bounded`` when the index offers it and
    falls back to the exhaustive ``search_candidates`` only for a block whose boundary tie
    group was cut (P4-02M). The HTTP entry cannot see either call, so the in-process
    attribution round - which runs the *real* model against the same fixture and query -
    observes them here. This is an observation shim: both methods are the product's own.
    """

    def __init__(self, index: Any) -> None:
        self._index = index
        self.bounded_calls = 0
        self.fallback_calls = 0
        self._pending_fallback = False

    def reset(self) -> None:
        self.bounded_calls = 0
        self.fallback_calls = 0
        self._pending_fallback = False

    def search_candidates_bounded(self, **kwargs: Any) -> Any:
        rows, resolved = self._index.search_candidates_bounded(**kwargs)
        self.bounded_calls += 1
        self._pending_fallback = not bool(resolved)
        return rows, resolved

    def search_candidates(self, **kwargs: Any) -> Any:
        if self._pending_fallback:
            self.fallback_calls += 1
            self._pending_fallback = False
        return self._index.search_candidates(**kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._index, name)


def scenario_attribution(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """Component split of the current 300k text path (diagnostic, not end-to-end PASS).

    Synthetic/internal timing only: this names where the warm-text seconds go, so a FAIL can
    point at one retrieval-execution component. It never replaces the HTTP evidence.
    """
    from photo_search.catalog.db import Catalog
    from photo_search.search.embedding import encode_query
    from photo_search.search.retrieval import (
        AUTHORITY_PAGE_LIMIT,
        SearchScope,
        authoritative_page,
        fts_channel,
        pending_projection_page,
        resolve_binding,
        vector_channel,
    )
    from photo_search.search.service import default_vector_index, search_assets
    from photo_search.search.tokens import build_match
    from photo_search.workers.query_encoder import build_worker_query_encoder

    root = Path(args.data_root).resolve()
    binding = binding_of(args.manifest)
    scope = SearchScope.build(
        source_id=None,
        availability="any",
        place_ids=None,
        include_descendants=True,
        place_state="any",
        person_ids=None,
        person_match="any",
        capture=None,
    )
    report: dict[str, Any] = {
        "task": "P4-02W",
        "scenario": "attribution_text_components",
        "generated_at": now_iso(),
        "git_head": args.git_head,
        "data_root": str(root),
        "model_dir": str(args.model_dir),
        "device": args.device,
        "query_text": QUERY_TEXT,
        "gpu": gpu_facts(),
        "binding": binding,
        "note": "internal service timing for attribution only; never an end-to-end PASS",
        "rounds": [],
    }
    encoder = build_worker_query_encoder(
        model_dir=Path(args.model_dir), manifest_path=Path(args.manifest), device=args.device
    )
    catalog = Catalog.open(data_root=root)
    try:
        vb = resolve_binding(
            catalog,
            generation_id=str(binding["generation_id"]),
            space_id=str(binding["space_id"]),
            dimension=int(binding["dimension"]),
        )
        match = build_match(QUERY_TEXT)
        index = _BoundedCallLedger(default_vector_index(catalog))

        def once() -> dict[str, Any]:
            record: dict[str, Any] = {}
            started = time.perf_counter()
            vector = encode_query(
                encoder,
                QUERY_TEXT,
                space_id=str(binding["space_id"]),
                dimension=int(binding["dimension"]),
            )
            record["encode_ms"] = round((time.perf_counter() - started) * 1000, 1)
            started = time.perf_counter()
            text_hits = fts_channel(catalog, match, scope, top_k=int(args.top_k))
            record["fts_ms"] = round((time.perf_counter() - started) * 1000, 1)
            record["fts_hits"] = len(text_hits)
            index.reset()
            started = time.perf_counter()
            block = vector_channel(
                catalog, index=index, binding=vb, query_vector=vector, scope=scope,
                top_k=int(args.top_k),
            )
            record["vector_ms"] = round((time.perf_counter() - started) * 1000, 1)
            record["engine_blocks"] = int(block["blocks"])
            # P4-02X: the HTTP envelope only carries ``engine_blocks``, so the two call
            # counts of the access path are recorded from this in-process round (real model,
            # same fixture, same query and same ``top_k`` as the HTTP samples).
            record["bounded_calls"] = int(index.bounded_calls)
            record["fallback_calls"] = int(index.fallback_calls)
            record["scanned_assets"] = int(block["scanned_assets"])
            record["vector_truncated"] = bool(block["truncated"])
            record["vector_hits"] = len(block["hits"])
            started = time.perf_counter()
            authority = authoritative_page(catalog, scope, limit=AUTHORITY_PAGE_LIMIT)
            record["authority_ms"] = round((time.perf_counter() - started) * 1000, 1)
            record["authority_total"] = int(authority["total"])
            started = time.perf_counter()
            pending_projection_page(catalog, binding=vb, scope=scope, match=match)
            record["pending_page_ms"] = round((time.perf_counter() - started) * 1000, 1)
            started = time.perf_counter()
            search_assets(
                catalog,
                text=QUERY_TEXT,
                encoder=encoder,
                space_id=str(binding["space_id"]),
                generation_id=str(binding["generation_id"]),
                dimension=int(binding["dimension"]),
                top_k=int(args.top_k),
            )
            full_ms = round((time.perf_counter() - started) * 1000, 1)
            record["full_service_ms"] = full_ms
            record["service_minus_encode_fts_vector_ms"] = round(
                full_ms
                - float(record["encode_ms"])
                - float(record["fts_ms"])
                - float(record["vector_ms"]),
                1,
            )
            return record

        once()  # warm-up round, discarded
        for _ in range(int(args.rounds)):
            report["rounds"].append(once())
    finally:
        catalog.close()
        encoder.close()
    components = ("encode_ms", "fts_ms", "vector_ms", "authority_ms", "pending_page_ms", "full_service_ms")
    report["component_stats"] = {
        name: sample_stats([float(round_[name]) for round_ in report["rounds"]])
        for name in components
    }
    return report, 0



# --------------------------------------------------------------------------- scenario B
def scenario_first_screen(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    """B: real first-screen picture visibility -- HTTP-layer diagnostic only.

    The card forbids adding Playwright/Selenium and forbids turning this into a browser
    automation project, and this build ships no recorded real-browser timing capability. So
    the formal verdict is ``BROWSER_TIMING BLOCKED``; what is measured here is the real HTTP
    chain the browser drives (search response, then the first-screen thumbnail requests) and
    is reported strictly as a lower-layer diagnostic, never as the browser first-screen P95.
    """
    root = Path(args.data_root).resolve()
    binding = binding_of(args.manifest)
    report: dict[str, Any] = {
        "task": "P4-02W",
        "scenario": "B_browser_first_screen",
        "generated_at": now_iso(),
        "git_head": args.git_head,
        "data_root": str(root),
        "model_dir": str(args.model_dir),
        "device": args.device,
        "query_text": QUERY_TEXT,
        "gpu": gpu_facts(),
        "binding": binding,
        "fixture_before": read_fixture(root, binding),
        "threshold_ms": float(args.threshold_ms),
        "verdict": "BROWSER_TIMING BLOCKED",
        "reason": (
            "no reliable, repeatable browser first-screen timing capability exists in this "
            "build; the card forbids adding a browser automation dependency or expanding "
            "into browser-automation work"
        ),
        "missing_capability": (
            "a recorded real-browser harness that can time first-image paint on the real "
            "front-end without a new automation dependency"
        ),
        "http_layer_diagnostic_note": (
            "search + first-screen thumbnails fetched sequentially over real HTTP; a real "
            "browser fetches thumbnails in parallel, so this is a conservative HTTP-layer "
            "proxy, not the browser first-screen time"
        ),
        "first_screen_size": int(args.first_screen),
        "rounds": [],
    }
    server = _server_for(args, "first-screen-server.log")
    try:
        server.start()
        report["semantic_channel"] = server.semantic_channel()
        report["session"] = server.open_session()
        assert server.client is not None
        # warm the encoder / first search away from the recorded rounds
        post_search(server, text_body(binding, "p4-02w-b-cold", args.top_k, args.page_size))
        for round_index in range(int(args.rounds)):
            started = time.perf_counter()
            response = server.client.post(
                "/api/v1/search",
                json=text_body(binding, f"p4-02w-b-{round_index:03d}", args.top_k, args.page_size),
                headers=server.headers,
            )
            search_ms = round((time.perf_counter() - started) * 1000, 1)
            hits = (
                [int(hit["asset_id"]) for hit in (response.json().get("hits") or [])]
                if response.status_code == 200
                else []
            )
            thumbnails = []
            first_thumb_ms = None
            for asset_id in hits[: int(args.first_screen)]:
                thumb_started = time.perf_counter()
                thumb = server.client.get(
                    f"/api/v1/assets/{asset_id}/thumbnail", headers=server.headers
                )
                thumb_ms = round((time.perf_counter() - thumb_started) * 1000, 1)
                if first_thumb_ms is None:
                    first_thumb_ms = thumb_ms
                thumbnails.append(
                    {
                        "asset_id": asset_id,
                        "status": int(thumb.status_code),
                        "ms": thumb_ms,
                        "bytes": len(thumb.content),
                    }
                )
            report["rounds"].append(
                {
                    "round": round_index,
                    "search_status": int(response.status_code),
                    "search_http_ms": search_ms,
                    "hits": len(hits),
                    "first_thumb_ms": first_thumb_ms,
                    "first_screen_http_complete_ms": round(
                        (time.perf_counter() - started) * 1000, 1
                    ),
                    "thumbnails": thumbnails,
                    "thumbnail_failures": [t for t in thumbnails if t["status"] != 200],
                }
            )

        report["fixture_after"] = read_fixture(root, binding)
    finally:
        server.stop()
    if report["rounds"]:
        report["diagnostic_stats"] = {
            "search_http_ms": sample_stats([float(r["search_http_ms"]) for r in report["rounds"]]),
            "first_thumb_ms": sample_stats(
                [float(r["first_thumb_ms"]) for r in report["rounds"] if r["first_thumb_ms"] is not None]
            ),
            "first_screen_http_complete_ms": sample_stats(
                [float(r["first_screen_http_complete_ms"]) for r in report["rounds"]]
            ),
        }
    return report, 0


# --------------------------------------------------------------------------- CLI
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="p4-02w", description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=REPO_ROOT / "tmp" / "p4-02w")
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--git-head", default="")
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--page-size", type=int, default=20)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--smoke", type=int, default=4)
    commands = parser.add_subparsers(dest="scenario")

    warm = commands.add_parser("warm-text", help="scenario A: warm plain-text search")
    warm.add_argument("--data-root", type=Path, required=True)
    warm.add_argument("--threshold-ms", type=float, default=5000.0)
    warm.set_defaults(func=scenario_warm_text)

    background = commands.add_parser("background", help="scenario D: real build + text search")
    background.add_argument("--data-root", type=Path, required=True)
    background.add_argument("--backlog-library", type=Path, required=True)
    background.add_argument("--backlog", type=int, default=12000)
    background.add_argument("--threshold-ms", type=float, default=8000.0)
    background.add_argument("--background-grace-s", type=float, default=1800.0)
    background.set_defaults(func=scenario_background)

    upload = commands.add_parser("upload", help="scenario C: upload image search")
    upload.add_argument("--data-root", type=Path, required=True)
    upload.add_argument("--image", type=Path, action="append", required=True)
    upload.add_argument("--threshold-ms", type=float, default=5000.0)
    upload.set_defaults(func=scenario_upload)

    stats = commands.add_parser("stats", help="recompute the stats of a written report")
    stats.add_argument("--input", type=Path, required=True)
    stats.set_defaults(func=scenario_stats)
    attribution = commands.add_parser("attribution", help="diagnostic component split of the text path")
    attribution.add_argument("--data-root", type=Path, required=True)
    attribution.add_argument("--rounds", type=int, default=5)
    attribution.set_defaults(func=scenario_attribution)

    first_screen = commands.add_parser("first-screen", help="scenario B: real first-screen diagnostic")
    first_screen.add_argument("--data-root", type=Path, required=True)
    first_screen.add_argument("--rounds", type=int, default=5)
    first_screen.add_argument("--first-screen", type=int, default=24)
    first_screen.add_argument("--threshold-ms", type=float, default=6000.0)
    first_screen.set_defaults(func=scenario_first_screen)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "func", None) is None:
        parser.print_help()
        return 2
    report, code = args.func(args)
    if hasattr(args, "scenario") and args.scenario != "stats":
        emit(args.output, report, _summary(report))
    return code


def _summary(report: dict[str, Any]) -> str:
    if "component_stats" in report:
        component = report["component_stats"]
        return (
            f"{report.get('scenario')}: diagnostic only "
            f"vector_p50={component.get('vector_ms', {}).get('p50')}ms "
            f"full_p50={component.get('full_service_ms', {}).get('p50')}ms"
        )
    stats = report.get("stats", {})
    return (
        f"{report.get('scenario')}: n={stats.get('n')} p50={stats.get('p50')} "
        f"p95={stats.get('p95')} max={stats.get('max')} "
        f"failures={report.get('failure_count')} verdict={report.get('verdict')}"
    )


if __name__ == "__main__":
    raise SystemExit(main())