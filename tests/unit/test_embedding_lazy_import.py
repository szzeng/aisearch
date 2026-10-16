"""Constructing an unloaded provider leaves PyTorch and tokenizers deferred."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("provider,guarded", [("clip", False), ("clip", True), ("qwen", False)])
def test_unloaded_adapter_stays_framework_free(
    tmp_path: Path, provider: str, guarded: bool
) -> None:
    name = "chinese-clip-vit-b-16" if provider == "clip" else "qwen3-vl-embedding-2b"
    manifest = ROOT / "tools/prepare_models/manifests" / f"{name}.json"
    script = """
import json, sys
from pathlib import Path
manifest, models = Path(sys.argv[1]), Path(sys.argv[2])
if sys.argv[3] == 'True':
    from photo_search.workers.ai_worker import prepare_framework_stack
    prepare_framework_stack(manifest)
from photo_search.models.embedding.factory import build_adapter
from photo_search.models.embedding.base import analyze_manifest, read_manifest
adapter = build_adapter(models, manifest)
_, identity = analyze_manifest(read_manifest(manifest))
assert adapter.spec().processor_id == identity.processor_id
assert adapter.spec().space_id == identity.space_id
assert adapter.spec().device == 'unresolved'
assert 'torch' not in sys.modules
assert 'transformers' not in sys.modules
assert 'transformers.models.qwen3_vl.modeling_qwen3_vl' not in sys.modules
print(json.dumps({'processor_id': adapter.spec().processor_id, 'frameworks_loaded': False}))
"""
    environment = dict(os.environ)
    environment.pop("USE_TORCH", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script, str(manifest), str(tmp_path / "absent-models"), str(guarded)],
        capture_output=True, text=True, timeout=60, env=environment,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["frameworks_loaded"] is False
