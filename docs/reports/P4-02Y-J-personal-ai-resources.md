# P4-02Y-J — 个人试用 AI 资源就绪

**结论：READY（2026-09-29）——`Chinese-CLIP READY（已有） / OCR READY / Face READY`，产品代码 0 修改。**
任务卡：[P4-02Y-J](../tasks/P4-02Y-J.md)。本卡只补齐/校验个人试用数据根 `D:\SZ\project\PhotoSearchData`
下的 OCR 与 Face 运行资源，不跑全图库 OCR/Face 建库，不换模型版本，不改 hash/阈值/processor/space 契约。

## 1. 基线与环境

| 项 | 值 |
|---|---|
| HEAD / 分支 | `d7224b6`（`Merge branch 'master'`，含 `0957179` P4-02Y-T 收口、`97cb7b9` P4-02Y-P 立卡）/ `master`，开工前工作树干净 |
| 数据根 | `D:\SZ\project\PhotoSearchData` |
| 模型根 | `D:\SZ\project\PhotoSearchData\models`（产品按数据根解析，`--data-root` 即选中它） |
| Python / uv | 3.12.10 / uv（`.uv-cache`） |
| 运行时 | `onnxruntime-gpu 1.30.0`、`rapidocr-onnxruntime 1.4.4`、`insightface 2.0`、`pillow-heif 1.8.0` |
| providers | `TensorrtExecutionProvider` / `CUDAExecutionProvider` / `CPUExecutionProvider`（RTX 4060 Laptop） |
| schema | 16（未变；本卡不碰 catalog/迁移/依赖/`uv.lock`/ADR） |

Chinese-CLIP（`models\chinese_clip\vit-b-16`）按卡面不再检查。

## 2. 资源安装结果与校验

两条来源都复用**项目冻结版本**，逐字节与 P0-04 记录一致：

* **OCR**：本机已装的项目固定 wheel `rapidocr_onnxruntime 1.4.4` 自带 PP-OCRv4 mobile 三个 ONNX 与
  `config.yaml`，按 P0-04 工具路径复制到 `models\ocr\ppocr-v4-mobile`（无下载）。
* **Face**：本机**没有**任何既有副本（全盘按文件名搜 `det_10g.onnx` / `w600k_r50.onnx` / `buffalo_l.zip`
  只命中 venv 内的 OCR 图；P0-04 时代的数据根
  `…\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\PhotoSearchData\models` 已不存在），因此用现有准备工具
  `tools/prepare_models/prepare_face_ocr.py` 取官方 model-zoo 包：`buffalo_l.zip`
  **288 621 354 B / sha256 `80ffe37d…b0ca2f`**（与 P0-04 manifest 的 `pack_release` 逐字节一致），
  缓存在 `D:\SZ\project\PhotoSearchData\cache\buffalo_l.zip`，解出两个固定图到 `models\face\buffalo_l`。

| role | 路径（相对 `models\`） | bytes | sha256（前 12） | 与 P0-04 manifest | 与产品契约表 |
|---|---|---|---|---|---|
| ocr/detection | `ocr\ppocr-v4-mobile\ch_PP-OCRv4_det_infer.onnx` | 4 745 517 | `d2a7720d45a5` | ✅ | ✅ |
| ocr/classification | `ocr\ppocr-v4-mobile\ch_ppocr_mobile_v2.0_cls_infer.onnx` | 585 532 | `e47acedf6632` | ✅ | ✅ |
| ocr/recognition | `ocr\ppocr-v4-mobile\ch_PP-OCRv4_rec_infer.onnx` | 10 857 958 | `48fc40f24f6d` | ✅ | ✅ |
| ocr/config | `ocr\ppocr-v4-mobile\config.yaml` | 1 221 | `bf94a1da4cba` | ✅ | ✅ |
| face/detection | `face\buffalo_l\det_10g.onnx` | 16 923 827 | `5838f7fe0536` | ✅ | ✅ |
| face/recognition | `face\buffalo_l\w600k_r50.onnx` | 174 383 860 | `4c06341c33c2` | ✅ | ✅ |

校验入口（只读，`tmp\p4-02y-j\verify_artifacts.py`，证据 `test-results/p4-02y-j-artifacts.json`）：
6/6 文件同时等于
`tools/prepare_models/manifests/face-ocr.json` 与 `photo_search.ocr.contract` / `photo_search.face.contract`
的字节数与 SHA-256。**仓库内的 manifest 与契约表零改动**（准备工具的最终 manifest 步骤写到 `tmp\`，
本机因只装 `onnxruntime-gpu` 而无法产出——见 §5 F2——因此校验改由上述脚本独立完成）。
产品指纹与校验和不变量：OCR `2a5ae91b8a9e…`（config `bf94a1da4cba…`）、
Face `62b5343df26e…`、`space_id=insightface-buffalo-l:4c06341c33c2`、`dimension=512`。

## 3. 产品契约校验（确认实际从当前 D 盘数据根加载）

产品自带的受控入口、显式指向个人试用数据根，全部 `status=ok`：

```
uv run --no-sync python -m photo_search.ocr.cli  weights --data-root D:\SZ\project\PhotoSearchData --json
uv run --no-sync python -m photo_search.ocr.cli  status  --data-root D:\SZ\project\PhotoSearchData --json
uv run --no-sync python -m photo_search.face.cli weights --data-root D:\SZ\project\PhotoSearchData --json
uv run --no-sync python -m photo_search.face.cli status  --data-root D:\SZ\project\PhotoSearchData --json
```

| 观测 | 结果 |
|---|---|
| OCR `weights` | `model_root = D:\SZ\project\PhotoSearchData\models`，4 个固定文件 `size_matches=true`，`model_fingerprint` / `config_hash` 与冻结值一致 |
| Face `weights` | 2 个固定文件 `size_matches=true`，`space_id=insightface-buffalo-l:4c06341c33c2`、`dimension=512`、`config_hash` 与冻结值一致 |
| OCR `status` | `weights_present=true`、`state=NOT_RUN`（真实库尚未跑 OCR，符合本卡「不跑全图库」） |
| Face `status` | `weights_present=true`、`state=NOT_RUN`（真实库 3666 资产全为 `pending_assets`，未跑全库 face） |

证据：`test-results/p4-02y-j-{ocr,face}-weights.json`、`test-results/p4-02y-j-{ocr,face}-status.json`。
`weights_present` 就是来源卡/`/capabilities` 使用的同一投影函数（`ocr.weights.weights_present` /
`face.weights.weights_present`）。

## 4. Smoke（真实推理）

两次 smoke 都把 `PHOTO_SEARCH_DATA_ROOT` 指向 `D:\SZ\project\PhotoSearchData`，即**先对 D 盘数据根的字节做
一次产品校验、再复制进本次工作目录**，因此断言的推理用的就是本卡安装的副本；夹具全部是自建合成图与依赖包
自带的公开样例，**不接触任何真实照片库**。

### OCR（`tools/diagnostics/ocr_smoke_fixture.py`）

`status=ok`、12.3 s（证据 `test-results/p4-02y-j-ocr-smoke.json`）：

* `enqueue.queued=3`、`run.jobs={"SUCCEEDED": 3}`、`projection.state=SYNCED`、`ocr_summary.state=SYNCED`；
* 发票图 5 个区域，`original_text` 含 `发票金额1280元`、`车牌苏A12345`、`编号20260914`、`合计3760.50`
  （`min_confidence` 0.9719 / `max_confidence` 0.9999）；
* 高密度页 `slice_count=2`（确实切片，没有把整图压到 320/448）、空白页 `regions=0`（诚实空文档）；
* FTS 命中：`发票`/`1280`/`20260914` 三条查询都命中发票图；provider `CPUExecutionProvider`（OCR 契约为 CPU）。

### Face（`tools/diagnostics/face_smoke_fixture.py --device cpu`）

`status=ok`、`problems=[]`、32.6 s（证据 `test-results/p4-02y-j-face-smoke-cpu.json`）：

* 6 张夹具 → **12 张脸**、6/6 资产有人脸、1 张低质量（`SMALL`）、8 个未命名人物组；
* 独立候选索引 `state=READY`、`is_active=true`、**`dimension=512`**、12 条 `CONFIRMED`、
  `space_id=insightface-buffalo-l:4c06341c33c2`；
* 复合图（同一人两次）按契约记为 1 条 `CONFIRMED` + 1 条 `SAME_IMAGE_CONFLICT` 的 `SUGGESTED`；
* 现场重算的真实/冒充分布与冻结的 `CALIBRATION` 一致（`min_genuine=0.6433`、`max_impostor=0.2061`、
  `gap` 成立、阈值未动 `merge=0.56` / `suggest=0.29`）；
* 强制二次运行不新增脸/组（12/8 不变）。

## 5. 发现（均非阻断，本卡不改产品）

* **F1（既有能力事实，非本卡引入）本机 GPU 不能承载固定 SCRFD 图**：产品自己的探测
  `FaceEngineCache().probe(data_root)` → `cpu.usable=true`，
  `gpu.usable=false / FACE_DEVICE_UNAVAILABLE(detection, CUDAExecutionProvider)`；
  产品因此 `qualified_devices=["cpu"]`，face 作业按契约以 CPU 降级运行（`degraded_jobs`），
  搜索/刷新不受阻塞。这与 **P0-06 已记录**的现象完全一致
  （[`P0-06-face-gpu-smoke.json`](P0-06-face-gpu-smoke.json)：`det_10g` 有 12 个节点只有默认 CPU EP 能接，
  CPU 回退被显式禁用时 session 创建失败）。证据 `test-results/p4-02y-j-face-device-probe.json`。
* **F2 环境/工具口径**：`tools/prepare_models/prepare_face_ocr.py` 在复制/解包成功后的 **manifest 生成步骤**报
  `PackageNotFoundError: No package metadata was found for onnxruntime`（本机只装 `onnxruntime-gpu`，
  该 `describe` 步骤固定查询 `onnxruntime` 的 dist 元数据）⇒ exit 1、未写 manifest；资源本身已就位，
  校验改由 §2 的独立只读脚本完成。
* **F3 诊断工具口径**：`tools/diagnostics/face_smoke_fixture.py --device auto` 在 F1 的机器上会在 calibration
  阶段直接 `cache.engine(device="auto")`（绕过 `probe` 的可用性判定）而抛 `FACE_DEVICE_UNAVAILABLE` 退出 1；
  改用受支持的 `--device cpu` 全绿。属诊断脚本口径问题，不影响产品（产品走 `probe` → 只用可用设备）。

## 6. 结论与边界

**验收达成**：`Chinese-CLIP READY（已有）` + `OCR READY` + `Face READY`，且 OCR/Face 的真实推理与 512 维
人脸嵌入都由产品代码路径在**当前 D 盘数据根**的资源上跑通。

按卡面未做：Place 数据集 / GeoNames（另立卡 [P4-02Y-P](../tasks/P4-02Y-P.md)）、全图库 OCR/Face 建库、
Chinese-CLIP 复测、模型升级/重训、模型下载 UI、首启 UI、installer/PyInstaller、发行与许可证处理。

未验证（沿用既有边界）：真实个人图库上的 face/OCR 规模与质量、`>50 000` 增量、本机 GPU 上的 face
（F1 使其不可用）、place 解析。原图安全：本卡只写 `models\` 与 `cache\`，未读改写任何来源目录中的照片；
smoke 只在工作目录内建夹具。**产品代码 0 修改。**

## 7. 产物

* 安装物：`D:\SZ\project\PhotoSearchData\models\ocr\ppocr-v4-mobile\`（4 文件）、
  `D:\SZ\project\PhotoSearchData\models\face\buffalo_l\`（2 文件）、
  准备工具缓存 `D:\SZ\project\PhotoSearchData\cache\buffalo_l.zip`（288.6 MB，可删；再次补齐时会重新下载）。
* 证据：`test-results/p4-02y-j-artifacts.json`、`p4-02y-j-ocr-weights.json`、`p4-02y-j-ocr-status.json`、
  `p4-02y-j-face-weights.json`、`p4-02y-j-face-status.json`、`p4-02y-j-ocr-smoke.json`、
  `p4-02y-j-face-smoke-cpu.json`、`p4-02y-j-face-device-probe.json`。
* 一次性只读校验脚本（`tmp\`，不入库）：`tmp\p4-02y-j\verify_artifacts.py`。
* 仓库改动：仅本报告、`docs\tasks\P4-02Y-J.md` 状态行与 `docs\development-status.md`；`src/`、`tests/`、
  依赖、`uv.lock`、schema、ADR 均为零改动。
* 文档面检查：`uv run --no-sync ruff check .` → exit 0（All checks passed）；
  `git diff --check` → exit 0；`git status` 只有上述三个文档文件。本卡零代码改动，故未跑 pytest
  （卡面未要求，V3.1 低 token 规则）。
