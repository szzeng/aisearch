# P4-WEMM-PROVIDER — WeMM-Embedding-2B 512D 正式接入（后续条件任务卡）

- **状态**：DRAFT / NOT STARTED。PILOT在公开500图上完成，结论 **INCONCLUSIVE**：平均排序有收益，但猫前排天花板、类别退步与约2.81倍编码成本使正式接入门禁未满足。不是当前应直接执行的任务；见[试验报告](../reports/P4-WEMM-QUALITY-PILOT.md)。
- **日期**：2026-10-20设计，2026-10-21随PILOT结论交付。**仅为条件任务卡，正式接入尚未执行。**
- **推荐执行模型**：GPT-6 High；用户手动选择。
- **参考基线**：`szzeng/aisearch` `master @ 5bf53eb5377e62a1d3c2bbc075080b102a0861a9`；开工核对最新 HEAD/工作区。
- **目标**：在不影响现有 Qwen3-VL-Embedding-2B 512D EXIF v2 / Chinese-CLIP 的条件下，正式增加 WeMM-Embedding-2B **512D EXIF v2**：注册可安装/可查询 Provider、可选候选建库、模型管理中可查看状态、满足既有门禁后可停机切换。**默认仍保持 Qwen 不变**。

## 0. 启动门禁（必须先满足）

读取 `docs/reports/P4-WEMM-QUALITY-PILOT.md`，确认真实模型固定 revision、本地模型 CUDA smoke、资源与质量/误差证据、`GO / NO-GO / INCONCLUSIVE` 结论。只有 **GO 且用户明确授权正式接入** 才启动此卡。无标签时仅有排名变化、或硬件不支持时不得伪称 GO。若运行环境有无法化解的依赖冲突，先记录最小兼容性反例，不重构全系统。

## 1. 必读及可复用框架

`AGENTS.md → docs/development-status.md → 本卡 → docs/plans/P4-EMBEDDING-ARCH.md`，以及 V3.0 §6.2–6.4、§8.1/8.5、§9.2/9.5、§11.3、§12.3/12.4，V3.1 §2.4、§3.2/3.4，ADR-0003、既有 P4 模型管理和切换报告。读 `models/embedding/{base,registry,factory,qwen3_vl,cli,management,candidate_task}.py`、`workers/{ai_worker,query_encoder}.py`、`search/{profile,service,perf_diagnostics}.py`、`web/search.js`、`indexing/semantic.py`、相关测试。勿重新设计已有的 ProcessorSpec、SearchProfile、Generation、Outbox、lease 及 LanceDB 基础协议。

## 2. 关键实现要求

1. **Adapter**：新建 `src/photo_search/models/embedding/wemm.py`，worker 内懒加载，不让 FastAPI/搜索主线程导入重型依赖。严格按固定版官方 `AutoModel` + `AutoProcessor` + `model.embedding(**inputs)` + `<embedding>` 位置生成原生 2048D；前 512D + L2 normalize，输出 float32。按官方聊天模板与视觉预处理、专用 token/图像 patch 规则执行，保留真实 EXIF orientation 行为，不把 Qwen last-token-pooling 或其 instruction 当作 WeMM。提供 `load`, `encode_text`, `encode_image`, `encode_queries`, `encode_images`, `spec`, `health`, `close`，以及符合项目的设备、异常、缺件/OOM/remote URL 拒绝。HEIC/CR2 在现有受控解码能力内兼容；未支持则明确拒绝，不静默退化成低分辨率缩略图。
2. **独立身份**：在 `base.py` 新增新的 adapter 名称及 `SPEC_REVISION`、`_wemm_identity`/`KNOWN_ADAPTERS`/identity builder，并在 `factory.py` 注册。新 manifest `tools/prepare_models/manifests/wemm-embedding-2b-exif-v2.json`，独立 `provider_id`（建议 `wemm-embedding-2b-exif-v2`），固定源码/权重 revision、全部必要文件 hash、tokenizer/chat template、图片预处理、精度/设备、规范化、输入指令、原生2048→512 的 MRL 方法。`processor_id/space_id/generation` 必须不同于 Qwen 与 CLIP；改动任何影响向量的配置创建新空间。绝对不能直接更改旧已发布 Provider 的历史身份或导入跨空间向量。
3. **安装与完整性**：调整 `cli.py` 的 Qwen 专有 `verify_pinned_files` 分派，给 WeMM 使用独立 hash/权重文件校验；支持 `embedding list / prepare-local`。确认模型自定义代码的安全审查、来源固定与**离线加载**；无网络自动拉取。版本冲突有明确错误码；相同完整内容重复安装应幂等，旧 Model Manifest/权重/空间不受影响。
4. **运行时与设备**：先复用 PILOT 已测依赖：若产品主环境 Transformers 5.2.0 与原 Qwen/CLIP/OCR/人脸等全部相容，可选择受控统一环境并更新 `pyproject.toml`、`uv.lock`，由此触发相关完整回归；否则通过现有受管 Worker 协议选择 **WeMM 独立 Python 解释器/虚拟环境**，实现健康握手、进程关闭、runtime_package_id/设备/精度真实报告，禁止单进程同时常驻冲突 CUDA 栈。绝不能把“独立实验 `.venv`”直接当生产部署方案。GPU BF16/FP16 仅使用各自 PILOT 已验证的配置，GTX1660 不能自动承诺可用，无静默 CPU/offload；每次只允许一套重型语义模型常驻。
5. **正式查询与候选建库**：沿用 `indexing/semantic.py`、job/artifact/outbox/LanceDB、有界候选构建、暂停/恢复/单 owner、资源仲裁。候选建库不激活、不挤占高优先级搜索；切换时沿用服务停机租约、权威覆盖、已确认世代与运行健康门禁，禁止绕过/修改现有完整性校验。Qwen 数据不清除，切回旧模型时对新照片的覆盖不足按现有门禁拒绝。OCR/人脸/地点/收藏/用户人工资料不重新识别或删除。
6. **UI 和语义约定**：模型管理列表及搜索页正确显示 WeMM 的 provider/space/generation、CUDA 就绪/待建库/索引缺失/资格拒绝；`search/perf_diagnostics.py` 和 `web/search.js` 中的支持列表/显示名按需扩展。**WeMM 高度匹配阈值先为不可用**（`strong_match_available=false`），绝不能沿用 Qwen cosine0.25 或显示假“0 张”。智能合集的 frozen rule、旧 Profile/Generation 身份、刷新资格、人工排除、分页及模型切换后的失效提示保持正确；必要受影响定向测试，不为它新建一套规则协议。

## 3. 性能设计（PERF-BOUNDED；如改公共热路径/主依赖则升级测试范围）

- **链路/风险**：WeMM 编码、查询模型 worker、分批建库、停机切换。主要风险是 GPU 显存峰值、模型并驻、HTTP 首屏等待、反复重算及在当前活动模型外误发布向量。
- **限制/方案**：512D 单向量、沿用现有 Top5000 / RRF / LanceDB / SQLite 权威候选；批次上限和搜索优先权沿用既有实现，先在隔离库做小样本真实模型闭环；GPU OOM/依赖冲突拒绝而不静默回退；不引入 ANN、云 API、额外二阶段 reranker 或大模型推理服务。
- **验证/停止**：对新增适配器、身份、原有双 provider、后台恢复、UI、停机选择做受影响 B/D 回归；若实际改主依赖、共享 worker runtime/进程边界，按 `docs/testing.md` 触发更广范围/完整 unit+integration 并真实 smoke。真实 30 万搜索/GTX1660 不是本卡默认性能承诺，未验不可标 PASS。资源超现有设备预算则 NO-GO/降级可选而非硬推生产。

## 4. 验收矩阵与发布安全

| 核对项 | 最低验收 |
| --- | --- |
| 模型身份 | 注册表、manifest、processor/spec/space/dimension 同源；错误代码、hash、维度版本拒绝，旧 Qwen/CLIP identity 不变 |
| 实际编码 | 真实 GPU 文字+照片及上传以图搜图，512D finite L2，EXIF/解码与输入路径一致，worker health 与 runtime 资格一致 |
| 查询结果 | WeMM 真实少量照片搜索 + 一次用户词“猫”；相关度基础排序、结果出处、分页 Session，未标定高度匹配不显示 |
| 备份与人工事实 | 隔离数据根中迁移/建库/停机切换/回退不会删除原模型、原图、人工归类、智能合集排除 |
| 建库状态 | 运行/暂停/恢复、并发拒绝、重启对账、pending/repair/outbox 对账、候选不自动激活 |
| 切换门禁 | 完整覆盖+健康才可选择；故障旧活动 Profile 不变；切回 Qwen 资格与覆盖独立检查 |
| GPU 与资源 | 实际 device/dtype、OOM 处理、单模型常驻，8GB 实测；1660 仅已实测才声明支持 |
| 主依赖兼容 | 升级 `transformers` 则覆盖 Qwen/CLIP/人脸/OCR/原入口的适当完整回归；独立解释器则覆盖安装/停止/崩溃/可重启与发布包边界 |
| 质量说明 | 沿用 PILOT 的小样本比较；产品接入不自动宣称更准确或已解决“猫”问题 |

**不得为本卡添加数据库迁移**，除非确有不可回避的协议需求并先单独分析/审批；此时遵守 `docs/database-migrations.md` 的新库、前一版本升级、失败回滚三种隔离测试。该限制不构成绕过必要迁移的理由。

## 5. 交付和独立复核

- 唯一报告：`docs/reports/P4-WEMM-PROVIDER.md`；当前代码身份、依赖选择理由、应用内使用操作、实际命令、隔离真实模型 smoke、受影响 JUnit/Ruff、失败与未验写明。
- 独立审查：涉及共享运行依赖/身份/切换状态，执行 **DS-H 全受影响面工程审查**；风险聚焦项再按 V3.1 的 Sol focused review 规则决定，不删原有硬门禁。
- 按仓库 `AGENTS.md` 收口更新 `docs/development-status.md`，而不是修改已闭合任务的历史事实。
- **STOP**：全部目标验收完即停止。不新增单独 WeMM 搜索后处理、双模型融合、强匹配策略、全库重建、GTX 1660 强行部署、0.5.0 发行任务。需要这些必须另有明确授权和独立证据。
- 用户已于2026-10-21授权提交推送PILOT代码、结论及本条件卡；这不等于授权执行正式Provider，也不构成其验收或发布。

## 给 Codex 的执行提示词（仅在 PILOT 得到 GO 且用户批准后使用）

> 仅在本卡启动门禁满足且用户授权正式接入后：基于最新 master、`AGENTS.md`、`P4-WEMM-QUALITY-PILOT` 的可复现实测报告，执行 `P4-WEMM-PROVIDER`，只增加 WeMM 2B 512D EXIF v2 第三语义模型正式支持。复用原有 registry/worker/ProcessorSpec/Generation/Profile/候选分批建库与停机切换，不改 Qwen/CLIP 已发布身份，不触碰个人原图和人工状态。固定 WeMM 官方源码和权重 revision/hash；先解决已证明的 Transformers 依赖与设备策略，拒绝未验证的 BF16/FP16、CPU offload 和全局环境替换。新模型默认不激活、未标定不显示“高度匹配”；隔离库完整走安装、建库、搜索、暂停/恢复、切换和故障回退，并按受影响面测试和独立复核。对真实图库的正式候选全量建库与切换需要另行授权；提交推送范围以该次用户授权为准。本次仅交付条件卡，不执行本提示词。
