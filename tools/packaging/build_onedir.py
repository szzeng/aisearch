"""Build the minimal P0-05 onedir executable without bundling user model weights."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    manifest = root / "tools" / "prepare_models" / "manifests" / "chinese-clip-vit-b-16.json"
    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--name", "PhotoSearch",
        "--paths", str(root / "src"), "--add-data", f"{manifest};tools/prepare_models/manifests",
        "--collect-binaries", "onnxruntime", "--collect-data", "tokenizers",
        "--exclude-module", "torch", "--exclude-module", "torchvision",
        str(root / "src" / "photo_search" / "__main__.py"),
    ]
    return subprocess.run(command, cwd=root, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())