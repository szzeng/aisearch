"""P4-00C-A §4 evidence: hash every derived payload of the *existing* input formats.

The task card asks for a processor-id ruling: either the artifacts of JPEG/PNG/WebP/BMP/GIF
are byte-for-byte the same before and after HEIC support was added, or the processor
identity has to be bumped. This tool produces that evidence by decoding a fixed synthetic
fixture set and printing the SHA-256 of each rendered thumbnail/preview payload, so two runs
(baseline source tree vs. the changed tree) can be compared line by line.

Usage (the interpreter is the project ``.venv``; nothing here writes outside the project)::

    python tools/diagnostics/media_artifact_fingerprint.py <output.txt> [<work-dir>]

``PHOTO_SEARCH_SRC`` selects the source root to import from (default ``src``); point it at
an extraction of the baseline commit to reproduce the "before" side.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

SOURCE_ROOT = os.environ.get("PHOTO_SEARCH_SRC", "src")
sys.path.insert(0, str(Path(SOURCE_ROOT).resolve()))

from PIL import Image  # noqa: E402

from photo_search.media import contract, decode  # noqa: E402

ORIENTATION = 0x0112


def quadrants(width: int, height: int) -> Image.Image:
    """Four distinct colours plus an off-centre marker, as the media tests use."""
    image = Image.new("RGB", (width, height))
    image.paste((230, 20, 20), (0, 0, width // 2, height // 2))
    image.paste((20, 230, 20), (width // 2, 0, width, height // 2))
    image.paste((20, 20, 230), (0, height // 2, width // 2, height))
    image.paste((235, 235, 30), (width // 2, height // 2, width, height))
    image.paste((0, 0, 0), (8, 8, 56, 40))
    return image


def build_fixtures(directory: Path) -> list[Path]:
    """One file per existing input format, including the four EXIF orientations."""
    directory.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for orientation in (1, 3, 6, 8):
        exif = Image.Exif()
        exif[ORIENTATION] = orientation
        path = directory / f"photo-{orientation}.jpg"
        quadrants(4032, 3024).save(path, "JPEG", quality=93, exif=exif)
        paths.append(path)
    grey = directory / "grey.png"
    quadrants(640, 480).convert("L").save(grey, "PNG")
    paths.append(grey)
    alpha = directory / "alpha.png"
    rgba = Image.new("RGBA", (500, 300), (255, 0, 0, 255))
    rgba.paste((0, 0, 0, 0), (0, 0, 250, 300))
    rgba.save(alpha, "PNG")
    paths.append(alpha)
    webp = directory / "photo.webp"
    quadrants(1200, 900).save(webp, "WEBP", quality=90)
    paths.append(webp)
    bmp = directory / "photo.bmp"
    quadrants(320, 200).save(bmp, "BMP")
    paths.append(bmp)
    gif = directory / "photo.gif"
    quadrants(300, 180).convert("P").save(gif, "GIF")
    paths.append(gif)
    return paths


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    output = Path(sys.argv[1])
    work = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(".p4-00c-a-fingerprint")
    lines: list[str] = []
    for path in build_fixtures(work / "fixtures"):
        for spec in (contract.THUMBNAIL_SPEC, contract.PREVIEW_SPEC):
            decoded = decode.load_normalized(path, long_edge=spec.long_edge)
            payload = decode.render_jpeg(
                decoded.image, long_edge=spec.long_edge, quality=spec.jpeg_quality
            )
            lines.append(
                f"{path.name} {spec.kind} {decoded.source_format} "
                f"{decoded.width}x{decoded.height} {decoded.orientation} "
                f"{hashlib.sha256(payload).hexdigest()}"
            )
            decoded.image.close()
    text = "\n".join(lines) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8", newline="\n")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
