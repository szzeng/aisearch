r"""Corpus scanning and deterministic attribute extraction for the authorized sample.

Only the explicitly authorized source directories are read. Nothing is written next to the
photos and no photo is modified, moved or deleted. The corpus file keeps opaque image ids and
the absolute paths stay in the user data directory, outside the repository.

The attributes here are the *label signal* for the evaluation: exposure/colour statistics,
capture time and orientation come from the image itself, so the ground truth never depends on
the model that is being evaluated.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
STAT_SIDE = 384
EPOCH = dt.datetime(1900, 1, 1)

HUE_NAMES = ("red", "orange", "yellow", "green", "cyan", "blue", "purple", "magenta")


class CorpusError(RuntimeError):
    """The authorized corpus could not be built as requested."""


@dataclass(frozen=True)
class CorpusImage:
    id: str
    path: Path
    source: str

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "path": str(self.path), "source": self.source}


def scan_sources(sources: Iterable[Path], *, limit: int | None = None) -> list[CorpusImage]:
    """List image files under the authorized roots, in a deterministic order.

    A stride is applied when more files exist than the requested limit, so the sample is
    spread across all authorized directories instead of taking one folder's prefix.
    """
    found: list[tuple[str, Path, str]] = []
    for source in sources:
        root = Path(source)
        if not root.is_dir():
            raise CorpusError(f"authorized source is not a directory: {root}")
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            try:
                relative_key = path.relative_to(root).as_posix().lower()
            except ValueError:  # pragma: no cover - defensive
                relative_key = path.name.lower()
            found.append((f"{root.name}/{relative_key}", path, root.name))
    found.sort(key=lambda entry: (entry[0], str(entry[1])))
    if limit is not None and len(found) > limit:
        stride = len(found) / limit
        found = [found[min(len(found) - 1, int(index * stride))] for index in range(limit)]
    return [
        CorpusImage(id=f"img_{index:05d}", path=path, source=source)
        for index, (_, path, source) in enumerate(found, start=1)
    ]


def _hue_name(pixel: np.ndarray) -> str:
    # Pillow's HSV mode stores hue in 0..255, not 0..360.
    hue = pixel[..., 0].astype(np.float32) * (360.0 / 255.0)
    saturation = pixel[..., 1]
    value = pixel[..., 2]
    vivid = (saturation >= 60) & (value >= 60)
    if not bool(vivid.any()):
        return "neutral"
    counts = []
    for index, name in enumerate(HUE_NAMES):
        low = index * 30
        high = 360 if index == 7 else low + 30
        counts.append((name, int(((hue >= low) & (hue < high) & vivid).sum())))
    # 330-360 degrees is red, not magenta.
    magenta, red = counts[7], counts[0]
    counts[7] = (magenta[0], magenta[1])
    counts[0] = (red[0], red[1] + magenta[1])
    best = max(counts, key=lambda entry: entry[1])
    return best[0] if best[1] > 0 else "neutral"


def _capture_time(image: Image.Image, path: Path) -> tuple[dt.datetime, str]:
    for key in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
        raw = (image.getexif() or {}).get(0x9003 if key == "DateTimeOriginal" else 0x9004 if key == "DateTimeDigitized" else 0x0132)
        if not raw:
            continue
        try:
            parsed = dt.datetime.strptime(str(raw).strip(), "%Y:%m:%d %H:%M:%S")
        except ValueError:
            continue
        if parsed > EPOCH:
            return parsed, "exif"
    return dt.datetime.fromtimestamp(path.stat().st_mtime), "mtime"


def image_attributes(image: CorpusImage) -> dict[str, Any]:
    """Exposure, colour, geometry and capture-time facts for one authorized image."""
    path = image.path
    try:
        with Image.open(path) as source:
            exif_time, time_source = _capture_time(source, path)
            oriented = ImageOps.exif_transpose(source).convert("RGB")
            width, height = oriented.size
            thumbnail = oriented.copy()
            thumbnail.thumbnail((STAT_SIDE, STAT_SIDE), Image.Resampling.BILINEAR)
            array = np.asarray(thumbnail, dtype=np.uint8)
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise CorpusError(f"cannot read {path.name}: {type(error).__name__}: {error}") from error

    hsv = np.asarray(Image.fromarray(array).convert("HSV"), dtype=np.uint8)
    luma = (0.299 * array[..., 0] + 0.587 * array[..., 1] + 0.114 * array[..., 2]) / 255.0
    saturation = hsv[..., 1].astype(np.float32) / 255.0
    channel_spread = (
        array.max(axis=2).astype(np.int16) - array.min(axis=2).astype(np.int16)
    ) / 255.0
    # Channel means are kept in 0..1 so the colour rules read like the other thresholds.
    channels = array.astype(np.float32).mean(axis=(0, 1)) / 255.0
    red, green, blue = (float(value) for value in channels)
    return {
        "id": image.id,
        "path": str(path),
        "source": image.source,
        "bytes": path.stat().st_size,
        "width": width,
        "height": height,
        "orientation": (
            "portrait" if height > width * 1.05 else "landscape" if width > height * 1.05 else "square"
        ),
        "aspect": round(width / height, 4),
        "brightness": round(float(luma.mean()), 4),
        "contrast": round(float(luma.std()), 4),
        "saturation": round(float(saturation.mean()), 4),
        "colourfulness": round(float(channel_spread.mean()), 4),
        "grey_ratio": round(float((channel_spread < 0.06).mean()), 4),
        "dominant_hue": _hue_name(hsv),
        "red_mean": round(red, 4),
        "green_mean": round(green, 4),
        "blue_mean": round(blue, 4),
        "redness": round(red - max(green, blue), 4),
        "greenness": round(green - max(red, blue), 4),
        "blueness": round(blue - max(red, green), 4),
        "warmth": round(red - blue, 4),
        "luma_mean": round(float((red + green + blue) / 3.0), 4),
        "captured_at": exif_time.isoformat(timespec="seconds"),
        "capture_time_source": time_source,
        "year": exif_time.year,
        "month": exif_time.month,
        "hour": exif_time.hour,
        "weekday": exif_time.weekday(),
        "sha256": None,
    }


def file_digest(path: Path, chunk_bytes: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_bytes), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def default_evaluation_dir() -> Path:
    import os

    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return base / "PhotoSearchData" / "evaluation"
