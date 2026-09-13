"""P2-03B: the explicit local dataset tool and its checked-in example.

The tool is the only supported way to prepare and import a package; the application never
downloads one. Its example spec is verified here against the checked-in manifest, so the
documented sample cannot drift away from the builder.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from photo_search.catalog.schema import SUPPORTED_SCHEMA_VERSION as SUPPORTED
from photo_search.geo import dataset as dataset_module

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL = REPO_ROOT / "tools" / "places" / "manage_dataset.py"
SPEC = REPO_ROOT / "tools" / "places" / "examples" / "mini-cities.spec.json"
MANIFEST = REPO_ROOT / "tools" / "places" / "examples" / "mini-cities.manifest.json"


def run_tool(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), *arguments],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
        check=False,
    )


def test_the_example_spec_builds_the_checked_in_manifest(tmp_path: Path) -> None:
    spec = json.loads(SPEC.read_text(encoding="utf-8"))

    manifest = dataset_module.build_package(spec, tmp_path / "package")

    assert manifest == json.loads(MANIFEST.read_text(encoding="utf-8"))
    dataset_module.load_content(tmp_path / "package", supported_schema_version=int(SUPPORTED))


def test_the_local_tool_builds_imports_activates_and_reports_status(tmp_path: Path) -> None:
    package = tmp_path / "package"
    data_root = tmp_path / "PhotoSearchData"

    built = run_tool("build", "--spec", str(SPEC), "--out", str(package))
    assert built.returncode == 0, built.stderr
    assert json.loads(built.stdout)["status"] == "ok"
    assert (package / "manifest.json").is_file()

    imported = run_tool(
        "--data-root", str(data_root), "import", "--source", str(package), "--activate"
    )
    assert imported.returncode == 0, imported.stderr
    body = json.loads(imported.stdout)
    assert body["imported"]["state"] == "VALIDATED"
    assert body["activated"]["state"] == "ACTIVE"

    status = run_tool("--data-root", str(data_root), "status")
    assert status.returncode == 0, status.stderr
    projection = json.loads(status.stdout)["datasets"]
    assert projection["state"] == "active"
    assert projection["active_dataset"]["dataset_id"] == "geo-mini-cn-example"
    assert projection["active_dataset"]["record_count"] == 3
    assert str(data_root) not in status.stdout

    # There is only one package, so a rollback has nothing to return to.
    rollback = run_tool("--data-root", str(data_root), "rollback")
    assert rollback.returncode == 2
    assert json.loads(rollback.stderr)["code"] == "PLACE_DATASET_MISSING"

    # The active package is referenced, so it cannot be cleaned up.
    cleanup = run_tool(
        "--data-root", str(data_root), "cleanup", "--dataset-id", "geo-mini-cn-example"
    )
    assert cleanup.returncode == 2
    assert json.loads(cleanup.stderr)["code"] == "PLACE_REFERENCED"
