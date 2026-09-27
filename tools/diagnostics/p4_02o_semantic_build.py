"""P4-02O closure: the production ``indexing.cli build`` entry with the real pinned model.

What this driver proves, in one run and with the real weights:

1. a synthetic library is scanned by the production scanner and the **product command**
   ``python -m photo_search.indexing.cli build`` turns every photo into an embedding, confirms
   the outbox and activates the search profile of the built generation;
2. a second ``build --dry-run`` finds nothing left to do (incremental by content revision);
3. the real application (built through ``resolve_query_encoder``, the same seam a real run
   uses) answers a Chinese text query **without any explicit binding** - the activated profile
   supplies it - and ranks the semantically matching photos first.

The library is synthetic (colour families, the same fixture shape ``demo-search`` uses) and
lives in a throw-away project-local directory: no real photo library is read. This is
diagnostic evidence, not §12.2 acceptance: it says nothing about latency targets, GTX 1660 or
retrieval quality on real photos.
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
#: The frozen P2 fixture clock (it drives the scanner in tests): reused so the scan this
#: driver performs is the same production scan the tests use.
sys.path.insert(0, str(REPO_ROOT / "tests"))

from httpx import ASGITransport, AsyncClient  # noqa: E402
from PIL import Image  # noqa: E402

from photo_search.api.settings import ApiSettings  # noqa: E402
from photo_search.app import create_app  # noqa: E402
from photo_search.ingestion.service import CatalogService  # noqa: E402
from photo_search.workers.query_encoder import resolve_query_encoder  # noqa: E402

QUERY_TEXT = "红色的图片"
SESSION_TOKEN = "p4-02o-session-token"
ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]", "testserver"})
ORIGIN = "http://127.0.0.1"
#: Three colour families; the query names the red one, so the expectation is a *set*: every
#: photo of the matching family must come before the others.
FAMILIES = {
    "red": ((220, 40, 40), (180, 60, 50), (250, 90, 70)),
    "green": ((40, 180, 70), (60, 150, 60), (90, 200, 110)),
    "blue": ((45, 80, 210), (60, 110, 220), (90, 140, 240)),
}


def _ms_since(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def _seed_library(work: Path, per_family: int) -> tuple[Any, Path, dict[str, int]]:
    """Write the synthetic photos and scan them with the production scanner."""
    root = work / "library"
    root.mkdir(parents=True, exist_ok=True)
    for family, colours in FAMILIES.items():
        for index in range(int(per_family)):
            colour = colours[index % len(colours)]
            image = Image.new("RGB", (256, 192), colour)
            pixels = image.load()
            for x in range(0, 256, 4):
                for y in range(0, 192, 4):
                    pixels[x, y] = (
                        (colour[0] + x) % 256,
                        (colour[1] + y) % 256,
                        (colour[2] + index * 3) % 256,
                    )
            image.save(root / f"{family}-{index:02d}.png", "PNG")
    from p2_search_fixtures import Clock

    clock = Clock()
    service = CatalogService.open(
        data_root=work / "PhotoSearchData",
        clock=clock,
        lease_seconds=60,
        wall_clock_ms=clock,
        stability_sleep=clock.advance,
    )
    source = service.register_source(display_name="p4-02o-fixture", root_path=root)
    service.run_scan(int(source["source_id"]))
    assets = {
        str(row["relative_path"]).replace("\\", "/").rsplit("/", 1)[-1]: int(row["asset_id"])
        for row in service.catalog.read_all("SELECT asset_id, relative_path FROM file_locations")
    }
    return service, root, assets


def _run_cli(args: list[str]) -> tuple[int, dict[str, Any], float]:
    started = time.perf_counter()
    process = subprocess.run(
        [sys.executable, "-m", "photo_search.indexing.cli", *args, "--json"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=7200,
    )
    elapsed = _ms_since(started)
    payload: dict[str, Any] = {}
    for line in reversed((process.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            payload = json.loads(line)
            break
    if not payload:
        payload = {"stderr_tail": (process.stderr or "")[-400:]}
    return int(process.returncode), payload, elapsed


async def _search(app: Any) -> dict[str, Any]:
    body = {"request_id": "p4-02o", "text": QUERY_TEXT}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=ORIGIN, follow_redirects=False
    ) as client:
        session = await client.get("/api/v1/session", params={"token": SESSION_TOKEN})
        csrf = str(session.json()["csrf_token"])
        headers = {"X-CSRF-Token": csrf, "Origin": ORIGIN}
        capabilities = await client.get("/api/v1/capabilities")
        started = time.perf_counter()
        response = await client.post("/api/v1/search", json=body, headers=headers)
        http_ms = _ms_since(started)
    return {
        "capabilities": (
            capabilities.json().get("search") if capabilities.status_code == 200 else None
        ),
        "status": int(response.status_code),
        "http_ms": http_ms,
        "body": response.json() if response.status_code == 200 else {"text": response.text[:400]},
    }


def _family_order(ranked: list[str]) -> bool:
    """Red (the queried family) before green before blue, by mean rank."""
    positions: dict[str, list[int]] = {"red": [], "green": [], "blue": []}
    for index, name in enumerate(ranked, start=1):
        for family in positions:
            if name.startswith(f"{family}-"):
                positions[family].append(index)
    if not all(positions.values()):
        return False
    means = {family: sum(values) / len(values) for family, values in positions.items()}
    return means["red"] < means["green"] < means["blue"]


def run(args: argparse.Namespace) -> int:
    work = Path(args.work_dir)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    data_root = work / "PhotoSearchData"
    report: dict[str, Any] = {
        "model_dir": str(args.model_dir),
        "manifest": str(args.manifest),
        "device": args.device,
        "per_family": int(args.per_family),
        "query_text": QUERY_TEXT,
    }
    service = None
    try:
        service, root, assets = _seed_library(work, int(args.per_family))
        report["fixture"] = {"library": str(root), "assets": len(assets)}
        common = [
            "--data-root",
            str(data_root),
            "--model-dir",
            str(args.model_dir),
            "--manifest",
            str(args.manifest),
            "--device",
            str(args.device),
        ]
        code, build, build_ms = _run_cli(["build", *common, "--limit", str(len(assets))])
        report["build"] = {"exit": code, "wall_ms": build_ms, **build}
        if code == 0:
            per_image = round(build_ms / max(1, len(assets)), 1)
            report["build"]["ms_per_image"] = per_image
            report["build"]["images_per_second"] = round(1000 / per_image, 2)
        code, again, again_ms = _run_cli(
            ["build", "--data-root", str(data_root), "--manifest", str(args.manifest), "--dry-run"]
        )
        report["second_pass"] = {"exit": code, "wall_ms": again_ms, **again}

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
        result = asyncio.run(_search(app))
        report.update(result)
        app.state.api.close()
    finally:
        if service is not None:
            service.close()

    body = report.get("body") or {}
    hits = body.get("hits") or []
    channels = body.get("channels") or {}
    by_id = {int(value): name for name, value in (report["fixture"] and assets).items()}
    ranked = [by_id.get(int(hit["asset_id"]), "?") for hit in hits]
    report["ranked"] = ranked
    report["ranked_red_count"] = len([name for name in ranked if name.startswith("red-")])
    #: The synthetic families differ only slightly, so the honest check is the *order of the
    #: families* rather than a perfect partition: the queried family must come first and its
    #: mean rank must be better than each other family.
    checks = {
        "build_command_succeeded": report.get("build", {}).get("exit") == 0,
        "every_asset_was_encoded": report.get("build", {}).get("targets") == len(assets),
        "all_jobs_succeeded": report.get("build", {}).get("run", {}).get("jobs")
        == {"SUCCEEDED": len(assets)},
        "generation_is_ready": report.get("build", {}).get("generation", {}).get("state")
        == "READY",
        "entries_are_confirmed": report.get("build", {}).get("generation", {}).get("entries")
        == {"CONFIRMED": len(assets)},
        "profile_is_active": bool(
            report.get("build", {}).get("profile", {}).get("active_profile_id")
        ),
        "second_pass_has_no_target": report.get("second_pass", {}).get("targets") == 0,
        "search_answers_200": report.get("status") == 200,
        "search_used_the_profile_binding": body.get("profile_id")
        == (report.get("build", {}).get("profile", {}).get("profile_id")),
        "vector_channel_engaged": bool((channels.get("vector") or {}).get("engaged")),
        "top_hit_is_the_queried_family": bool(ranked) and ranked[0].startswith("red-"),
        "family_order_matches_the_query": _family_order(ranked),
        "vector_channel_ranked_every_asset": int((channels.get("vector") or {}).get("candidates") or 0)
        == len(assets),
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
    parser.add_argument("--work-dir", type=Path, default=REPO_ROOT / "tmp" / "p4-02o-build")
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=REPO_ROOT / "tools" / "prepare_models" / "manifests" / "chinese-clip-vit-b-16.json",
    )
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--per-family", type=int, default=6)
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
