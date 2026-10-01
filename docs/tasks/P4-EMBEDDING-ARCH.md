# P4-EMBEDDING-ARCH — 实现可插拔语义模型架构并接入 Qwen3-VL-Embedding-2B 512D

日期：2026-09-30。状态：**READY FOR IMPLEMENTATION / NOT STARTED**。
基线：`2d5fd94`（设计起点）；开工必须重新核对 HEAD/工作区，保留并行编辑。
上层需求：P4-Embedding-Provider。唯一当前接受任务：本卡。
推荐实现：**DS-H / DeepSeek V4.1 Flash / 高推理**；模型由用户手动选择。
独立审查：DS-H 全受影响面工程审查 → **Sol high 必选聚焦终审**。

## 1. 用户目标与停止点

尽快在现有应用中使用 Qwen3-VL-Embedding-2B 512D 文字搜图和以图搜图；Chinese-CLIP 保留可选。将生产链路改为可注册 adapter，后续兼容单向量图文协议的模型只添加 adapter/manifest/注册项。

按[接入策略](../plans/P4-EMBEDDING-ARCH.md)实施。只做一个闭环：两个 provider、一个活动 profile、独立向量空间、真实建库/搜索、受控停机选择、小样本验证。不要启动全量图库重建、性能专项或完整 P4-03B/P4-04。

## 2. 必读资料

读取顺序：AGENTS.md → development-status → 本卡 → 接入策略 → [设计核对报告](../reports/P4-EMBEDDING-ARCH-design.md)。

只读主架构 §1.0/§1.3、§3.1–3.3、§6.2–6.4、§7.1–7.4、§8.1/8.5、§9.2/9.5、§11.3.3–11.3.7、§12.4 第 23–28 项；V3.1 §2.2–2.4、§3.2/3.4、§4.1、§9.2、§11/12。本次设计已作跨模块对齐，实现不要重复全文阅读。

冻结上游：ADR-0001；ADR-0003 §2–8；ADR-0013 §1/§4/§7（覆盖、停机边界、原子门禁，未实现 UpgradeRun 不可伪装存在）；只补读直接受影响条款。
历史速度证据：[P4-QWEN-SPEED](../reports/P4-QWEN-SPEED.md)和 `test-results/p4-qwen-speed.json`，无需重跑。
未收口任务：P4-02Y-W、P4-02Y-L 仍待独立审查；不得改成 PASS。

## 3. 允许修改的受影响面

现存路径：

- `src/photo_search/models/embedding/`、`workers/{ai_worker,client,protocol,query_encoder}.py`；
- `src/photo_search/indexing/semantic.py`、`refresh/pipeline.py`；
- `src/photo_search/__main__.py`、`app.py`、`api/{context,refresher,routes,models}.py`，仅接入与能力展示；
- `search/{embedding,upload,profile,service}.py`，仅模型绑定/切换/高度匹配策略；
- `runtime/`、`scheduler/capability.py`，仅为 Qwen 加真实 PyTorch CUDA 事实映射，保持既有资格矩阵；
- `web/search.js`，仅活动模型/不可用原因及高度匹配隐藏；
- `tools/prepare_models/`，新增本地 Qwen 校验/导入工具与参考实现出处；
- `pyproject.toml`、`uv.lock`、直接相关 tests、README、任务报告/状态/架构与策略的相关说明。

新增建议路径：`models/embedding/{base,registry,qwen3_vl}.py`（复用已有命名更合理时按实际调整），定向测试 `tests/unit/test_p4_embedding_provider.py`、`tests/integration/test_p4_embedding_provider_flow.py`，真实 smoke 工具 `tools/diagnostics/p4_embedding_provider_smoke.py`。这三个测试/工具尚不存在，实施时创建并记录最终路径。

不改既有迁移/资产身份/人工人物地点协议、SearchProfile 列集、lease/outbox 顺序、向量后端、候选/融合算法、Top5000、block size 或包配置；不新增云服务、运行时 API key、自动模型下载、双模型召回、视频/reranker/ANN。发现必须突破冻结协议时先输出具体冲突，生成裁决卡，不能边实现边重定义。

## 4. 实现步骤（同一会话完成）

### A. 轻量模型描述与统一工厂

以 ADR-0003 的 spec 为唯一公共类型，实现六方法 adapter 语义；worker 工厂按 registry 懒加载模型。Chinese-CLIP 用薄包装保持已发布 processor/space ID、输出及旧 manifest/CLI 兼容；旧 ID 仅允许完整固定配置的兼容映射，关键配置变化应新建 ID。

轻量 spec 解析不加载框架。删除建库/查询层的 ONNX-only 假设；不在 API 顶层 import torch/transformers 或具体 adapter。现存 `pinned_embedding_spec` 可保留为旧接口包装，生产通用链路用统一解析器。

### B. Qwen 512D adapter 与单环境依赖

按策略 §5 固定本地 revision/hash、FP16 CUDA、batch=1、eager、8192 tokens、4096–262144 图像 pixels、官方 pooling/指令。输出 2048D 前缀 512D 后转 float32 再 L2 normalize，图像/文本/上传图共用此规则。未声明支持 MRL 的其他模型不得自动截断。

优先复用 `tmp/qwen-speed-model/` 的本地权重；正式模型目录 `<data_root>/models/qwen3_vl_embedding/`。固定代码来源和许可证、拒绝远程输入和隐式下载。升级主环境 transformers 并锁定实测 torch/torchvision/qwen-vl-utils 组合；检查当前 CLIP tokenizer、ONNX、Face/OCR import/启动的直接回归，必要结果写报告。

PyTorch CUDA 是独立执行事实，不能套用 ONNX provider 清单。用真实加载+文本/图像推理记录 processor 专项资格；仅 `torch.cuda.is_available()` 不足。官方自动 CPU 选择须被 wrapper 阻止；全参数和输入设备、precision 要与派发一致。无 GPU、OOM、hash 错、缺依赖均结构化拒绝，不改选 CPU、像素预算、精度或模型。

### C. 建库、搜索与增量贯通

Qwen 图像索引必须走正式 semantic jobs/artifact/outbox/index_entries 路径；以旧产物/向量替代不可接受。将 manifest/spec/facts 一致地传给查询和建库。每次搜索固定完整 profile，并核对 worker 返回的 processor/space/dimension。

候选 build 与激活拆开；改掉 `ensure_search_profile` 对非活动候选的立即激活行为，保留既有新库/活动模型日常建库的合法行为。新 source、daily/build/retry/progress 都按活动模型的当前空间统计，不能从旧 CLIP 覆盖推断 Qwen 完成；空输入仍“未开始/无可索引内容”或对应真实终态，不写假 generation。

### D. 轻量命令与受控停机选择

实现 list/prepare-local/build-candidate/select-profile 的最小 CLI 用例，实际名称写入 README/报告和 `--help`；采用现有命令风格。无活动 profile 时可显式以 Qwen 初始化；已有 profile 时启动参数不能强行改搜索编码器。

选择动作只允许服务/全部 writer 已停止且取得维护互斥；租约不证明非受管理 writer 已停止。按策略 §6，将当前 ACTIVE 资产完整的当前 revision CONFIRMED 覆盖、pending/repair/outbox、artifact 真正可读、runtime 资格与 profile 绑定校验放入 `activate_profile` 同一写事务，可加内部校验 seam，不能“事务外检查后事务内切换”。Lance/模型推理健康检查在事务外准备，事务内复核对应记录，禁止在 SQL 事务内推理。空代、scope 子集或离线缺覆盖拒绝。独立维护入口与 ADR-0013 RUN_SUCCEEDED 门禁的适用性作为必审未决项，详见策略 §6；不绕过升级 API、不伪造成功 run。审查前仅夹具验证。

选择失败旧指针不变；指针提交后崩溃，重启按新持久指针恢复或明确不可用。切回旧 CLIP 同样查当前覆盖；Qwen 期间新增未覆盖资产应先补建 CLIP。无后台双版追赶/自动清理。先卸载旧语义模型再加载候选，保证 8 GB 不双驻留。

### E. 现有页面与高度匹配

capabilities/进度明确活动模型与当前空间覆盖；现有搜索页可用，无需复杂模型管理页。CLIP 保留暂定 1.3 计数策略；Qwen/未知空间 `strong_match_available=false`，UI 隐藏统计句，不能显示假“0 张”。只在 channels.vector 加兼容只读字段，不改 SearchResult 顶层或 profile schema，不影响候选与排名。

## 5. 本卡验证范围：用户授权的快速验收例外

本轮不是性能或完整发行验收。用户明确要求简单的近期测试并复用已测性能；按 V3.1 §9.2 的限定例外，用以下定向集替代本卡全量 B/C 回归与大型质量矩阵，保留对应 D 边界注入和独立审查。不重跑 30 万性能曲线，不构建 exe。代码/依赖新变化不能复用旧成功日志冒充当前通过。

先运行定向回归，最终代码收敛后只运行一次下面集；失败后只补跑失败和直接回归。执行时路径若变更，按真实最终路径改命令并记原因。

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
uv lock --check
uv run --no-sync ruff check src tests tools
uv run --no-sync pytest -q -p no:cacheprovider --basetemp=.pytest-tmp/p4-embedding-arch --junitxml=test-results/p4-embedding-arch.xml tests/unit/test_p4_embedding_provider.py tests/integration/test_p4_embedding_provider_flow.py tests/unit/test_p4_02n_query_encoder_wiring.py tests/unit/test_p4_02o_semantic_build.py tests/unit/test_p4_02y_w_search_capacity.py tests/unit/test_p2_contracts.py tests/unit/test_worker_protocol.py tests/integration/test_p2_search_profile_migration.py tests/integration/test_p2_search_image.py tests/integration/test_p2_search_endpoint.py tests/integration/test_p2_index_submission.py tests/integration/test_p2_index_repair.py tests/integration/test_p4_02y_library_refresh.py
git diff --check
```

若触及 capability/scheduler 代码，再加入 `tests/unit/test_p2_scheduler.py` 和 `tests/integration/test_p2_scheduler_recovery.py`；触及其他直接调用面补相应定向测试，不因全量测试通过而省略新模型 smoke。

### 必测场景

| 类别 | 必须核验的行为 |
|---|---|
| provider/身份 | 旧 CLIP manifest → 原 ID/原行为；Qwen → 独立 ID；维度/权重/prompt/预处理变化 → 新 ID；未知 provider 拒绝 |
| 同维空间 | 错 encoder/写入/cache/profile 返回既有结构化错误，零新增 artifact/entry/活动指针副作用 |
| 数值 | 图文/上传同为 512D FP32，finite、norm≈1；NaN/零/shape 错拒绝；MRL 同一 forward 对照 |
| 建库 | 两模型独立 generation、CONFIRMED 覆盖；候选不激活；重复/重启不产生逻辑重复；旧 lease 迟到仍拒绝 |
| 选择 | 缺覆盖/离线/未确认 outbox/不健康 → 指针不变；校验失败/事务失败/提交后重启分别注入 |
| 增量/切回 | Qwen 活动时新增走 Qwen；CLIP 缺新图时切回拒绝，补齐后成功；人工知识原值保留 |
| 资源 | GPU_REQUIRE 无 CUDA/worker 错 device/OOM 拒绝；无 CPU/offload；close 幂等，重启重新加载 |
| 查询/显示 | 文本/上传图实际选 Qwen；AND 权威筛选、分页/会话失效；Qwen 高度匹配隐藏，CLIP 边界正常 |

真实 smoke：新增工具提供固定最大 30 图、固定 query、隔离 data root 和 JSON 输出参数，命令落报告。使用项目合成/已合法公共 fixture 12–30 图、6–10 条中文 query、≥2 次上传图，分别完成 CLIP 与 Qwen 真实 CUDA 建库/HTTP 搜索；在现有浏览器页面各完成文字/上传展示。保存 `test-results/p4-embedding-arch-smoke.json`，记录版本、revision/hash、配置、device/precision、图/查询数、覆盖、known-target 观察、退出码/失败、少量耗时与峰值 VRAM。不得用 fake encoder 或直接官方 demo 替代产品链路。

不扫描任何真实个人照片来源。夹具/数据库/临时数据放 `tmp/p4-embedding-arch/`，pytest 放 `.pytest-tmp/`；模型不入 Git。WinError 5 按正常权限路径重跑，不改 ACL。没有证据的项目写 PENDING/BLOCKED，不能写 PASS。

## 6. 报告、审查与交接

主报告：`docs/reports/P4-EMBEDDING-ARCH-implementation.md`，包含真实基线/文件/接口差异、最终固定配置、操作命令、JUnit 计数与 smoke、失败/未验证、无全量性能/包声明。状态只保留短摘要。

实现验证完成后生成 `docs/tasks/P4-EMBEDDING-ARCH-review.md`，停止实现；状态 `IMPLEMENTED / SCOPED VALIDATION COMPLETE / INDEPENDENT REVIEW PENDING`。

独立 DS-H 按 V3.1 §2.4 审**全部本卡改动和直接调用面**：registry/spec/legacy ID、worker/runtime 真值、jobs/outbox 空间隔离、候选不激活、覆盖门禁/原子指针/重启、增量/切回、API/UI和依赖。复核对应 JUnit/真实 smoke，缺口才补测；不是重跑历史性能专项。
工程审查报告：`docs/reports/P4-EMBEDDING-ARCH-review-dsh.md`。只有 V3.1 §2.2 六项之一有明确证据才建议 DS-MAX，并写编号、证据和未决问题；Windows/依赖问题推荐 C1。

DS-H 通过后写 `ENGINEERING REVIEW PASS / SOL FOCUSED REVIEW PENDING`，生成阶段交接并停止。Sol 为必选：只审同维空间/旧 ID兼容、实际设备资格、完整 profile/覆盖/故障/人工知识边界和未决 ADR 项；不重复全 diff/成功实验。输入为 DS-H 压缩包、点名条款/符号、最小反例，报告 `docs/reports/P4-EMBEDDING-ARCH-review-sol.md`。Sol 不通过交回 DS-H 定点修复，短审直接回归。

本卡最终只能写 **PASS — 本机源码运行的双 provider 接入与定向验收**；不表示全量 B/C、GTX 1660、Qwen 全库质量/性能、现有 exe 支持或 P4-03B/P4-04 已通过。全部本卡必测与独立终审完成才可标此 PASS。之后转日常试用，真实图库范围授权后才建库；仅在用户需要打包时另立 Qwen 包接入卡，不自动追加性能研究。
