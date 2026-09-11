# P0-07 — P0 可行性汇总、失败组件与进入 P1 的约束

**Status: PASS**（2026-09-11，文档汇总任务，A 级验收）。

本任务不新增功能，只把 P0-01～P0-06 的真实结论、失败/受限组件和进入 P1 的约束固化成可复现的
收尾汇总，并生成 `docs/tasks/P1-01.md`。P1 的任何实现都未在本任务内开始。

执行模型：本任务由用户在当前 Codex 会话中手动选择的模型执行，未做、也未声称做任何模型切换。
基线：`ba3ebdf`（P0-06 完成提交）+ `381f7a9`（本任务卡提交），工作树干净；本任务未改
`src/**`、`tests/**`、`tools/**`、`pyproject.toml`、`uv.lock`。

## 1. 产物清单

所有模型权重、评测语料与数据根都在仓库之外；仓库只保存清单、工具、契约和聚合证据。

| 产物 | 位置 | 关键事实 | 校验方式（hash / 命令 / 证据 JSON） |
|---|---|---|---|
| GPU onedir 包 | `dist/PhotoSearch`（`.gitignore` 忽略，不提交） | `PhotoSearch.exe` 33,832,941 字节；2692 个文件、3,228,256,514 字节（3.01 GiB）；`_internal/nvidia_dlls` 21 个 NVIDIA DLL、1,728,264,632 字节（1.61 GiB） | 构建 `uv run python tools/packaging/build_onedir.py`；smoke `uv run python tools/packaging/smoke_packaged_gpu.py` → `docs/reports/P0-06-packaged-gpu-smoke.json`；`PhotoSearch.spec` 被 `*.spec` 忽略，未纳入版本控制，正式入口是构建脚本 |
| 依赖锁 | `uv.lock`（153,920 字节） | 95 个包；单一运行时 `onnxruntime-gpu` 1.30.0（无 `onnxruntime` 发行包）；`[tool.uv] override-dependencies` 阻止 `rapidocr-onnxruntime` 的传递 CPU `onnxruntime` 覆盖 GPU 构建 | `uv lock --check`（本任务复跑：`Resolved 95 packages`，exit 0）；安装 `uv sync --all-groups`；证据 `docs/reports/P0-06-gpu-and-model-eval.md` §A1 |
| 模型清单：Chinese-CLIP ViT-B/16（基线，冻结） | `tools/prepare_models/manifests/chinese-clip-vit-b-16.json`、`chinese-clip-vit-b-16-validation.json`；权重在 `%LOCALAPPDATA%\PhotoSearchData\models\chinese_clip\vit-b-16` | revision `36e679e65c2a2fead755ae21162091293ad37834`；`image_tower.fp32.onnx` 344,912,315 字节 sha256 `c8ac42d3…dec0`；`text_tower.fp32.onnx` 408,416,523 字节 sha256 `4699a6ef…a967`；512 维 / cosine / L2 | 清单内含逐文件字节数与 SHA-256；`uv run python tools/prepare_models/verify_chinese_clip_v2.py --model-dir <模型目录>`（`status: pass`，image max abs 1.907e-06、text 1.526e-05、最低 cosine 0.9999998808、Top-K `[0,1,2,4,3]`）；契约测试 `tests/integration/test_chinese_clip_manifest.py` |
| 模型清单：InsightFace `buffalo_l` | `tools/prepare_models/manifests/face-ocr.json`；权重在 `%LOCALAPPDATA%\PhotoSearchData\models\face\buffalo_l` | `det_10g.onnx` 16,923,827 字节 sha256 `5838f7fe…5b91`；`w600k_r50.onnx` 174,383,860 字节 sha256 `4c06341c…9e43`；只启用检测/识别，不用年龄性别与 3D | `uv run python tools/prepare_models/verify_face_ocr.py`（重新 hash + CPU 探针，`status: pass`）；CUDA smoke `docs/reports/P0-06-face-gpu-smoke.json`；契约测试 `tests/integration/test_face_ocr_manifest.py` |
| 模型清单：PP-OCRv4 mobile（RapidOCR 1.4.4） | `tools/prepare_models/manifests/face-ocr.json`；权重在 `%LOCALAPPDATA%\PhotoSearchData\models\ocr\ppocr-v4-mobile` | det 4,745,517 / cls 585,532 / rec 10,857,958 字节，`config.yaml` 1,221 字节，各自记录 SHA-256；从锁定的 RapidOCR wheel 复制 | 同 `verify_face_ocr.py`；设备状态证据 `docs/reports/P0-06-rapidocr-gpu-status.json`、`P0-06-rapidocr-no-preload.json` |
| 模型清单：Chinese-CLIP ViT-L/14（候选，可选增强，非产品默认） | `tools/prepare_models/manifests/chinese-clip-vit-l-14.json`；权重在 `D:\PhotoSearchData\models\chinese_clip\vit-l-14`（**不在默认数据根内**） | revision `660941af70c6ff89ce658a1735404c0f3e536c38`；image tower 1,216,143,425 字节 sha256 `d3718da0…a9c2`；text tower 409,202,957 字节 sha256 `31a3577a…0df3`；768 维，独立 space | 清单内含 SHA-256；导出脚本 `uv run python tools/prepare_models/prepare_chinese_clip_candidate.py --model-dir … --manifest tools/prepare_models/manifests/chinese-clip-vit-l-14.json`；评测证据 `docs/reports/P0-06-eval-candidate-gpu.json` |
| worker 与协议 | `src/photo_search/workers/{protocol,client,ai_worker}.py`、`src/photo_search/runtime/device.py`、`src/photo_search/models/embedding/chinese_clip_onnx.py`、`src/photo_search/__main__.py` | spawn worker、有界队列、1 MiB JSON 消息上限、结构化错误与 stderr tail；默认 CUDA、显式 CPU 降级、禁用 CPU EP fallback；`demo-search --device cuda` 或 `--device cpu --output <json>`；冷启动截止 900 s | 证据 `docs/reports/P0-06-source-gpu-worker.json`、`P0-06-source-cpu-worker.json`；测试 `tests/unit/test_worker_protocol.py`、`tests/unit/test_runtime_device.py`、`tests/integration/test_ai_worker_lifecycle.py`（含 `PHOTO_SEARCH_GPU_TESTS=1 … -k gpu` 真 CUDA opt-in 用例） |
| 评测工具与数据契约 | `tools/evaluation/{corpus,build_corpus,build_query_set,random_baseline,chinese_clip_eval}.py`、`src/photo_search/evaluation/{dataset,metrics}.py` | 语料属性/人脸/OCR 构建（可续跑缓存）、规则查询集生成与边界校验、随机基线、nDCG@20 / Recall@50 评测；拒绝无授权的数据集 | 聚合证据 `docs/reports/P0-06-eval-corpus-summary.json`、`P0-06-eval-query-set.json`、`P0-06-eval-random-baseline.json`、`P0-06-eval-baseline-gpu.json`、`P0-06-eval-candidate-gpu.json`、`P0-06-eval-baseline-cpu-compare.json`；契约/指标测试 `tests/unit/test_evaluation_query_set.py`、`tests/unit/test_evaluation_metrics.py` |
| 评测语料与标注集（仓库外） | `%LOCALAPPDATA%\PhotoSearchData\evaluation`：`corpus.json` 3,121,860 字节、`corpus-attributes.json` 2,613,228 字节、`faces.jsonl` 525,780 字节、`ocr.jsonl` 667,124 字节、`chinese-clip-eval-dataset.json` 2,115,930 字节 | 3231 张、150 条查询；数据集内记录 `authorization`（用户 2026-09-11 授权 5 个来源、只读）、`image_split_policy`、`annotation_rules`；绝对路径与 OCR 原文一律不进入仓库 | `uv run python tools/evaluation/build_corpus.py --faces --ocr`、`build_query_set.py`、`chinese_clip_eval.py`（命令与退出码见 §8）；工具只读授权目录 |

冻结的向量空间标识由清单派生：`space_id = "chinese-clip-vit-b-16:" + image_tower.sha256[:16]`
= `chinese-clip-vit-b-16:c8ac42d3907c57b0`，`processor_id = chinese-clip-vit-b-16@36e679e65c2a`
（`src/photo_search/models/embedding/chinese_clip_onnx.py`）。

## 2. P0 逐项状态表

只记录仓库中可查到的结论；每条都有报告文件。

| 任务 | 状态 | 证据 |
|---|---|---|
| P0-01 环境清单与最小骨架 | PASS | `docs/reports/P0-01-environment-and-skeleton.md` |
| P0-02A Windows SQLite FTS5 探针 | PASS | `docs/reports/P0-02A-sqlite-fts5.md` |
| P0-02B LanceDB 持久化/重启/CRUD | PASS | `docs/reports/P0-02B-lancedb.md` |
| P0-02C LanceDB 过滤与未索引新增检索 | PASS | `docs/reports/P0-02C-lancedb-filter.md` |
| P0-03 Chinese-CLIP ONNX 与参考一致性 | PASS | `docs/reports/P0-03-chinese-clip-onnx.md` |
| P0-04 人脸/OCR 权重、清单与加载探针 | PASS | `docs/reports/P0-04-face-ocr-load.md` |
| P0-05 AI worker、打包与中文/空格路径 | PASS（CPU 阶段，未追溯重做） | `docs/reports/P0-05-worker-and-packaging.md`、`P0-05-demo-output.json`、`P0-05-packaged-demo-output.json` |
| P0-06 A GPU runtime 硬门槛 | PASS | `docs/reports/P0-06-gpu-and-model-eval.md` §A1–A11 与 `P0-06-chinese-clip-gpu-check.json`、`P0-06-face-gpu-smoke.json`、`P0-06-rapidocr-gpu-status.json`、`P0-06-rapidocr-no-preload.json`、`P0-06-source-gpu-worker.json`、`P0-06-source-cpu-worker.json`、`P0-06-packaged-gpu-smoke.json` |
| P0-06 B 授权样本质量评测 | PASS | `docs/reports/P0-06-gpu-and-model-eval.md` §B1–B3 与 `P0-06-eval-corpus-summary.json`、`P0-06-eval-query-set.json`、`P0-06-eval-random-baseline.json`、`P0-06-eval-baseline-gpu.json`、`P0-06-eval-candidate-gpu.json`、`P0-06-eval-baseline-cpu-compare.json` |

基线数字（CUDA，3231 张 / 150 条查询）：Chinese-CLIP ViT-B/16 nDCG@20 **0.2592**、Recall@50
**0.0410**（holdout 0.2415）；候选 ViT-L/14 0.2672 / 0.0417（holdout 0.2430，显存 2687 MiB）；
随机排序 0.1100 / 0.0157。结论：保留 ViT-B/16 为 6 GB 基线，ViT-L/14 记为可选高显存增强，
**未触发任何架构或模型路线变更**，因此不需要 C2 决策。

评测集构成：3231 张 10.97 GiB（pic 1350 / vsco 872 / 朋友 881 / canon 99 / 网络 29；横 2111 /
竖 994 / 方 126）；934 张含人脸（共 2446 张脸，604 单脸、330 多脸）；1141 张含文字、339 含数字；
排除 1 张 HEIC 与 3 个 RAR；语料构建 1086.3 s。查询 150 条（50 调参 / 100 保留），
正样本数 13–1282、中位 363。

## 3. 失败与受限组件清单

| 项 | 事实（仓库内证据） | 影响与归属 |
|---|---|---|
| GTX 1660 6 GB 未实机验证 | 全部 GPU 证据来自 RTX 4060 Laptop GPU / 8188 MiB / 驱动 616.56；`ADR-0001` §6 与策略 §9.1 要求最迟 P4-07/P4-08 在真机复验 | **BLOCKED（外部硬件）**，阻止完整发行 PASS，不阻塞 P1；最低配置不得宣称已支持 |
| InsightFace 检测图 12 个算子落在 CPU EP | 严格运行识别图 **130/130 CUDA**；检测图为 **141 CUDA / 12 CPU**，CPU 算子为 `Concat`×2、`Gather`×4、`Slice`×2、`Unsqueeze`×4（形状/索引管线，非卷积）；带 fallback 的会话才能在 ORT 1.30 下初始化（`P0-06-face-gpu-smoke.json`） | 属 ADR-0001 §2 允许的**记录在案的逐算子分配**，不是整图 CPU 降级；P3/P4 复检未来 ONNX Runtime 是否可全量分配 |
| RapidOCR 未预加载 CUDA DLL 时静默回退 CPU | 预加载后三个会话均报告 `['CUDAExecutionProvider','CPUExecutionProvider']`，p50 75.95 ms（同进程 CPU 917.94 ms）；未预加载时实际 providers 仅 `['CPUExecutionProvider']` 而 `engine_use_cuda` 仍为 `true`，p50 977.94 ms（`P0-06-rapidocr-gpu-status.json`、`P0-06-rapidocr-no-preload.json`） | RapidOCR 1.4.4 不禁用 CPU fallback、也不报错；OCR 设备状态必须在 P3 显式化，是否硬 GPU 化由实测收益决定 |
| 解码主导建库吞吐 | 评测中 image p50 370.8 ms（≈8.34 张/秒），其中 GPU 推理仅 ≈12 ms/张；400 张子集 CPU/GPU 排序完全一致，query p50 3.38 vs 17.43 ms；按 8.3 张/秒，50 万张单 worker 约 **16.7 小时**（`P0-06-eval-baseline-gpu.json`、`P0-06-eval-baseline-cpu-compare.json`） | 吞吐问题是前端解码而不是模型；P1/P2 必须规划有界解码池与缩略图/降采样前置 |
| 驱动不提供 per-process 显存 | 驱动 616.56 对 per-process GPU memory 报 `[N/A]`；显存结论只能由总量差值给出：桌面 611 MiB → 两塔加载后 1785 MiB，峰值 1805 MiB，worker 增量 ≈1194 MiB（RAM 工作集峰值 1014.5 MiB） | 6 GB 预算是**预算预检**，不是实测上限；GTX 1660 实测归 P4-07/P4-08 |
| 冷启动慢且脆弱 | 曾观测单次 `cuInit` 2 分 23 秒（常态 ≈14 s）、首个探针适配器加载 238.7 s；在 CUDA 初始化中终止 worker 后，驱动约 4 分钟内无法新建 CUDA 上下文（`P0-06-gpu-and-model-eval.md` Findings 1） | P2 调度器必须区分“冷 GPU 启动”与“worker 卡死”，不能把正在初始化的 worker 当作超时进程直接杀掉 |
| 评测标签来自固定规则，非人工判断 | 150 条查询的标注由与被评模型无关的固定规则派生（EXIF 时间、几何、曝光/色彩、人脸数、OCR 文本存在性），构建器强制正样本落在 3%～40% 区间；`P0-06-eval-query-set.json` | 只能说明“属性定义图片的排序能力”，**不能**用于声称语义检索质量（如“吃火锅”“生日蛋糕”）；语义质量需人工标注保留集 |
| 仅验证 batch=1 FP32 | 动态批处理、FP16/量化、TensorRT（provider 被列出但未使用）均未验证 | 任何精度/批量优化须单独验收，不得直接扩大固定形状模型 |
| 中文文件名必须走 Pillow | 第一遍语料构建中 `cv2.imread` 无法打开非 ASCII Windows 路径，静默丢失 546 张人脸测量，后改为 Pillow 载图（`P0-06-gpu-and-model-eval.md` Findings 10） | 后续任何 OpenCV 组件必须同样处理，否则会静默跳过照片 |

### 3.1 仓库内已核对的两处记录不一致（只记录，不改证据文件）

1. `docs/reports/P0-06-gpu-and-model-eval.md` 的命令表记录了任务中快照
   `44 passed, 1 skipped`，而 `docs/development-status.md` 记录 `51 passed + 1 skipped`。
   本任务按只读规则复跑了整套测试：**51 passed + 1 skipped（52 collected），exit 0**，
   与状态文件一致；报告表格中的 44 是 B 阶段早期快照，晚于它的测试文件
   （`test_evaluation_metrics.py`、`test_evaluation_query_set.py`）已提交。以本次复跑为准。
2. `docs/reports/P0-06-model-evaluation.json` 仍是 A 阶段写下的
   `{"stage": "B", "status": "BLOCKED", "reason": "no authorized 2,000-image sample source…"}`
   快照，而 B 阶段随后真实完成并 PASS。该 JSON 属 P0-06 证据，按任务卡只读不改；
   后续读取时应以 `docs/reports/P0-06-gpu-and-model-eval.md` §B 与 `P0-06-eval-*.json` 为准。

## 4. 进入 P1 的九条门禁逐条对照（策略 §5.2）

| # | 门禁 | 结论 | 证据 |
|---|---|---|---|
| 1 | `CUDAExecutionProvider` 已真实可用 | 满足。项目环境（非 A0 探针）`get_available_providers()` 含 `CUDAExecutionProvider`；会话只用 `("CUDAExecutionProvider", {"use_tf32": "0"})`，`session.disable_cpu_ep_fallback=1` | `P0-06-gpu-and-model-eval.md` §A1–A2；`P0-06-chinese-clip-gpu-check.json` 的 `available_providers` |
| 2 | Chinese-CLIP image/text tower 已在 RTX 4060 上推理 | 满足。图像塔 **1320/1320 节点 CUDA**、文本塔 **395/395 节点 CUDA**，零 CPU 节点；输出 512 维、L2 范数 0.99999994–1.00000006 | `P0-06-chinese-clip-gpu-check.json`、`P0-06-gpu-and-model-eval.md` §A3 |
| 3 | worker 内 GPU 路径已闭环，不是独立 demo | 满足。`python -m photo_search demo-search --device cuda` exit 0：worker 内文本编码、图像编码、Top-K 全部 CUDA（1320/395 节点事件、零 CPU 节点）；显式 `--device cpu` 走 `CPUExecutionProvider` 并标 `is_degradation: true` | `P0-06-source-gpu-worker.json`、`P0-06-source-cpu-worker.json`、`P0-06-gpu-and-model-eval.md` §A6 |
| 4 | CPU/GPU 输出一致性达到 P0-06 门槛 | 满足。同进程 CPU/GPU FP32：图像最大绝对差 3.13e-07、文本 8.94e-08～1.79e-07，cosine 1.0（阈值 ≥0.9999），固定查询 Top-K `[0,1,2,4,3]` 与 P0-03 记录一致；400 张子集整排序完全一致 | `P0-06-chinese-clip-gpu-check.json`、`P0-06-eval-baseline-cpu-compare.json`、`P0-06-gpu-and-model-eval.md` §A4/§B3 |
| 5 | GPU 延迟和显存有真实记录 | 满足。暖机 batch=1：图像 p50 11.76 ms、文本 p50 10.29 ms（CPU 对照 58.28 ms / 130.66 ms）；显存总量 611 MiB → 1785 MiB，峰值 1805 MiB，worker 增量 ≈1194 MiB，RAM 峰值 1014.5 MiB；per-process 显存不可得已记录 | `P0-06-chinese-clip-gpu-check.json`、`P0-06-gpu-and-model-eval.md` §A5 |
| 6 | InsightFace detection/recognition GPU smoke 已通过 | 满足（带已记录的逐算子边界）。识别图 130/130 CUDA、输出 `[1,512]`；检测图 141 CUDA / 12 CPU 记录在案；公共领域夹具 1 张脸、`det_score` 0.8305、暖机 p50 17.49 ms | `P0-06-face-gpu-smoke.json`、`P0-06-gpu-and-model-eval.md` §A7 |
| 7 | 默认 GPU、故障不静默整图回退、显式 CPU 降级/基线仍可复现 | 满足。worker 默认 CUDA；CUDA 被屏蔽时以结构化 `DeviceUnavailableError`（`details.cpu_fallback = "disabled"`）失败；`gpu_required` + 显式 CPU 被拒绝；显式 CPU 降级从同一 GPU 包内运行并报告实际 provider | `P0-06-gpu-and-model-eval.md` §A6 及 `tests/integration/test_ai_worker_lifecycle.py` 的故障注入用例 |
| 8 | GPU 打包产物 smoke 已通过，最低配置实机待验项已明确交接到 P4 | 满足交接、实测部分完成。3.01 GiB onedir 包从中文/空格路径、离线、working dir 在包外运行：CUDA 路径 36.7 s exit 0（1320/395 节点全 CUDA），显式 CPU 3.5 s exit 0；GTX 1660 运行库/性能/OOM/打包验证项已写入本报告 §6 | `P0-06-packaged-gpu-smoke.json`、`P0-06-gpu-and-model-eval.md` §A11 与 “GTX 1660 6 GB handoff” |
| 9 | P0-06 授权样本质量评测已通过 | 满足（带规则标注限制）。3231 张授权样本、150 条查询、调参/保留分离，基线 0.2592 / 0.0410 与候选 0.2672 / 0.0417 均约 2.4×/2.6× 于随机基线 0.1100 / 0.0157；结论为保留 ViT-B/16 | `P0-06-gpu-and-model-eval.md` §B1–B3、`P0-06-eval-*.json` |

九条全部满足 → P0 判定为**可进入 P1**，并按用户“首版必须支持 GPU”的产品约束保持 CUDA 硬指标。

## 5. 交接给 P1 的不变量

以下内容在 P1（及后续阶段）不得被实现模型自行改变：

1. **应用数据在安装目录之外**：数据根为 `PhotoSearchData`（`%LOCALAPPDATA%` 或用户显式选择的目录），
   `dist/PhotoSearch` 内不写运行数据（主架构 §11.1；P0-04 数据根解析经验）。
2. **设备策略只有三种值**：`gpu_required / cpu_allowed / cpu_preferred`（`src/photo_search/runtime/device.py`），
   默认 CUDA、无静默整图 CPU 回退；显式降级必须记录实际 provider/device/precision。
3. **冻结的向量空间**：`chinese-clip-vit-b-16:c8ac42d3907c57b0`，512 维、cosine、L2 归一化；
   `ProcessorSpec.processor_id = chinese-clip-vit-b-16@36e679e65c2a`。
4. **模型身份与 manifest 不改**：P0-03 的 `chinese-clip-vit-b-16.json` 与 P0-04 的 `face-ocr.json`
   为只读引用；不同 space 必须隔离，不得混用同维向量。
5. **原图永不修改**：扫描、hash、解码、缩略图全部只读原文件；用户从索引移除不删除磁盘文件。
6. **P0 证据只增不改**：`docs/reports/P0-*.md`、`P0-06-*.json`、`tools/prepare_models/manifests/*`
   在后续任务中保持只读。

## 6. 交接给 P4 / P1 / P2 的具体项

**给 P4-07 / P4-08（最低配置发行门槛，策略 §9.1）：**

- GTX 1660 实机 + 锁定驱动/运行库下，Chinese-CLIP image/text 与 InsightFace detection/recognition
  的 CUDA 执行证据与输出一致性（可用 `tools/runtime/*` 探针 + P0-03 固定 fixture 的 Top-K 对照）。
- 6 GB 单 worker、有界 batch、加载/卸载、排队、OOM 恢复与显式 CPU 降级；不依赖 Tensor Core。
- 打包产物在最低配置上的中文/空格路径、离线启动、GPU 编码/搜索、正常退出与重启，并记录 DLL 来源与实际 provider。
- 需复核的已知边界：检测图 12 个 CPU 算子；RapidOCR 的 CUDA 前置加载与真实 provider；
  驱动不提供 per-process 显存，只能给总量差值。缺实机则该项 BLOCKED，不能标完整 PASS。

**给 P1（本阶段直接使用）：**

- **解码池与缩略图前置**：≈360 ms/张的解码成本是建库瓶颈，扫描/索引前应有有界解码池、
  缩略图/降采样前置与坏图隔离，不能按 12 ms 的 GPU 推理速度规划吞吐。
- 数据身份与位置语义：同字节多位置共享 asset、重命名/移动不改 `asset_id`、同路径内容替换
  指向新 asset 且不覆盖旧 asset 与人工信息、未完成完整 hash 不创建永久身份。
- 路径边界：Windows 分隔符/驱动器/中文/空格/长路径，重叠来源拒绝，默认不跟随联接与符号链接，
  数据目录不得位于来源内部（主架构 §5.3）。
- Windows 原生行为（路径、权限、文件系统语义）交 C1 处理。

**给 P2：**

- 调度必须区分冷 GPU 启动与卡死 worker（冷 `cuInit` 曾达 2 分 23 秒；初始化中被杀约 4 分钟无法新建上下文），
  并保留“默认 CUDA、显式降级、无静默回退”的既有契约。
- 任务唯一键与状态机、租约续约/过期重领、迟到提交拒绝、outbox 重放按主架构 §6.2/§6.3 落实。
- 检索质量若需对外声称语义能力，必须先有**人工标注**保留集；规则标注结果不能替代。

**可选项（非默认）：**

- Chinese-CLIP ViT-L/14：768 维、显存 2687 MiB（≈2.3×）、holdout 仅 +0.6 相对提升，
  仅作为高显存机器的可选增强，不替换 6 GB 基线；启用前需人工标注保留集复核。

## 7. 未验证项与“不可声称”清单

未验证（不得当作已通过）：

- GTX 1660 6 GB 的 CUDA 兼容、性能、显存、OOM 与打包行为（P4-07/P4-08）。
- 动态批处理、FP16/量化、TensorRT、Tensor Core 相关优化。
- 真实图库规模的端到端建库吞吐、前台争用、长时稳定性、API 层 P95 查询延迟。
- OCR 的真实截图/文档质量（准确率、阅读顺序、框正确性、混合文字）与 OCR 设备最终决策。
- 人脸检测/识别精度、阈值校准、侧脸/儿童/低光表现。
- 语义检索质量与人工相关度；InsightFace 权重对终端用户的再分发条款。

明确不可声称：

- 不得声称“最低配置 GTX 1660 已支持”或最低配置发行验收已 PASS。
- 不得声称语义检索质量已达标（现有指标只反映规则定义的属性排序）。
- 不得声称 OCR 已 GPU 化（未预加载时 RapidOCR 会静默回退 CPU）。
- 不得声称建库吞吐已达标（解码主导，≈8.3 张/秒、50 万张 ≈16.7 小时/单 worker）。
- 不得把 RTX 4060 的结果当作 GTX 1660 的性能或显存结论。
- 不得声称本任务做过模型切换；也不得把 `P0-06-model-evaluation.json` 的 B `BLOCKED` 快照当作当前状态。

## 8. 本任务验收

任务卡规定 A 级（文档汇总，未改任何 `.py`）：

| 命令 | 退出码 | 结果 |
|---|---|---|
| `uv run ruff check .` | 0 | All checks passed! |
| `git diff --check` | 0 | 无空白错误 |
| `uv run pytest -q -p no:cacheprovider tests/unit tests/integration`（只读复核测试计数，非本任务必需项） | 0 | 51 passed, 1 skipped（GPU opt-in） |
| `uv lock --check` | 0 | `Resolved 95 packages`，锁文件未变 |
| `git status` | 0 | 仅新增/修改本任务允许的三份文档 |

未复跑 GPU 探针与 PyInstaller 构建（任务卡不要求，沿用已有证据文件）。本任务未读取任何
用户照片目录，只通过已提交的聚合 JSON 与仓库外 `PhotoSearchData` 的清单/元数据做只读核对。

## 9. 交接记录

```text
任务 ID / 状态：P0-07 / PASS（A 级文档汇总，P0 九条门禁逐条对照全部满足，可进入 P1）
执行模型 / reasoning：由用户在当前 Codex 会话手动选择；本会话未切换、未声称切换模型
基线提交号：ba3ebdf（P0-06 完成）+ 381f7a9（P0-07 任务卡），工作树干净
已完成行为：产出 P0 产物清单（GPU onedir 包、uv.lock、四个模型清单、worker/协议、评测工具与仓库外语料）、
  P0-01～P0-06 状态表、失败与受限组件清单（GTX 1660 未实机、检测图 12 CPU 算子、RapidOCR 静默回退、
  解码主导吞吐、无 per-process 显存、冷启动脆弱、规则标注限制）、策略 §5.2 九条门禁逐条证据对照、
  P1 不变量、P4/P1/P2 交接项与“不可声称”清单；生成 docs/tasks/P1-01.md；更新 development-status
修改文件：docs/reports/P0-07-p0-summary.md（新建）、docs/tasks/P1-01.md（新建）、docs/development-status.md
数据/接口/迁移变化：无（未改 src/tests/tools/pyproject.toml/uv.lock）
实际命令与结果：uv run ruff check .（0）、git diff --check（0）、uv run pytest -q …（0，51 passed 1 skipped）、
  uv lock --check（0，95 packages）
报告路径：docs/reports/P0-07-p0-summary.md
未验证项与阻塞：GTX 1660 6 GB 实机（BLOCKED，外部硬件，P4-07/P4-08）；语义质量需人工标注保留集
失败修复轮次：0（发现并记录两处历史记录不一致，未修改证据文件）
下一任务：P1-01 资产/位置/来源/扫描最小持久任务/迁移与约束，任务卡 docs/tasks/P1-01.md，
  模型路由 C2 冻结核心契约 → DS-H 实现 → C2 聚焦复核，需用户手动切换模型后执行
独立审查者与结果：不需要（P0-07 未改变架构、数据身份或模型路线）
```
