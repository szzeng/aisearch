"""P4-02S quality smoke: does a photo find itself? (known target, no picture is viewed)

For a deterministic sample of already-indexed assets this posts each photo **as an uploaded
image query** to ``POST /api/v1/search/image`` and checks that the top hit is that very asset.
The target is known without looking at any picture: the same content must produce its own
vector, so distance 0 has to win. This also exercises the upload path on the real library.

Nothing is written to the catalog and no file is modified; the report carries asset ids,
latencies and codes only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

from httpx import ASGITransport, AsyncClient  # noqa: E402

from photo_search.api.settings import ApiSettings  # noqa: E402
from photo_search.app import create_app  # noqa: E402
from photo_search.catalog.db import Catalog  # noqa: E402
from photo_search.workers.query_encoder import resolve_query_encoder  # noqa: E402

SESSION_TOKEN = "p4-02s-self-token"
ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]", "testserver"})
ORIGIN = "http://127.0.0.1"
#: Uploads above this are skipped: the endpoint has its own byte ceiling and this smoke is not
#: about that gate (the skipped count is reported).
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def _sample(data_root: str, count: int, seed: int) -> tuple[list[dict[str, Any]], int]:
    catalog = Catalog.open(data_root=data_root)
    try:
        rows = catalog.read_all(
            "SELECT a.asset_id, a.byte_size, l.relative_path, s.root_path "
            "FROM assets AS a "
            "JOIN index_entries AS e ON e.asset_id = a.asset_id AND e.state = 'CONFIRMED' "
            "JOIN file_locations AS l ON l.location_id = ("
            "  SELECT l2.location_id FROM file_locations AS l2 WHERE l2.asset_id = a.asset_id "
            "  AND l2.availability = 'PRESENT' ORDER BY l2.location_id LIMIT 1) "
            "JOIN source_roots AS s ON s.source_id = l.source_id "
            "ORDER BY a.asset_id"
        )
    finally:
        catalog.close()
    usable = [dict(row) for row in rows if int(row["byte_size"] or 0) <= MAX_UPLOAD_BYTES]
    skipped = len(rows) - len(usable)
    picked = random.Random(int(seed)).sample(usable, min(int(count), len(usable)))
    return picked, skipped


async def _run(args: argparse.Namespace, samples: list[dict[str, Any]]) -> dict[str, Any]:
    wiring = resolve_query_encoder(
        model_dir=args.model_dir, manifest_path=args.manifest, device=args.device
    )
    settings = ApiSettings(
        data_root=Path(args.data_root),
        bind_host="127.0.0.1",
        bind_port=8000,
        session_token=SESSION_TOKEN,
        allowed_hosts=ALLOWED_HOSTS,
    )
    app = create_app(settings=settings, encoder_factory=wiring.factory)
    results: list[dict[str, Any]] = []
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=ORIGIN, follow_redirects=False
        ) as client:
            session = await client.get("/api/v1/session", params={"token": SESSION_TOKEN})
            csrf = str(session.json()["csrf_token"])
            headers = {"X-CSRF-Token": csrf, "Origin": ORIGIN}
            for index, row in enumerate(samples):
                path = Path(str(row["root_path"])) / str(row["relative_path"])
                payload = path.read_bytes()
                started = time.perf_counter()
                response = await client.post(
                    "/api/v1/search/image",
                    files={"image": (path.name, payload, "application/octet-stream")},
                    data={
                        "request_id": f"p4-02s-self-{index}",
                        "contract_version": "p2-contracts.v1",
                    },
                    headers=headers,
                )
                elapsed = round((time.perf_counter() - started) * 1000, 1)
                entry: dict[str, Any] = {
                    "asset_id": int(row["asset_id"]),
                    "http_ms": elapsed,
                    "status": int(response.status_code),
                }
                if response.status_code == 200:
                    body = response.json()
                    hits = body.get("hits") or []
                    entry["top_asset_id"] = int(hits[0]["asset_id"]) if hits else None
                    entry["self_top1"] = bool(hits) and int(hits[0]["asset_id"]) == int(
                        row["asset_id"]
                    )
                    channels = body.get("channels") or {}
                    entry["vector_engaged"] = bool((channels.get("vector") or {}).get("engaged"))
                else:
                    error = response.json()
                    entry["code"] = error.get("code")
                    entry["reason"] = (error.get("details") or {}).get("reason")
                results.append(entry)
    finally:
        app.state.api.close()
    return {"wiring": wiring.to_dict(), "results": results}


def run(args: argparse.Namespace) -> int:
    samples, skipped = _sample(str(args.data_root), int(args.samples), int(args.seed))
    report = asyncio.run(_run(args, samples))
    results = report["results"]
    ok = [item for item in results if item.get("status") == 200]
    self_top1 = [item for item in ok if item.get("self_top1")]
    latencies = sorted(float(item["http_ms"]) for item in ok)
    def pick(fraction: float) -> float:
        if not latencies:
            return 0.0
        index = max(0, min(len(latencies) - 1, int(round(fraction * len(latencies) + 0.5)) - 1))
        return latencies[index]
    summary = {
        "sampled": len(samples),
        "skipped_over_upload_limit": skipped,
        "answered_200": len(ok),
        "self_top1": len(self_top1),
        "self_top1_rate": round(len(self_top1) / max(1, len(ok)), 4),
        "http_p50_ms": pick(0.5),
        "http_p95_ms": pick(0.95),
        "http_max_ms": max(latencies) if latencies else 0.0,
        "failures": [item for item in results if not item.get("self_top1")],
    }
    report["summary"] = summary
    #: A refusal the *upload gate* is supposed to produce (a photo above the byte ceiling, or a
    #: family this build refuses) is recorded, not counted as a quality failure; every sample
    #: that was answered must have found itself.
    expected_refusals = {"UPLOAD_TOO_LARGE", "FORMAT_UNSUPPORTED"}
    unexpected = [
        item
        for item in results
        if item.get("status") != 200 and str(item.get("code")) not in expected_refusals
    ]
    report["unexpected_failures"] = unexpected
    report["passed"] = bool(ok) and len(self_top1) == len(ok) and not unexpected
    report["status"] = "pass" if report["passed"] else "fail"
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is not None:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=REPO_ROOT / "tools" / "prepare_models" / "manifests" / "chinese-clip-vit-b-16.json",
    )
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20260927)
    parser.add_argument("--output", type=Path, default=None)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
