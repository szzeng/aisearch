"""P4-02Y §10/§11: the library-refresh acceptance at the 30万 scale.

The 300 000-asset wave fixture (``tmp/p402f-阶段0/scale-300000-wave``) is used **read-only**:
the tool copies its ``data/`` directory into a working directory and never touches the
fixture, its library or its vectors. The source root the copied catalog already registers
*is* the fixture library, which is exactly the point - a fast refresh must re-enumerate those
300 000 positions without opening a single original file again.

Two modes:

``noop``   §11-A. Point the product's own HTTP entry at the copied data root, press refresh
           once and measure: total wall, hash targets/bytes, the delta counts, every
           derivation stage's real targets, how many originals were actually opened for
           hashing or decoding, and how many legal searches were refused while the refresh
           was writing.
``delta``  §11-B. Establish a second source with a few hundred real photos, then apply a
           day's changes (new files, renames, deletions) and measure the refresh with the
           real pinned model: how many files were hashed, how much AI work was queued, the
           start -> SEARCH_READY wall time, and whether a real HTTP text search already finds
           the day's new content at that moment.

Everything here is evidence generation: no product code path is simulated, and the numbers
are read from the production status document the API serves.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE = REPO_ROOT / "tmp" / "p402f-阶段0" / "scale-300000-wave"
MANIFEST = REPO_ROOT / "tools" / "prepare_models" / "manifests" / "chinese-clip-vit-b-16.json"
DEFAULT_MODEL_DIR = Path("D:/SZ/project/PhotoSearchData/models/chinese_clip/vit-b-16")
SESSION_TOKEN = "p4-02y-scale-token"
HOST = "127.0.0.1"
PORT = {"value": 8377}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("noop", "delta"), default="noop")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--work", type=Path, required=True, help="working directory (copied data)")
    parser.add_argument("--reuse-work", action="store_true", help="keep an existing work copy")
    parser.add_argument(
        "--with-artifacts",
        action="store_true",
        help="also copy the fixture's 300 000 artifact files (not needed by either mode)",
    )
    parser.add_argument(
        "--repair-revisions",
        action="store_true",
        help=(
            "make the working copy internally consistent: the wave fixture published its "
            "embeddings with synthetic artifact input revisions, so the frozen "
            "'CONFIRMED entry of the asset's current sha256' selector considers all 300 000 "
            "assets pending. This rewrites artifacts.input_revision to the asset's sha256 "
            "in the copy only."
        ),
    )
    parser.add_argument("--batch", type=int, default=128, help="observation batch (128 or 256)")
    parser.add_argument("--port", type=int, default=8377)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--photos", type=int, default=300, help="delta: genuinely new photos")
    parser.add_argument("--duplicates", type=int, default=10)
    parser.add_argument("--renames", type=int, default=10)
    parser.add_argument("--deletes", type=int, default=10)
    parser.add_argument("--search-text", default="照片")
    parser.add_argument(
        "--probe-text",
        default="day-00000",
        help="delta mode: the text whose search must already return the day's new photo",
    )
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def base_url() -> str:
    return f"http://{HOST}:{int(PORT['value'])}"


# ------------------------------------------------------------------------ fixtures
def prepare_work(
    fixture: Path, work: Path, *, reuse: bool, with_artifacts: bool = False
) -> dict[str, Any]:
    """Copy the fixture's data directory; the registered library stays where it is.

    The wave fixture keeps one JSON artifact file per embedding (300 000 tiny files under
    ``artifacts/pending``). Nothing in either measurement reads an *old* artifact - the
    refresh publishes new ones and the search path reads the vector table - so the artifact
    tree is created empty unless ``with_artifacts`` is asked for. That keeps the copy to the
    catalog and the vector dataset, both of which are a handful of plain files.
    """
    source = Path(fixture) / "data"
    target = Path(work) / "data"
    if reuse and target.is_dir():
        return {"copied": False, "data_root": str(target)}
    if target.exists():
        shutil.rmtree(target)
    started = time.perf_counter()
    target.mkdir(parents=True, exist_ok=True)
    for entry in sorted(source.iterdir()):
        destination = target / entry.name
        if entry.name == "artifacts" and not with_artifacts:
            destination.mkdir(parents=True, exist_ok=True)
            continue
        if entry.is_dir():
            shutil.copytree(entry, destination)
        else:
            shutil.copyfile(entry, destination)
    return {
        "copied": True,
        "copy_ms": int(round((time.perf_counter() - started) * 1000)),
        "data_root": str(target),
    }


def catalog_digest(db: Path) -> dict[str, Any]:
    """A full-row digest of assets and locations: any change to either table shows up."""
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        assets = connection.execute(
            "SELECT asset_id, sha256, byte_size, state FROM assets ORDER BY asset_id"
        ).fetchall()
        locations = connection.execute(
            "SELECT location_id, source_id, relative_path, path_key, asset_id, size, mtime_ns, "
            "availability FROM file_locations ORDER BY location_id"
        ).fetchall()
        queue = connection.execute("SELECT COUNT(*) FROM search_projection_queue").fetchone()[0]
    finally:
        connection.close()
    digest = hashlib.sha256()
    for row in (*assets, *locations):
        digest.update(repr(row).encode("utf-8"))
    return {
        "assets": len(assets),
        "locations": len(locations),
        "projection_queue": int(queue),
        "digest": digest.hexdigest(),
    }


def explicit_binding(db: Path) -> dict[str, Any]:
    """The P2-06A binding a request needs while no search profile is activated.

    The wave fixture ships an active generation but no profile, and the frozen contract then
    requires the caller to name the space, the generation and the dimension explicitly - a
    search that omits them is a caller error (422), not a legal request.
    """
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT space_id, generation_id, dimension FROM index_generations "
            "WHERE is_active = 1 ORDER BY generation_id LIMIT 1"
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return {}
    return {
        "space_id": str(row[0]),
        "image_generation_id": str(row[1]),
        "dimension": int(row[2]),
    }


def read_scalar(db: Path, sql: str, parameters: tuple = ()) -> Any:
    """One read-only scalar from the copied catalog (the tool's own thread)."""
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = connection.execute(sql, parameters).fetchone()
    finally:
        connection.close()
    return None if row is None else row[0]


def write_photos(directory: Path, count: int, *, start: int, size: int = 96) -> list[Path]:
    """Deterministic real JPEGs; distinct pixels keep every content hash distinct."""
    directory.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    moment = (1_700_000_000_000 - 3_600_000) * 1_000_000
    for index in range(start, start + count):
        path = directory / f"day-{index:05d}.jpg"
        image = Image.new("RGB", (size, size), (index % 251, (index * 7) % 251, (index * 13) % 251))
        pixels = image.load()
        for x in range(0, size, 4):
            for y in range(0, size, 4):
                pixels[x, y] = ((index + x) % 256, (index * 3 + y) % 256, (index * 7) % 256)
        image.save(path, "JPEG", quality=92)
        os.utime(path, ns=(moment, moment))
        created.append(path)
    return created


def repair_artifact_revisions(data_root: Path) -> dict[str, Any]:
    """Point every confirmed artifact at the current revision of the asset that owns it.

    Only the working copy is touched. This is a *fixture* repair, not a product behaviour:
    the P4-02F capacity tooling published the wave fixture's vectors under synthetic input
    revisions, which the frozen P4-02O selector does not accept as "the current content".
    """
    db = Path(data_root) / "catalog" / "catalog.db"
    connection = sqlite3.connect(str(db))
    try:
        # One linear pass: the per-artifact correlated form turns into 300 000 nested lookups
        # and is unusably slow on this fixture.
        pairs = connection.execute(
            "SELECT e.artifact_id, s.sha256 FROM index_entries e "
            "JOIN assets s ON s.asset_id = e.asset_id WHERE e.state = 'CONFIRMED'"
        ).fetchall()
        stale = connection.execute(
            "SELECT COUNT(*) FROM index_entries e JOIN assets s ON s.asset_id = e.asset_id "
            "JOIN artifacts a ON a.artifact_id = e.artifact_id "
            "WHERE e.state = 'CONFIRMED' AND a.input_revision <> s.sha256"
        ).fetchone()[0]
        connection.executemany(
            "UPDATE artifacts SET input_revision = ? WHERE artifact_id = ?",
            [(str(sha), str(artifact_id)) for artifact_id, sha in pairs],
        )
        connection.commit()
        after = connection.execute(
            "SELECT COUNT(*) FROM index_entries e JOIN assets s ON s.asset_id = e.asset_id "
            "JOIN artifacts a ON a.artifact_id = e.artifact_id "
            "WHERE e.state = 'CONFIRMED' AND a.input_revision <> s.sha256"
        ).fetchone()[0]
    finally:
        connection.close()
    return {"confirmed_entries": len(pairs), "stale_before": int(stale), "stale_after": int(after)}


def _pick(library: Path, index: int) -> Path:
    """One existing original of the fixture library, chosen deterministically (read-only)."""
    for bucket in sorted(library.iterdir()):
        if bucket.is_dir():
            entries = sorted(bucket.iterdir())
            if index < len(entries):
                return entries[index]
    raise RuntimeError("the fixture library has no readable originals")


# --------------------------------------------------------------------------- server
def start_server(data_root: Path, args: argparse.Namespace):
    import uvicorn

    from photo_search.api.settings import ApiSettings
    from photo_search.app import create_app
    from photo_search.workers.query_encoder import resolve_query_encoder

    wiring = resolve_query_encoder(
        model_dir=args.model_dir,
        manifest_path=args.manifest,
        device=None if args.device in ("auto", "") else args.device,
    )
    settings = ApiSettings(
        data_root=data_root,
        bind_host=HOST,
        bind_port=args.port,
        session_token=SESSION_TOKEN,
        allowed_hosts=frozenset({HOST, "localhost"}),
    )
    app = create_app(
        settings=settings,
        encoder_factory=wiring.factory,
        refresh_manifest_path=str(args.manifest) if wiring.available else None,
        refresh_batch_size=args.batch,
    )
    config = uvicorn.Config(app, host=HOST, port=args.port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True, name="p4-02y-uvicorn")
    thread.start()
    deadline = time.monotonic() + 120
    while not getattr(server, "started", False) and time.monotonic() < deadline:
        time.sleep(0.05)
    if not getattr(server, "started", False):
        raise RuntimeError("the local API did not start")
    return server, thread, app, wiring


def session_client() -> tuple[httpx.Client, str]:
    client = httpx.Client(base_url=base_url(), timeout=600.0)
    response = client.get("/api/v1/session", params={"token": SESSION_TOKEN})
    response.raise_for_status()
    return client, str(response.json()["csrf_token"])


def post_headers(csrf: str) -> dict[str, str]:
    return {"X-CSRF-Token": csrf, "Origin": base_url()}


# ---------------------------------------------------------------------- measuring
class ReadCounter:
    """Count the original files this process really opened, by wrapping the two entries."""

    def __init__(self) -> None:
        self.hash_calls: list[str] = []
        self.decode_calls: list[str] = []
        self._restore: list[tuple[Any, str, Any]] = []

    def install(self) -> None:
        from photo_search.ingestion import scanner
        from photo_search.media import decode

        real_hash = scanner.sha256_file
        real_decode = decode.load_normalized

        def counting_hash(path: Path) -> str:
            self.hash_calls.append(str(path))
            return real_hash(path)

        def counting_decode(path: Path, *args: Any, **kwargs: Any) -> Any:
            self.decode_calls.append(str(path))
            return real_decode(path, *args, **kwargs)

        scanner.sha256_file = counting_hash
        decode.load_normalized = counting_decode
        self._restore = [
            (scanner, "sha256_file", real_hash),
            (decode, "load_normalized", real_decode),
        ]

    def remove(self) -> None:
        for module, name, value in self._restore:
            setattr(module, name, value)


def refresh_through_api(
    client: httpx.Client, csrf: str, *, source_ids: list[int] | None, poll_ms: int = 250
) -> dict[str, Any]:
    """POST the product entry and poll its read-only status until it is terminal."""
    body: dict[str, Any] = {} if source_ids is None else {"source_ids": source_ids}
    started = time.perf_counter()
    accepted = client.post("/api/v1/library/refresh", json=body, headers=post_headers(csrf))
    accepted.raise_for_status()
    first = accepted.json()
    search_ready_ms: int | None = None
    while True:
        status = client.get("/api/v1/library/refresh").json()
        elapsed = int(round((time.perf_counter() - started) * 1000))
        if search_ready_ms is None and status.get("search_ready"):
            search_ready_ms = elapsed
        if status.get("state") in ("COMPLETE", "PARTIAL_FAILED", "FAILED"):
            break
        time.sleep(poll_ms / 1000.0)
    status["refresh_wall_ms"] = int(round((time.perf_counter() - started) * 1000))
    status["search_ready_wall_ms"] = search_ready_ms
    status["accepted"] = bool(first.get("accepted"))
    return status


def concurrent_searches(
    stop: threading.Event,
    *,
    text: str,
    results: dict[str, Any],
    csrf: str,
    cookies: dict[str, str],
    binding: dict[str, Any] | None = None,
) -> threading.Thread:
    """One client issuing a legal search every ~0.5 s while the refresh writes."""

    def loop() -> None:
        client = httpx.Client(base_url=base_url(), timeout=600.0)
        client.cookies.update(cookies)
        try:
            while not stop.is_set():
                started = time.perf_counter()
                try:
                    reply = client.post(
                        "/api/v1/search",
                        json={
                            "request_id": f"p4-02y-{len(results['samples'])}",
                            "text": text,
                            **(binding or {}),
                        },
                        headers=post_headers(csrf),
                    )
                    body: dict[str, Any] = {}
                    if "json" in reply.headers.get("content-type", ""):
                        body = reply.json()
                    results["samples"].append(
                        {
                            "status": reply.status_code,
                            "state": body.get("state"),
                            "elapsed_ms": int(round((time.perf_counter() - started) * 1000)),
                        }
                    )
                except Exception as error:  # noqa: BLE001 - a transport error is a refusal too
                    results["samples"].append(
                        {
                            "status": None,
                            "error": type(error).__name__,
                            "elapsed_ms": int(round((time.perf_counter() - started) * 1000)),
                        }
                    )
                stop.wait(0.5)
        finally:
            client.close()

    thread = threading.Thread(target=loop, daemon=True, name="p4-02y-search")
    thread.start()
    return thread


def warm_search(
    client: httpx.Client, csrf: str, *, text: str, binding: dict[str, Any] | None = None
) -> dict[str, Any]:
    """One search before the measurement: it proves the path serves and warms the worker."""
    started = time.perf_counter()
    reply = client.post(
        "/api/v1/search",
        json={"request_id": "p4-02y-warm", "text": text, "page_size": 5, **(binding or {})},
        headers=post_headers(csrf),
    )
    return {
        "status": reply.status_code,
        "elapsed_ms": int(round((time.perf_counter() - started) * 1000)),
        "state": reply.json().get("state") if reply.status_code == 200 else None,
    }


def search_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "issued": len(samples),
        "refused": [item for item in samples if int(item.get("status") or 0) != 200],
        "states": sorted({str(item.get("state")) for item in samples if item.get("state")}),
        "samples": samples,
    }


# --------------------------------------------------------------------------- modes
def run_noop(args: argparse.Namespace, data_root: Path) -> dict[str, Any]:
    server, _, app, wiring = start_server(data_root, args)
    counter = ReadCounter()
    counter.install()
    stop = threading.Event()
    samples: dict[str, Any] = {"samples": []}
    searcher = None
    try:
        client, csrf = session_client()
        db = data_root / "catalog" / "catalog.db"
        before = catalog_digest(db)
        binding = explicit_binding(db)
        warmed = warm_search(client, csrf, text=args.search_text, binding=binding)
        searcher = concurrent_searches(
            stop,
            text=args.search_text,
            results=samples,
            csrf=csrf,
            cookies=dict(client.cookies),
            binding=binding,
        )
        status = refresh_through_api(client, csrf, source_ids=None)
        stop.set()
        searcher.join(timeout=120)
        after = catalog_digest(db)
        result = {
            "mode": "noop",
            "batch": args.batch,
            "encoder_available": bool(wiring.available),
            "status": status,
            "originals_hashed": len(counter.hash_calls),
            "originals_decoded": len(counter.decode_calls),
            "catalog_before": before,
            "catalog_after": after,
            "catalog_identical": before == after,
            "warm_search": warmed,
            "search_binding": binding,
            "searches": search_summary(samples["samples"]),
        }
        client.close()
    finally:
        stop.set()
        if searcher is not None:
            searcher.join(timeout=30)
        counter.remove()
        app.state.api.close()
        server.should_exit = True
    return result


def run_delta(args: argparse.Namespace, data_root: Path, work: Path) -> dict[str, Any]:
    server, _, app, wiring = start_server(data_root, args)
    counter = ReadCounter()
    stop = threading.Event()
    samples: dict[str, Any] = {"samples": []}
    searcher = None
    second = work / "day-library"
    try:
        client, csrf = session_client()
        if second.exists():
            # A previous run of this mode leaves its own day source behind; the copied
            # catalog is fresh, so the second source must start empty instead of inheriting
            # already renamed/deleted files.
            shutil.rmtree(second)
        registered = client.post(
            "/api/v1/sources",
            json={"display_name": "day", "root_path": str(second)},
            headers=post_headers(csrf),
        )
        registered.raise_for_status()
        second_source_id = int(registered.json()["source_id"])
        db = data_root / "catalog" / "catalog.db"
        fixture_library = Path(
            str(read_scalar(db, "SELECT root_path FROM source_roots WHERE source_id = 1"))
        )
        for index in range(args.duplicates):
            source = _pick(fixture_library, index)
            target = second / "duplicates" / f"copy-{index:04d}.jpg"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        steady = write_photos(second / "steady", args.renames + args.deletes, start=90_000)
        counter.install()
        established = refresh_through_api(client, csrf, source_ids=[second_source_id])
        counter.hash_calls.clear()
        counter.decode_calls.clear()

        # The day's change: ten renames, ten deletions and the new photos. The new photos are
        # written only now, so the measured refresh really sees them as new content.
        for index in range(args.renames):
            os.rename(steady[index], steady[index].with_name(f"moved-{index:04d}.jpg"))
        for index in range(args.renames, args.renames + args.deletes):
            os.unlink(steady[index])
        write_photos(second / "new", args.photos, start=0)

        binding = explicit_binding(data_root / "catalog" / "catalog.db")
        warmed = warm_search(client, csrf, text=args.search_text, binding=binding)
        searcher = concurrent_searches(
            stop,
            text=args.search_text,
            results=samples,
            csrf=csrf,
            cookies=dict(client.cookies),
            binding=binding,
        )
        measured = refresh_through_api(client, csrf, source_ids=None)
        stop.set()
        searcher.join(timeout=120)

        probe = second / "new" / "day-00000.jpg"
        probe_asset = read_scalar(
            db,
            "SELECT asset_id FROM file_locations WHERE relative_path LIKE ?",
            ("%day-00000.jpg",),
        )
        found: dict[str, Any] = {
            "probe_file": probe.name,
            "probe_text": args.probe_text,
            "probe_asset": None if probe_asset is None else int(probe_asset),
        }
        search = client.post(
            "/api/v1/search",
            json={
                "request_id": "p4-02y-delta-probe",
                # The search path a user has: the day's photo must already be findable by its
                # own name at the moment the refresh says SEARCH_READY.
                "text": args.probe_text,
                "page_size": 20,
                "space_id": binding["space_id"],
                "image_generation_id": binding["image_generation_id"],
                "dimension": binding["dimension"],
            },
            headers=post_headers(csrf),
        )
        if search.status_code == 200:
            body = search.json()
            found.update(
                state=str(body.get("state")),
                hits=len(body.get("hits", [])),
                searchable=[int(item["asset_id"]) for item in body.get("hits", [])],
            )
        else:
            found.update(status_code=search.status_code, body=search.text[:200])
        result = {
            "mode": "delta",
            "batch": args.batch,
            "encoder_available": bool(wiring.available),
            "established": established,
            "measured": measured,
            "originals_hashed": len(counter.hash_calls),
            "originals_decoded": len(counter.decode_calls),
            "hashed_sample": [Path(item).name for item in counter.hash_calls[:20]],
            "warm_search": warmed,
            "search": found,
            "searches": search_summary(samples["samples"]),
        }
        client.close()
    finally:
        stop.set()
        if searcher is not None:
            searcher.join(timeout=30)
        counter.remove()
        app.state.api.close()
        server.should_exit = True
    return result


def main() -> int:
    args = parse_args()
    PORT["value"] = int(args.port)
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    prepared = prepare_work(
        args.fixture, work, reuse=bool(args.reuse_work), with_artifacts=bool(args.with_artifacts)
    )
    data_root = Path(prepared["data_root"])
    started = time.perf_counter()
    if args.repair_revisions:
        prepared["revision_repair"] = repair_artifact_revisions(data_root)
    if args.mode == "noop":
        result = run_noop(args, data_root)
    else:
        result = run_delta(args, data_root, work)
    result["work"] = prepared
    result["wall_ms"] = int(round((time.perf_counter() - started) * 1000))
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output is not None:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(payload + "\n", encoding="utf-8", newline="\n")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
