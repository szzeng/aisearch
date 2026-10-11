"""Exercise the shipped organization modules with synthetic paged API responses."""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("scenario", ["faces", "inbox", "merge", "mapping", "history", "retry", "stale", "mode", "selection"])
def test_person_organization_workflows(scenario: str) -> None:
    node = shutil.which("node")
    assert node, "Node is required for the person workflow checks"
    result = subprocess.run(
        [node, "tests/unit/person_ux_simplify_harness.cjs", scenario],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True, encoding="utf-8", timeout=15,
    )
    assert result.returncode == 0, result.stderr
