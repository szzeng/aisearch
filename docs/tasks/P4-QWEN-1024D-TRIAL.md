# P4-QWEN-1024D-TRIAL — 基于 master 的 Qwen 1024D 真实图库试用接入

**状态**：IMPLEMENTED / ACCEPTANCE PARTIAL；Luna High 复核完成（2026-10-21）
**日期**：2026-10-21
**优先级**：P2，个人图库小范围功能扩展；非 0.5.0 发布阻断项
**推荐模型**：GPT-6 High；按 V3.1 §2.4 对身份/候选建库/切换受影响面进行独立审查
**核对基线**：`szzeng/aisearch` 的 `master @ 900028dd984f0f9ad74cbff2fb036e23e87d39b8`（2026-10-20）。执行前重新获取 master HEAD，确认工作区与已有未完成任务；不覆盖其他人的改动。

## 1. 唯一目标与验收边界

让用户在现有 PhotoSearch「图库管理 → 搜索模型」中，找到 **Qwen3-VL-Embedding-2B（1024D，EXIF v2）**，**共享当前 512D 的 Qwen 权重**，按现有按钮**开始候选建库 → 暂停/继续 → 检查 → 安排停机切换 → 重启后实际搜图**。不满意时，按相同安全门禁切回原 **Qwen 512D**。

这是**增加一个输出维度配置**，不是新增模型算法。完成的定义是：可发现、可建库、可切换、可查询、可回切，旧数据和默认行为完全不变；**真实图库建库与切换留给用户在 UI 中主动发起**。不自动读写真实图库的数据库、照片和向量；真实 CUDA smoke 如需沿用现有 `PhotoSearchData/models/qwen3_vl_embedding` 权重，只允许**只读加载模型文件**，不得写入其目录。不重新做 500 图/COCO 评估、不强制 30 万压测、不接入 WeMM、不修改默认模型或 0.5.0 发布逻辑。

## 2. master 已确认的实现事实与最小方案

已核对实际代码：

- `src/photo_search/models/embedding/qwen3_vl.py` 的 `Qwen3VLEmbeddingAdapter._encode()` 已按 Manifest 的 `embedding.dimension` 截取**原生 2048D 的前缀**并重新 L2 归一化，**不需新 Adapter 或重写推理**。
- `src/photo_search/models/embedding/base.py` 的 `_qwen_identity()` 把输出维度等配置纳入 `config_hash/space_id`；`processor_id` 由 `provider_id@revision` 组成，所以 1024D **必须使用不同 provider_id**。不要仅修改原 512D 的 `dimension`。
- `src/photo_search/models/embedding/registry.py` 从 `<data_root>/models/manifests` **优先**发现 Manifest，再使用仓库 `tools/prepare_models/manifests`；同一 `model_dir` 可以对应不同 Provider。`registry.default()` 明确返回原 `qwen3-vl-embedding-2b-exif-v2`，**不得修改**。
- `src/photo_search/models/embedding/management.py` 根据独立 processor/space 获取对应 Generation；`candidate_task.py`、`indexing/semantic.py`、`indexing/selection.py` 和 `search/profile.py` 已有候选分批建库、覆盖率核验和停机切换边界。原则上**直接复用**，不要为 1024D 另造数据库表、状态机或切换 API。
- `src/photo_search/web/app.js` 当前按名称 `EXIF v2` 将相应 Provider 放入「其他配置」；`src/photo_search/web/search.js` 和 `src/photo_search/search/perf_diagnostics.py` 有明确的 Provider 白名单/名称映射，需要补充新 ID。
- `src/photo_search/search/service.py` 的“高度匹配”阈值按完整 `space_id` 前缀区分；1024D 使用自己的显式前缀和 `1.5` 阈值（归一化向量 cosine ≥0.25），不通过 512D 前缀继承；该值仅影响显示计数。

### 唯一新增正式模型配置

复制 `tools/prepare_models/manifests/qwen3-vl-embedding-2b-exif-v2.json` 为：

`tools/prepare_models/manifests/qwen3-vl-embedding-2b-1024d-exif-v2.json`

仅改变：

| Manifest 字段 | 新值 |
|---|---|
| `provider.provider_id` | `qwen3-vl-embedding-2b-1024d-exif-v2` |
| `provider.display_name` | `Qwen3-VL-Embedding-2B (1024D, EXIF v2)` |
| `embedding.dimension` | `1024` |

保持 `provider.adapter=qwen3_vl`、`provider.model_dir=models/qwen3_vl_embedding`、原始 `2048D`、模型 `resolved_revision`、全部固定文件 SHA256、图像/文本指令、EXIF 处理、MRL、归一化、FP16/CUDA、预处理像素预算及 runtime 等其他字段与 **master 标准 512D 配置相同**。不复制、重下载、软链或第二次安装约 4.26GB 的权重；不要修改已安装的 512D Manifest。

**安装边界（必验）**：同目录共享权重并不自动证明文件正确。`registry.require_installed()` 主要检查存在性，真实 Worker 还有 hash/版本校验；`<data_root>/models/manifests` 若已经有同名 1024D 配置会覆盖 shipped 配置。如果用户本机安装的 Qwen 512D revision/hash 与 master 的标准清单不同，应**拒绝假装直接兼容**，记录差异并停止；不得覆盖旧 Manifest 或直接调用 `prepare-local --force`。标准安装且权重一致时，新增 shipped Manifest 就可发现，不要求 `prepare-local` 或复制权重。

## 3. 最小实现工作（按顺序）

1. **清单和身份**：新增以上 JSON。用独立隔离数据根测试 `registry_for()` 同时发现 512D/1024D；`installed` 状态真实；默认仍指向 512D。确认新旧 `provider_id`、`processor_id`、`space_id`、Generation ID 均不同；原 512D ID、权重 SHA、配置字节、活动 Profile 不变。配置来源优先级及损坏/不匹配 Manifest 的拒绝必须有回归测试。
2. **试用入口**：保留 `app.js` 原「开始建库/暂停/继续/检查/安排切换」功能；只做必要的展示调整，让 **Qwen 512D 与 Qwen 1024D 都容易找到**，1024D 明确标注“试用配置”。避免 EXIF v2 默认全折叠而用户找不到新增入口；不引入新页面或新按钮流程。列表不重复 Provider，活动状态来自真实 Profile，不按名字猜测。
3. **搜索状态和诊断**：在 `search.js` 的 `perfProviderName()` 和 `search/perf_diagnostics.py` 的 `SUPPORTED_PROVIDERS` 增加**新完整 ID**，显示 `Qwen 1024D EXIF v2`。检查上传以图搜图、按照片搜相似、文本搜索绑定活动 Generation 的 `space_id/dimension`，以及搜索会话/智能合集绑定与旧保存凭据的失效；不修改 RRF、Top5000、分页或语义召回逻辑。
4. **高度匹配计数**：为完整 `qwen3-vl-embedding-2b-1024d-exif-v2:` space 前缀设置独立阈值 `squared-L2 <= 1.5`（归一化向量 `cosine >= 0.25`）；搜索状态栏显示 API 返回的真实候选计数。未知/未配置的空间继续返回 `strong_match_available=false` 并显示“高度匹配：未标定”，不能伪造数量。该值不参与召回、排序或 RRF。
5. **候选与回切**：沿用 `candidate_task.py` 的 batch=100、GPU 资源门禁和持久状态，验证候选建库不激活、暂停/继续/重启恢复、故障保持旧 Profile、覆盖率检查、停机切换与切回。仅在证明共享目录/新 provider 引起真实兼容缺陷时修改这些共用模块，否则**不碰核心切换代码**。

## 4. 不可破坏的行为

- **绝对不改**原 512D/Chinese-CLIP 的历史 Manifest/ProcessorSpec/`processor_id`/`space_id`/Generation、模型默认值、向量表、Artifact、图片、人物、OCR、地点、收藏、备注、人工排除。
- 1024D 要重新对图库内目标照片生成向量；旧 512D 向量不能补零升级或改列宽复用。只共享**权重文件**，绝不复用旧向量、缓存或错误 Profile。
- 图文查询向量和入库图片向量均必须为 1024 个 finite、非零、L2≈1 的数；已激活模型与查询 Worker、Generation、向量索引的身份一致，不符合直接报错，不静默转向 512D。
- 新库默认仍是现有 Qwen512；试用者只在明确点击并通过现有安全门禁后切换。切换失败保留原 Profile；回切 512D 也必须重新核验其最新照片覆盖，不能保证新增照片后无条件切回。
- 旧搜索 Session、智能合集已保存规则、相似照片预览和搜索保存 token 按现有活动模型绑定规则过期/拒绝；不让 512D 的旧候选继续伪装成 1024D 的结果。
- 无需新增 migration、依赖、模型下载器、双模型同时常驻策略或 ANN；若真正需要超出本卡的协议改动，先出具体阻塞证据，不直接扩大范围。

## 5. 性能设计（三行，PERF-BOUNDED）

- **链路及风险**：1024D 与 512D 的模型前向推理相同，但输出、Artifact、LanceDB 存储约增加一倍；候选编码本身需要重新遍历目标图片，精确向量检索可能变慢；共享权重不意味着 512D 现有向量能转换。
- **限制与做法**：复用单模型驻留、批量边界（当前 `BATCH_LIMIT=100`）、搜索优先协调、现有 SearchProfile/Generation 和索引。数据量为约 5,000 张时 1024D 原始向量约 20.48MB（不含 Artifact、索引、元数据）；无需重新识别人脸/OCR，不引入新增全库状态统计查询。
- **验证与停止**：用隔离小图库做同机 512/1024D 编码及检索时间的简短观察（记录输入数、设备、阶段和实际数值）；如发现明显性能回退/OOM，则报告并保持默认 512D。**不要求** 30 万压测、不宣称正式 P95 或 GTX1660 适配。

## 6. 最少但必要的验证

按 `AGENTS.md`、`docs/testing.md`、`docs/performance.md`、V3.1 §2.4/§3.2/§3.4 执行。优先复用：`tests/unit/test_p4_embedding_provider.py`、`tests/integration/test_p4_embedding_provider_flow.py`、`tests/unit/test_embedding_management_status.py`、`tests/unit/test_embedding_management_coordination.py`、`tests/unit/test_p4_embedding_check_ux.py`、`tests/unit/test_embedding_image_orientation.py`、`tests/unit/test_search_perf_diagnostics.py`，以及实际受影响的搜索上传、Session、智能合集测试。**不以测试数量代替受影响边界覆盖**。

| 验证项 | 最低通过条件 |
|---|---|
| Manifest / Registry | 新旧共享 `model_dir` 但 identity 全隔离；既有 512D `default()` 与 IDs 不变；安装清单优先级、权重缺件/hash 失败诚实拒绝 |
| 真实 CUDA 编码 smoke | 仅只读引用现有模型权重（不扫描图库图片/数据库）、合成 JPEG 与一张 EXIF 方向图；Qwen 1024D 文本/图像 finite、L2、维度正确，原生前缀规则正确；不加载第二套权重、不自动下载 |
| 候选 Generation | 隔离库可分批建库与暂停/恢复；1024D 不自动激活，故障和未覆盖拒绝切换且原 Profile 不变；两个候选/已发布表彼此不覆盖 |
| 切换及回切 | 512 → 1024 → 512 的受控停机切换路径；重启后活动 Profile 的编码/搜索查询/上传图向量维度一致；旧 Session/智能合集的模型绑定正确失效 |
| UI | Codex 内置浏览器 `127.0.0.1`，在隔离数据根找到 1024D、能操作建库状态/检查/安排切换，并显示实际活动模型；高度匹配未标定时不出现虚假数字 |
| 最小质量/速度观察 | “猫”等 2～3 个查询可正常返回；记录小库 512/1024D 检索耗时和显存观察，不宣称谁更准或 30 万可用 |

测试结果保留**真实退出码、独立 JUnit XML、Ruff、JS 语法、`git diff --check`**；临时/原始数据保存在仓库忽略的 `.tmp/`、`.pytest-tmp/`、`test-results/`。如需改共享运行时、数据库/协议、依赖或广泛公共调用链，按仓库规则扩大回归和独立审查。核心 CUDA、身份、切换、浏览器项未验，不可写 `SCOPED PASS`；可如实报告 `ACCEPTANCE PARTIAL` 和未验证条件。

**独立复核**：因为发布了新的持久 Processor/Space/Profile 身份，并涉及切换闭环，按照 V3.1 §2.4 对全部直接受影响面作 DS-H 工程审查。只新增 Manifest/名称展示、未改共用运行时的情况下，不借此要求重跑无关全仓测试或正式发布认证。

## 7. 交付与用户实际试用步骤

仓库交付目标：一个新 Manifest、必要的模型展示/诊断白名单修正、直接回归测试、`docs/reports/P4-QWEN-1024D-TRIAL.md` **唯一简短报告**、`docs/development-status.md` 一段状态记录。无新 Adapter、DB migration、权重文件、依赖锁变更。结束前列出精确源码范围与受影响回归、未验证设备/规模边界。

**用户随后在真实图库手工操作（不由 Codex 自动执行）**：

1. 确认已正常使用 512D，必要时记录搜索“猫”的前 20/50 名截图作为观察对照；使用原数据根启动更新后的应用。
2. 在「图库管理 → 搜索模型」找到 `Qwen3-VL-Embedding-2B (1024D, EXIF v2)`，点击「开始建库」，按现有页面显示暂停/继续；建库过程会占用 GPU，切勿同时强行切换。
3. 完成后点击「检查」，检查成功才「安排切换」；正常退出并重新打开，确认活动模型与维度为 1024D。
4. 用相同关键词重搜比较。若不满意，对原 512D 执行独立「检查」与「安排切换」并重启；原 512D 向量必须仍然存在。

**停止条件**：做到“可在真实图库里自己点击体验”即停止，不擅自扫描、写入或修改个人图库（只读模型权重烟测除外）、不执行全量建库、不要为提升 AP 再开发 reranker/WeMM、不要自动提交/推送。若模型权重实际版本不一致、或必须修改冻结协议，报告精确事实并停止，而不是冒险强行兼容。

## 给本地 Codex 的执行提示词

> 基于最新 `master` 执行 `P4-QWEN-1024D-TRIAL`：为 Qwen3-VL-Embedding-2B 增加独立的 `1024D EXIF v2` Provider。只新增固定 Manifest（新 `provider_id=qwen3-vl-embedding-2b-1024d-exif-v2`、维度 1024），与原 512D 共享现有 `models/qwen3_vl_embedding` 权重，严禁覆盖原 512D、下载或复制模型。复用 Registry/Adapter/候选分批建库/检查/停机切换；只修正必要的 UI 可发现性、搜索诊断白名单和定向测试。新旧 Processor/Space/Generation/Profile 必须独立，默认继续 Qwen512；1024D 不沿用 512D 的高度匹配阈值。验证 Manifest 来源优先级和实际安装 revision/hash；隔离数据根完成 CUDA 1024D smoke、建库、切换回切、旧搜索会话/智能合集身份、真实内置浏览器关键操作。不得读写真实图库照片、数据库及索引；真实模型 smoke 只准只读使用现有权重，不做 500 图/30万新实验，不做 WeMM，不修改依赖或发布版号。依据 AGENTS.md 选择受影响回归和独立审查，交付简短报告与界面操作步骤。未获单独授权不得提交/推送。

## 8. 执行记录（2026-10-21）

- 已按本卡实现并从原 512D EXIF v2 Manifest 派生新清单；仅 `provider_id`、`display_name` 和 `embedding.dimension` 变化。Registry 共享同一模型目录，默认仍为原 512D EXIF v2。1024D 使用独立 Processor/Space/Profile/Generation；普通文本和上传图片搜索均绑定活动空间。
- UI 将两种 Qwen EXIF v2 配置放入无需展开的主要列表，1024D 显示“试用配置”；搜索性能诊断白名单和展示名称已补充，更新 app.js/search.js 缓存版本。
- 只读检查 `PhotoSearchData/models/qwen3_vl_embedding` 权重与标准 Manifest 五个固定文件均一致，revision=`9f2f7e710d6d81056aa5c0a4f04764fec6bb7bda`、SHA256/大小匹配。未访问数据库或真实图片。RTX 4060 Laptop `cuda:0` FP16 smoke 使用临时合成 JPEG 与中文文本，输出 1024D finite/L2≈1、重复文本 cosine=1；峰值 4.27 GB allocated / 4.29 GB reserved。2 图点积耗时不作为性能结论。
- `test-results/P4-QWEN-1024D-TRIAL-r7.xml`：受影响 unit/integration 及 profile/session/smart-collection 边界 134 passed、0 failed、0 skipped；Ruff、py_compile、两个 JS 语法检查与 diff-check 通过。首轮 Manifest 与 API 测试桩失败分别保留在 r1/r3/r4 XML，修复后由 r7 关闭。
- Codex 内置浏览器连接隔离合成数据根，页面确认 512D 和有“试用配置”标记的 1024D 均在主列表；该隔离根未安装模型，因此建库/检查按钮按预期禁用。未在真实图库启动候选建库或安排切换。功能状态为 **IMPLEMENTED / ACCEPTANCE PARTIAL**。
- 用户指定的 Luna High 独立复核发现 Registry 本地覆盖 blocker；修复后复核确认 blocker 已关闭，校验不存在绕过或误纳入其他字段的路径。无数据库迁移、依赖、默认模型或 0.5.0 发布逻辑改动；未提交、未推送。
- 复核发现同 ID 的有效 data-root 清单可以覆盖标准 revision/hash/dimension，已在 Registry 限制 1024D 本地覆盖只能更改展示名；错 revision、错权重 SHA、错维度均被记录为 skipped，并采用固定 shipped 清单。损坏 JSON 的 skipped/fallback 也有回归覆盖。复核修复后的受影响 JUnit `test-results/P4-QWEN-1024D-TRIAL-review-r2.xml` 为 168 passed、0 failed、0 skipped；Ruff、py_compile、JS 语法及 diff-check 通过。
- 补做 RTX 4060 Laptop 同机、单模型顺序驻留的 512D/1024D 编码观察（FP16、1 张 320x240 合成 JPEG、中文文本，各预热后 2 次）：512D text 36.0/67.7ms、image 109.2/93.3ms；1024D text 31.9/43.5ms、image 89.0/56.8ms。单样本短观察波动较大，不用于延迟/速度结论。2 张合成向量穷举点积仍约 0.0305/0.0991ms（512D/1024D）。原始记录 `.tmp/P4-QWEN-1024D-TRIAL/cuda-comparison.json`。
- 1024D 候选任务管理器专属暂停/继续/进程恢复尚未由新集成用例验证；共享状态/协调既有用例通过。真实 UI 建库状态机、重启后实搜、真实库切换继续留待用户在真实图库手工执行，故维持 ACCEPTANCE PARTIAL。
- 用户要求为 1024D 设置具体高度匹配阈值，已为其独立 space 前缀设置 `squared-L2 <= 1.5`，与现有 Qwen 512D 值相同但不走继承路由；该值约等于 cosine >=0.25。此值是按用户要求选定的初始显示阈值，尚未用真实图库相关性标签单独校准。计数仅对已检索候选应用，不改变检索/排序。阈值、API、状态栏最终 JUnit `test-results/P4-QWEN-1024D-TRIAL-threshold-r2.xml` 为 59 passed、0 failed、0 skipped；Ruff、JS 语法和 diff-check 通过。Luna High 独立复核未发现代码问题；页面版本为 `p4-qwen-1024d-trial-3`。
- 初始阈值记录：`squared-L2 <= 1.5`（cosine >=0.25），测试与 Luna High 复核结果见上次记录。根据用户“再宽松一点点”的反馈，当前 1024D 专属阈值改为 `squared-L2 <= 1.6`（归一化向量 cosine >=0.20）；512D 阈值保持 1.5 不变。仅已召回候选的显示计数受影响，不改检索/排序；真实图库相关性校准未执行。首次 r3 因旧断言漏更新而失败，修正后 r4 为 59 passed、0 failed、0 skipped；Ruff 与 diff-check 通过。Luna High 本轮有界复核通过，未发现代码问题。
- 按用户“再紧一点点”的反馈，1024D 专属阈值从 1.6（cosine >=0.20）收紧至 `squared-L2 <= 1.56`（归一化向量 cosine >=0.22）；512D 仍为 1.5。计数范围与排序行为不变。`test-results/P4-QWEN-1024D-TRIAL-threshold-r5.xml` 为 59 passed、0 failed、0 skipped，Ruff 与 diff-check 通过；Luna High 本轮有界复核通过，未发现代码或测试问题。
- 用户要求恢复最初试用值，当前 1024D 专属阈值回到 `squared-L2 <= 1.5`（归一化向量 cosine >=0.25）；与 512D 数值相同但仍由独立前缀常量配置。`test-results/P4-QWEN-1024D-TRIAL-threshold-r6.xml` 为 59 passed、0 failed、0 skipped；Ruff 与 diff-check 通过。此前 1.5 阈值已完成 Luna High 有界复核且无代码问题。
