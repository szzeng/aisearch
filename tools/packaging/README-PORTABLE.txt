Photo Search Windows x64 Portable — Personal Trial

System requirements
- Windows 11 x64 is the intended target. Windows 10 x64 has not been separately accepted.
- NVIDIA GPU and a compatible driver are required for AI embedding. This package does not certify GTX 1660, other minimum hardware, or another PC.
- This is a personal-use portable package, not a signed installer or public redistribution release.

Start
1. Extract the complete ZIP to a normal directory. The application data is stored separately.
2. Double-click Download-Qwen.cmd to confirm and download the fixed Qwen revision (about 4.3 GB); keep its console visible for progress. Ctrl+C cancels. Or use the `embedding prepare-local` command below for an existing verified local model directory.
3. Double-click Start-PhotoSearch.cmd. Keep its console window open while using the app.
4. To exit normally, press Ctrl+C in that console and wait for shutdown to complete. Closing the browser tab does not stop the local service.
5. You may move the complete program directory and launch it again. Deleting the ZIP or program directory does not remove the data root.

Model files
The shipped manifests are templates, not weights. Do not download an unpinned upstream "latest" model. Use a locally prepared model whose revision and every file SHA-256 match the manifest under _internal/tools/prepare_models/manifests. Qwen3-VL-Embedding-2B is pinned to revision 9f2f7e710d6d81056aa5c0a4f04764fec6bb7bda; its model.safetensors SHA-256 is c73fa9caeddeb3ff831d46c085a7a5708343248ca777e90f2d486964464509c1. The 512D and 1024D EXIF v2 providers share these weights but use independent vector spaces. To install an already-prepared local directory, use:
  PhotoSearch.exe embedding prepare-local --provider qwen3-vl-embedding-2b-exif-v2 --source <prepared-model-directory>
  PhotoSearch.exe embedding prepare-local --provider qwen3-vl-embedding-2b-1024d-exif-v2 --source <prepared-model-directory>
The app verifies every pinned size and SHA-256 before accepting files. Download uses the pinned repository/commit and allowlisted filenames only. It stages under the data root and does not publish incomplete downloads or replace a valid install.

Data and diagnostics
Default data root: %LOCALAPPDATA%\PhotoSearchData. The data root is independent of this program directory. For isolated diagnostics, pass --data-root <directory> after PhotoSearch.exe; never point a smoke test at a personal library.
The app binds to 127.0.0.1 and creates a one-time local session. If startup fails, keep the console open and read its error. No model, CUDA, port, permission, or worker failure is supposed to silently switch the AI provider or run Qwen on CPU.

Supported content and safety
Original photos are read-only. Catalog, vectors, thumbnails, runtime files and prepared model files live under the data root. Startup does not migrate, rebuild, or clean an existing library automatically. Back up the data root before using the app.

Third-party components and notices
This build includes Python 3.12.10, PyTorch 2.8.0+cu128, torchvision 0.23.0+cu128, Transformers 4.57.0, tokenizers, safetensors, qwen-vl-utils 0.0.14, ONNX Runtime GPU, NVIDIA runtime DLLs, Pillow/pillow-heif, rawpy, FastAPI, Uvicorn, LanceDB and their locked transitive dependencies. See each bundled *.dist-info/METADATA and LICENSE/NOTICE file under _internal for its license terms. PyTorch/torchvision and ONNX Runtime use distinct CUDA DLL locations; do not merge or replace them manually.
Qwen3-VL-Embedding-2B is Apache-2.0 as identified by its shipped manifest. Model weights are not included. InsightFace third-party model weights are not included. Verify the license and redistribution rights of every component before sharing this ZIP; this package is for local personal evaluation only.

Unverified
This package is not accepted for clean-PC cross-machine operation, GTX 1660 support, public redistribution, formal capacity/performance targets, or model quality. It does not include an installer, updater, or data migration wizard.
