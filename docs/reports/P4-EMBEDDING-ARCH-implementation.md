# P4-EMBEDDING-ARCH 实现报告 — 可插拔语义 provider + Qwen3-VL-Embedding-2B 512D

日期：2026-09-30。状态：**IMPLEMENTED / SCOPED VALIDATION COMPLETE / INDEPENDENT REVIEW PENDING**。
基线：`2d5fd94`（设计起点；开工时工作区只有本轮规划文档的未提交修改，已保留）。
任务卡：[P4-EMBEDDING-ARCH](../tasks/P4-EMBEDDING-ARCH.md)；策略：[接入策略](../plans/P4-EMBEDDING-ARCH.md)。
审查卡：[P4-EMBEDDING-ARCH-review](../tasks/P4-EMBEDDING-ARCH-review.md)。

本报告只声明本卡范围：**本机源码运行的双 provider 接入与定向验收**。不表示全量 B/C 回归、30 万
性能曲线、Qwen 全库质量/性能、GTX 1660、现有 exe/打包支持，或 P4-03B/P4-04 已通过。

## 1. 最终固定配置

| 配置 | 值 |
|---|---|
| CLIP provider | `chinese-clip-vit-b-16` / adapter `chinese_clip_onnx` / `processor_id=chinese-clip-vit-b-16@36e679e65c2a` / `space_id=chinese-clip-vit-b-16:c8ac42d3907c57b0`（**未变**） |
| Qwen provider | `qwen3-vl-embedding-2b` / adapter `qwen3_vl` / revision `9f2f7e710d6d81056aa5c0a4f04764fec6bb7bda` / `processor_id=qwen3-vl-embedding-2b@9f2f7e710d6d` / `space_id=qwen3-vl-embedding-2b:753345e04eeac275`（随 manifest 指纹而定） |
| Qwen 原始→输出 | 2048D 前缀 512D → float32 → L2 normalize；manifest 必须声明 MRL 方法才允许截断 |
| Qwen 设备/精度 | PyTorch + Transformers，CUDA only（`gpu_required`）、FP16、batch 1、eager attention、无 offload |
| Qwen 指令/pooling | 官方默认指令 `Represent the user's input.`（文本/图片/查询同一常量并进入指纹）；attention-mask 最后有效 token pooling |
| Qwen token/像素 | `max_length=8192`；`min_pixels=4096`、`max_pixels=262144`、patch 16、merge 2、RGB、rescale 1/255、mean/std 0.5 |
| Qwen 固定文件 | `model.safetensors`(4 255 140 312 B) / `config.json` / `preprocessor_config.json` / `tokenizer.json` / `chat_template.jinja`，全部 sha256 固定并在加载前校验 |
| 依赖组合（实测） | torch `2.8.0+cu128`、torchvision `0.23.0+cu128`、transformers `4.57.0`、qwen-vl-utils `0.0.14`、onnxruntime-gpu `1.30.0`、Python 3.12.10 |
| 参考实现出处 | Qwen 官方仓库 `scripts/qwen3_vl_embedding.py`（sha256 `8ffa74a1a6bb759610c57865ea416fd4daf9936cb787520e1112a3e1d547f36a`，Apache-2.0）；产品侧**重写**同语义模型壳，不从数据目录 import 可执行脚本 |

## 2. 受影响面与接口差异

新增（全部为纯新增文件）：

| 路径 | 作用 |
|---|---|
| `src/photo_search/models/embedding/base.py` | 与框架无关的 manifest 解析、provider 归一化、**唯一**持久 `ProcessorSpec` 构造器、指纹/空间推导 |
| `src/photo_search/models/embedding/registry.py` | 数据根 registry：已安装 provider、`processor_id` 反查、结构化未知 provider/缺件 |
| `src/photo_search/models/embedding/factory.py` | worker 侧“manifest → adapter”工厂（重型 import 只发生在这里） |
| `src/photo_search/models/embedding/qwen3_vl.py` | Qwen3-VL-Embedding adapter（PyTorch/CUDA/FP16） |
| `src/photo_search/models/embedding/cli.py` | `list` / `prepare-local` / `build-candidate` / `select-profile` |
| `src/photo_search/indexing/selection.py` | 受控选择的覆盖/outbox/artifact/Lance 门禁与事务内复核闭包 |
| `tools/prepare_models/manifests/qwen3-vl-embedding-2b.json` | Qwen 固定 manifest（provider 块、指令、像素预算、运行时、5 个固定文件 sha256） |
| `tools/diagnostics/p4_embedding_provider_smoke.py` | 真实 CUDA 双 provider 建库/选择/HTTP/浏览器前置 smoke |
| `tests/unit/test_p4_embedding_provider.py`、`tests/integration/test_p4_embedding_provider_flow.py` | 定向单元/集成回归 |

修改（关键差异，均有对应测试）：

* `models/embedding/chinese_clip_onnx.py`：adapter spec 增加 `provider_id`/`adapter`，身份改由共享层推导；
  **历史空间 ID 逐字保持**：只有当 manifest 的“内容指纹”（model id、embedding、preprocessing、
  tokenizer、onnx）等于冻结值 `LEGACY_CLIP_SPACE_HASH` 时才继续使用 `<image sha16>` 作为空间摘要，
  任何内容变化改由指纹派生新空间（resolved revision 单变只换 processor_id，不动空间）。
* `workers/query_encoder.py`：`pinned_embedding_spec` 保留为旧接口包装（输出物逐字段不变）；
  新增 `active_provider_id` / `resolve_provider_encoder`；`_runtime_facts` 增加 PyTorch provider
  映射与“**实际解析出的设备**优先”；`build_worker_query_encoder` 把 manifest 设备策略与调用方策略
  取**更严**的一侧并传给 worker。
* `workers/ai_worker.py`：worker 不再写死 Chinese-CLIP，改为读 manifest 调 factory；结构化
  `CatalogError`（缺 runtime/设备/哈希）把 `code` 与 `details` 带回父进程。
* `__main__.py`：启动时按**活动 profile 的 provider**（只读 SQLite 查询）+ registry 解析编码器；
  `--model-dir/--model-manifest` 仍是显式单 provider 覆盖；新增 `embedding` 子命令。
* `indexing/semantic.py`：`build_index` 新增 `expected_space_id/expected_processor_id`（worker 空间
  核对）；`ensure_search_profile` 只在“无活动 profile 或同一空间”时激活，否则返回
  `CANDIDATE_NOT_ACTIVATED`；`record_runtime_evidence` 用真实 provider 名（PyTorch 路径不再冒充
  ONNX `CUDAExecutionProvider`）。
* `search/profile.py`：`activate_profile` 新增 `precondition` 事务内校验 seam（在 bundle/readiness
  之后、指针更新之前；原 `activation_hook` 与错误语义不变）。
* `refresh/pipeline.py`：daily/build 建库把目标 space/processor 传给 `build_index` 做二次核对。
* `search/service.py` + `api/routes.py` + `web/search.js`：`channels.vector` 增加只读
  `strong_match_available`；capabilities 增加 `active_embedding_model`（按**活动 profile 的图像世代**
  解析，而不是按 active_generations[0]）；页面只在可用时显示“高度匹配”句，并显示活动模型与不可用原因。
* `catalog/errors.py`：新增 provider/选择族 12 个结构化码（未知码仍映射 500，不影响既有映射）。
* `pyproject.toml` / `uv.lock`：transformers `4.46.3 → 4.57.0`、torch `2.14.0 → 2.8.0+cu128`、
  torchvision `0.29.0 → 0.23.0+cu128`、新增 `qwen-vl-utils==0.0.14`（连带 `av==19.0.0`、
  `tokenizers 0.20.3 → 0.22.2`、`setuptools 84 → 81`）；新增 `pytorch-cu128` explicit index 与
  `[tool.uv.sources]`，使 CPU-only torch 不可能被误解析。

真实数据根实测后追加的三处修复（§8 记录触发它们的真实证据）：

* `indexing/selection.py`：覆盖门禁的“必需集合”改为**当前、可读、本索引能索引的图像**。
  分类用冻结媒体策略（`semantic.image_eligibility`）在**事务外**完成，事务内改为复核两件事：
  整个可搜索图库的 `(asset_id, sha256)` 摘要未变（新增/删除/重扫/换内容 → 新拒绝原因
  `CATALOG_CHANGED_IN_TRANSACTION`），以及**恰好这些 id** 的 CONFIRMED 覆盖行。
  报告新增 `required_ids` / `catalog_digest` / `out_of_scope`。原因：真实图库 3610 个可读资产里
  只有 3182 个是图像，316 个视频、76 个无法识别、36 个 TIFF 永远不可能被图像索引覆盖；旧写法会
  把“库的固有性质”变成“永久拒绝切换”。
* `workers/ai_worker.py`：worker 进程现在也执行产品唯一的 HEIF 注册点
  （`media.heif.ensure_heif_support()`），健康回执里多一个 `heif` 事实（fail-closed，插件不可用仍按
  逐文件结构化拒绝）。原因：`pillow-heif` 原来只在主进程注册（`image_eligibility` 里），worker 子进程
  用裸 PIL 解码 HEIC 会失败——真实图库唯一未被 CLIP 覆盖的那张图正是这个 HEIC（asset 492）。
* `models/embedding/cli.py`：`build-candidate` 新增 `--retry-failed`（把该 provider 同输入版本的 FAILED
  job 重绕后重试），用于真实建库末尾那一张 HEIC 的定点补建。
* `workers/ai_worker.py` + `runtime/device.py`：ONNX provider 的 worker 进程改为 `USE_TORCH=0` +
  ORT「仅 nvidia 站点包」预加载，消除 onnxruntime 的 PyTorch CUDA 主版本提示并让该进程只保留一套
  CUDA 栈（详见 §5.6 与 §8）。

未改动（按要求冻结）：迁移/资产身份/人工人物地点协议、`SearchProfile` 列集、lease/outbox 顺序、
向量后端（LanceDB 与候选/融合算法）、Top5000、block size、包配置与 PyInstaller。

关于卡内“允许修改”但本轮**未改**的两处，给出理由供审查核对：`runtime/device.py` 与
`scheduler/capability.py` 保持不变。PyTorch 的 CUDA 事实不是新增一套 ONNX provider 常量，而是
adapter 自带 `DeviceResolution`（`PyTorchCUDA`/`PyTorchCPU`、precision、`cpu_fallback_disabled`）→
`workers/query_encoder._runtime_facts`（provider→device 映射 + 实际设备优先）→
`indexing/semantic.adapter_runtime_facts`（provider **名**进入 capability/qualification）三段如实传递，
资格矩阵本身（device ∈ {cpu,gpu}、provider/precision 需与健康快照一致）未改。`search/upload.py`、
`search/embedding.py` 同样未改：它们只消费 ADR-0003 adapter 的既有方法，Qwen 走同一条 `encode_images`
路径（smoke 的上传自查询已实测）。

## 3. 操作命令（实际名称，`--help` 同步）

```powershell
uv run photo-search embedding list [--data-root DIR] [--json]
uv run photo-search embedding prepare-local --provider ID --source DIR [--revision R] [--force] [--data-root DIR]
uv run photo-search embedding build-candidate --provider ID [--limit N] [--source-id S] [--device auto|cuda|cpu]
uv run photo-search embedding select-profile --profile PROFILE_ID --stopped [--data-root DIR]
```

启动服务不需要新参数：进程按活动 profile 的 provider 从 `<data_root>/models/...` 解析模型；
`--model-dir/--model-manifest/--model-device`（及 `PHOTO_SEARCH_MODEL_*`）保留为显式单 provider
覆盖。README 已写入同一组命令与规则。

## 4. 验证证据（最终收敛代码）

`uv lock --check`、`uv run --no-sync ruff check src tests tools`、`git diff --check` 全部退出码 0。

JUnit（本卡定向集 + 触及 capability/scheduler 后按卡要求加入的两个套件 + 被本卡 worker 改动影响的
worker lifecycle 套件）：`test-results/p4-embedding-arch.xml` =
**203 tests / 0 failed / 0 errors / 6 skipped**（跳过的 6 项是
`test_ai_worker_lifecycle` 里需要 `LOCALAPPDATA` 已安装 CLIP 模型或 `PHOTO_SEARCH_GPU_TESTS=1` 的用例，
本机 `LOCALAPPDATA` 无模型），套件：

```
tests/unit/test_p4_embedding_provider.py            tests/integration/test_p4_embedding_provider_flow.py
tests/unit/test_p4_02n_query_encoder_wiring.py      tests/unit/test_p4_02o_semantic_build.py
tests/unit/test_p4_02y_w_search_capacity.py         tests/unit/test_p2_contracts.py
tests/unit/test_worker_protocol.py                  tests/unit/test_p2_scheduler.py
tests/integration/test_p2_search_profile_migration.py  tests/integration/test_p2_search_image.py
tests/integration/test_p2_search_endpoint.py        tests/integration/test_p2_index_submission.py
tests/integration/test_p2_index_repair.py           tests/integration/test_p2_scheduler_recovery.py
tests/integration/test_p4_02y_library_refresh.py    tests/integration/test_ai_worker_lifecycle.py
```

### 4.1 真实 smoke（真实权重、真实 CUDA、真实 HTTP）

命令：`uv run --no-sync python tools/diagnostics/p4_embedding_provider_smoke.py --images 12 --upload-queries 2`
原始数据：`test-results/p4-embedding-arch-smoke.json`（**24/24 checks = true，status=pass，退出码 0**）。
夹具：12 张项目合成 PNG（形状×颜色×背景确定），8 条固定中文 query，2 次上传图自查询；隔离 data root
`tmp/p4-embedding-arch/smoke/data`（保留），不读取任何真实照片来源。

| 观测 | CLIP | Qwen |
|---|---|---|
| 安装（`prepare-local`，逐文件 sha256） | 3 文件 / 753 438 378 B / 750 ms | 5 文件 / 4 266 571 889 B / 4 622 ms |
| 建库 | 12/12 CONFIRMED，generation READY，device **gpu**，provider `CUDAExecutionProvider`，FP32 | 12/12 CONFIRMED，generation READY，device **gpu**，provider `PyTorchCUDA`，FP16 |
| 候选行为 | 首个建库直接激活（新库） | `activated=false`、`reason=CANDIDATE_NOT_ACTIVATED`，活动指针仍是 CLIP |
| 受控选择 | — | `changed=true`，coverage 12/12、pending=0、Lance `full` 12/12 healthy、readiness `PyTorchCUDA/FP16` |
| HTTP 文字 query | 8/8 → 200，向量通道命中 | 8/8 → 200，向量通道命中 |
| 上传图自查询 | 2/2 第 1 名命中自身 | 2/2 第 1 名命中自身 |
| 高度匹配句 | `strong_match_available=true`（页面显示） | `strong_match_available=false`（页面隐藏） |
| 冷启动/耗时 | capabilities 5 621 ms（worker 冷启动）、build 8 067 ms、warm query encode 10.8 ms | capabilities 28 304 ms（加载+哈希）、build 24 345 ms、select 26 958 ms、warm query encode 101 ms |
| 峰值显存 | 不适用（ONNX EP） | `torch.cuda.max_memory_allocated=4 287 900 672 B`、`reserved=4 313 841 664 B`（8 GB 卡未 OOM） |

文字 query 是**观察**而非质量结论：4 条“颜色+形状”query 在两个 provider 下都命中对应合成图（例如
“红色的圆形”→`fixture-00`、“蓝色的正方形”→`fixture-07`），其余泛化 query 的 top-3 已原样记入
smoke JSON；不宣称 Qwen 优于 CLIP。

### 4.2 浏览器页面（真实页面，非 mock）

在保留的 smoke data root 上启动本机服务（临时脚本 `tmp/p4-embedding-arch/browser_check_server.py`，
仅为验证，不属于产品代码），用应用内浏览器打开 `/search`：

* 绑定提示实际渲染为「活动语义模型 Qwen3-VL-Embedding-2B (512D)（adapter=qwen3_vl · 512D · 本机已
  安装）· 该空间没有标定高度匹配统计，本页不显示“高度匹配”句。」；
* 文字搜索 `红色的圆形` → 状态行「已显示 12 行 · 候选共 12 个 · 已到末尾」（**没有**“高度匹配”
  句），12 个卡片全部“图文向量命中”，第 1 名为资产 #1（= `fixture-00`，红色圆形）；
* 上传 `fixture-03.png` → 「上传图查询成功（结果见上面的网格）」，结果头显示
  `320×240 · PNG · 1332 字节`，第 1 名为资产 #4（= `fixture-03`，自查询自身命中）；
* 覆盖率面板：已确认向量条目 24、待修复 0、未确认 outbox 0、活动世代两条 READY、投影 SYNCED。

### 4.3 必测场景 ↔ 证据映射

| 场景 | 证据 |
|---|---|
| 旧 CLIP manifest → 原 ID/原行为 | `test_the_historical_clip_manifest_keeps_its_exact_identity`、`test_the_legacy_spec_document_is_unchanged`（含独立重算的历史 config_hash）+ smoke 实测同一 `space_id` |
| Qwen 独立 ID；维度/权重/prompt/预处理/revision 变化 → 新 ID | `test_the_qwen_manifest_is_its_own_space_at_the_same_dimension`、`test_any_identity_change_publishes_a_new_space_or_processor`（9 组） |
| 未知 provider 拒绝 | `test_an_unknown_or_undeclared_provider_is_refused`、`test_a_manifest_only_provider_is_reported_as_not_installed` |
| 同维错 encoder/空间零写入 | `test_a_build_refuses_an_encoder_of_another_space_with_zero_side_effects`（artifacts/entries/outbox/generations 全 0，活动指针不变） |
| 数值（512D FP32、finite、norm≈1） | Qwen 适配器 `_normalized_vector` + `test_the_pinned_files_are_verified_before_any_load` 上游校验；real smoke 中文字/上传 512 维 norm=1.0（select 的 `model_health`） |
| 两模型独立 generation/CONFIRMED、候选不激活、重复/重启幂等 | `test_a_candidate_of_another_space_never_moves_the_pointer`、`test_a_candidate_build_never_moves_the_pointer_and_the_selection_does`（重开连接读指针）、`test_retry_failed_rewinds_the_same_logical_job`、`test_a_repeat_refresh_after_completion_changes_nothing` |
| 选择：缺覆盖/离线/未确认 outbox/不健康 → 指针不变 | `test_switching_back_requires_full_coverage_and_rejects_a_gap`、`test_a_pending_outbox_or_an_offline_asset_blocks_the_selection`、`test_a_build_refuses_an_encoder_of_another_space_with_zero_side_effects` |
| 校验失败/提交后重启注入 | `test_every_activation_refusal_is_structured_and_writes_nothing`、`test_an_interrupted_activation_rolls_back_both_writes`（P2-06A 原有 hook 保持）、选择闭包 `COVERAGE_CHANGED_IN_TRANSACTION` |
| 增量/切回 | `test_switching_back_requires_full_coverage_and_rejects_a_gap`（Qwen 期间新增 → CLIP 拒绝 → 补建 → 成功）；日常增量跟随活动 profile 由 `test_a_new_photo_is_searchable_when_the_refresh_says_search_ready` 等 P4-02Y 套件覆盖 |
| 资源：无 CUDA/显式 CPU/OOM/缺件/哈希 | `test_a_strict_model_cannot_be_softened_into_a_cpu_run`、`test_the_pinned_files_are_verified_before_any_load`、`test_every_model_parameter_must_be_on_the_dispatch_device`；OOM 分支为结构化 `EMBEDDING_DEVICE_UNAVAILABLE`（未真实注入 OOM，见 §5） |
| 查询/显示 | `test_the_selected_provider_serves_search_and_hides_the_strong_match_statistic`（真实 HTTP）+ §4.2 浏览器 |

## 5. 未验证 / 已知限制（PENDING，不写 PASS）

1. **Qwen OOM 与 GTX 1660**：未真实注入显存不足（8 GB 卡峰值 4.29 GB；真实 3182 图建库全程未 OOM），
   也未在 GTX 1660 / 无 CUDA 机器上运行真实模型；代码路径与结构化拒绝有单测，真实硬件结论 PENDING。
2. **Lance 健康检查的规模上限**：`indexing/selection.py` 的行数交叉核对通过既有 `LanceVectorIndex.count()`
   实现，会物化整代，因此限制为 ≤20 000 行；更大世代返回 `checked=bounded` 并**拒绝**选择（需要新增
   有界行数读取后再放开）。本卡 12 图库与真实 3182 图库都走的是 `full`。
3. **CLIP 侧未做“候选 + 选择”真实演练**：smoke 里 CLIP 是首个建库（合法直接激活），切回 CLIP 的
   门禁只有夹具覆盖（集成测试），没有对真实 CLIP generation 走一次 `select-profile`。真实数据根现状：
   切回 CLIP 会被门禁拒绝，因为 CLIP 只覆盖 3181/3182 张图像——缺的正是那张 HEIC（worker 侧已修好，
   但 CLIP 世代是修复前建的）。要切回需先跑
   `build-candidate --provider chinese-clip-vit-b-16 --retry-failed` 补那 1 张，再 `select-profile`。
4. **打包/exe**：未构建、未运行，`pyproject`/uv.lock 的改动未在 PyInstaller 产物验证；按卡不属于本
   slice。
5. **全量回归与性能**：未跑全量 pytest、未重跑 30 万曲线或图像质量矩阵（卡内明示例外）。
6. **onnxruntime-gpu 1.30 与 torch cu128 的 CUDA 版本提示（已解决）**：原提示为
   「PyTorch 2.8.0+cu128 uses CUDA 12.x but onnxruntime-gpu is built with CUDA 13.x」，根因是
   `onnxruntime/__init__.py` 在 `preload_dlls()` 里只看 `"torch" in sys.modules`（与 `directory`
   参数无关），而 Chinese-CLIP adapter 顶层的 `from transformers import BertTokenizerFast` 会把 torch
   一起 import 进来。解决方式：`workers/ai_worker.prepare_framework_stack()` 只在 ONNX provider 的
   worker 进程、且在 transformers 首次 import 之前设 `USE_TORCH=0`（Qwen worker 不动，操作者已显式
   设置的 `USE_TORCH` 不覆盖），并让 ONNX 进程只保留一套 CUDA 栈；`runtime/device.preload_runtime_dlls`
   同时改为显式使用 ORT 的「仅 nvidia 站点包」模式，避免将来出现兼容版 torch 时静默换 DLL 来源。
   过程中排除的一种做法（把 tokenizer 改成惰性 import）会让 torch 晚于 ORT 加载，真实运行直接失败：
   `WinError 127 ... torch\lib\cudnn_cnn64_9.dll`（cuDNN 9 在两栈同名但链接不同 CUDA 主版本），因此
   已回退；**加载顺序是有实际后果的，不能随意调整**。验证：`demo-search` 真实 CUDA 运行
   **stdout 0 条警告、stderr 为空**、`CUDAExecutionProvider` 且 image/text 1320/395 节点全在 CUDA、
   排序与修复前一致（red 0.4408 > green 0.4186 > blue 0.4059）；ONNX worker 健康回执自报
   `framework_stack: {"torch_imported": false, "use_torch": "0"}`。残余范围：任何 *自己* import torch
   的进程（例如在进程内直接用 adapter 的评测脚本）仍会看到该提示——它是信息性输出，不影响执行。
7. **transformers 4.57.0 被上游 yank**（"Error in the setup causing installation issues"）：按卡“固定实测
   组合”保留 4.57.0（wheel 安装与真实推理均通过）；若要改用 4.57.6 需重新实测并更新 manifest 版本记录。
8. **未决 ADR 问题保持原状**：本维护入口不创建 `UpgradeRun`，不声称满足 ADR-0013 §7 的
   `RUN_SUCCEEDED` 六门禁，也不伪造成功 run；该适用性判定留给独立 DS-H/Sol 审查（策略 §6 已列为必审项）。
9. **P4-02Y-W / P4-02Y-L 的独立审查仍未完成**；本轮触及了 W 的展示面
   （`channels.vector.strong_match_available`、`web/search.js`），该 diff 应纳入本卡审查，但不追认 W/L。
10. **真实数据根的日常增量与切回只做了机制验证**：daily 刷新绑定到活动 Qwen profile 已由启动行与
    fixture 套件证明，但**没有**在真实图库上点一次“刷新图库”跑完整增量（那会重新扫描 3610 个真实
    文件，属于用户日常操作）；切回 CLIP 的补齐步骤见 §5.3。
11. **§8 的真实建库未做质量评估**：只验证链路、覆盖与数值契约，不宣称 Qwen 检索质量更好。

## 6. 结论

两个 provider 已在本机源码环境真实贯通「安装 → 建库 → 候选 → 受控停机选择 → HTTP 文字/上传检索 →
现有页面展示」；Chinese-CLIP 的历史身份、旧 manifest/CLI 兼容与旧空间行为保持不变；Qwen 512D 以独立
空间、独立 generation 与独立 profile 运行，普通搜索始终固定一个完整活动 profile。剩余项见 §5，
本卡状态保持 **IMPLEMENTED / SCOPED VALIDATION COMPLETE / INDEPENDENT REVIEW PENDING**。

## 7. 额外发现（全量套件；不属于本卡实现，供审查路由）

卡内定向集之外，本轮额外跑了一次**全量** pytest 以排查依赖升级（torch/transformers）的直接回归：
`test-results/p4-embedding-arch-full.xml` = **1911 tests / 5 failures / 0 errors / 48 skipped**。

* **1 个是本卡改动引起的，已定点修复**：`tests/integration/test_ai_worker_lifecycle.py::
  test_startup_error_contains_stderr_tail` 期望 stderr 里出现 `FileNotFoundError`；worker 现在读 manifest
  后再建 adapter，缺 manifest 变成结构化 `CatalogError(EMBEDDING_MANIFEST_INVALID)`。已把断言改为校验
  `payload.exception_type`、`details.code`、`details.manifest` 与 stderr 文本，并保留“stderr 尾部可读、
  stderr 文件被清理”的原意（单独复跑该套件：2 passed / 6 skipped，无模型/GPU 相关跳过）。
* **4 个是 HEAD 已有、与本卡无关的既有失败**：`test_p3_person_search_contracts`、
  `test_p3_person_search_filter`、`test_p4_time_contracts`、`test_p4_upgrade_contracts` 都在比对
  `docs/decisions/contracts/p2-contracts.v1.json` 的冻结哈希，期望 `ec7de5f0cbe6…`，而该文件（含 HEAD
  blob）实际为 `4270997cac64…`。证据：`git diff --name-only HEAD` 未包含该 JSON 与这四个测试文件，
  `git show HEAD:…` 的摘要与工作区一致；`git log` 显示该文件最后一次被修改是 `c1d0a34`
  （P4-02Y-L OCR CUDA，新增 `structured_model`），四个常量未同步。本卡没有修改该契约或这些常量，
  也不在本卡范围内“顺手”更新冻结哈希；建议路由到 **P4-02Y-L 的独立 DS-H 审查**（它本来也仍待审查），
  由该任务决定是更新 ADR/常量还是回退契约变更。

## 8. 真实数据根：安装、全库建库与切换（用户明确要求后执行）

用户指示“装进真实数据根并切换到 QWEN”，于是对 `D:\SZ\project\PhotoSearchData`（仓库外的真实应用
数据根，4 个真实来源、3610 个可读资产）执行了完整流程。**没有**运行任何扫描（未重算源文件哈希、未改
图库身份/人工知识），只读取既有 catalog 记录并对已登记的图像做编码；照片本身只读。

1. **前置只读勘察**：`assets=3610`、`PRESENT=3638` 个位置、`offline=0`；按冻结媒体策略分类为
   **3182 张图像** + 428 个范围外文件（316 ISO-BMFF 视频、76 UNIDENTIFIED、36 TIFF）；活动 profile
   当时是 Chinese-CLIP（`semantic-image-gen-5bcba729dfda4897a94974b7`，3181/3182 当前版本已确认，
   唯一缺口就是那张 HEIC）。
2. **安装**：`embedding prepare-local --provider qwen3-vl-embedding-2b` →
   `D:\SZ\project\PhotoSearchData\models\qwen3_vl_embedding\`（5 个文件、4 266 571 889 B、
   `hashes_verified=true`，模型目录 3.97 GiB），清单写入 `models\manifests\qwen3-vl-embedding-2b.json`。
3. **候选建库**：`embedding build-candidate --provider qwen3-vl-embedding-2b` →
   3182 个目标、3181 SUCCEEDED、1 FAILED（就是那张 HEIC）。首末 artifact 间隔 **1044.6 s
   （17.4 分钟）**，端到端约 21 分钟，平均约 0.33 s/张；占用 artifacts 0.08 GB + vectors 0.02 GB。
   候选 profile 建好但**没有**激活（`reason=CANDIDATE_NOT_ACTIVATED`，活动指针仍是 CLIP）。
4. **修复 + 重试**：按 §2 的 HEIF 修复后 `build-candidate --retry-failed` →
   1 个目标、1 步、`{"SUCCEEDED": 3182}`，Qwen 空间的 job 全部成功。
5. **受控选择**：`embedding select-profile --profile semantic-image-gen-854bd13ffef8bfc90708575b
   --stopped` → coverage `required=3182 / covered=3182 / missing=0`、`offline=0`、`pending_outbox=0`、
   `pending_repair=0`、`stale=0`、`unreadable_artifacts=[]`；Lance `checked=full / rows=3182 / healthy`；
   模型健康检查 512D 文本与图像向量 finite、norm=1.0；readiness `PyTorchCUDA / FP16 / gpu`；指针提交。
   持久化结果为：活动 profile `semantic-image-gen-854bd13ffef8bfc90708575b`（Qwen），
   Qwen 空间 `3182 CONFIRMED`、outbox 全部 CONFIRMED；CLIP 世代仍保留 `READY`（可切回的前提）。
6. **真实 HTTP 复核**：用产品入口
   `photo-search --data-root D:\SZ\project\PhotoSearchData --port 8137` 启动，`/capabilities` 报
   `active_embedding_model = Qwen3-VL-Embedding-2B (512D)`、`query_encoder_available=true`、
   `strong_match_available=false`；`风景` / `人物` / `海边日落` 三条中文 query 全部 200、向量通道命中
   `candidates=3182`（暖态 431–586 ms）。启动行同时确认 daily 刷新绑定的模型目录/清单已是 Qwen，
   即后续增量建库跟随活动模型。

这一节的证据是本机真实数据根的操作记录（命令与数值见上）；按项目规则**不**把个人照片路径、文件名或
查询命中的资产内容写入仓库文档，只保留计数、空间 ID、耗时与状态事实。
