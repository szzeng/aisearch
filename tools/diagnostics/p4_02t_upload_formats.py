"""P4-02T closure: a phone's own MPO and a rotated HEIC are usable upload queries.

Before this card the upload boundary answered 409 ``FORMAT_UNSUPPORTED`` for both families
while the grid and the semantic index already displayed and ranked them. This driver proves
the amended boundary end to end, with the real pinned model:

1. build a *synthetic* library (no real photo is read) holding a two-frame MPO, a JPEG twin
   of its first frame, a HEIC whose EXIF says orientation 6 and a TIFF;
2. index it through the **product** build command (``indexing.cli build``);
3. serve the production app and post each file to ``POST /api/v1/search/image``:
   the MPO must be accepted and rank its own first frame (it or the twin) first, the rotated
   HEIC must find itself, and the TIFF must keep its structured refusal.

This is diagnostic evidence, not an acceptance test: it says nothing about §12.2 latency.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tests"))

from httpx import ASGITransport, AsyncClient  # noqa: E402
from p2_search_fixtures import Clock, stamp  # noqa: E402
from p4_heic_fixtures import quadrants, write_exif_heic  # noqa: E402
from PIL import Image  # noqa: E402

from photo_search.api.settings import ApiSettings  # noqa: E402
from photo_search.app import create_app  # noqa: E402
from photo_search.ingestion.service import CatalogService  # noqa: E402
from photo_search.search import upload as upload_boundary  # noqa: E402
from photo_search.workers.query_encoder import resolve_query_encoder  # noqa: E402

SESSION_TOKEN = "p4-02t-session-token"
ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]", "testserver"})
ORIGIN = "http://127.0.0.1"
MANIFEST = REPO_ROOT / "src" / "photo_search" / "models" / "embedding" / "fg-clip2-base.json"
FIRST_FRAME = (220, 30, 30)
SECOND_FRAME = (30, 30, 220)
#: The three upload probes: file name -> expected outcome.
PROBES = ("phone-burst.jpg", "phone-portrait.heic", "raw-like.tif")


def _seed(work: Path) -> tuple[Path, dict[str, int]]:
    """Write the synthetic library and scan it with the production scanner."""
    root = work / "library"
    root.mkdir(parents=True, exist_ok=True)
    first = Image.new("RGB", (256, 192), FIRST_FRAME)
    second = Image.new("RGB", (256, 192), SECOND_FRAME)
    mpo = root / "phone-burst.jpg"
    first.save(mpo, format="MPO", save_all=True, append_images=[second])
    stamp(mpo)
    twin = root / "phone-burst-first-frame.jpg"
    first.save(twin, "JPEG", quality=92)
    stamp(twin)
    # Orientation 6 with no container transform: the shape that needs the EXIF restoration.
    write_exif_heic(
        root / "phone-portrait.heic",
        orientation=6,
        container_orientation=False,
        image=quadrants(256, 192),
    )
    tiff = root / "raw-like.tif"
    quadrants(64, 48).save(tiff, "TIFF")
    stamp(tiff)

    clock = Clock()
    service = CatalogService.open(
        data_root=work / "PhotoSearchData",
        clock=clock,
        lease_seconds=60,
        wall_clock_ms=clock,
        stability_sleep=clock.advance,
    )
    try:
        source = service.register_source(display_name="p4-02t-fixture", root_path=root)
        service.run_scan(int(source["source_id"]))
        assets: dict[str, int] = {}
        for row in service.catalog.read_all(
            "SELECT a.asset_id, l.relative_path FROM assets AS a "
            "JOIN file_locations AS l ON l.asset_id = a.asset_id ORDER BY l.location_id"
        ):
            name = str(row["relative_path"]).replace("\\", "/").rsplit("/", 1)[-1]
            assets.setdefault(name, int(row["asset_id"]))
    finally:
        service.close()
    return root, assets


def _build(args: argparse.Namespace, data_root: Path) -> dict[str, Any]:
    """Run the product build command (the same entry an operator uses)."""
    command = [
        sys.executable,
        "-m",
        "photo_search.indexing.cli",
        "build",
        "--data-root",
        str(data_root),
        "--model-dir",
        str(args.model_dir),
        "--manifest",
        str(args.manifest),
        "--device",
        str(args.device),
        "--json",
    ]
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    payload: dict[str, Any] = {}
    for line in reversed((completed.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = dict(json.loads(line))
            except ValueError:
                payload = {}
            break
    return {
        "exit": int(completed.returncode),
        "wall_ms": round((time.perf_counter() - started) * 1000, 1),
        "stdout_keys": sorted(payload),
        "targets": payload.get("targets"),
        "out_of_scope": payload.get("out_of_scope"),
        "generation": payload.get("generation"),
        "stderr_tail": (completed.stderr or "").strip()[-400:],
    }


async def _upload_probes(
    root: Path, args: argparse.Namespace, data_root: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    wiring = resolve_query_encoder(
        model_dir=args.model_dir, manifest_path=args.manifest, device=args.device
    )
    settings = ApiSettings(
        data_root=Path(data_root),
        bind_host="127.0.0.1",
        bind_port=8000,
        session_token=SESSION_TOKEN,
        allowed_hosts=ALLOWED_HOSTS,
    )
    app = create_app(settings=settings, encoder_factory=wiring.factory)
    results: dict[str, Any] = {}
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=ORIGIN, follow_redirects=False
        ) as client:
            session = await client.get("/api/v1/session", params={"token": SESSION_TOKEN})
            csrf = str(session.json()["csrf_token"])
            headers = {"X-CSRF-Token": csrf, "Origin": ORIGIN}
            for name in PROBES:
                payload = (root / name).read_bytes()
                started = time.perf_counter()
                response = await client.post(
                    "/api/v1/search/image",
                    files={"image": (name, payload, "application/octet-stream")},
                    data={
                        "request_id": f"p4-02t-{name}",
                        "contract_version": "p2-contracts.v1",
                    },
                    headers=headers,
                )
                entry: dict[str, Any] = {
                    "status": int(response.status_code),
                    "http_ms": round((time.perf_counter() - started) * 1000, 1),
                }
                if response.status_code == 200:
                    body = response.json()
                    hits = body.get("hits") or []
                    entry["top_asset_id"] = int(hits[0]["asset_id"]) if hits else None
                    entry["top5"] = [int(hit["asset_id"]) for hit in hits[:5]]
                    entry["vector_engaged"] = bool(
                        ((body.get("channels") or {}).get("vector") or {}).get("engaged")
                    )
                    entry["image_query"] = body.get("image_query")
                else:
                    error = response.json()
                    entry["code"] = error.get("code")
                    entry["reason"] = (error.get("details") or {}).get("reason")
                    entry["details"] = error.get("details")
                results[name] = entry
    finally:
        app.state.api.close()
    return wiring.to_dict(), results


def run(args: argparse.Namespace) -> int:
    work = Path(args.work_dir)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "model_dir": str(args.model_dir),
        "manifest": str(args.manifest),
        "device": args.device,
        "upload_whitelist": sorted(upload_boundary.UPLOAD_INPUT_FORMATS),
    }
    service_root, assets = _seed(work)
    data_root = work / "PhotoSearchData"
    report["fixture"] = {"library": str(service_root), "assets": assets}
    report["build"] = _build(args, data_root)
    wiring, results = asyncio.run(_upload_probes(service_root, args, data_root))
    report["wiring"] = wiring
    report["uploads"] = results

    mpo = results["phone-burst.jpg"]
    heic = results["phone-portrait.heic"]
    tiff = results["raw-like.tif"]
    first_frame_assets = {
        assets.get("phone-burst.jpg"),
        assets.get("phone-burst-first-frame.jpg"),
    }
    top5 = mpo.get("top5") or []
    checks = {
        "build_succeeded": report["build"]["exit"] == 0,
        "build_confirmed_every_target": (report["build"]["generation"] or {}).get("entries")
        == {"CONFIRMED": 3},
        "mpo_upload_is_accepted": mpo.get("status") == 200,
        "mpo_uses_the_vector_channel": bool(mpo.get("vector_engaged")),
        "mpo_finds_its_own_first_frame": bool(top5) and top5[0] in first_frame_assets,
        "mpo_first_frame_pair_is_top2": len(set(top5[:2]) & first_frame_assets) == 2,
        "heic_upload_is_accepted": heic.get("status") == 200,
        "heic_finds_itself": heic.get("top_asset_id") == assets.get("phone-portrait.heic"),
        "tiff_upload_keeps_the_structured_refusal": tiff.get("status") == 409
        and tiff.get("code") == "FORMAT_UNSUPPORTED",
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
    parser.add_argument("--work-dir", type=Path, default=REPO_ROOT / "tmp" / "p4-02t-upload")
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())