"""Exercise OCR through a small production library build using synthetic JPEGs only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tests" / "integration"))

from test_p4_02y_library_refresh import MANIFEST, RefreshEnv, StubPinnedAdapter  # noqa: E402

from tools.diagnostics.ocr_smoke_fixture import install_pinned_weights  # noqa: E402


def run(work_dir: Path) -> dict[str, Any]:
    env = RefreshEnv.build(
        work_dir,
        photos=3,
        adapter=StubPinnedAdapter(),
        manifest=MANIFEST,
        enrich=True,
    )
    try:
        install_pinned_weights(env.data_root)
        refresher = env.context.refresher()
        started = refresher.start(mode="build")
        if not started.get("accepted"):
            raise RuntimeError("the synthetic library build was not accepted")
        if not refresher.wait_idle(timeout=180.0):
            raise TimeoutError("the synthetic library build did not finish")
        status = refresher.status()
        with env.factory() as service:
            rows = service.catalog.read_all(
                "SELECT state, requested_device, resolved_device, runtime_provider, precision "
                "FROM jobs WHERE task_type = 'image_ocr' ORDER BY job_id"
            )
            jobs = [dict(row) for row in rows]
        if not jobs or any(
            row["state"] != "SUCCEEDED"
            or row["requested_device"] != "auto"
            or row["resolved_device"] != "gpu"
            or row["runtime_provider"] != "CUDAExecutionProvider"
            or row["precision"] != "FP32"
            for row in jobs
        ):
            raise AssertionError({"status": status, "ocr_jobs": jobs})
        return {
            "status": "ok",
            "mode": "build",
            "synthetic_photos": 3,
            "refresh": status,
            "ocr_jobs": jobs,
            "data_root": str(env.data_root),
        }
    finally:
        env.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=Path("tmp/p4-02y-l/refresh-build"))
    parser.add_argument(
        "--output", type=Path, default=Path("test-results/p4-02y-l-refresh-build.json")
    )
    args = parser.parse_args()
    result = run(args.work_dir.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "mode": result["mode"],
                "ocr_jobs": len(result["ocr_jobs"]),
                "resolved_device": result["ocr_jobs"][0]["resolved_device"],
                "runtime_provider": result["ocr_jobs"][0]["runtime_provider"],
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
