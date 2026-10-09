from __future__ import annotations

from pathlib import Path

import pytest

from tools.diagnostics.p4_02y_l_workdir import prepare_work_dir


def test_fixture_workdir_refuses_to_replace_existing_content(tmp_path: Path) -> None:
    target = tmp_path / "existing-library"
    target.mkdir()
    sentinel = target / "keep.jpg"
    sentinel.write_bytes(b"user content")

    with pytest.raises(FileExistsError, match="existing content was preserved"):
        prepare_work_dir(target)

    assert sentinel.read_bytes() == b"user content"


def test_fixture_workdir_refuses_a_new_child_under_existing_content(tmp_path: Path) -> None:
    parent = tmp_path / "existing-library"
    parent.mkdir()
    sentinel = parent / "keep.jpg"
    sentinel.write_bytes(b"user content")
    target = parent / "new-workdir"

    with pytest.raises(FileExistsError, match="parent is not empty"):
        prepare_work_dir(target)

    assert sentinel.read_bytes() == b"user content"
    assert not target.exists()


def test_fixture_workdir_refuses_a_path_outside_dedicated_temp_roots() -> None:
    with pytest.raises(ValueError, match="must be below project OCR temp"):
        prepare_work_dir(Path(__file__).resolve().parents[2])


def test_fixture_workdir_accepts_a_new_empty_temp_directory(tmp_path: Path) -> None:
    target = tmp_path / "synthetic"

    assert prepare_work_dir(target) == target.resolve()
    assert target.is_dir()
    assert list(target.iterdir()) == []
