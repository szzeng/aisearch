"""P4-Embedding-Arch closure: two real providers, one isolated library, real CUDA and HTTP.

This driver proves, with the *real* weights and the production wiring:

1. both providers install into one isolated data root through the new ``prepare-local`` path
   (size/hash verified; nothing is downloaded and nothing outside ``--work-dir`` is written);
2. the Chinese-CLIP space is built first and activates, because it is the first profile;
3. the Qwen3-VL-Embedding-2B 512D space is built as a **candidate**: its generation is READY and
   fully confirmed, and the served pointer does not move;
4. the controlled ``select-profile`` gate moves the pointer for Qwen and refuses nothing;
5. the application then serves text search and uploaded-image search over the *newly active*
   profile, reports the active model in ``/capabilities`` and hides the Chinese-CLIP strong-match
   statistic, which was never calibrated for this space.

The library is synthetic (deterministic shapes/colours) and lives in a throw-away project-local
directory: no real photo library is read. Text queries are **observations** - the models are real
but synthetic drawings are not a quality benchmark - while the uploaded-image self-query is a
deterministic check (the same image must rank first). It is diagnostic evidence, not an acceptance
test: it says nothing about §12.2 latency targets, large-library throughput or retrieval quality.
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
from PIL import Image, ImageDraw  # noqa: E402

from photo_search.__main__ import _encoder_wiring  # noqa: E402
from photo_search.api.settings import ApiSettings  # noqa: E402
from photo_search.app import create_app  # noqa: E402
from photo_search.catalog.db import Catalog  # noqa: E402
from photo_search.ingestion.service import CatalogService  # noqa: E402
from photo_search.models.embedding import cli as embedding_cli  # noqa: E402
from photo_search.models.embedding.registry import registry_for  # noqa: E402

SESSION_TOKEN = "p4-embedding-arch-session-token"
ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]", "testserver"})
ORIGIN = "http://127.0.0.1"
CLIP_PROVIDER = "chinese-clip-vit-b-16"
QWEN_PROVIDER = "qwen3-vl-embedding-2b"
DEFAULT_CLIP_SOURCE = Path(r"D:\SZ\project\PhotoSearchData\models\chinese_clip\vit-b-16")
DEFAULT_QWEN_SOURCE = REPO_ROOT / "tmp" / "qwen-speed-model"

#: 12 deterministic fixtures: one background, one shape, one colour each.
SHAPES = ("circle", "square", "triangle")
COLORS = {
    "red": (214, 45, 45),
    "green": (60, 170, 80),
    "blue": (50, 90, 205),
    "yellow": (235, 200, 60),
}
BACKGROUNDS = ((245, 245, 245), (40, 45, 60))
TEXT_QUERIES = (
    "红色的圆形",
    "绿色的三角形",
    "蓝色的正方形",
    "黄色的圆形",
    "深色背景上的图形",
    "浅色背景上的图形",
    "一张几何图形图片",
    "电脑生成的图形",
)


def _ms_since(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def _seed_library(
    data_root: Path, root: Path, *, images: int, keep: bool
) -> tuple[Any, Path, dict[str, int]]:
    """Write ``images`` deterministic PNG fixtures and scan them into an isolated data root."""
    if root.exists() and not keep:
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    planned: list[dict[str, Any]] = []
    for index in range(int(images)):
        shape = SHAPES[index % len(SHAPES)]
        color_name = list(COLORS)[(index // len(SHAPES)) % len(COLORS)]
        background = BACKGROUNDS[index % len(BACKGROUNDS)]
        name = f"fixture-{index:02d}.png"
        path = root / name
        if not path.exists():
            image = Image.new("RGB", (320, 240), background)
            draw = ImageDraw.Draw(image)
            color = COLORS[color_name]
            if shape == "circle":
                draw.ellipse((90, 50, 230, 190), fill=color)
            elif shape == "square":
                draw.rectangle((90, 50, 230, 190), fill=color)
            else:
                draw.polygon([(160, 40), (250, 200), (70, 200)], fill=color)
            draw.rectangle((0, 0, 320, 8), fill=(20 + index, 20, 20))
            image.save(path, "PNG")
        planned.append(
            {
                "name": name,
                "shape": shape,
                "color": color_name,
                "background": "light" if background[0] > 128 else "dark",
            }
        )
    service = CatalogService.open(data_root=data_root, lease_seconds=60)
    source = service.register_source(display_name="p4-embedding-arch", root_path=root)
    service.run_scan(int(source["source_id"]))
    assets = {
        str(row["relative_path"]).replace("\\", "/").rsplit("/", 1)[-1]: int(row["asset_id"])
        for row in service.catalog.read_all(
            "SELECT asset_id, relative_path FROM file_locations ORDER BY location_id"
        )
    }
    service.close()
    return planned, root, assets


def _prepare(args: argparse.Namespace, data_root: Path) -> dict[str, Any]:
    """Install both providers into the isolated data root through the shipped CLI path."""
    prepared: dict[str, Any] = {}
    for provider, source in (
        (CLIP_PROVIDER, Path(args.clip_source)),
        (QWEN_PROVIDER, Path(args.qwen_source)),
    ):
        started = time.perf_counter()
        namespace = argparse.Namespace(
            provider=provider,
            source=source,
            revision=args.revision,
            data_root=data_root,
            force=False,
            json=True,
        )
        payload = embedding_cli._prepare_local(namespace)
        payload["elapsed_ms"] = _ms_since(started)
        payload["source"] = str(source)
        prepared[provider] = payload
    return prepared


def _build(args: argparse.Namespace, data_root: Path, provider: str) -> dict[str, Any]:
    namespace = argparse.Namespace(
        provider=provider,
        data_root=data_root,
        limit=int(args.images) + 4,
        source_id=None,
        device=args.device,
        json=True,
    )
    started = time.perf_counter()
    payload = embedding_cli._build_candidate(namespace)
    payload["elapsed_ms"] = _ms_since(started)
    return payload


def _select(data_root: Path, profile_id: str) -> dict[str, Any]:
    namespace = argparse.Namespace(
        profile=profile_id, data_root=data_root, stopped=True, json=True
    )
    started = time.perf_counter()
    payload = embedding_cli._select_profile(namespace)
    payload["elapsed_ms"] = _ms_since(started)
    return payload


def _confirmation_counts(data_root: Path, generation_id: str) -> dict[str, Any]:
    with Catalog.open(data_root=data_root) as catalog:
        entries = {
            str(item["state"]): int(item["count"])
            for item in catalog.read_all(
                "SELECT state, COUNT(*) AS count FROM index_entries WHERE generation_id = ? "
                "GROUP BY state",
                (str(generation_id),),
            )
        }
        pending = catalog.read_one(
            "SELECT COUNT(*) AS value FROM index_outbox WHERE generation_id = ? "
            "AND state <> 'CONFIRMED'",
            (str(generation_id),),
        )
        generation = catalog.read_one(
            "SELECT state, dimension FROM index_generations WHERE generation_id = ?",
            (str(generation_id),),
        )
    return {
        "entries": entries,
        "generation_state": None if generation is None else str(generation["state"]),
        "dimension": None if generation is None else int(generation["dimension"]),
        "pending_outbox": int(pending["value"]) if pending is not None else 0,
    }


def _http_phase(
    args: argparse.Namespace,
    data_root: Path,
    *,
    expected_provider: str,
    library: Path,
    assets: dict[str, int],
    images: int,
) -> dict[str, Any]:
    """Start the real application over the isolated data root and search through HTTP."""
    wiring = _encoder_wiring(
        argparse.Namespace(model_dir=None, model_manifest=None, model_device=args.device),
        data_root,
    )
    settings = ApiSettings(
        data_root=data_root,
        bind_host="127.0.0.1",
        bind_port=8000,
        session_token=SESSION_TOKEN,
        allowed_hosts=ALLOWED_HOSTS,
    )
    app = create_app(settings=settings, encoder_factory=wiring.factory)
    report: dict[str, Any] = {"wiring": wiring.to_dict()}

    async def scenario() -> None:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=ORIGIN, follow_redirects=False
        ) as client:
            session = await client.get("/api/v1/session", params={"token": SESSION_TOKEN})
            assert session.status_code == 200, session.text
            csrf = str(session.json()["csrf_token"])
            headers = {"X-CSRF-Token": csrf, "Origin": ORIGIN}
            started = time.perf_counter()
            capabilities = await client.get("/api/v1/capabilities")
            report["capabilities_http_ms"] = _ms_since(started)
            report["capabilities_status"] = int(capabilities.status_code)
            report["capabilities"] = (
                capabilities.json().get("search") if capabilities.status_code == 200 else None
            )
            observations = []
            for query in TEXT_QUERIES:
                started = time.perf_counter()
                response = await client.post(
                    "/api/v1/search",
                    json={"request_id": f"p4-ea-{abs(hash(query)) % 100000}", "text": query,
                          "page_size": 5},
                    headers=headers,
                )
                elapsed = _ms_since(started)
                body = response.json() if response.status_code == 200 else {}
                names = [
                    _name_of(int(hit["asset_id"]), assets) for hit in (body.get("hits") or [])
                ]
                observations.append(
                    {
                        "query": query,
                        "status": int(response.status_code),
                        "http_ms": elapsed,
                        "top_hits": names,
                        "vector": (body.get("channels") or {}).get("vector"),
                        "error": None if response.status_code == 200 else response.text[:200],
                    }
                )
            report["text_queries"] = observations
            uploads = []
            for index in range(min(int(args.upload_queries), len(assets))):
                name = f"fixture-{index:02d}.png"
                with (library / name).open("rb") as stream:
                    started = time.perf_counter()
                    response = await client.post(
                        "/api/v1/search/image",
                        files={"image": (name, stream, "image/png")},
                        data={"request_id": f"p4-ea-upload-{index}", "page_size": 5},
                        headers=headers,
                    )
                elapsed = _ms_since(started)
                body = response.json() if response.status_code == 200 else {}
                hits = body.get("hits") or []
                top = _name_of(int(hits[0]["asset_id"]), assets) if hits else None
                uploads.append(
                    {
                        "image": name,
                        "status": int(response.status_code),
                        "http_ms": elapsed,
                        "top_hit": top,
                        "self_rank_1": top == name,
                        "error": None if response.status_code == 200 else response.text[:200],
                    }
                )
            report["upload_queries"] = uploads
            if report.get("capabilities") is not None:
                report["strong_match_available"] = bool(
                    (report["capabilities"].get("active_embedding_model") or {}).get(
                        "strong_match_available"
                    )
                )

    asyncio.run(scenario())
    encoder = app.state.api.query_encoder()
    if encoder is not None:
        started = time.perf_counter()
        vector = encoder.encode_queries(["红色的圆形"])[0]
        report["query_encode_ms"] = _ms_since(started)
        report["query_vector_dimension"] = len(vector)
        report["health"] = encoder.health()
    app.state.api.close()
    report["expected_provider"] = expected_provider
    return report


def _name_of(asset_id: int, assets: dict[str, int]) -> str:
    for name, value in assets.items():
        if int(value) == int(asset_id):
            return name
    return f"asset-{asset_id}"


def run(args: argparse.Namespace) -> int:
    work = Path(args.work_dir)
    if work.exists() and not args.keep:
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "task": "P4-Embedding-Arch",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "work_dir": str(work),
        "device": args.device,
        "images": int(args.images),
        "python": sys.version.split()[0],
    }
    try:
        return _run(args, work, report)
    except Exception as error:  # noqa: BLE001 - a failed run is reported as its own evidence
        report["error"] = {
            "type": type(error).__name__,
            "message": str(error)[:400],
            "code": str(getattr(error, "code", "")) or None,
            "details": dict(getattr(error, "details", {}) or {}),
        }
        report["passed"] = False
        report["status"] = "error"
        report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        text = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output is not None:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(text + "\n", encoding="utf-8", newline="\n")
        print(json.dumps(report["error"], ensure_ascii=False, indent=2))
        print(f"status=error output={args.output}")
        return 1


def _run(args: argparse.Namespace, work: Path, report: dict[str, Any]) -> int:
    try:
        import torch
        import transformers

        report["runtime"] = {
            "torch": str(torch.__version__),
            "cuda": str(torch.version.cuda),
            "cuda_available": bool(torch.cuda.is_available()),
            "device_name": str(torch.cuda.get_device_name(0)) if torch.cuda.is_available() else None,
            "transformers": str(transformers.__version__),
        }
    except Exception as error:  # noqa: BLE001 - reported, not raised
        report["runtime"] = {"error": f"{type(error).__name__}: {error}"}
    data_root = work / "data"

    planned, library, assets = _seed_library(
        data_root, work / "library", images=int(args.images), keep=args.keep
    )
    report["fixture"] = {
        "library": str(library),
        "images": planned,
        "assets": assets,
        "text_queries": list(TEXT_QUERIES),
    }
    report["prepare"] = _prepare(args, data_root)
    registry = registry_for(data_root)
    report["providers"] = [entry.to_dict() for entry in registry.providers()]

    clip = _build(args, data_root, CLIP_PROVIDER)
    report["clip_build"] = clip
    report["clip_entries"] = _confirmation_counts(
        data_root, str(clip["build"]["generation"]["generation_id"])
    )
    report["clip_http"] = _http_phase(
        args,
        data_root,
        expected_provider=CLIP_PROVIDER,
        library=library,
        assets=assets,
        images=int(args.images),
    )

    qwen = _build(args, data_root, QWEN_PROVIDER)
    report["qwen_build"] = qwen
    report["qwen_entries"] = _confirmation_counts(
        data_root, str(qwen["build"]["generation"]["generation_id"])
    )
    report["qwen_selection"] = _select(data_root, str(qwen["profile"]["profile_id"]))
    report["qwen_http"] = _http_phase(
        args,
        data_root,
        expected_provider=QWEN_PROVIDER,
        library=library,
        assets=assets,
        images=int(args.images),
    )

    checks = _checks(report, images=int(args.images), assets=assets)
    report["checks"] = checks
    report["passed"] = all(checks.values())
    report["status"] = "pass" if report["passed"] else "fail"
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    report["work_dir_retained"] = True
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is not None:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report["checks"], ensure_ascii=False, indent=2))
    print(f"status={report['status']} output={args.output}")
    return 0 if report["passed"] else 1


def _checks(report: dict[str, Any], *, images: int, assets: dict[str, int]) -> dict[str, bool]:
    clip = report.get("clip_build") or {}
    qwen = report.get("qwen_build") or {}
    selection = report.get("qwen_selection") or {}
    clip_http = report.get("clip_http") or {}
    qwen_http = report.get("qwen_http") or {}
    clip_caps = clip_http.get("capabilities") or {}
    qwen_caps = qwen_http.get("capabilities") or {}
    clip_model = clip_caps.get("active_embedding_model") or {}
    qwen_model = qwen_caps.get("active_embedding_model") or {}
    clip_uploads = clip_http.get("upload_queries") or []
    qwen_uploads = qwen_http.get("upload_queries") or []
    text_ok = lambda phase: all(  # noqa: E731 - one local helper keeps the table readable
        item.get("status") == 200 for item in (phase.get("text_queries") or [])
    )
    return {
        "both_providers_prepared": len(report.get("providers") or []) >= 2,
        "both_providers_installed": all(
            item.get("installed") for item in (report.get("providers") or [])
        ),
        "clip_build_confirmed_all": (
            (report.get("clip_entries") or {}).get("entries", {}).get("CONFIRMED") == int(images)
        ),
        "clip_build_cuda": (
            (clip.get("build", {}).get("evidence", {}) or {}).get("resolved_device") == "gpu"
        ),
        "clip_generation_ready": (report.get("clip_entries") or {}).get("generation_state")
        == "READY",
        "clip_http_encoder_available": bool(clip_caps.get("query_encoder_available")),
        "clip_http_active_is_clip": clip_model.get("provider_id") == CLIP_PROVIDER,
        "clip_http_strong_match_available": clip_model.get("strong_match_available") is True,
        "clip_http_text_queries_ok": text_ok(clip_http),
        "clip_http_self_queries_rank_first": bool(clip_uploads)
        and all(item.get("self_rank_1") for item in clip_uploads),
        "qwen_candidate_not_activated": (qwen.get("profile") or {}).get("activated") is False,
        "qwen_candidate_reason": (qwen.get("profile") or {}).get("reason")
        == "CANDIDATE_NOT_ACTIVATED",
        "qwen_build_confirmed_all": (
            (report.get("qwen_entries") or {}).get("entries", {}).get("CONFIRMED") == int(images)
        ),
        "qwen_build_cuda": (
            (qwen.get("build", {}).get("evidence", {}) or {}).get("resolved_device") == "gpu"
        ),
        "qwen_build_precision_fp16": (
            (qwen.get("build", {}).get("evidence", {}) or {}).get("resolved_precision") == "FP16"
        ),
        "qwen_build_provider_is_pytorch_cuda": (
            (qwen.get("build", {}).get("evidence", {}) or {}).get("resolved_provider")
            == "PyTorchCUDA"
        ),
        "qwen_selection_changed": selection.get("changed") is True,
        "qwen_selection_coverage_complete": (selection.get("coverage") or {}).get("missing") == 0,
        "qwen_selection_readiness_is_cuda": (selection.get("readiness") or {}).get(
            "provider_name"
        )
        == "PyTorchCUDA",
        "qwen_http_active_is_qwen": qwen_model.get("provider_id") == QWEN_PROVIDER,
        "qwen_http_strong_match_hidden": qwen_model.get("strong_match_available") is False,
        "qwen_http_text_queries_ok": text_ok(qwen_http),
        "qwen_http_self_queries_rank_first": bool(qwen_uploads)
        and all(item.get("self_rank_1") for item in qwen_uploads),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--work-dir", type=Path, default=REPO_ROOT / "tmp" / "p4-embedding-arch" / "smoke"
    )
    parser.add_argument("--clip-source", type=Path, default=DEFAULT_CLIP_SOURCE)
    parser.add_argument("--qwen-source", type=Path, default=DEFAULT_QWEN_SOURCE)
    parser.add_argument(
        "--images", type=int, default=12, help="fixture images (the task card allows 12-30)"
    )
    parser.add_argument("--upload-queries", type=int, default=2)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--revision", default=None)
    parser.add_argument(
        "--output", type=Path, default=REPO_ROOT / "test-results" / "p4-embedding-arch-smoke.json"
    )
    parser.add_argument("--keep", action="store_true", help="keep the throw-away fixture root")
    args = parser.parse_args(argv)
    if not 1 <= int(args.images) <= 30:
        parser.error("--images must be between 1 and 30")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
