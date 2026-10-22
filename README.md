# Photo Search

本地个人照片搜索与整理工具，支持增量扫描建库、中文文字搜索、以图搜图，
以及按人物、地点和拍摄时间浏览。照片、模型和索引保存在本机。

开发与验证入口：[`当前状态`](docs/development-status.md)、
[`日常测试`](docs/testing.md)、[`搜索质量与本地产品改进`](docs/reports/P4-SEARCH-QUALITY.md)。
当前代码和验证仍在迭代，完整验收范围与未完成项以状态页和对应报告为准。

当前个人源码测试版为 **0.5.0**（Catalog Schema 42，API v1）。更新说明和已知限制见
[`CHANGELOG`](CHANGELOG.md) 与 [`发布报告`](docs/reports/P4-RELEASE-050.md)。升级前停止应用，
备份已有数据根，并另行备份原图和模型权重；升级后的数据库不保证可供旧版程序使用。
当前开发分支已增加 Schema 43 的可选机器标签存储；它尚不是新的正式发布版。

## 安装与启动（Windows 源码版）

先安装 [uv](https://docs.astral.sh/uv/getting-started/installation/)，然后在仓库根目录的
PowerShell 中执行。项目要求 **Python 3.12.10**；下面将依赖缓存和 uv 下载的 Python
保留在项目内，依赖环境默认建在 `.venv/`。

```powershell
$env:UV_CACHE_DIR = "$PWD\.uv-cache"
$env:UV_PYTHON_INSTALL_DIR = "$PWD\.tmp\uv-python"
uv sync --locked --python 3.12.10
```

当前依赖包含 CUDA 版 PyTorch 和 ONNX Runtime，首次安装体积较大；安装依赖不会下载模型权重。
将下面的路径替换为实际使用的数据目录，再启动服务。正式使用时，数据目录应位于安装目录之外；
已有图库应继续使用原数据目录，新目录不会自动继承原来的模型或索引。

```powershell
uv --cache-dir .uv-cache run photo-search --data-root "D:\path\to\PhotoSearchData"
```

默认服务地址是 `127.0.0.1:8000`。在浏览器打开启动器本次打印的**一次性会话链接**，
不要只打开裸地址或重复使用旧链接。首次使用时，在“图库管理”的“新增来源”中填写照片文件夹，
点击“注册来源”，再点该来源的“开始建库”。后续用“同步全部变化”处理新增或改动的文件。
画面搜索需要提前准备模型，详见下方模型说明；文字识别完成后，图片里的文字才可被搜索到。

## 如何选择搜索方式

| 方式 | 适用场景与实际行为 |
| --- | --- |
| 综合搜索（默认） | 描述画面，例如“2023年张三在济南拍的日落”。结合画面语义和文字检索；识别到的时间、人物和地点显示为可取消条件。画面语义需要可用模型和索引。 |
| 按文字找 | 查文件名、已归属地点名称和已识别的图片文字，适合截图、票据、编号。查询中的姓名、地名和年份保留为文字，不自动变成筛选条件；不调用图像模型。 |
| 以图搜图 | 上传图片，按画面相似程度检索；需要可用模型和索引。可使用来源、可用性和地点筛选，不支持人物或拍摄时间筛选。 |

“按文字找”使用已有文字索引，不会自动纠正 OCR 错字；尚未识别的图片文字无法命中。
引号可以限定连续短语，但匹配仍经过文字规范化，并非逐字节比较。备注、标签、自动图片描述
目前不是已开放的文字搜索来源。显式选择的人物、来源、地点、时间等筛选仍会约束文字结果；
不填查询词时，也可仅用筛选条件浏览照片。

## 物体与场景辅助搜索（可选）

搜索页展开“物体与场景识别（可选）”，确认暂时占用模型资源后，点击“识别尚未处理的照片”。
程序在本机用 RAM++ 生成机器识别结果，可暂停、继续；重启后手动继续，单张坏图会记录失败并跳过。
识别期间综合搜索和以图搜图暂不可用；暂停后第一次语义搜索会重新加载原模型。
新增照片或修好失败照片后，可再次启动一批。不会改动原图、人工标签或收藏。

识别完成后，搜索“猫”“花”等明确类别词，可点击“试试物体与场景辅助”比较两种排序，随时切回普通搜索。
模型词表有 4585 个中英文概念，**不表示每类都已经验证准确**。当前只支持完整、无歧义的类别名，
并要求使用 Qwen EXIF v2 的 512 维搜索空间；“海边的猫”等复杂句继续使用普通搜索。
辅助模式只重排普通搜索已找到的最多 5000 个候选；未识别照片保留原位置，不保证找到所有目标。
结果可保存为普通合集，试用排序暂不支持动态智能合集。

模型不会自动下载。使用本项目导出的完整模型包，将 `manifest.json` 和 `model.ts` 一起放入
`<实际数据目录>/models/taggers/ram-plus-v1/`，停止应用后再安装；不要覆盖或混用不同包的文件。
模型包约 669 MB；切换数据根时，需要将完整包复制到新数据根的相同子目录。
正式运行复用现有 PyTorch，无需另装 RAM 官方旧版依赖。
模型换版会使用新的结果身份，需要重新识别，不会把两个版本混在一起。
若旧任务已暂停、中断或失败，可点击“用当前模型开始新一轮”；原有识别结果保留。

也可在应用停止时，从仓库根运行有界命令；重复 build 会接着处理未完成照片，
`--retry-failed` 明确重试失败项。已在界面暂停的任务应在界面继续。

```powershell
uv --cache-dir .uv-cache run python -m photo_search.tagging.cli status --data-root "D:\path\to\PhotoSearchData"
uv --cache-dir .uv-cache run python -m photo_search.tagging.cli build --data-root "D:\path\to\PhotoSearchData" --max-jobs 1000
```

这是默认关闭的辅助功能：公开数据实测有整体排序和小主体召回收益，也有类别退步。
详细结果、模型导出方法、存储代价及 GTX1660 验证限制见
[搜索改进报告](docs/reports/P4-SEARCH-QUALITY-PRODUCT.md#ram-可选辅助正式接入)。

## 仓库目录与本地文件

| 位置 | 用途与 Git 规则 |
| --- | --- |
| `src/`、`tests/`、`tools/` | 应用源码、回归测试、开发与打包工具；包括其中的 `models/`、`runtime/` 和测试数据目录，应纳入 Git。 |
| `docs/` | 架构决策、使用说明和简洁的最终变更报告；原始测试/审查输出不默认提交。 |
| `.venv/`、`.tmp/` | 依赖环境，以及按任务命名的隔离环境、合成数据、原始日志和恢复副本，全部忽略；清理前先检查引用与运行进程。 |
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

Semantic search and uploaded-image search are served by exactly **one active model**;
literal text search (按文字找) does not use it. Two model
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

下面以 Qwen 为例；`--source` 要换成已下载并解压、与仓库固定清单相符的模型目录，
`prepare-local` 只校验并复制本地文件。`build-candidate` 默认每次最多处理 5000 张，
大图库需要重复执行以继续构建；覆盖当前图库后，才能切换到输出中的实际 `profile_id`。

```powershell
uv --cache-dir .uv-cache run photo-search embedding list --data-root D:\path\to\PhotoSearchData

uv --cache-dir .uv-cache run photo-search embedding prepare-local --provider qwen3-vl-embedding-2b `
  --source .tmp/downloads/qwen3-vl-embedding-2b --data-root D:\path\to\PhotoSearchData

uv --cache-dir .uv-cache run photo-search embedding build-candidate --provider qwen3-vl-embedding-2b `
  --data-root D:\path\to\PhotoSearchData

# Publish the candidate: stop the service and every other writer of this data root first.
uv --cache-dir .uv-cache run photo-search embedding select-profile --profile semantic-image-gen-XXXXXXXX `
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
