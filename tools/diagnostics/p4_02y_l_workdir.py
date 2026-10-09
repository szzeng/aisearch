"""Guard work directories used by P4-02Y-L diagnostic fixtures."""

from __future__ import annotations

import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECT_FIXTURE_ROOT = (REPO_ROOT / "tmp" / "p4-02y-l").resolve()
PROJECT_PYTEST_TEMP_ROOT = (REPO_ROOT / ".pytest-tmp").resolve()
SYSTEM_TEMP_ROOT = Path(tempfile.gettempdir()).resolve()


def prepare_work_dir(work_dir: str | Path) -> Path:
    """Create an empty synthetic-work directory without replacing existing files."""
    target = Path(work_dir).expanduser().resolve()
    allowed = (PROJECT_FIXTURE_ROOT, PROJECT_PYTEST_TEMP_ROOT, SYSTEM_TEMP_ROOT)
    if not any(target != root and root in target.parents for root in allowed):
        raise ValueError(
            "OCR fixture work directories must be below project OCR temp, pytest temp, or system temp"
        )
    existing_parent = target.parent
    while not existing_parent.exists():
        existing_parent = existing_parent.parent
    if existing_parent not in allowed and any(existing_parent.iterdir()):
        raise FileExistsError(
            f"OCR fixture parent is not empty; existing content was preserved: {existing_parent}"
        )
    if target.exists():
        if not target.is_dir():
            raise FileExistsError(f"OCR fixture work directory is not a directory: {target}")
        if any(target.iterdir()):
            raise FileExistsError(
                f"OCR fixture work directory is not empty; existing content was preserved: {target}"
            )
    target.mkdir(parents=True, exist_ok=True)
    return target


def new_work_dir(label: str) -> Path:
    """Reserve a fresh project-local run directory for a diagnostic CLI default."""
    if not label or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in label):
        raise ValueError("label must be lowercase ASCII letters, digits, or hyphens")
    PROJECT_FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    target = Path(tempfile.mkdtemp(prefix=f"{label}-", dir=PROJECT_FIXTURE_ROOT))
    return prepare_work_dir(target)
