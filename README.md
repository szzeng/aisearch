# Photo Search

本地个人照片搜索与整理工具，支持增量扫描建库、中文文字搜索、以图搜图，
以及按人物、地点和拍摄时间浏览。照片、模型和索引保存在本机；模型须提前安装。

使用 Python 3.12.10 和 `uv`。安装依赖后，用 `uv run photo-search --data-root D:\path\to\PhotoSearchData`
启动服务，并在浏览器打开启动器给出的一次性会话链接。数据目录应位于安装目录之外。

开发与验证入口：[`当前状态`](docs/development-status.md)、
[`日常测试`](docs/testing.md)、[`性能与可靠性改动`](docs/reports/P4-UPSTREAM-MERGE-20261016.md)。
当前代码和验证仍在迭代，完整验收范围与未完成项以状态页和对应报告为准。

## 仓库目录与本地文件

| 位置 | 用途与 Git 规则 |
| --- | --- |
| `src/`、`tests/`、`tools/` | 应用源码、回归测试、开发与打包工具；包括其中的 `models/`、`runtime/` 和测试数据目录，应纳入 Git。 |
| `docs/` | 架构决策、使用说明和简洁的最终变更报告；原始测试/审查输出不默认提交。 |
| `.tmp/` | 按任务命名的隔离环境、合成数据、原始日志和恢复副本，全部忽略。本机 Python 环境也在这里，清理前先检查引用与运行进程。 |
| `.uv-cache/`、`.pytest-tmp/`、`.pytest_cache/`、`.ruff_cache/` | 依赖和测试缓存，全部忽略；相关工具退出后可按需清理。 |
| `test-results/` | JUnit、测量 JSON、浏览器截图等原始输出，默认忽略；CI 中作为构建附件保存。已有上游跟踪的历史证据保留。 |
| `PhotoSearchData/` | 本机已准备的模型、地点包和设置，全部忽略；不是源码。正式使用时，应用数据目录应位于安装目录之外。 |
| `build/`、`dist/` | 打包输出，忽略；固定版本的前端 vendor 源码按例外保留。 |

新下载的部署包可放到 `.tmp/downloads/`；核对解压文件后再删除 ZIP。
不要整目录删除 `.tmp/` 或 `PhotoSearchData/`：其中可能有正在使用的环境、模型和恢复副本。
`.env` 及数据库日志边文件不进入 Git；不含凭据的 `.env.example` 等模板可以提交。
本地数据目录的忽略规则只匹配根目录，避免隐藏 `src/photo_search/runtime/` 等新增源码。
提交前分别查看 `git diff` 和 `git ls-files --others --exclude-standard`；不要用 `git clean -fdx` 清理本地数据。

## OCR 文字识别

新的 CPU/GPU 识别任务使用 `@3` 处理器，修复密集图片中完整文字被重叠尾段覆盖、
正常长行被方向分类器误翻转的问题。继续使用原有 PP-OCRv4 权重，无需重新下载。
旧 `@1/@2` 结果仍可读取和搜索；升级不会自动重算整个图库。需要更新某张照片的结果时，
将下面的 `123` 替换为它的实际 asset ID，按目标重新识别：

```powershell
uv --cache-dir .uv-cache run python -m photo_search.ocr.cli recognize --data-root D:\path\to\PhotoSearchData --asset-id 123 --force
```

新结果成功后才替换旧结果；失败或取消时保留旧结果。
修复范围与验证说明见 [性能与可靠性改动](docs/reports/P4-UPSTREAM-MERGE-20261016.md)。

## Semantic providers (P4-Embedding-Arch)

Text search and uploaded-image search are served by exactly **one active model**. Two model
families ship today, with legacy and orientation-corrected providers. Each provider has its own
representation space, index generation and search profile, and a
512-dimensional vector from one of them is never mixed with the other:

| provider id | model | adapter | device / precision |
|---|---|---|---|
| `chinese-clip-vit-b-16` | Chinese-CLIP ViT-B-16 (512D) | `chinese_clip_onnx` | CUDA by default, an explicit CPU request is a reported degradation |
| `qwen3-vl-embedding-2b` | Qwen3-VL-Embedding-2B (2048D, 512D output) | `qwen3_vl` | CUDA required, FP16, batch 1, no CPU/offload path |
| `chinese-clip-vit-b-16-exif-v2` | Same CLIP weights; JPEG/HEIF orientation normalized | `chinese_clip_onnx` | Same device policy as legacy CLIP |
| `qwen3-vl-embedding-2b-exif-v2` | Same Qwen weights; also normalizes HEIF EXIF-only orientation | `qwen3_vl` | Same CUDA/FP16 policy as legacy Qwen |

Installed models live under `<data_root>/models/...` and their manifests under
`<data_root>/models/manifests/`. Nothing is downloaded at run time: a missing file, a changed
checksum or an unknown provider is a structured refusal (`EMBEDDING_WEIGHTS_MISSING`,
`EMBEDDING_WEIGHTS_HASH_MISMATCH`, `EMBEDDING_PROVIDER_UNKNOWN`).

Uploads pass the validated original file to the selected adapter, so indexing and image queries
use identical preprocessing. Legacy providers keep their published pixel behavior and identities.
The `-exif-v2` providers share the existing model directories and weight files; they require a new
index generation, but no model download or duplicate weight installation. They do not switch an
existing library automatically. Use the build/select commands below with the chosen `-exif-v2`
provider, and retain the old profile if a return to its previous behavior is needed.

```powershell
uv run photo-search embedding list --data-root D:\path\to\PhotoSearchData

uv run photo-search embedding prepare-local --provider qwen3-vl-embedding-2b `
  --source tmp/qwen-speed-model --data-root D:\path\to\PhotoSearchData

uv run photo-search embedding build-candidate --provider qwen3-vl-embedding-2b `
  --data-root D:\path\to\PhotoSearchData

# Publish the candidate: stop the service and every other writer of this data root first.
uv run photo-search embedding select-profile --profile semantic-image-gen-XXXXXXXX `
  --data-root D:\path\to\PhotoSearchData --stopped
```

The rules those commands enforce:

* `build-candidate` never moves the served pointer. A profile for another space is created as an
  immutable candidate; the first build of a new library, and every daily/incremental build of the
  space that is already active, keep their previous behaviour.
* `select-profile` acquires the data-root maintenance lease, refuses `--stopped`-less runs, and
  publishes the pointer only inside one `BEGIN IMMEDIATE` transaction that re-verifies the whole
  bundle: current-library coverage by the current content revision, no unconfirmed outbox or
  repair entry, readable artifact content, a healthy Lance table, and recorded runtime
  qualification for the device the model really ran on. A failed check leaves the old profile
  active. An offline file or a partial build is a refusal, never a silent partial switch.
* The lease only excludes *managed* processes. The operator's `--stopped` declaration covers the
  HTTP service, the first-party maintenance writers and anything else holding the data root.

Evidence for this slice: [`docs/reports/P4-EMBEDDING-ARCH-implementation.md`](docs/reports/P4-EMBEDDING-ARCH-implementation.md).
Orientation compatibility and validation: [performance and reliability changes](docs/reports/P4-UPSTREAM-MERGE-20261016.md).
