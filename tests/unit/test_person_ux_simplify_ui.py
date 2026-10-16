"""Exercise the shipped organization modules with synthetic paged API responses."""

import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest


@pytest.mark.parametrize("scenario", ["navigation", "ignored_refresh", "collection_choices", "faces", "inbox", "merge", "mapping", "history", "retry", "stale", "mode", "selection", "escape"])
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


def test_unrecognized_page_keeps_low_frequency_tools_inside_collapsed_more_menu() -> None:
    class IdTree(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.stack: list[tuple[str, str | None]] = []
            self.nodes: dict[str, tuple[str, str | None]] = {}
            self.duplicate_ids: set[str] = set()
            self.text: list[str] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            attributes = dict(attrs)
            node_id = attributes.get("id")
            parent_id = self.stack[-1][1] if self.stack else None
            if node_id:
                if node_id in self.nodes:
                    self.duplicate_ids.add(node_id)
                self.nodes[node_id] = (tag, parent_id)
            if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
                self.stack.append((tag, node_id or parent_id))

        def handle_endtag(self, tag: str) -> None:
            for index in range(len(self.stack) - 1, -1, -1):
                if self.stack[index][0] == tag:
                    del self.stack[index:]
                    break

        def handle_data(self, data: str) -> None:
            self.text.append(data)

    root = Path(__file__).resolve().parents[2]
    html = (root / "src/photo_search/web/persons.html").read_text(encoding="utf-8")
    tree = IdTree()
    tree.feed(html)
    assert not tree.duplicate_ids
    assert tree.nodes["pa-organize-tools"][0] == "details"
    assert tree.nodes["pa-inbox-content"][0] == "div"
    for node_id in ("pa-quality-tools", "pt-review-entry", "pt-history-entry", "pa-inbox-help", "pa-recognition-tools"):
        parent_id = tree.nodes[node_id][1]
        ancestors = {parent_id}
        while parent_id and parent_id in tree.nodes:
            parent_id = tree.nodes[parent_id][1]
            ancestors.add(parent_id)
        assert "pa-organize-tools" in ancestors, node_id
    inbox_text = " ".join(tree.text)
    assert "选一个熟悉的人，把这张脸归进去" not in inbox_text
    assert "更多操作" in inbox_text and "未识别人脸" in inbox_text
    assert 'id="pa-inbox-batch-toggle" type="button" hidden' not in html
    assert 'id="pa-inbox-hide-temporary" type="button" hidden' in html
