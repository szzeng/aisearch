"""Prepare a disposable frozen-bundle copy with one pillow-heif half removed (P4-00C-B).

The degraded acceptance needs a bundle that *cannot* decode HEIC while everything else is
intact. Doing that by hand is neither reproducible nor auditable, so this tool copies the
bundle, removes exactly the named component, verifies what is left, and prints one JSON
inventory that the environment report can quote:

* ``--remove libheif``   - the ``libheif-<hash>.dll`` runtime stays broken/missing while the
  compiled ``_pillow_heif`` extension is still present. This is the case that used to answer
  ``DECODE_FAILED``: ``pillow_heif.register_heif_opener()`` installs the Pillow opener
  *before* it asks the extension for its codecs.
* ``--remove extension`` - ``import pillow_heif`` itself fails inside the frozen process.
* ``--remove both``      - nothing of the decoder is left.

Usage::

    uv run --no-sync python tools/diagnostics/heif_degraded_bundle.py \
        --source dist/PhotoSearch --dest .packaged-heif-degraded-p400cb --remove libheif
    uv run --no-sync python tools/diagnostics/packaged_api_smoke.py \
        --bundle .packaged-heif-degraded-p400cb --heif-degraded libheif \
        --work-dir .packaged-heif-degraded-smoke-p400cb --output test-results/...json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

EXTENSION_PREFIX = "_pillow_heif"
LIBHEIF_PREFIX = "libheif-"
#: Which component kinds each removal mode deletes.
REMOVAL_KINDS = {
    "libheif": ("libheif",),
    "extension": ("extension",),
    "both": ("libheif", "extension"),
}


def digest(path: Path) -> str:
    sha256 = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def component_paths(root: Path, kind: str) -> list[Path]:
    """Every file of one half of the decoder inside a bundle."""
    found: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        name = path.name.lower()
        if kind in ("extension", "both") and name.startswith(EXTENSION_PREFIX):
            found.append(path)
        elif kind in ("libheif", "both") and name.startswith(LIBHEIF_PREFIX) and name.endswith(".dll"):
            found.append(path)
    return found


def inventory(root: Path) -> dict[str, Any]:
    return {
        kind: {
            str(path.relative_to(root)): {"bytes": path.stat().st_size, "sha256": digest(path)}
            for path in component_paths(root, kind)
        }
        for kind in ("libheif", "extension")
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="the complete onedir bundle")
    parser.add_argument("--dest", type=Path, required=True, help="where the copy is written")
    parser.add_argument("--remove", choices=("libheif", "extension", "both"), required=True)
    parser.add_argument("--force", action="store_true", help="replace an existing destination")
    args = parser.parse_args()

    source = args.source.resolve()
    dest = args.dest.resolve()
    if not (source / "PhotoSearch.exe").is_file():
        print(f"error: no packaged executable under {source}", file=sys.stderr)
        return 2
    if dest.exists():
        if not args.force:
            print(f"error: {dest} already exists (pass --force to replace it)", file=sys.stderr)
            return 2
        shutil.rmtree(dest)
    shutil.copytree(source, dest)

    before = inventory(dest / "_internal")
    targets = component_paths(dest / "_internal", args.remove)
    removed = [str(path.relative_to(dest)) for path in targets]
    if not removed:
        print(
            f"error: no {args.remove} component found under {dest / '_internal'}",
            file=sys.stderr,
        )
        return 1
    for path in component_paths(dest / "_internal", args.remove):
        path.unlink()

    remaining = inventory(dest / "_internal")
    kinds = REMOVAL_KINDS[args.remove]
    report = {
        "status": "ok",
        "source": str(source),
        "destination": str(dest),
        "removed_kind": args.remove,
        "removed": removed,
        "components_before": before,
        "components_after": remaining,
        "source_untouched": (source / "PhotoSearch.exe").is_file(),
        "verified": all(not remaining[kind] for kind in kinds),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
