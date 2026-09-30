"""P4-02N closure: the pinned FG-CLIP 2 model, a real index and a real HTTP text search.

What this driver proves, in **one process** and with the real weights:

1. the application is built by the production wiring (``resolve_query_encoder`` from
   ``photo_search.__main__`` / ``photo_search.workers.query_encoder``) - the same seam a real
   run uses, not a test-injected stand-in;
2. ``GET /api/v1/capabilities`` reports ``query_encoder_available=true``;
3. three synthetic photos are indexed with vectors the *real image tower* produced, published
   through the production artifact/outbox path and confirmed by the production coordinator;
4. ``POST /api/v1/search`` with a Chinese text query answers ``200`` (not the old structured
   409), engages the vector channel and ranks the semantically matching photo first.

The library is synthetic (solid red/green/blue images, the same fixture shape
``demo-search`` uses) and lives in a throw-away project-local directory: no real photo
library is read and nothing outside ``--work-dir`` is written. The frozen P2 fixture glue is
imported from ``tests/`` on purpose, so the published vectors go through the same production
outbox path the P2/P4 tests use and this driver cannot invent an index shape the product does
not have. It is diagnostic evidence, not an acceptance test: it says nothing about §12.2
latency targets or retrieval quality.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tests"))

from httpx import ASGITransport, AsyncClient  # noqa: E402
from p2_search_fixtures import Clock, confirm_pending, submit_vector  # noqa: E402
from PIL import Image  # noqa: E402

from photo_search.api.settings import ApiSettings  # noqa: E402
from photo_search.app import create_app  # noqa: E402
from photo_search.artifacts.store import generation_id_for  # noqa: E402
from photo_search.ingestion.service import CatalogService  # noqa: E402
from photo_search.scheduler import engine  # noqa: E402
from photo_search.scheduler.contracts import RuntimeCapabilities  # noqa: E402
from photo_search.workers.query_encoder import (  # noqa: E402
    build_worker_query_encoder,
    resolve_query_encoder,
)

#: The same fixture colours ``demo-search`` uses, so the expected ranking is a known fact.
IMAGES = {"red": (220, 40, 40), "green": (40, 180, 70), "blue": (45, 80, 210)}
QUERY_TEXT = "红色的图片"
EXPECTED_TOP = "red.png"
SESSION_TOKEN = "p4-02n-session-token"
ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]", "testserver"})
PROVIDER_BY_DEVICE = {"gpu": "torch-cuda", "cpu": "torch-cpu"}
ORIGIN = "http://127.0.0.1"


def _ms_since(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def _seed_library(work: Path) -> tuple[Any, Path, dict[str, int]]:
    """Write the three fixture photos and scan them with the production scanner."""
    root = work / "library"
    root.mkdir(parents=True, exist_ok=True)
    for name, colour in IMAGES.items():
        Image.new("RGB", (256, 192), colour).save(root / f"{name}.png", "PNG")
    clock = Clock()
    service = CatalogService.open(
        data_root=work / "PhotoSearchData",
        clock=clock,
        lease_seconds=60,
        wall_clock_ms=clock,
        stability_sleep=clock.advance,
    )
    source = service.register_source(display_name="p4-02n-fixture", root_path=root)
    service.run_scan(int(source["source_id"]))
    assets = {
        str(row["relative_path"]).replace("\\", "/").rsplit("/", 1)[-1]: int(row["asset_id"])
        for row in service.catalog.read_all(
            "SELECT asset_id, relative_path FROM file_locations ORDER BY location_id"
        )
    }
    return service, root, assets


def _record_evidence(service: Any, spec: Any) -> None:
    """Record the capability snapshot and qualification rows of the *live* worker facts."""
    package = str(spec.runtime_package_id)
    package_name, _, version = package.partition("@")
    devices = [str(item) for item in spec.envelope["supported_devices"]]
    precision = str(spec.supported_precisions[0])
    providers = [
        {
            "name": PROVIDER_BY_DEVICE[device],
            "device": device,
            "precisions": [precision],
            "available": True,
            "health": "healthy",
            "health_checked_at": 1_000,
            "evidence_id": None,
        }
        for device in devices
        if device in PROVIDER_BY_DEVICE
    ]
    scheduler = engine.Scheduler(service.catalog, [spec])
    scheduler.record_capability(
        RuntimeCapabilities.seal(
            {
                "runtime_package_id": package,
                "runtime_backend": str(spec.runtime_backend),
                "runtime_package_name": package_name,
                "runtime_package_version": version,
                "python_version": "3.12.10",
                "platform": "windows-x86_64",
                "driver_version": None,
                "providers": providers,
                "health_status": "healthy",
                "health_checked_at": 1_000,
                "measured_at": 1_000,
                "evidence_ids": [],
                "failure_code": None,
            }
        ),
        now_ms=1_000,
    )
    for device in devices:
        scheduler.record_qualification(
            processor_id=str(spec.processor_id),
            spec_revision=str(spec.spec_revision),
            device=device,
            precision=precision,
            provider_name=PROVIDER_BY_DEVICE[device],
            runtime_package_id=package,
            now_ms=1_000,
        )


def _publish_vectors(
    service: Any, spec: Any, root: Path, assets: dict[str, int], encoder: Any
) -> dict[str, Any]:
    """Encode every photo with the real image tower and publish its vector."""
    scheduler = engine.Scheduler(service.catalog, [spec])
    encode_ms: dict[str, float] = {}
    for name, asset_id in sorted(assets.items()):
        started = time.perf_counter()
        with Image.open(root / name) as image:
            vector = encoder.encode_images([image.convert("RGB")])[0]
        encode_ms[name] = _ms_since(started)
        submit_vector(
            service.catalog,
            scheduler,
            spec,
            asset_id=int(asset_id),
            vector=vector,
            artifact_id=f"p4-02n-{name}",
        )
    confirmed = confirm_pending(service.catalog)
    generation = generation_id_for(
        str(spec.envelope["space_id"]), str(spec.processor_id)
    )
    row = service.catalog.read_one(
        "SELECT state, dimension, space_id, processor_id FROM index_generations "
        "WHERE generation_id = ?",
        (generation,),
    )
    entries = int(
        service.catalog.read_one(
            "SELECT COUNT(*) AS value FROM index_entries WHERE generation_id = ? "
            "AND state = 'CONFIRMED'",
            (generation,),
        )["value"]
    )
    return {
        "generation_id": generation,
        "generation_state": None if row is None else str(row["state"]),
        "confirmed_entries": entries,
        "confirmed_outbox_events": int(confirmed),
        "image_encode_ms": encode_ms,
    }


async def _search(app: Any, spec: Any, generation: str) -> dict[str, Any]:
    """One authenticated capabilities read plus a cold and a warm text search."""
    body = {
        "request_id": "p4-02n-real",
        "text": QUERY_TEXT,
        "space_id": str(spec.envelope["space_id"]),
        "image_generation_id": generation,
        "dimension": int(spec.envelope["dimension"]),
    }
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=ORIGIN, follow_redirects=False
    ) as client:
        session = await client.get("/api/v1/session", params={"token": SESSION_TOKEN})
        assert session.status_code == 200, session.text
        csrf = str(session.json()["csrf_token"])
        headers = {"X-CSRF-Token": csrf, "Origin": ORIGIN}
        # The first request that resolves the encoder pays the worker's cold start: for a
        # real run that is whichever request arrives first, here this capabilities read.
        capabilities_started = time.perf_counter()
        capabilities = await client.get("/api/v1/capabilities")
        capabilities_ms = _ms_since(capabilities_started)
        cold_started = time.perf_counter()
        cold = await client.post("/api/v1/search", json=body, headers=headers)
        cold_ms = _ms_since(cold_started)
        warm_started = time.perf_counter()
        warm = await client.post("/api/v1/search", json=body, headers=headers)
        warm_ms = _ms_since(warm_started)
    return {
        "session_status": int(session.status_code),
        "capabilities": (
            capabilities.json().get("search") if capabilities.status_code == 200 else None
        ),
        "capabilities_http_ms": capabilities_ms,
        "cold_search": {"status": int(cold.status_code), "http_ms": cold_ms},
        "warm_search": {"status": int(warm.status_code), "http_ms": warm_ms},
        "body": warm.json() if warm.status_code == 200 else {"text": warm.text[:400]},
    }


def run(args: argparse.Namespace) -> int:
    work = Path(args.work_dir)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "model_dir": str(args.model_dir),
        "manifest": str(args.manifest),
        "device": args.device,
        "query_text": QUERY_TEXT,
    }
    service = None
    index_encoder = None
    try:
        service, root, assets = _seed_library(work)
        report["fixture"] = {"library": str(root), "assets": assets}
        index_encoder = build_worker_query_encoder(
            model_dir=args.model_dir, manifest_path=args.manifest, device=args.device
        )
        spec = index_encoder.spec()
        report["index_spec"] = {
            "processor_id": str(spec.processor_id),
            "space_id": str(spec.envelope["space_id"]),
            "dimension": int(spec.envelope["dimension"]),
            "runtime_package_id": str(spec.runtime_package_id),
            "supported_devices": list(spec.envelope["supported_devices"]),
        }
        _record_evidence(service, spec)
        report["index"] = _publish_vectors(service, spec, root, assets, index_encoder)

        wiring = resolve_query_encoder(
            model_dir=args.model_dir, manifest_path=args.manifest, device=args.device
        )
        report["wiring"] = wiring.to_dict()
        settings = ApiSettings(
            data_root=Path(service.data_root),
            bind_host="127.0.0.1",
            bind_port=8000,
            session_token=SESSION_TOKEN,
            allowed_hosts=ALLOWED_HOSTS,
        )
        app = create_app(settings=settings, encoder_factory=wiring.factory)
        result = asyncio.run(_search(app, spec, report["index"]["generation_id"]))
        report.update(result)
        encoder = app.state.api.query_encoder()
        if encoder is not None:
            started = time.perf_counter()
            vector = encoder.encode_queries([QUERY_TEXT])[0]
            report["query_encode_ms"] = _ms_since(started)
            report["query_vector_dimension"] = len(vector)
        app.state.api.close()
    finally:
        if index_encoder is not None:
            index_encoder.close()
        if service is not None:
            service.close()

    body = report.get("body") or {}
    hits = body.get("hits") or []
    channels = body.get("channels") or {}
    by_id = {int(value): name for name, value in (report["fixture"]["assets"] or {}).items()}
    top = by_id.get(int(hits[0]["asset_id"])) if hits else None
    report["top_hit_name"] = top
    checks = {
        "wiring_available": bool((report.get("wiring") or {}).get("available")),
        "capability_reports_encoder": bool(
            (report.get("capabilities") or {}).get("query_encoder_available")
        ),
        "generation_ready": report["index"]["generation_state"] == "READY",
        "all_assets_confirmed": report["index"]["confirmed_entries"] == len(assets),
        "search_answers_200": report.get("warm_search", {}).get("status") == 200,
        "vector_channel_engaged": bool((channels.get("vector") or {}).get("engaged")),
        "top_hit_is_the_red_image": top == EXPECTED_TOP,
    }
    report["checks"] = checks
    report["passed"] = all(checks.values())
    report["status"] = "pass" if report["passed"] else "fail"
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is not None:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)
    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)
    return 0 if report["passed"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--work-dir", type=Path, default=REPO_ROOT / "tmp" / "p4-02n-real"
    )
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=REPO_ROOT / "src" / "photo_search" / "models" / "embedding" / "fg-clip2-base.json",
    )
    parser.add_argument(
        "--device", default="cuda", help="cuda (default), cpu, or auto for the ADR-0001 policy"
    )
    parser.add_argument("--keep", action="store_true", help="keep the throw-away fixture root")
    parser.add_argument("--output", type=Path, default=None)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
