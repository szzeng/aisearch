# P4-WEMM-QUALITY-PILOT — WeMM 2B 512D 本地适配可行性与真实图库检索对照

- **状态**：已按用户指示暂停（2026-10-22）。A 阶段合成 GPU smoke 已完成；B 真实照片对照未开始，C 未启动。恢复工作等待用户后续指示。
- **优先级**：P2，探索性质量与部署验证；不纳入当前 v0.5.0 发布阻断项。
- **推荐执行模型**：GPT-6 High，在本地 Codex 单个独立会话完成；使用者手动选择模型。
- **设计核对基线**：`szzeng/aisearch`，`master @ 5bf53eb5377e62a1d3c2bbc075080b102a0861a9`。这不是实施时的冻结 HEAD；开工先核对最新 HEAD、未提交工作区、当前进行中的任务和本地数据根，**不得覆盖其他工作**。
- **授权范围**：用户已明确允许本地 Codex 使用本人真实 `PhotoSearchData` 做模型检索分析；**仅可读取数据库及原图、读取现有模型/索引**。不得写入、迁移、重建、清理、重命名、移动、删除或批量修改真实图库及其索引；输出仅限项目内忽略的 `.tmp/` / `test-results/`。本卡不授权上传个人图片、搜索词、路径或索引，不授权改活动模型，也不授权自动下载多 GB 权重。
- **后续关系**：本卡只产出是否值得正式接入的可复现实验结论。通过“模型可运行 + 质量证据足够 + 性能可接受”后，才由用户单独授权 `P4-WEMM-PROVIDER`；**不自动执行后续卡**。

## 1. 背景、目标与不可误认的事实

用户在真实图库搜索“猫”时看到：前排猫照片准确，后续掺杂其他照片，仍有明显猫照片排名偏后。当前大量搜索卡片显示“图文向量命中”，在纯向量命中之间基本保留原始向量相对顺序。需要判断：换用 `tencent/WeMM-Embedding-2B` 的 **512D** 能否改善「不相关内容插队、真正相关照片偏后」问题，而不仅是把高度匹配阈值调严。

当前产品有可插拔 Embedding 注册表、工厂、统一 Query/Worker 入口、独立 Generation/Profile、候选分批建库和停机切换。但目前工厂实际只实现 Chinese-CLIP 与 Qwen；本卡**不得将 WeMM 的官方脚本运行成功冒充已接入产品**。

真实已有证据：仓库 `docs/reports/P4-SEARCH-QUALITY.md` 中 COCO-CN 小图库 Qwen512 / Qwen2048 的 Top-1 分别为 72.19% / 74.61%，**不是**真实图库中“猫”的准确率。`docs/reports/P4-QWEN-SPEED.md` 的 30 万规模 2048D 检索延迟是历史合成估算，**不是**本机现时真实 HTTP 延迟。腾讯 MMEB 分数不是双方固定为 512D、也不是该图库“猫”搜索的真实 Precision。

本卡完成后，应能回答：
1. WeMM 2B 的官方 512D 文本/图像 Embedding 能否在本地 GPU 正确运行？RTX 4060 8GB 的峰值 VRAM/首轮和预热耗时如何？（GTX 1660 6GB 为**可选、单独判定**的设备测试。）
2. 同一批真实照片、同一组查询条件下，Qwen512、Qwen2048、WeMM512 的排名具体怎样变化？尤其“猫”的前 20/50 和中后段。
3. 哪些结果是模型之间的明显分歧，哪些真实猫仍然漏掉？能否得出有充分证据的换模型决定？
4. 若可用，正式接入将面临何种 Transformers/自定义代码、GPU 显存及产品边界风险？

## 2. 必读与资料固定

开工依次读 `AGENTS.md` → `docs/development-status.md` → 本卡；仅按需看 `docs/tasks/TASK-TEMPLATE.md`、`docs/performance.md`、`docs/testing.md`、`docs/plans/P4-EMBEDDING-ARCH.md`，`src/photo_search/models/embedding/{base,factory,registry,qwen3_vl}.py`、`src/photo_search/workers/{ai_worker,query_encoder}.py`、`tools/evaluation/{public_search_quality,peer_search_quality}.py`、`docs/reports/{P4-SEARCH-QUALITY,P4-QWEN-SPEED}.md`、相关设备和照片解码代码。

官方参考（实施前记录具体 commit / revision，**不可直接取可变 main 当作结果身份**）：
- https://github.com/Tencent/WeMM-Embedding — README、`examples/transformers_inference.py`、requirements。
- https://huggingface.co/tencent/WeMM-Embedding-2B — 模型权重、许可证、remote code 文件和版本。
- 官方当前参考要求：`transformers==5.2.0`，`AutoProcessor` / `AutoModel`、`model.embedding(**inputs)`，模型内部专用 `<embedding>` Token，输出原生 2048D；512D 为前缀截取**再 L2 归一化**。官方示例使用 BF16；FP16 与特定 GPU 是否可用必须单独实测，不得假设。
- 不复用 Qwen 当前的 last-token pooling、默认 instruction、chat-template add-generation-prompt 或像素预算来“模拟” WeMM；保持各自官方输入规范，并在报告中列明差异。

## 3. 边界：这次允许修改与绝对禁止

**允许**：
- 在仓库 `tools/evaluation/` 中新增仅供离线试验的有界脚本（建议 `wemm_quality_pilot.py`），必要的纯函数测试在 `tests/unit/`；本卡无需把生产 Adapter 注册入厂。
- 使用仓库内部 `.tmp/wemm-pilot/` 创建独立 Python 3.12.10 + `uv` 实验环境、官方模型只读缓存、本地中间向量和导出 HTML；环境变量 `TEMP`/`TMP`/`UV_CACHE_DIR`/`HF_HOME` 指向仓库内部隔离路径。
- 对真实图库进行**显式只读** SQL、元数据读取、原图只读解码；对选取的有界小样本重新编码并写入隔离输出，不写入正式 LanceDB。
- 若已安装模型缺失，先报告版本、许可、大小、预计磁盘与显存需求；**取得用户新的下载授权后**才把固定版本下载到项目本地忽略目录。不得在应用启动或测试脚本执行时静默联机下载。

**禁止**：
- 改 `pyproject.toml`、`uv.lock`、`.venv` 里的既有生产依赖，或修改现有 Qwen/Chinese-CLIP 模型清单及权重。
- 调用真实数据根的 `Catalog.open()`、`build_index()`、`catch_up()`、`prepare-local`、`select-profile`、写入型任务/刷新/SQLite PRAGMA、向量表 `create/append/update` 等有副作用入口；不得调用可能隐式迁移/排队/补建的 API 代替只读 SQL。
- 创建或覆盖生产 `catalog.db`、WAL、原图、缩略图、`vectors/`、`models/manifests/`、搜索 Profile、人工状态、智能合集缓存；也不新建数据库迁移。
- 把任何私人照片、绝对原图路径、模型生成的包含隐私文本上传、提交进 Git、放到远端 issue/PR；原始 HTML 联系表仅离线本地查看。
- 未验证即发布“准确率提高 N%”“GTX1660 可用”“300k 搜索达标”“模型正式支持”等结论。

**强制只读实现**：SQLite 使用仓库 `catalog.sqlite_io.open_readonly` 或明确 `mode=ro` 的只读连接，必要时 `PRAGMA query_only=ON`；确保 WAL 有效快照，不能错误地对带 WAL 的活跃 DB 使用 `immutable=1`。LanceDB 只能使用确认无写副作用的读取接口。若读路径会隐式修改真实文件，或其他 writer 并发导致快照不一致，则**停止并记录**，改用事先授权的离线一致性快照，不得擅自冻结/移动真实库。SQL 优先批量取有界 asset_id，禁止 N+1 查询/全库向量拷贝。

## 4. 实施步骤：先小后大，逐阶段停止

### A. 本地与官方模型预检查（未确认前不进行大下载）

1. 检查 `git status --short`、本机 GPU 型号/实际 VRAM、CUDA、项目 Python 版本、可用磁盘空间；从活动 Profile/Generation 判断当前 Qwen512 是否真正在用，记录 `processor_id`、`space_id`、`generation_id`、模型文件版本和向量覆盖。不得依赖“目录里有模型”推断当前绑定。
2. 固定官方代码和权重的真实 revision、文件 SHA256、版权/许可证与需要执行的 remote code；先审查自定义 Python 文件及依赖行为，仅在隔离本地可信版本下使用 `trust_remote_code=True`。不依赖运行中的 `main`。
3. 优先从项目内已有合法权重缓存复用；没有时请求下载授权。**最小实验环境**先用 `transformers==5.2.0`、官方所需辅助库和经过核对的 GPU Torch，主 `.venv` 完全不变。不要默认安装 SentenceTransformers/vLLM/SGLang，除非官方最小路径确有依赖证据。
4. 验证官方样例可以对 1 段中文、1 张本地 JPEG 编码成 2048D；512D 为同次输出前缀 + normalize，输出是 finite float32、形状正确、L2 norm≈1；重复相同输入排序/向量相似性稳定，图像 EXIF 方向不反复转置。核对本机实际 device/dtype 和峰值分配，禁止悄悄 CPU/offload 代替 GPU。
5. 在首选 RTX4060 8GB 上 BF16 失败/OOM 时，**只允许在实验中明确改为 FP16 重测**，每一配置分开报告；不能更改官方预处理来硬凑显存。GTX1660 的 FP16 路径是后续可选试验，不得承诺。加载失败、remote code 风险不明、空目录、权重不全或多次 OOM，记录 BLOCKED 并停止后续大样本试验。

### B. 对真实图库做 30～50 张有界烟测

1. 从已存在、有效且可只读解码的照片里固定 seed 选取不同尺寸/横竖/EXIF 方向的 30～50 张，其中必须包含“猫”、非猫、容易混淆物；有 HEIC/CR2 可加入少量**能被当前项目正常解码**的样本，但不能为了凑数而启动写入型媒体生成。离线/缺文件/解码失败保留计数和原因，不静默跳过改变样本定义。
2. 用完全相同的 asset_id 与真实图片字节分别编码：Qwen 从完整 2048D 输出衍生 Qwen2048/Qwen512；WeMM 输出 WeMM512。双方都保持原生合法的图像预处理/文字模板，统一最终 L2-normalize、余弦分数和排名稳定平局规则；模型之间**禁止比较绝对 cosine 值或复用阈值**。
3. 如果重算 Qwen512 与生产缓存向量有可读且可核对的对应值，抽样比较二者并解释差异（版本、EXIF、解码输入、模型配置、浮点）；差异未经解释，不得称实验等价于当前生产结果。
4. 8～10 个短查询烟测（含“猫”），输出每模型 Top10 与耗时。确保文本向量来源可追溯、维度/space 不混淆；不访问生产 SearchProfile 来发布搜索结果。

### C. 真实图库 500～1000 张冻结样本的公平对照

只在 A/B 成功且资源可接受后进行；数量可根据显存、运行耗时在开始前缩至 300～500，但不能缩后仍宣传为 1000 张结论。

1. **冻结样本选择方法并记录覆盖偏差**：从全量可用且支持只读解码的照片做确定性随机抽样（至少占样本约一半）；其余从现有 Qwen “猫”及其他查询的高名次/中名次/后名次和明显混淆类别有界选取，固定 seed、每个来源与抽样权重，SHA256 去重。只按 Qwen TopK 抽会偏向 Qwen，必须保留随机样本及独立分层报告。未编码/离线/失败不得默默以别的图片替代；报告覆盖和缺失率。
2. **查询集先冻结，随后计算**：例如“猫”“两只猫”“狗”“毛绒玩具”“红色汽车”“海边日落”“两个人吃饭”“合照里有猫”等，共 12～20 组，至少含单物体/颜色/数量/动作/关系、视觉混淆、用户实际反馈词。可加明确无匹配的负查询，但在**人工或独立证据证明图库无匹配前**只能称“预期无匹配”，不能作为真实负例测量 FPR。
3. 三方案：**Qwen512（优先同一次 full Qwen 前缀）、Qwen2048、WeMM512**。每个模型/维度的 query 与 image 来自同一编码规格，分别 L2 归一化；只对同一固定资产全集打分、确定性 `(similarity desc, asset_id asc)` 排序；不混入 FTS、RRF、OCR、EXIF 时间排序，不套当前“高度匹配”阈值。独立保留「生产 Qwen512 真正 Top50」作为辅助诊断，明确小样本排名≠全库排名。
4. 同一图只做每模型必要的一次图像编码；同一 query 只编码一次；中间向量缓存在本地独立目录（含模型/权重/前处理/维度/样本 sha 身份），中断可续，复跑身份不一致必须拒绝旧缓存。GPU 串行仅驻留一套大模型，禁止并驻冲突与无界大 batch。
5. 导出本地 HTML：按查询并排展示三方案 Top20/Top50、同图名次升降、按模型分歧挑出的“猫中后排/非猫高排”照片，以及缺失、样本偏差与支持范围。图片通过受控本地静态预览或仅工作区内临时缩略图读取，限制数量和大小；**不能把用户原图内嵌到共享报告、数据 URL 或提交文件**；展示不可用时仍输出脱敏 asset 映射到本地私有文件。

### D. 评价方法：自动优先，但不能伪造真值

- 无人工标签：输出各模型 TopK 重合度、排名变动、得分分布、Query 速度、GPU 峰值，以及易混淆案例的本地可视化；质量结论必须写 **UNVERIFIED / RELATIVE RANKING ONLY**，不能计算冒充 `Precision@20` 的数字。独立 VLM 自动判“有无猫”可作为**弱标签**，单列模型/模板/错误局限，不得作为独立真实精度结论，不能用同一个 Embedding 相似度自证。
- 若用户愿意做**可选**的少量现场判定（不是执行前置条件）：先在冻结样本中对 6～10 条有代表性的查询各抽双方/三方的 Top20 及关键排名分歧做去重、盲法人工标记；允许 `相关/不相关/不确定`，统计只在被判定的共同候选集合上成立的精度、覆盖及假阳性。对 Top50 precision 只有该 query Top50 逐张被标注时才可称为该 query 的 Precision@50；未穷尽全集标签不能宣称 recall。
- 能用公开 COCO-CN 配对标注实验做辅助强证据，但它评价的是“已知配对图的排名”，不能代替个人图库中所有相关猫照片的全量标签。
- 所有对比必须声明真实图库照片数、测试有效资产数、缺失、query 组别、运行设备、模型 revision/精度、抽样方案、时间以及统计的适用范围；同一测试集调参/反复挑最好结果必须标探索，不称独立留出验证。

### E. 真实性能与决策

至少记录模型启动冷/热、文本编码、单图/批次编码、GPU peak allocated/reserved、RAM 峰值与离线矩阵相似度计算耗时；同设备串行执行，记录有效样本数/失败/计时范围，不要求对本任务做全量 30 万 P95 或启动应用的正式 HTTP 认证。

**事前判定原则（不以跑出的数字倒填门槛）：**
1. 不能稳定加载、GPU OOM 或出现未解决的向量身份/预处理差异 → **BLOCKED**，不推进生产支持。
2. 能运行但质量只有无标签对照 → **PILOT COMPLETE / QUALITY UNVERIFIED**；可推荐进一步验证，**不得凭排名变化自行决定替换 Qwen**。
3. 有可信的同集标签且 WeMM 的 Top20/Top50 相关率明显改善，同时显存、编码速度可被当前个人环境接受 → **PROVIDER CANDIDATE**；也保留其退步查询及限制，不宣称默认切换。
4. 性能明显退化、没有质量优势、对真实常见查询没有改善 → **NO-GO**；本任务结束，不继续正式接入或全库重建。

## 5. 性能设计（PERF-BOUNDED）

- **链路/风险**：真实文件读取→小样本解码→独立 GPU 编码→离线向量检索→本地报告。风险在反复全库查询、原图 N 次解码、重复编码、模型并驻、长时间锁库和超显存。
- **做法/上限**：30～50 smoke，再最多 1000 张固定样本；每模型每图一次、每 query 一次；每轮一套 GPU 模型；只读 SQL 批量化；输出本地忽略目录；运行过程中若 OOM/处理失败率异常则停止并如实上报，**不隐式扩大到全库**。
- **验证/停止**：测试样本身份证据 + 同输入确定性排名 + 资源峰值 + 错误矩阵 + 原数据只读验证。历史合成 300k 和 COCO-CN 仅做参考。本卡不承诺生产 P50/P95 或新硬件最低规格。

## 6. 必要测试与验收矩阵

| 场景 | 可接受证据 | 不能冒充的结论 |
| --- | --- | --- |
| 实际模型加载 | 固定 revision、本地文件校验、真实 `model.embedding` 推理、实际设备/精度/显存 | 导入类成功 ≠ GPU 可用 |
| 向量正确性 | Qwen512/2048 与 WeMM512 各自维度、有限、归一化、同模型查询+图片空间一致 | 两模型同为 512 ≠ 相同空间 |
| 原图方向及格式 | 横/竖、EXIF；有条件加入 HEIC/CR2；失败原因可见 | 缩略图正常 ≠ 正式原图向量正确 |
| 只读边界 | 写操作禁止；前后核对状态、主索引代数与活动 Profile；禁用任何模型/图库写 API | `sqlite3.connect()` 默认不是只读 |
| 数据集公平 | 随机 + 问题样本分层、同一固定样本、query 先冻结、无泄漏 | Qwen TopK-only 不代表全图库 |
| 排名结果 | Top20/50 HTML、本地快照、模型之间分歧样本、负例不确定性 | cosine 不同模型间不可直比 |
| 性能 | 峰值 VRAM、冷/热、编码耗时、OOM/CPU 回退拒绝、设备信息 | 小样本不代表 30 万检索 |
| 无标注情形 | 标明仅排名观察、不给精度承诺 | “自动认为猫”不是真实标注 |
| 异常/恢复 | 缺模型、版本冲突、OOM、读取失败、断点重跑及输入身份变化拒绝 | 失败不能静默跳过或降级 |

**建议单测**：对抽样确定性、结果并集去重、Qwen 512 前缀截断再归一化、L2/数值验证、相似度排序平局、混用模型空间拒绝、无标注不得输出 precision、读路径安全和缓存身份进行有界单测。测试 fixture 全为合成数据；针对真实 GPU 的 smoke 必须独立记录，不能以 mock 代替。执行 Ruff、相关 pytest 与 `git diff --check`；不改变生产依赖、schema/协议/服务代码时**不要求全仓测试**。

**执行入口**：新增脚本 CLI 至少提供 `preflight`、`smoke`、`compare`、`report` 四种子命令，并有 `--help`、`--data-root`、`--out-dir`、`--max-assets`、`--seed`、`--device`、`--qwen-model-dir`、`--wemm-model-dir` 参数；设备和模型权重必须明确绑定，缺少目录则清楚拒绝。Codex 可按实际接口给出最终、可复制的 PowerShell 命令，不允许在卡里杜撰已存在的 CLI。

## 7. 交付物、审查、结束规则

- **唯一报告**：`docs/reports/P4-WEMM-QUALITY-PILOT.md`（仅存结论、身份摘要、可重现命令、依据与未验项）。
- **原始数据**：`test-results/p4-wemm-quality-pilot/` 与 `.tmp/wemm-pilot/`，不跟踪、不推送。含脱敏摘要 JSON、样本 hash/抽样身份证据、独立排名、计时、GPU/库版本、日志、HTML 本地对照及单测 JUnit。
- **状态摘要**：若实施，按 `AGENTS.md` 在 `docs/development-status.md` 增加简短结论和报告链接，不篡改现有任务的状态；已有冲突先检查。
- **独立审查**：按照 V3.1 §2.4 对这次测量工具、真实数据只读边界、数据抽样及指标诚实性进行**有界独立复核**；发现生产共享代码、用户状态、索引或依赖变更则扩大受影响面审查。不能把主执行者的自检称独审。
- **PASS 范围**：只能写“实验代码与真实测试证据完成”；是否值得进一步支持单独写 GO / NO-GO / INCONCLUSIVE。若缺权重/下载授权/可用 GPU，任务状态 `BLOCKED`，不是“试验通过”。
- **提交限制**：本轮 ChatGPT 仅设计任务卡。以后 Codex 执行时，也不得擅自 `git commit`、`git push`、创建 PR/Tag/Release；必须获得用户明确授权。不要删除/覆盖其他人未提交的工作。

## 8. 给 Codex 的直接任务提示词

> 执行 `P4-WEMM-QUALITY-PILOT`。先按 `AGENTS.md → docs/development-status.md → 本任务卡` 核对最新 HEAD 和工作区，检查真实 Qwen Profile/Generation、WeMM 权重存在性及硬件，并按官方固定 revision 审查运行代码。不要修改任何生产依赖、生产数据库/原图/向量索引或活动模型；本机已授权的数据根只读，所有输出放项目内部忽略目录。先做 30～50 张 512D GPU smoke，成功再做最多 1000 张同集 Qwen512/Qwen2048/WeMM512 对照，生成脱敏 JSON 与离线 HTML，明确有无标签时能得出的不同结论。缺权重先给我授权需求，不自动大下载；OOM、模型身份不一致或写副作用立即停机报告。完成定向测试和有界复核，只交付试验结论、所用命令、资源与质量证据；不接正式 Provider，不提交或推送。

## 9. 本轮执行补充（2026-10-20）

**状态：A SYNTHETIC GPU SMOKE PASS / B PENDING INDEPENDENT REVIEW AND COORDINATOR RELEASE。** 用户已授权固定 revision 权重下载和独立实验环境；模型文件大小与完整 SHA-256 已匹配。首次 GPU 资源复核见 3,351 MiB free，低于权重本身约 5,189 MiB，故未尝试模型加载或停止任何进程。用户正常退出 PhotoSearch 后，最新资源复核见 7,877 MiB free；随后完成 BF16 合成输入 A smoke。本轮实现基线为 `900028dd984f0f9ad74cbff2fb036e23e87d39b8`（已复核）；开工时工作树仅有其他任务遗留的未跟踪 `.pytest-tmp-*` 目录，均保留。 用户明确授权的本地真实图库有界只读预检已执行。活动 Profile 绑定 Qwen EXIF v2 512D READY generation；WeMM 固定权重未在活动数据根预期模型目录发现。用户已授权下载至项目忽略目录；固定 revision 的9个小文件与tokenizer已落地，tokenizer SHA匹配，5,441,695,216-byte权重已从官方HTTP端点下载，SHA-256匹配远端LFS OID。独立 Python 3.12.10 venv 已安装 Torch 2.8.0+cu128、Torchvision 0.23.0+cu128、Transformers 5.2.0 和 qwen-vl-utils 0.0.14，CPU侧导入路径已确认在venv；初始化前CUDA未初始化。用户先通知 GPU 空闲，但资源复核仅有3,351 MiB free，低于权重文件；后续用户正常退出 PhotoSearch，重新复核7,877 MiB free后完成BF16合成输入A smoke。没有读取真实照片或进行B/C编码，也未启动Provider。

**本次性能段（PERF-BOUNDED）**：完成有界 catalog 身份快照、模型文件与独立环境准备、CPU tokenizer/预处理核验及一次限定为中文文本和一张合成 JPEG 的 BF16 GPU smoke。没有复制全库向量或读取真实图片。R3 记录 forward-only 与 H2D+forward+D2H，显存峰值及 RAM 指标；小型合成输入不作为真实图库延迟或质量证明。B 的真实照片 smoke 仍待独立审查与协调者放行。

独立 Luna High 对本阶段完整改动面、只读/WAL 边界、模型文件身份、证据口径与安全输出完成有界复核，无剩余 blocker；由于未改产品、schema、依赖或共享执行路径，不触发范围升级。本复核不代表模型运行通过；本轮用户随后授权权重和独立环境准备。

### 授权后准备门禁补充（2026-10-20）

固定 revision tokenizer 已下载并通过远端 LFS SHA-256 核验；官方权重已完整下载并核对字节数及SHA-256。独立环境通过CPU导入隔离验证；用户正常退出PhotoSearch后释放GPU资源并通过模型驻留复核。合成 256×192 图的本地官方 processor/qwen-vl-utils 路径输出 patch16 网格 `image_grid_thw=[[1,14,20]]`，默认 chat-template 文本虽以 `<|im_end|>` 结束，但本地 tokenizer 的 TemplateProcessing 会在最终 token IDs 自动追加 `<embedding>`（id 248077）。文本与合成图输入的最后有效 token 均已核实是该专用 token且未重复；不手动追加，也不改用 vLLM embedding template。


GPU 资源门禁历史（2026-10-20）：首次 cuda:0 RTX 4060 Laptop GPU 复核为8,188 MiB总、3,351 MiB free、driver617.42，低于模型权重，停止且未结束进程。用户正常退出 photo_search 服务后重新复核为7,877 MiB free。

GPU通知后的脱敏进程复查显示早先photo_search模块及其multiprocessing.spawn子进程已退出。没有发送HTTP写请求或结束进程；复核资源后才执行A smoke。


A 阶段 R3 严格 image-only smoke：本地合成 JPEG 256×192、1,398 bytes、SHA-256 `a3c5a8a5601f6e1a1a1d461e50cc87f7fabe52b3e86900ca5f45c69169a5930b`。中文文本和 image-only 输入均三次 forward；raw 输出 BF16/cuda:0、2048D finite，末有效 token 248077；512D 前缀 fp32 L2 norm=1，重复输出最大差0。image-only 首次峰值 allocated/reserved 5,491,527,168 / 5,607,784,448 bytes。原始 JSON 位于忽略目录 `.tmp/wemm-pilot/smoke-a-gpu-bf16-r3-image-only.json`，命令 exit 0。R2 的图像输入带额外文本，只作为 image+text runtime smoke，未作 image-only 证据。A 仅证明合成运行性，不代表质量通过。R3 输入与身份字段等待独立复核；获协调者放行后才可开始 B。
