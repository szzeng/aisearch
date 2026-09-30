"""Measure pinned-model retrieval and the real local API on downloaded test sets.

Model Recall@K uses each dataset's own 1,000-image candidate pool. The product check uses
all downloaded images through the real scanner, scheduler, outbox, LanceDB and HTTP API.
It reports a deterministic sample separately; neither result claims 100k-library speed.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
import subprocess
import time
from pathlib import Path

import httpx
import numpy as np

from photo_search.api.context import ApiContext
from photo_search.api.settings import ApiSettings
from photo_search.app import create_app
from photo_search.artifacts.store import generation_id_for
from photo_search.config import resolve_data_root
from photo_search.indexing import semantic
from photo_search.ingestion.service import CatalogService
from photo_search.models.embedding.manifest import DEFAULT_MANIFEST
from photo_search.workers.query_encoder import build_worker_query_encoder, default_model_dir


def percentiles(values):
    return {"count": len(values), "p50_ms": float(np.percentile(values, 50)),
            "p95_ms": float(np.percentile(values, 95)), "mean_ms": float(np.mean(values))} if values else {}


def recall(ranks):
    return {f"R@{k}": round(sum(r <= k for r in ranks) / len(ranks), 6) for k in (1, 5, 10)}


class MeasuredEncoder:
    """Record actual production image encodes; restartable under the identical space."""
    def __init__(self, encoder, path):
        self.encoder, self.path = encoder, path
        self.rows = {}
        if path.exists():
            for line in path.read_text().splitlines():
                row = json.loads(line)
                self.rows[row["path"]] = row
        self.new_ms = []

    def health(self):
        return self.encoder.health()

    def encode_image(self, path):
        started = time.perf_counter()
        vector = self.encoder.encode_image(path)
        elapsed = (time.perf_counter() - started) * 1000
        row = {"path": str(Path(path).resolve()), "vector": vector, "elapsed_ms": elapsed}
        with self.path.open("a") as out:
            out.write(json.dumps(row) + "\n")
        self.rows[row["path"]] = row
        self.new_ms.append(elapsed)
        if len(self.new_ms) % 100 == 0:
            print(f"Encoded {len(self.new_ms)} images ({np.mean(self.new_ms):.1f} ms/image)", flush=True)
        return vector


def quality(data, directory, measured, encoder, output):
    files = [str((directory / "images" / x["file"]).resolve()) for x in data["images"]]
    for file in files:
        if file not in measured.rows:  # content duplicates may share one catalog asset
            measured.encode_image(file)
    matrix = np.asarray([measured.rows[file]["vector"] for file in files], dtype=np.float32)
    positions = {entry["file"]: n for n, entry in enumerate(data["images"])}
    ranks, latencies, samples = [], [], []
    predictions = output / f"{data['name']}-predictions.jsonl"
    with predictions.open("w") as out:
        for n, query in enumerate(data["queries"]):
            started = time.perf_counter()
            vector = encoder.encode_queries([query["text"]])[0]
            latency = (time.perf_counter() - started) * 1000
            scores = matrix @ np.asarray(vector, dtype=np.float32)
            expected = positions[query["image"]]
            # Stable index breaks exact score ties consistently; no arbitrary favourable rank.
            order = np.lexsort((np.arange(len(scores)), -scores))
            rank = int(np.flatnonzero(order == expected)[0]) + 1
            ranks.append(rank)
            latencies.append(latency)
            row = {"id": query["id"], "text": query["text"], "expected": query["image"],
                   "rank": rank, "encode_ms": latency,
                   "top10": [data["images"][int(i)]["file"] for i in order[:10]]}
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            if n < 10:
                samples.append(row)
            if (n + 1) % 500 == 0:
                print(f"{data['name']}: {n + 1}/{len(data['queries'])} queries", flush=True)
    return {"images": len(files), "queries": len(ranks), "recall": recall(ranks),
            "median_rank": float(np.median(ranks)), "query_encoding": percentiles(latencies),
            "source": data["source"], "revision": data["revision"], "split": data["split"],
            "annotation_kind": data["annotation_kind"],
            "dataset_manifest_sha256": hashlib.sha256((directory / "dataset.json").read_bytes()).hexdigest(),
            "samples": samples,
            "predictions": str(predictions)}


async def api_check(app, datasets, assets, spec, generation):
    latencies, ranks, failures = [], [], []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                base_url="http://127.0.0.1") as client:
        response = await client.get("/api/v1/session", params={"token": "benchmark-local"})
        response.raise_for_status()
        headers = {"Origin": "http://127.0.0.1", "X-CSRF-Token": response.json()["csrf_token"]}
        response = await client.get("/api/v1/capabilities")
        response.raise_for_status()
        for data, directory in datasets:
            # Fixed, evenly spaced queries, chosen before any scores are inspected.
            indices = np.linspace(0, len(data["queries"]) - 1, 30, dtype=int)
            for index in indices:
                query = data["queries"][int(index)]
                body = {"request_id": f"bench-{data['name']}-{index}", "text": query["text"],
                        "space_id": spec.envelope["space_id"], "image_generation_id": generation,
                        "dimension": spec.envelope["dimension"], "page_size": 50}
                started = time.perf_counter()
                response = await client.post("/api/v1/search", json=body, headers=headers)
                latencies.append((time.perf_counter() - started) * 1000)
                if response.status_code != 200:
                    failures.append({"status": response.status_code, "body": response.text[:600]})
                    continue
                hits = [x["asset_id"] for x in response.json()["hits"]]
                expected = assets[str((directory / "images" / query["image"]).resolve())]
                ranks.append(hits.index(expected) + 1 if expected in hits else 10**9)
        data, directory = datasets[0]
        first = directory / "images" / data["images"][0]["file"]
        fields = {"request_id": "benchmark-image", "space_id": spec.envelope["space_id"],
                  "image_generation_id": generation, "dimension": str(spec.envelope["dimension"])}
        with first.open("rb") as inp:
            response = await client.post("/api/v1/search/image", data=fields,
                                         files={"image": (first.name, inp, "image/jpeg")}, headers=headers)
        image_body = response.json()
        image_hit = response.status_code == 200 and bool(image_body.get("hits")) and (
            image_body["hits"][0]["asset_id"] == assets[str(first.resolve())])
    return {"queries": len(latencies), "latency": percentiles(latencies),
            "sample_recall": recall(ranks) if ranks else {}, "failures": failures,
            "image_search_status": response.status_code, "image_self_top1": image_hit,
            "candidate_assets": len(set(assets.values()))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--datasets", nargs="+", default=["coco-cn", "flickr8k"])
    parser.add_argument("--device", default="auto")
    args = parser.parse_args()
    data_root = resolve_data_root(args.data_root)
    root = data_root / "benchmarks"
    output = root / "results"
    output.mkdir(parents=True, exist_ok=True)
    datasets = []
    for name in args.datasets:
        directory = root / name
        data = json.loads((directory / "dataset.json").read_text())
        for item in data["images"]:
            path = directory / "images" / item["file"]
            with path.open("rb") as inp:
                if hashlib.file_digest(inp, "sha256").hexdigest() != item.get("sha256"):
                    raise ValueError(f"incomplete or changed dataset: {path}")
        datasets.append((data, directory))
    result = {"status": "running", "platform": platform.platform(), "device_requested": args.device,
              "python": platform.python_version(), "git_head": subprocess.check_output(
                  ["git", "rev-parse", "HEAD"], text=True).strip(), "working_tree_modified": True}
    result_path = output / ("report-" + "-".join(args.datasets) + ".json")
    def save():
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    encoder = context = service = None
    try:
        started = time.perf_counter()
        encoder = build_worker_query_encoder(model_dir=default_model_dir(data_root),
                                             manifest_path=DEFAULT_MANIFEST, device=args.device)
        result["model_start_seconds"] = time.perf_counter() - started
        result["health"] = encoder.health()
        spec = encoder.spec()
        key = spec.envelope["space_id"].replace(":", "-")
        measured = MeasuredEncoder(encoder, output / f"{key}-images.jsonl")
        result["cached_images_at_start"] = len(measured.rows)
        service = CatalogService.open(data_root=root / "app")
        for data, directory in datasets:
            row = service.catalog.read_one("SELECT source_id FROM source_roots WHERE display_name=?",
                                           (data["name"],))
            source_id = int(row["source_id"]) if row else int(service.register_source(
                display_name=data["name"], root_path=directory / "images")["source_id"])
            service.run_scan(source_id)
        started = time.perf_counter()
        build = semantic.build_index(service.catalog, encoder=measured,
                                      manifest_path=DEFAULT_MANIFEST, limit=None, device=args.device)
        result["build_seconds"] = time.perf_counter() - started
        result["build"] = build
        result["image_encoding"] = percentiles(measured.new_ms)
        result["image_encoding_images_per_second"] = (
            1000 / np.mean(measured.new_ms) if measured.new_ms else None)
        generation = generation_id_for(spec.envelope["space_id"], spec.processor_id)
        result["profile"] = semantic.ensure_search_profile(service.catalog, spec,
            space_id=spec.envelope["space_id"], generation_id=generation)
        assets = {str((Path(row["root_path"]) / row["relative_path"]).resolve()): int(row["asset_id"])
                  for row in service.catalog.read_all("SELECT f.asset_id,f.relative_path,s.root_path "
                      "FROM file_locations f JOIN source_roots s ON s.source_id=f.source_id")}
        service.close()
        service = None
        save()
        result["quality"] = {}
        for data, directory in datasets:
            result["quality"][data["name"]] = quality(data, directory, measured, encoder, output)
            result["memory"] = encoder._client.provider_usage()["sessions"]
            save()
        context = ApiContext(ApiSettings(data_root=root / "app", session_token="benchmark-local"),
                             encoder_factory=lambda: encoder)
        app = create_app(settings=context.settings, context=context)
        result["product_api"] = asyncio.run(api_check(app, datasets, assets, spec, generation))
        result["memory"] = encoder._client.provider_usage()["sessions"]
        checks = result["product_api"]
        result["status"] = "ok" if not checks["failures"] and checks["image_self_top1"] else "failed"
        save()
        print(json.dumps({"status": result["status"], "report": str(result_path)}, ensure_ascii=False))
        return 0 if result["status"] == "ok" else 1
    except BaseException as error:
        result.update(status="failed", error=f"{type(error).__name__}: {error}")
        save()
        raise
    finally:
        if service is not None:
            service.close()
        if context is not None:
            context.close()
        elif encoder is not None:
            encoder.close()


if __name__ == "__main__":
    raise SystemExit(main())
