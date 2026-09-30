"""P4-02P: warm text-query latency and hit counts on the real trial library.

The application is built the way a real run builds it (``resolve_query_encoder`` from
``photo_search.__main__``), the session is a real one-time credential, and every query goes
through ``POST /api/v1/search`` with no explicit binding - the activated search profile
supplies it. Nothing is written to the catalog and no photo is modified.

This measures **latency and whether results exist**, not relevance: there is no human-labelled
query/target set, so no quality or Recall claim is made. The library here is ~3.2k photos, not
the 300k acceptance scale, so §12.2 is not claimed either.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

from httpx import ASGITransport, AsyncClient  # noqa: E402

from photo_search.api.settings import ApiSettings  # noqa: E402
from photo_search.app import create_app  # noqa: E402
from photo_search.workers.query_encoder import resolve_query_encoder  # noqa: E402

SESSION_TOKEN = "p4-02p-query-token"
ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]", "testserver"})
ORIGIN = "http://127.0.0.1"
DEFAULT_QUERIES = (
    "日落",
    "猫",
    "海边",
    "全家福",
    "吉他",
    "城市夜景",
    "花朵",
    "食物",
    "红色的图片",
    "生日蛋糕",
)


def _percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile of a sorted sample list (no interpolation invention)."""
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = max(0, min(len(ordered) - 1, int(round(fraction * len(ordered) + 0.5)) - 1))
    return round(float(ordered[index]), 1)


async def _measure(args: argparse.Namespace, queries: list[str]) -> dict[str, Any]:
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
    report: dict[str, Any] = {"wiring": wiring.to_dict(), "queries": {}}
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=ORIGIN, follow_redirects=False
        ) as client:
            session = await client.get("/api/v1/session", params={"token": SESSION_TOKEN})
            csrf = str(session.json()["csrf_token"])
            headers = {"X-CSRF-Token": csrf, "Origin": ORIGIN}
            capabilities_started = time.perf_counter()
            capabilities = await client.get("/api/v1/capabilities")
            report["capabilities_http_ms"] = round(
                (time.perf_counter() - capabilities_started) * 1000, 1
            )
            search_capability = (
                capabilities.json().get("search") if capabilities.status_code == 200 else {}
            )
            report["query_encoder_available"] = (search_capability or {}).get(
                "query_encoder_available"
            )
            report["active_profile_id"] = (search_capability or {}).get("active_profile_id")
            report["active_image_generation_id"] = (search_capability or {}).get(
                "active_image_generation_id"
            )

            for index, text in enumerate(queries):
                samples: list[float] = []
                last: dict[str, Any] = {}
                rounds = (
                    int(args.canonical_samples)
                    if text == args.canonical
                    else int(args.samples)
                )
                for _ in range(rounds):
                    body = {"request_id": f"p4-02p-{index}", "text": text}
                    started = time.perf_counter()
                    response = await client.post(
                        "/api/v1/search", json=body, headers=headers
                    )
                    samples.append(round((time.perf_counter() - started) * 1000, 1))
                    last = response.json() if response.status_code == 200 else {"status": int(response.status_code)}
                hits = last.get("hits") or []
                names = [Path(str(hit.get("relative_path") or "")).name for hit in hits[:3]]
                channels = last.get("channels") or {}
                report["queries"][text] = {
                    "samples": len(samples),
                    "median_ms": round(statistics.median(samples), 1),
                    "min_ms": min(samples),
                    "max_ms": max(samples),
                    "status": int(last.get("state") == "OK"),
                    "total_candidates": last.get("total_candidates"),
                    "vector_engaged": bool((channels.get("vector") or {}).get("engaged")),
                    "vector_engine_blocks": (channels.get("vector") or {}).get("engine_blocks"),
                    "vector_candidates": (channels.get("vector") or {}).get("candidates"),
                    "hit_count": len(hits),
                    "top_names": names,
                }
                if text == args.canonical:
                    report["canonical"] = {"text": text, "samples": samples}
        return report
    finally:
        app.state.api.close()


def run(args: argparse.Namespace) -> int:
    queries = [text.strip() for text in str(args.queries).split(",") if text.strip()]
    report = asyncio.run(_measure(args, queries))
    canonical = report.get("canonical") or {}
    samples = list(canonical.get("samples") or [])
    if samples:
        canonical.update(
            {
                "samples": len(samples),
                "p50_ms": _percentile(samples, 0.5),
                "p95_ms": _percentile(samples, 0.95),
                "min_ms": min(samples),
                "max_ms": max(samples),
            }
        )
    report["report"] = str(args.output) if args.output else None
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is not None:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=REPO_ROOT / "src" / "photo_search" / "models" / "embedding" / "fg-clip2-base.json",
    )
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--queries", default=",".join(DEFAULT_QUERIES))
    parser.add_argument("--canonical", default="红色的图片")
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--canonical-samples", type=int, default=32)
    parser.add_argument("--output", type=Path, default=None)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
