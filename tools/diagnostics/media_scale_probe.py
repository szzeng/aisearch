"""P1-03 scale probe: thumbnail/preview cost, cache size and peak memory.

The probe only writes inside a temporary directory it creates itself (synthetic photo
fixtures plus a throwaway data root) and never reads a real photo library. It reports, as
one JSON document per fixture size:

* fixture creation, scan, thumbnail and preview wall times plus per-file cost;
* artifact bytes written and the on-disk cache inventory after the run;
* peak working set of the process, so memory can be compared across fixture sizes;
* the derived-media status counts the batch produced (published/failed/unsupported);
* optionally one deliberately oversized JPEG, to show that the JPEG DCT-scaled decode
  keeps peak memory bounded when the source is far larger than the long edge.

Usage::

    uv run python tools/diagnostics/media_scale_probe.py --files 200 --json
    uv run python tools/diagnostics/media_scale_probe.py --files 500 --image-size 1280x960 --json
    uv run python tools/diagnostics/media_scale_probe.py --files 20 --preview --huge --json

``--keep`` leaves the temporary tree in place for inspection.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scan_scale_probe import process_memory_bytes  # noqa: E402

from photo_search.ingestion.service import CatalogService  # noqa: E402
from photo_search.media.decode import probe_capabilities  # noqa: E402

DEFAULT_FILES = 200
DEFAULT_IMAGE_SIZE = (1600, 1200)
HUGE_IMAGE_SIZE = (12000, 9000)


def _photo(index: int, size: tuple[int, int], *, noise: bool = False) -> Image.Image:
    """A cheap but non-uniform image, so every fixture is a distinct asset."""
    width, height = size
    image = Image.new("RGB", size)
    top = (20 + (index * 37) % 200, 60 + (index * 11) % 180, 200 - (index * 13) % 160)
    bottom = (200 - (index * 7) % 180, 30 + (index * 19) % 200, 40 + (index * 23) % 200)
    strip = max(height // 64, 1)
    for row in range(0, height, strip):
        ratio = row / max(height - 1, 1)
        colour = tuple(
            int(top[channel] * (1 - ratio) + bottom[channel] * ratio) for channel in range(3)
        )
        image.paste(colour, (0, row, width, row + strip))
    for step in range(6):
        left = (index * 29 + step * 211) % max(width - 40, 1)
        upper = (index * 53 + step * 137) % max(height - 40, 1)
        image.paste(
            (255 - step * 30, 128, step * 40), (left, upper, left + 40, upper + 40)
        )
    if noise:
        # Real photos are nowhere near as compressible as flat bands; blending Gaussian
        # noise keeps artifact sizes and encode times in a realistic range.
        grain = Image.effect_noise(size, 48).convert("RGB")
        image = Image.blend(image, grain, 0.55)
    return image


def build_fixture(
    root: Path,
    *,
    files: int,
    image_size: tuple[int, int],
    directories: int = 16,
    noise: bool = False,
) -> int:
    root.mkdir(parents=True, exist_ok=True)
    per_directory = max((files + directories - 1) // directories, 1)
    for index in range(files):
        bucket = root / f"bucket-{index // per_directory:04d}"
        bucket.mkdir(exist_ok=True)
        _photo(index, image_size, noise=noise).save(
            bucket / f"photo-{index:08d}.jpg", "JPEG", quality=90
        )
    return files


def measure(
    *,
    files: int,
    image_size: tuple[int, int],
    noise: bool,
    with_preview: bool,
    huge: bool,
    keep: bool,
    workspace: Path | None,
) -> dict[str, Any]:
    base = Path(tempfile.mkdtemp(prefix="photo-search-media-", dir=workspace))
    try:
        library = base / "library"
        data_root = base / "data"
        started = time.perf_counter()
        written = build_fixture(library, files=files, image_size=image_size, noise=noise)
        fixture_ms = int(round((time.perf_counter() - started) * 1000))

        with CatalogService.open(data_root=data_root) as service:
            source_id = service.register_source(display_name="media", root_path=library)["source_id"]
            baseline_bytes = process_memory_bytes()
            scan_started = time.perf_counter()
            scan = service.run_scan(source_id)
            scan_ms = int(round((time.perf_counter() - scan_started) * 1000))

            thumbnail_started = time.perf_counter()
            thumbnails = service.generate_thumbnail()
            thumbnail_ms = int(round((time.perf_counter() - thumbnail_started) * 1000))
            after_thumbnails = process_memory_bytes()

            report: dict[str, Any] = {
                "files": written,
                "image_size": f"{image_size[0]}x{image_size[1]}",
                "fixture_ms": fixture_ms,
                "scan_ms": scan_ms,
                "scan_state": scan["state"],
                "thumbnail_ms": thumbnail_ms,
                "thumbnail_ms_per_file": round(thumbnail_ms / max(written, 1), 2),
                "thumbnails_per_second": round(written / max(thumbnail_ms, 1) * 1000, 1),
                "thumbnails_generated": thumbnails["generated"],
                "thumbnail_bytes_written": thumbnails["bytes_written"],
                "thumbnail_bytes_per_artifact": round(
                    thumbnails["bytes_written"] / max(thumbnails["generated"], 1)
                ),
                "failed": thumbnails["failed"],
                "unsupported": thumbnails["unsupported"],
                "peak_working_set_bytes": after_thumbnails,
                "working_set_before_media_bytes": baseline_bytes,
                "working_set_delta_bytes": after_thumbnails - baseline_bytes,
                "cache": service.media_report()["kinds"],
                "capabilities": probe_capabilities(),
            }

            if with_preview:
                preview_started = time.perf_counter()
                previews = service.generate_preview(limit=written)
                preview_ms = int(round((time.perf_counter() - preview_started) * 1000))
                report.update(
                    {
                        "preview_ms": preview_ms,
                        "preview_ms_per_file": round(preview_ms / max(written, 1), 2),
                        "previews_generated": previews["generated"],
                        "preview_bytes_written": previews["bytes_written"],
                        "preview_bytes_per_artifact": round(
                            previews["bytes_written"] / max(previews["generated"], 1)
                        ),
                        "peak_working_set_bytes": process_memory_bytes(),
                    }
                )

            if huge:
                report["huge"] = _measure_huge(service, base / "huge")

            report["cache"] = service.media_report()["kinds"]
            report["kept"] = keep
            report["temp_root"] = str(base) if keep else None
        return report
    finally:
        if not keep:
            shutil.rmtree(base, ignore_errors=True)


def _measure_huge(service: CatalogService, root: Path) -> dict[str, Any]:
    """One oversized JPEG: the DCT-scaled decode must keep peak memory bounded."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / "huge.jpg"
    start = time.perf_counter()
    # The fixture is written by a child process so this process's peak working set
    # reflects the decode path instead of the synthetic image construction.
    _write_huge_fixture(path)
    fixture_ms = int(round((time.perf_counter() - start) * 1000))
    source_id = service.register_source(display_name="huge", root_path=root)["source_id"]
    service.run_scan(source_id)
    before = process_memory_bytes()
    thumbnail_started = time.perf_counter()
    summary = service.generate_thumbnail(source_id=source_id)
    thumbnail_ms = int(round((time.perf_counter() - thumbnail_started) * 1000))
    preview_started = time.perf_counter()
    preview = service.generate_preview(source_id=source_id)
    preview_ms = int(round((time.perf_counter() - preview_started) * 1000))
    peak = process_memory_bytes()
    artifact = next((service.data_root / "thumbnails").rglob("*.jpg"), None)
    preview_artifact = next((service.data_root / "previews").rglob("*.jpg"), None)
    size = None
    preview_size = None
    if artifact is not None:
        with Image.open(artifact) as rendered:
            size = list(rendered.size)
    if preview_artifact is not None:
        with Image.open(preview_artifact) as rendered:
            preview_size = list(rendered.size)
    return {
        "source_pixels": HUGE_IMAGE_SIZE[0] * HUGE_IMAGE_SIZE[1],
        "source_bytes": path.stat().st_size,
        "fixture_written_in_child_process": True,
        "fixture_ms": fixture_ms,
        "working_set_before_media_bytes": before,
        "thumbnail_ms": thumbnail_ms,
        "thumbnail_state": summary["generated"],
        "thumbnail_size": size,
        "preview_ms": preview_ms,
        "preview_state": preview["generated"],
        "preview_size": preview_size,
        "working_set_after_huge_bytes": peak,
        "working_set_delta_bytes": peak - before,
    }


def _write_huge_fixture(path: Path) -> None:
    module_dir = Path(__file__).resolve().parent
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(module_dir)!r})\n"
        "import media_scale_probe as probe\n"
        f"probe._photo(3, probe.HUGE_IMAGE_SIZE).save({str(path)!r}, 'JPEG', quality=85)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:  # pragma: no cover - diagnostic tool
        raise RuntimeError(f"cannot write the oversized fixture: {completed.stderr}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, action="append", default=None)
    parser.add_argument("--image-size", default="1600x1200")
    parser.add_argument(
        "--noisy", action="store_true", help="add Gaussian grain for realistic file sizes"
    )
    parser.add_argument("--preview", action="store_true", help="also generate previews")
    parser.add_argument("--huge", action="store_true", help="also measure one 108 MP JPEG")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    width, height = (int(part) for part in str(args.image_size).lower().split("x", 1))
    sizes = args.files or [DEFAULT_FILES]
    reports = [
        measure(
            files=size,
            image_size=(width, height),
            noise=args.noisy,
            with_preview=args.preview,
            huge=args.huge,
            keep=args.keep,
            workspace=args.workspace,
        )
        for size in sizes
    ]
    if args.json:
        print(json.dumps({"status": "ok", "reports": reports}, ensure_ascii=False))
    else:
        for report in reports:
            print(
                f"{report['files']:>6} files {report['image_size']:>10}  "
                f"scan {report['scan_ms']:>6} ms  thumbnail {report['thumbnail_ms']:>6} ms  "
                f"{report['thumbnail_ms_per_file']:>6.2f} ms/file  "
                f"{report['thumbnail_bytes_per_artifact']:>6} B/artifact  "
                f"peak {report['peak_working_set_bytes'] / 1048576:>7.1f} MiB  "
                f"delta {report['working_set_delta_bytes'] / 1048576:>7.1f} MiB"
            )
            if "preview_ms" in report:
                print(
                    f"       preview {report['preview_ms']:>6} ms  "
                    f"{report['preview_ms_per_file']:>6.2f} ms/file  "
                    f"{report['preview_bytes_per_artifact']:>7} B/artifact"
                )
            if "huge" in report:
                huge = report["huge"]
                print(
                    f"       huge {huge['source_pixels'] / 1e6:.0f} MP  "
                    f"thumb {huge['thumbnail_ms']:>6} ms {huge['thumbnail_size']}  "
                    f"preview {huge['preview_ms']:>6} ms {huge['preview_size']}  "
                    f"delta {huge['working_set_delta_bytes'] / 1048576:>7.1f} MiB"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
