"""Exercise the shipped organization modules with synthetic paged API responses."""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("scenario", ["faces", "inbox", "merge", "mapping", "history", "retry", "stale", "mode", "selection", "escape"])
def test_person_organization_workflows(scenario: str) -> None:
    node = shutil.which("node")
    assert node, "Node is required for the person workflow checks"
    result = subprocess.run(
        [node, "tests/unit/person_ux_simplify_harness.cjs", scenario],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True, encoding="utf-8", timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_loaded_select_all_and_invert_shortcuts_are_absent_from_pages() -> None:
    root = Path(__file__).resolve().parents[2]
    pages = [
        root / "src/photo_search/web/persons.html",
        root / "docs/mockups/ui2-interactive/demo.html",
        root / "docs/mockups/ui2-interactive/app.js",
    ]
    for page in pages:
        content = page.read_text(encoding="utf-8")
        assert "全选已加载" not in content, page
        assert "反选已加载" not in content, page
        assert "select-loaded" not in content, page
        assert "invert-loaded" not in content, page
