# Windows personal portable package

From a **clean, committed** Windows x64 checkout with Python 3.12.10, the project `.venv`, `uv`, and the locked dependencies available, run this one command from the repository root (or invoke the script by path from another directory):

```powershell
.\tools\packaging\release_windows.ps1
```

The script reads the package version from `pyproject.toml` and checks it against `APP_VERSION` and the `uv.lock` project entry. It refuses dirty worktrees, mismatched versions, insufficient disk space, and existing files for the same version. It builds a fresh onedir in a unique staging directory, runs the frozen API smoke in place against an isolated synthetic data root, then writes a ZIP, SHA-256 file, and JSON report under `releases/`.

If the locked build dependencies are missing, install them in the project environment with `uv sync --group dev --locked`, then rerun the release command.

To add the optional 512D Qwen CUDA smoke, pass a model directory explicitly authorized for reading:

```powershell
.\tools\packaging\release_windows.ps1 -QwenModelDir 'D:\path\to\verified\qwen-model'
```

The smoke runs offline against the package's pinned 512D manifest. The model directory is not copied or recorded in the report. Without this explicit option, the report uses `GPU_NOT_TESTED`.

The ZIP is a personal candidate. When no model directory is explicitly provided, the report says `GPU_NOT_TESTED` and the console says the package can start but Qwen GPU was not tested. This does not certify another Windows machine or GPU. The package does not include a user library, database, model weights, or private configuration. The script does not create tags, upload a GitHub release, or publish the ZIP.

Verify the archive hash in PowerShell:

```powershell
Get-FileHash .\releases\PhotoSearch-v0.7.0-win-x64.zip -Algorithm SHA256
Get-Content .\releases\PhotoSearch-v0.7.0-win-x64.zip.sha256
```

Replace `0.7.0` with the version in the filenames. A future public release needs a separate license and redistribution review. Uploading a candidate manually is a separate user action.
