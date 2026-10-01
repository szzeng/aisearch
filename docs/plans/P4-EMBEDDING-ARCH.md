# P4-EMBEDDING-ARCH — 可插拔语义模型与 Qwen3-VL 512D 接入策略

日期：2026-09-30。状态：**DESIGN READY / IMPLEMENTATION NOT STARTED**。
上层需求名：P4-Embedding-Provider；执行任务 ID：P4-EMBEDDING-ARCH。
本计划是实现范围与验收安排，不是新的冻结协议或实现 PASS。

## 1. 交付结果与最快路径

在现有应用内注册 Chinese-CLIP 和 Qwen3-VL-Embedding-2B 两种语义 provider，文字搜图和上传以图搜图均使用当前活动模型；以后新增兼容“一个输入、一个图文向量”的模型时，只增加 adapter、模型清单和注册项，不改搜索、分页、筛选或 LanceDB 后端。

“多模型支持”本轮指可以安装、建库和选择不同模型，普通查询仍只使用一个活动 profile。不做并行多模型召回、向量平均、reranker、视频、模型市场或完整升级管理 UI。Qwen5、SigLIP3 等未来模型必须按其真实接口适配，不能承诺任意模型只改名称即可运行；多向量模型需要另外版本化协议。

最快闭环：**共用接口与 registry → 保持 Chinese-CLIP 兼容 → Qwen 图文编码 → 独立建库 → 停机选择完整 profile → 现有页面搜索 → 小样本验收 → 独立审查**。一个实现任务内按此顺序完成，不为每一步建立新会话。

## 2. 当前证据与实际缺口

基线：`2d5fd94`，设计前工作区干净。

| 已有部分 | 实际状态 | 本轮处理 |
|---|---|---|
| ADR-0003 §4–8，EmbeddingAdapter / ProcessorSpec / SearchProfile | 已冻结 | 沿用，不重新设计数据协议 |
| `search/embedding.py`、`search/profile.py` | 已核对 space、dimension、profile 与原子活动指针 | 保持接口与校验 |
| `workers/ai_worker.py` | 直接构造 ChineseClipOnnxAdapter | 改为 registry 工厂，重型 import 留在 worker |
| `workers/query_encoder.py` | manifest/spec/默认路径/provider 映射均有 Chinese-CLIP/ONNX 假设 | 抽取描述与 spec 解析，保留旧入口兼容包装 |
| `indexing/semantic.py`、`refresh/pipeline.py` | 建库 spec 固定旧 manifest；ensure_search_profile 创建后立即激活 | 支持目标 provider；候选构建与激活分开 |
| `pyproject.toml` | transformers `>=4.46,<4.47` | 与 Qwen 需求冲突，单环境升级并锁定 |
| P4-03A / ADR-0013 | 契约已冻结；未发现生产 upgrade 模块 | 不把计划接口冒充现成实现 |
| P4-02Y-W 高度匹配 | 全局 `distance <= 1.3`，当前门限修正仍未验证 | Chinese-CLIP 保持；Qwen 暂不显示高度匹配计数 |

[P4-QWEN-SPEED 报告](../reports/P4-QWEN-SPEED.md)和原始 JSON 已记录 RTX 4060 Laptop 8 GB、FP16 CUDA 短文本 query P50 72.86 ms；512D 约 3.81 s 是复用向量基线的估算，未证明真实 Qwen 图片建库或生产 HTTP 闭环。复用选型结论，不重复 30 万性能测试。

官方模型支持 64–2048D MRL，512D 可作为该模型的输出配置。[官方模型卡](https://huggingface.co/Qwen/Qwen3-VL-Embedding-2B)。本机已有权重与参考脚本位于 `tmp/qwen-speed-model/`；执行时校验 revision/hash 后复用，不重新下载。临时环境仅供参考，不能成为产品的隐含运行依赖。

## 3. 最小架构

```text
活动 SearchProfile
  → embedding_processor_id / space_id / runtime_package_id
  → registry 解析固定模型描述与 manifest
  → managed worker 创建对应 EmbeddingAdapter
  → encode_queries / encode_images
  → 既有 generation + LanceDB + FTS/RRF + Session
```

业务层只使用 ADR-0003 的 ProcessorSpec 和普通数值向量。统一方法语义：`spec / load / encode_queries / encode_images / health / close`；具体 Python 签名在实现中适配既有 worker 调用，不修改冻结 JSON 信封。旧 ONNX adapter 的局部 spec 通过薄包装转换，禁止新增第二套持久 ProcessorSpec。

registry 分为轻量描述解析和 worker 内懒加载 factory。描述包含稳定 `provider_id`、固定 manifest、数据目录相对路径及创建 adapter 的注册项；profile 继续绑定 processor，而不是另建活动模型指针。provider_id 不是运行执行 provider：Qwen 的 PyTorch CUDA 必须如实报告，不能冒充 ONNX `CUDAExecutionProvider`。

有活动 profile 时从其 processor 反解 adapter；启动参数指定另一个 provider 只能作为候选建库请求或被拒绝，不能覆盖当前搜索编码器。没有 profile 的新库默认 Chinese-CLIP，也允许显式选择 Qwen 完成首次建库。日常增量建库始终跟随活动 profile。

不新增 registry 数据库表或 profile 字段；模型映射复用 processors 的不可变 spec 和数据目录模型清单。清单缺失、未知 provider、hash 不一致都结构化失败，禁止按维度猜模型。

## 4. 身份、兼容与持久化

新配置的表示空间指纹至少覆盖：模型 resolved revision、全部权重 checksum、tokenizer/config、图像预处理、文本与图像指令、pooling、原始/输出维度、MRL 方法、归一化版本，以及未经兼容证明的精度/后端配置。处理器指纹还覆盖资格配置。具体 ID 格式实现时统一规范化并测试。

Qwen 512D 与 Chinese-CLIP 512D 必须有不同 processor/space/generation/profile；不能复用旧图像向量、artifact 或查询缓存。新模型必须重新编码目标图片；资产 ID、文件位置、OCR、人脸、人工人物/地点/备注均复用并保留。

当前 Chinese-CLIP 的历史 ID 已被真实数据引用，不能批量重命名或强制重建。为已固定、完整配置匹配的旧 manifest 保留兼容映射；完整配置变化必须拒绝映射到旧 ID并发布新 ID。兼容性测试要用历史 manifest，不能只比较昵称、维度或 image checksum。

图像/文本编码返回 512 个有限 float32 数值、非零且 L2 norm 接近 1；shape、NaN/Inf、零向量、错误 space/processor 都拒绝提交与检索。新模型仍声明 cosine；沿用现有归一化向量对应的 squared L2 距离排序，不改 RRF、Top5000 或权威候选算法。

## 5. Qwen 首轮固定配置

| 配置 | 本轮选择 |
|---|---|
| 模型 | `Qwen/Qwen3-VL-Embedding-2B`，resolved revision + 全文件校验 |
| 推理后端 | PyTorch + Transformers，worker 内本地加载 |
| 设备 | CUDA，GPU_REQUIRED；无静默 CPU/offload |
| 精度 / batch | FP16 / 1，FP32 保存向量；不加量化 |
| Attention | eager，沿用速度探针；不安装 FlashAttention/vLLM |
| 原始 → 输出 | 2048 → 前 512 维 → float32 L2 normalize |
| Pooling | 固定官方参考的最后有效 token pooling 与 attention mask 规则 |
| 指令 | 文本查询、图片建库、上传图均显式固定官方默认指令；进入指纹 |
| token 上限 | 8192，显式固定，不依赖库更新后的默认值 |
| 图像像素预算 | min_pixels=4096、max_pixels=262144；按 Qwen processor 规则 resize，进入指纹 |
| 图像输入 | 方向修正后的原文件/受控完整解码输入，RGB；不复用 224 crop 或 320 网格缩略图 |

图像像素预算是本轮初始配置，低于参考脚本默认最大值；不能把既有文本速度证据当成该图像配置的质量/显存验收。若 batch=1 仍 OOM，阻塞并记录；不得自动降低像素/精度或更改空间。调整须显式发布新配置并仅补跑受影响小样本。

优先固定已实测依赖组合：torch 2.8.0 CUDA 12.8、torchvision 0.23.0、transformers 4.57.0、qwen-vl-utils 0.0.14；执行时在单一 Python 3.12.10 环境验证与当前 ONNX/OCR/Face 依赖兼容后更新 `pyproject.toml` 与 `uv.lock`。不得只升级诊断 venv、不接入产品。确有不可调和冲突时记录最小冲突，再另行设计运行时隔离，本轮不默认增加解释器。

官方参考有自动 CPU 选择和远程输入支持；产品 wrapper 必须在加载前后核对 CUDA、precision、全部模型参数/输入的实际设备，禁止 offload 与远程图片。参考代码须固定来源、revision、checksum 和许可证；避免从可变数据目录任意 import 可执行脚本。依赖和权重离线本地加载，不允许启动时下载缺件。[官方实现](https://github.com/QwenLM/Qwen3-VL-Embedding)。

同一时刻只驻留一个重型语义模型；查询和图片建库共用有界 worker，按 batch=1 边界让出交互请求，HTTP 不同步推理。缺件、OOM、崩溃、关闭/重启以及实际 CUDA device evidence 纳入本轮 smoke。

## 6. 快速启用与停机切换

本轮提供轻量命令入口：列出可用 provider/profile；指定模型准备；指定 provider 构建候选；校验并停机选择 profile。复用现有 CLI/应用服务，不为了选择两个模型重做设置页。页面 capabilities/来源进度显示真实活动模型、当前空间的覆盖和未就绪原因。

具体命令名由实现时补齐 `--help` 和复制可用的操作说明；不能在设计报告中声称已有这些命令。

1. 使用合成隔离 data root 完成开发；正式权重放 `<data_root>/models/qwen3_vl_embedding/`，清单放 `<data_root>/models/manifests/`。不得放安装目录或 Git；已有本地权重校验后复制，新增大下载需用户明确授权。
2. 候选 build 只生成对应 processor 的 artifact、outbox、generation 和不可变 profile，**不自动激活**。复用 lease/input revision/确认协议，不新增 UpgradeRun 状态机。
3. 切换前停止服务和其他 writer、持有既有数据根维护互斥；该租约只能排斥受管理进程，不能证明外部 writer 已停止，CLI 操作说明须显式要求关闭全部写入者。停机核验所有当前 ACTIVE 资产的当前 revision CONFIRMED 覆盖、该代 pending/outbox/repair、被引用 artifact 的实际可读性、Lance 健康、运行时专项资格，以及 profile 的全部绑定。generation READY 或“至少一张成功”不足以通过覆盖门禁。
4. 离线/损坏图片或缺覆盖使切换拒绝、旧 profile 保持活动；本轮不提供隐式部分图库切换。不借 `source_id` 小范围构建悄悄切走整个库。
5. 在唯一候选 worker 中先做本地图文健康检查；权威覆盖、artifact 可读性、profile/runtime readiness 门禁与指针/生命周期更新必须在同一 `BEGIN IMMEDIATE` 事务内，不能先检查再另开事务搬指针。复用既有 `activate_profile` 事务逻辑，可增加事务内前置校验回调或提取内部 helper；保留原接口/错误与故障 hook，不嵌套事务。关闭 worker 后启动服务，启动按持久化 profile 解析模型。检查失败/事务回滚保持旧指针；提交后崩溃则重启识别新指针，缺运行时时报告不可用，禁止自动切回旧模型。
6. 切回保留的 Chinese-CLIP 同样核验当前完整覆盖与运行时健康；Qwen 启用后新增资产可能尚无旧向量，先补旧模型建库再切回。保留旧索引不等于完整回退。旧 session/cache 在重启后失效，人工知识不回滚。

这是个人试用的受控停机选择入口；需遵守 ADR-0003 §8 和 ADR-0013 §7 可适用的覆盖/健康/原子门禁，不实现或冒充 P4-03B/P4-04 的后台双版追赶、预算、在线切换、清理或完整升级演练。若必须改冻结契约/新增迁移才能实施，记录准确缺口并停止该边界，先安排契约裁决，禁止临时 SQL 绕过。

适用性未决项须交独立终审：本维护入口不创建 UpgradeRun，因而不声称满足 ADR-0013 §7 的 `RUN_SUCCEEDED` 升级运行门禁，也不伪造成功 run；该 ADR 的升级 API/六门禁规则保持冻结。DS-H/Sol 必须明确裁定独立停机维护选择是否属于 ADR-0003 的合法复用；若必须纳入 UpgradeRun，则该选择边界保持 BLOCKED，另生成最小契约/实现卡，不削掉 `RUN_SUCCEEDED` 来求 PASS。审查前仅在隔离夹具验证，不对个人库执行选择。

## 7. 高度匹配与现有任务

Chinese-CLIP 的 `distance <= 1.3` 是暂定展示门限，不代表 Qwen 的相关度分布。最小方案：按活动空间识别已配置的统计策略；CLIP 保持既有计数，Qwen/未知模型统计标为不可用、UI 隐藏该句。可在 `channels.vector` 增加只读 `strong_match_available`，保持原有数值/布尔字段兼容，Unavailable 的 0 不可渲染为“0 张高度匹配”。不增加 SearchResult 顶层字段或不可变 profile schema。

该策略不是用 provider 昵称确定所有配置的阈值；只认明确绑定的旧 CLIP 空间/兼容配置。5000 容量、排名、结果数量和 scan_truncated 继续适用，两模型的计数策略都不得过滤候选。新的 Qwen 门限标定另按真实反馈决定，本轮不开展。

P4-02Y-W 和 P4-02Y-L 独立审查仍待做，不能因本任务立卡写成 PASS。实现前按 W 最新代码核对相关测试；本轮触及 W 展示的 diff 纳入本轮完整受影响面审查，不重复它的性能实验，也不把本轮结果追认为 W 的独立验收。

## 8. 验收收敛与停止点

依照本次用户明确要求，本卡使用**定向功能回归 + 小图库真实 CUDA smoke + 必要 D 边界注入**。不跑全量 pytest、不重跑 30 万曲线/多维度比较、不做大型质量评测；本轮也不改 PyInstaller 配置或宣称 exe 已支持 Qwen。该范围例外仅限本任务，已同步 V3.1 §9.2；不修改全项目最低验收规则。

目标小集合：12–30 张项目生成/已有合法公共 fixture，6–10 条固定中文查询、至少 2 次上传图片查询。同一集合分别建 CLIP/Qwen 索引，用真实 API和现有浏览器页面确认使用模型、known-target 发现情况、过滤/分页与覆盖；记录失败查询，不宣称 Qwen 一定优于 CLIP。图像自查询的自身相似度及排序为确定性检查，语义观察单独记为观察。

专项边界：同维错空间零写入；新指纹/旧 ID 兼容；错误 shape/NaN/零向量；缺权重/依赖/校验错；实际 CUDA 与 OOM 无 CPU 回退；候选 build 不抢活动指针；缺覆盖拒绝；切换前失败/提交后重启；日常增量跟随活动模型；新增后切回拒绝，补齐后成功；人工数据不变；未知模型高度匹配隐藏。

运行时只记录小 smoke 的加载、编码、API 总耗时及峰值显存，用于发现 OOM/卡死，不设新的 P50/P95 优化门槛。所有 pytest 使用项目 `.uv-cache`、`.pytest-tmp` 和 JUnit XML。

实现收敛后独立 **DS-H 全受影响面工程审查 → Sol high 聚焦终审**，因为涉及同维空间隔离、设备资格和 profile 切换。审查复核代码/环境对应证据，仅补跑缺口和直接反例，不机械重跑成功实验。DS-MAX 只按 V3.1 §2.2 六项证据触发。

停止点：两 provider 真正贯通建库与搜索、CLIP 兼容、Qwen 512D CUDA 小图库可用、切换/失败不变量通过、独立审查完成后收口。真实个人图库建库需另外明确来源授权；完整包发行和大规模质量/吞吐结论保持未验证。后续只按实际使用失败增加任务。
