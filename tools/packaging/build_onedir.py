"""Build the onedir executable with the CUDA runtime and without user model weights.

The product ships one ONNX Runtime distribution (``onnxruntime-gpu``). Its CUDA execution
provider needs the matching CUDA/cuDNN DLLs, which the ``nvidia-*`` wheels install outside
the ``onnxruntime`` package. PyInstaller does not collect a namespace package like
``nvidia`` on its own, so the DLLs are stacked flat into ``nvidia_dlls/<name>`` inside the
bundle; ``photo_search.runtime.device.bundled_dll_directory`` finds that folder and
registers it before the CUDA session is created.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import onnxruntime

DLL_TARGET_DIRECTORY = "nvidia_dlls"


def pyinstaller_environment() -> dict[str, str]:
    """Prefer Windows' active VC runtime over unrelated DLLs earlier on PATH.

    PyInstaller resolves transitive Windows DLL dependencies through PATH. On this
    machine, a third-party application's older ``msvcp140.dll`` was ahead of
    System32 and was copied into the package, where it crashed before Python
    could print even ``--help``. Keep the change scoped to the PyInstaller child.
    """
    env = os.environ.copy()
    system32 = Path(os.environ["WINDIR"]) / "System32"
    env["PATH"] = os.pathsep.join((str(system32), env.get("PATH", "")))
    return env


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
    parser = argparse.ArgumentParser(description="Build the frozen PhotoSearch onedir bundle")
    parser.add_argument(
        "--distpath",
        type=Path,
        default=Path("dist"),
        help="PyInstaller output root (use a task-local path to preserve an existing bundle)",
    )
    parser.add_argument("--workpath", type=Path, default=Path("build"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    manifest_dir = root / "tools" / "prepare_models" / "manifests"
    dlls = nvidia_runtime_dlls(site_packages_directory())
    if not dlls:
        raise SystemExit(
            "no NVIDIA runtime DLLs found; run `uv sync --all-groups` so onnxruntime-gpu "
            "and its nvidia-* wheels are installed"
        )
    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--name", "PhotoSearch",
        "--distpath", str(args.distpath.resolve()), "--workpath", str(args.workpath.resolve()),
        "--specpath", str(args.workpath.resolve()),
        "--paths", str(root / "src"), "--add-data", f"{manifest_dir};tools/prepare_models/manifests",
        "--collect-binaries", "onnxruntime", "--collect-data", "tokenizers",
        # ``pillow_heif`` is imported only after the media capability probe.  PyInstaller
        # cannot see that dynamic import, and the wheel has both a top-level extension and
        # a sibling libheif DLL.  Collecting the package as a unit keeps the frozen build
        # capability-identical to the locked development environment (P4-00C-B).
        "--collect-all", "pillow_heif",
        # LibRaw is loaded at the CR2 adapter boundary rather than as a top-level import;
        # collect its extension and sibling runtime binaries explicitly.
        "--collect-all", "rawpy",
        # Qwen is loaded dynamically by the manifest-selected adapter in a spawned worker.
        # Collect its framework/runtime packages explicitly, plus the one Transformers model
        # family and helper used by that adapter; do not collect unrelated model families.
        "--collect-all", "torch", "--collect-all", "torchvision",
        "--collect-submodules", "transformers.models.qwen3_vl",
        "--collect-submodules", "qwen_vl_utils",
        "--collect-all", "huggingface_hub",
        "--copy-metadata", "torch", "--copy-metadata", "torchvision",
        "--copy-metadata", "transformers", "--copy-metadata", "tokenizers",
        "--copy-metadata", "safetensors", "--copy-metadata", "qwen-vl-utils",
        "--copy-metadata", "huggingface-hub",
        # Collect resources from this checkout directly. ``--collect-data photo_search``
        # can resolve an editable install in the build environment and silently package
        # different web files than the source tree used for the build.
        "--add-data", f"{root / 'src' / 'photo_search' / 'catalog' / 'migrations'};photo_search/catalog/migrations",
        "--add-data", f"{root / 'src' / 'photo_search' / 'web'};photo_search/web",
    ]
    for dll in dlls:
        command += ["--add-binary", f"{dll};{DLL_TARGET_DIRECTORY}"]
    command.append(str(root / "src" / "photo_search" / "__main__.py"))
    print(f"bundling {len(dlls)} NVIDIA runtime DLLs into {DLL_TARGET_DIRECTORY}/")
    result = subprocess.run(
        command, cwd=root, env=pyinstaller_environment(), check=False
    ).returncode
    if result == 0:
        bundle = args.distpath.resolve() / "PhotoSearch"
        for filename in (
            "README-PORTABLE.txt",
            "NEW-PC-QUICK-START.txt",
            "Download-Qwen.cmd",
            "Start-PhotoSearch.cmd",
        ):
            shutil.copy2(root / "tools" / "packaging" / filename, bundle / filename)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
