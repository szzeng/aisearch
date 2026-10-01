# P4-EMBEDDING-ARCH-review — 独立 DS-H 全受影响面工程审查

日期：2026-09-30。状态：**READY FOR INDEPENDENT REVIEW**。
被审实现：[实现报告](../reports/P4-EMBEDDING-ARCH-implementation.md)；任务卡：[P4-EMBEDDING-ARCH](P4-EMBEDDING-ARCH.md)；
策略：[接入策略](../plans/P4-EMBEDDING-ARCH.md)。实现已停止；未开始修复。

## 1. 审查定位

按 V3.1 §2.4 对本卡**全部改动与直接调用面**做完整受影响面工程审查（不是重跑历史性能专项）。可以夹具、
只读检查与定点反例复核；缺口才补测。审完通过后写
`ENGINEERING REVIEW PASS / SOL FOCUSED REVIEW PENDING` 并生成阶段交接，不生成 Sol 之前的新实现。

基线：`2d5fd94` + 本卡未提交改动（工作区同时存在用户先前对
`docs/development-status.md` 与两份架构文档的未提交修改，不属于本卡实现，请勿误判）。

## 2. 必须覆盖的符号与条款

1. **registry / spec / 兼容 ID**
   - `models/embedding/base.py`：`provider_block`、`identity_of`、`build_processor_spec`、
     `LEGACY_CLIP_SPACE_HASH`、`provider_id_of_processor`、`effective_device_policy`。
   - 历史空间 `chinese-clip-vit-b-16:c8ac42d3907c57b0` 是否在所有路径逐字保持；内容变化是否必定换空间。
   - `workers/query_encoder.pinned_embedding_spec` 作为旧接口包装是否与 P4-02N 输出逐字段一致。
2. **worker / runtime 真值**
   - `workers/ai_worker.py`（manifest→factory、结构化 details）、`models/embedding/factory.py`、
     `models/embedding/qwen3_vl.py`（`_resolve_device` / `_assert_placement` / `_normalized_vector` /
     `verify_pinned_files` / 远程输入拒绝）。
   - `indexing/semantic.adapter_runtime_facts` 与 `record_runtime_evidence` 的 provider/devices/precision
     映射：PyTorch CUDA 是否被如实记录（`PyTorchCUDA`），是否可能与 ONNX `CUDAExecutionProvider` 混淆。
   - 设备策略取严：manifest `gpu_required` 是否在任何调用方默认下都不可能被软化。
3. **jobs / outbox / 空间隔离**
   - `indexing/semantic.build_index` 的 `expected_space_id/expected_processor_id` 是否覆盖所有调用方；
     同维错 encoder 是否零副作用。
   - `ensure_search_profile` 的“候选不激活”是否只对**不同空间**生效，是否保留了新库/活动模型日常建库。
4. **候选不激活 / 覆盖门禁 / 原子指针 / 重启**
   - `indexing/selection.py`：`coverage_report`（当前 ACTIVE + PRESENT、当前 sha 的 CONFIRMED、
     lifecycle PUBLISHED）、`verify_artifacts`、`lance_health`、`require_selection_ready`、
     `selection_precondition`。
   - `search/profile.activate_profile` 的 `precondition` 位置是否确实在 bundle/readiness 之后、
     指针更新之前，且与既有 `activation_hook`/错误语义不冲突；空代/离线/未确认 outbox/不可读 artifact/
     bounded Lance 是否都拒绝；失败/中断后旧指针是否不变；提交后重启是否按新指针。
5. **增量 / 切回 / 人工知识**
   - `refresh/pipeline.py` 两处 `build_index` 目标核对；活动 profile 的 space/generation/processor 是否
     贯穿 daily 与 build；切回旧 CLIP 的覆盖拒绝与补齐后成功。
   - 人工人物/地点/备注是否完全未被本卡改动影响。
6. **API / UI / 能力展示**
   - `api/routes.active_embedding_model`（按活动 profile 的图像世代解析，而非 `active_generations[0]`）、
     `search/service.strong_match_policy` / `_channel_summary` 的只读 `strong_match_available`、
     `web/search.js` 的隐藏逻辑与绑定提示。
   - `channels.vector` 只增字段：`strong_match_count`/`more_possible`/`truncated` 语义是否未被改写。
7. **依赖与环境**
   - `pyproject.toml` 的 `pytorch-cu128` explicit index + `[tool.uv.sources]`、精确 pin
     （torch 2.8.0 / torchvision 0.23.0 / transformers 4.57.0 / qwen-vl-utils 0.0.14）与 `uv.lock`
     一致性；单环境是否与 ONNX/Face/OCR import/启动直接兼容（报告 §4 已记录 import 与真实 CLIP 建库）。
   - transformers 4.57.0 被上游 yank、onnxruntime CUDA 12/13 提示（报告 §5.6/§5.7）属于待跟踪事实，
     请判断是否需要在本卡收口前处理。

## 3. 必审未决项（策略 §6）

本维护入口**不创建 UpgradeRun**，因此不声称满足 ADR-0013 §7 的 `RUN_SUCCEEDED` 升级运行门禁，也不伪造
成功 run；ADR-0013 的升级 API/六门禁保持冻结。请明确裁定：独立停机维护选择是否属于 ADR-0003 的合法复用。
若必须纳入 UpgradeRun，则该选择边界保持 **BLOCKED**，另生成最小契约/实现卡，不得削掉 `RUN_SUCCEEDED`
求 PASS。审查前不对个人库执行选择（本卡只在夹具与隔离 smoke data root 上执行）。

## 4. 需要复核的证据

* `test-results/p4-embedding-arch.xml`：203 tests / 0 failed / 0 errors / 6 skipped（16 个套件，含
  触及 capability/profile 后按卡加入的 `test_p2_scheduler.py`、`test_p2_scheduler_recovery.py`，以及被
  worker 改动影响的 `test_ai_worker_lifecycle.py`）。
* `test-results/p4-embedding-arch-smoke.json`：真实 CUDA 双 provider smoke，24/24 checks、退出码 0，
  含版本/哈希/配置、设备与精度、覆盖、耗时与峰值显存、8 条中文 query 与 2 次上传自查询的原始观察。
* 实现报告 §4.2 的真实浏览器记录（文字搜索与上传搜图在现有页面各完成一次）。
* 命令复核：`uv lock --check`、`uv run --no-sync ruff check src tests tools`、`git diff --check`。
* 额外全量套件（非本卡要求，仅为排查依赖升级回归）：`test-results/p4-embedding-arch-full.xml`
  = 1911 / 5 failed / 0 error / 48 skipped，其中 1 个由本卡引起并已在
  `tests/integration/test_ai_worker_lifecycle.py` 定点修复（结构化 manifest 缺失错误），另外 4 个为
  `p2-contracts.v1.json` 冻结哈希与 `c1d0a34`（P4-02Y-L）不一致造成的**既有失败**，证据见实现报告 §7；
  请裁定是否要求本卡处理，或路由给 P4-02Y-L 审查。
* 追加的运行时修复（`workers/ai_worker.prepare_framework_stack` 的 `USE_TORCH=0`、`runtime/device.preload_runtime_dlls` 的仅 nvidia 模式）请一并复核：证据与"不能改加载顺序"的反例见实现报告 §5.6。
* 真实数据根操作记录见实现报告 §8（安装 → 3182 图候选建库 → 定点重试 → 受控选择 → 真实 HTTP 复核），
  以及为它追加的三处修复（选择门禁改为“可索引内容”、worker 侧 HEIF 注册、`--retry-failed`）。

## 5. 输出要求

工程审查报告写入 `docs/reports/P4-EMBEDDING-ARCH-review-dsh.md`，逐条给出符号/条款 → 证据 / 反例 →
结论。只有 V3.1 §2.2 六项之一有明确证据才建议 DS-MAX，并写编号、证据与未决问题；Windows/依赖类问题建议
C1。修复必须定点，修复后回归只跑受影响集合，且需要短复审。
