"""Build the onedir executable with the CUDA runtime and without user model weights.

The product ships one ONNX Runtime distribution (``onnxruntime-gpu``). Its CUDA execution
provider needs the matching CUDA/cuDNN DLLs, which the ``nvidia-*`` wheels install outside
the ``onnxruntime`` package. PyInstaller does not collect a namespace package like
``nvidia`` on its own, so the DLLs are stacked flat into ``nvidia_dlls/<name>`` inside the
bundle; ``photo_search.runtime.device.bundled_dll_directory`` finds that folder and
registers it before the CUDA session is created.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import onnxruntime

DLL_TARGET_DIRECTORY = "nvidia_dlls"


def site_packages_directory() -> Path:
    return Path(onnxruntime.__file__).resolve().parents[1]


def nvidia_runtime_dlls(site_packages: Path) -> list[Path]:
    """Every CUDA/cuDNN DLL the ``nvidia`` wheels installed, with duplicate names rejected."""
    root = site_packages / "nvidia"
    if not root.is_dir():
        return []
    seen: dict[str, Path] = {}
    for path in sorted(root.rglob("*.dll")):
        if not path.is_file():
            continue
        existing = seen.get(path.name)
        if existing is not None and existing.stat().st_size != path.stat().st_size:
            raise SystemExit(f"conflicting NVIDIA DLLs named {path.name}: {existing} vs {path}")
        seen[path.name] = path
    return [seen[name] for name in sorted(seen)]


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    manifest = root / "tools" / "prepare_models" / "manifests" / "chinese-clip-vit-b-16.json"
    dlls = nvidia_runtime_dlls(site_packages_directory())
    if not dlls:
        raise SystemExit(
            "no NVIDIA runtime DLLs found; run `uv sync --all-groups` so onnxruntime-gpu "
            "and its nvidia-* wheels are installed"
        )
    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--name", "PhotoSearch",
        "--paths", str(root / "src"), "--add-data", f"{manifest};tools/prepare_models/manifests",
        "--collect-binaries", "onnxruntime", "--collect-data", "tokenizers",
        "--exclude-module", "torch", "--exclude-module", "torchvision",
        # The catalog migrations are ``.sql`` data files inside the package: without them a
        # frozen build would start with no schema at all (P1-04 report §6.1).
        "--collect-data", "photo_search",
    ]
    for dll in dlls:
        command += ["--add-binary", f"{dll};{DLL_TARGET_DIRECTORY}"]
    command.append(str(root / "src" / "photo_search" / "__main__.py"))
    print(f"bundling {len(dlls)} NVIDIA runtime DLLs into {DLL_TARGET_DIRECTORY}/")
    return subprocess.run(command, cwd=root, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
