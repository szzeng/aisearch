"""Exercise the shipped organization modules with synthetic paged API responses."""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("scenario", ["navigation", "faces", "inbox", "merge", "mapping", "history", "retry", "stale", "mode", "selection", "escape"])
def test_person_organization_workflows(scenario: str) -> None:
    node = shutil.which("node")
    assert node, "Node is required for the person workflow checks"
    result = subprocess.run(
        [node, "tests/unit/person_ux_simplify_harness.cjs", scenario],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True, encoding="utf-8", timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_loaded_select_all_and_invert_shortcuts_are_scoped_to_collection_members() -> None:
    root = Path(__file__).resolve().parents[2]
    pages = [
        root / "src/photo_search/web/persons.html",
        root / "docs/mockups/ui2-interactive/demo.html",
        root / "docs/mockups/ui2-interactive/app.js",
    ]
    persons = pages[0].read_text(encoding="utf-8")
    collection_actions = persons.split('id="pa-collection-people-actions"', 1)[1].split("</div>", 1)[0]
    assert 'id="pa-collection-people-select-loaded"' in collection_actions
    assert 'id="pa-collection-people-invert-loaded"' in collection_actions
    for page in pages[1:]:
        content = page.read_text(encoding="utf-8")
        assert "全选已加载" not in content, page
        assert "反选已加载" not in content, page
        assert "select-loaded" not in content, page
        assert "invert-loaded" not in content, page
