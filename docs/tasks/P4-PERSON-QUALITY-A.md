# P4-PERSON-QUALITY-A — 现有人脸质量统计与人物整理页自动收起低质量候选

日期：2026-10-09。状态：**IMPLEMENTED / 定向合成验证通过；真实图库阈值试用待用户确认**。实际实施基线：`0959137e4916e2da3c8bacae0b09227f8715a7f10`。
个人相册 **一张卡**；只读展示与统计优化，**不重识别、不改模型参数、不迁移 Schema、不写图库事实**。

架构/策略对齐：V3.0 §8.3.1、§8.3.4、§10.2；V3.1 §2.4、§3.2。执行前按 `AGENTS.md`、`docs/development-status.md` 和本卡核对当前实施约束。

## 1. 用户目标和使用流程

用户现有图库中大量模糊、小脸、低可信度的已检测人脸占据「整理一下 → 未命名人物」和「还没认出是谁」，逐个处理成本高。

**更新程序后直接打开人物相册 → 整理一下，就默认采用清爽展示模式；不需用户先点诊断、运行脚本、等待一次“全库整理任务”或重新建库。** 清爽模式只是**每次服务端 SQL 查询现有 SQLite 时筛选展示资格**；没有预扫描、后台批量写回、重新聚类或修改人物身份。新照片完成现有识别流程后也自然按同一展示规则读取。

在「整理一下」提供一个简单开关 **「显示疑似低质量人脸」**（默认关闭）。打开即可找回自动收起的未命名人物及待整理脸；不需要先去「查看已忽略」。用户的**人工忽略/恢复**仍使用既有 `person_hidden` 和 `face_inbox_hidden`，不得混为同一概念。人物常规已命名页、搜索、照片详情及显式人物相册保持原语义。

## 2. 核对现状与规则定位

- `face/contract.py` 当前 detector score floor `0.50`；存储质量门限分别为 `LOW_DETECTION_SCORE=0.65`、`MIN_FACE_PIXELS=48`、`MIN_BLUR_VARIANCE=24`；`SUGGEST_THRESHOLD=0.29` / `MERGE_THRESHOLD=0.50`。底层识别仍会存储低质量检测记录与特征。
- `face/engine.py` 的 `quality_for()` 优先判断尺寸、清晰度、再判断 detector score；模糊方差来自对齐后的 **112×112 灰度脸**，不能将网上基于原图的 Laplacian 数值直接替换。
- `0010_face_core.sql` 已存储 `faces.det_score`、`face_pixels`、`blur_variance`、`quality_state`、`quality_reason`、`spec_revision`；无须模型重算。
- `persons/albums.py` 的 `people_page`、`inbox_page`、`inbox_group_page`、`inbox_group_faces`、`inbox_summary` 与 `_INBOX_WHERE` 使用不同的成员/待整理查询；其中 `MEMBER_PREDICATE` 表示**有效 CONFIRMED 成员**，`SUGGESTED` 不能被当成已确认成员。
- 现有已忽略人物、人脸、候选组与逐脸恢复已完成（P4-PERSON-HIDE-A / INBOX-HIDE-A / INBOX-HIDDEN-GROUP-A），不得重做或改变其事实。

## 3. 独立的整理展示质量策略

新建尽量小的只读 `persons/quality_policy.py`（或一个可复用的集中 SQL 表达式），禁止覆盖存储时的 `quality_state`、`quality_reason`，不得修改 `face/contract.py` 的冻结推理配置/哈希、`face/index.py` 的匹配门限、代表人脸资格或人员关系。

| 对照档位 | det_score 最低 | face_pixels 最低 | blur_variance 最低 |
| --- | ---: | ---: | ---: |
| 宽松（现存质量参考） | 0.65 | 48 | 24 |
| **均衡（第一版页面默认）** | **0.70** | **56** | **40** |
| 严格（仅统计模拟） | 0.75 | 64 | 60 |

这些是**展示试用值，不是论文证明的通用最佳标定**，三项均达标且存储 `quality_state='OK'` 才算机器自动候选的普通合格展示脸；任意一项不达标或现存 LOW 则标为“疑似低质量，仅整理页自动收起”。**不允许删除、拒绝新照片检测、修改归属或把它写入人工忽略表。** 先仅实现固定均衡档；宽松/严格用于统计，不增加复杂的运行时设置/数据库用户配置。

`face_pixels` 与 `blur_variance` 是处理时的指标：普通缩放检测和 source-detail 原图细节识别并非完全同一尺度。统计必须按 `spec_revision` / source-detail 分开，避免假装门限跨输入配置已经标定；旧版/异常缺项按**保守展示**处理并在诊断中列出，不硬判为垃圾脸。面部头像看起来模糊时，还可能是 preview→face thumbnail 裁剪/缩放的视觉效果，不应把缩略图清晰度当成已存的识别清晰度真值。

## 4. 服务端清爽视图：未命名人物和待整理脸

### A. 未命名人物

在 `GET /persons/browse` 追加可选 `quality_view=all|clean`；**省略仍默认 all**，保持旧调用者、人物选择弹窗、旧游标和 API 语义。前端仅在「整理一下」的未命名人物列表显式传 `clean`。

- 仅对尚未命名的、**机器自动有效成员**应用展示质量判定；一个未命名人物若有任何当前有效的合格机器成员脸，仍显示，**原相册全部照片及 asset_count 不变**。
- 若全体有效自动成员脸均不达标且无受保护人工关系，普通整理列表暂时收起整个分组；开关 `all` 后立即重新显示。不得物理删除该 person 或把其 `person_id` 放入 `person_hidden`。
- **任何有效人工确认 / MANUAL_ASSIGNED 成员或必要人工控制锚点不得被自动收起**；与 merge、undo、manual exclusions 的既有有效成员语义一致。已命名人物不做质量隐藏。
- 默认头像优先选达到展示质量规则的有效脸；用户已显式选定的封面应保持优先。只改该整理视图的封面投影，不覆盖 `persons.avatar_face_id`，不更改人物详情/其他页面。
- `visibility=visible|hidden|all` 原有语义保留；按候选、可见性、质量资格过滤后再全局照片数排序与分页，`total` 与列表同口径。新游标前缀绑定 `quality_view` 及固定规则版本；旧 `all` 游标沿用老协议。复用现有 `photos_desc` 的汇总计数，避免按人 N+1。

### B. 待整理收件箱

仅在日常 `all/suggested/pending` 收件箱的 `clean` 模式下自动收起**非人工控制的低质量人脸**。保留旧 API 默认 `quality_view=all`；页面显式选择 `clean`。

- `kind=quality`「小脸或清晰度不足」始终是**找回低质量脸的明确入口**：包括现有 `LOW` 及新增试用展示门限判为不合格但原为 `OK` 的脸；新原因需用独立只读标记投影，**不改** `organization_reason` 的历史业务含义。
- `kind=manual`「我移出的人脸」原样可见；人工锁、人工排除、原图补识别待确认及其它人工决定不能被自动掩盖或失去现有操作入口。
- `face_inbox_hidden` 一律优先，仍只在“已忽略人脸”页面恢复；**显示疑似低质量**不会让用户已忽略的脸混进普通收件箱。已忽略组/组内接口不加质量过滤。
- `_INBOX_WHERE` 的可见版本、原图细节替代、redetection OPEN hold、有效建议及排除保护不可破坏；按**全部合格数据先过滤 → 选唯一有效候选 → DISTINCT asset_id 计数分组 → 排序/分页**。过滤后不足两张不同照片时正常降为单脸，合照的其他脸不受影响。
- `inbox_page`、`inbox_group_page`、`inbox_group_faces`、`inbox_summary` 的规则一致；`quality` 入口独立使用完整未忽略候选池，不发生「明明显示筛掉 N 张却找不回来」。游标必须绑定展示模式，保留旧 all API 兼容。
- 现有待整理 summary 的 `REDETECTION_HOLD`、原原因统计不要被改写；新展示计数如另算，必须注明“当前可整理/质量收起/人工已忽略”三类口径互斥，不重复计数。

## 5. 最小前端交互

改 `web/persons-albums.js`、`persons.html` 和必要样式：

- 「整理一下」里显示一个非持久开关「显示疑似低质量人脸」，初始为关闭。关闭请求 `quality_view=clean`、打开请求 `all`；切页面后默认值按实现选择固定、不要把未知旧状态当成用户人工忽略。
- 人物列表与待整理卡共用**同一个页面质量模式**；用户显式选择 `kind=quality` 或 `kind=manual` 时，上述特定原因入口优先，不应出现空列表误导。
- 简单计数文案如「待整理 N 张脸，另有 M 张疑似低质量脸暂时收起」。N/M 必须来自权威 SQL、区分人脸张数与展示卡片数；统计不要在每次追加30卡时反复全库计算，允许初次进入/主动刷新时按需读取。
- 切换模式/原因时清理对应分页 cursor 并重读；禁止旧响应竞态覆盖新模式。已有批量管理选择如有跨视图不可见项，明确清理该模式选择并提示，**不要偷偷对隐藏于当前视图的旧 face_id 执行批量忽略或恢复**。
- 保留当前「整理更多」「候选组展开」「再看一些」「已忽略脸恢复」、点击原照片预览等流程；不自动折叠用户正在勾选的列表。

## 6. 只读三档统计（按需运行，绝不作为开启页面的前置任务）

最小工具建议为 `tools/diagnostics/person_face_quality.py`。读 SQLite 既有数据并按三档模拟，不调用模型/GPU、不读取原图/导出缩略图、不写任何事实。允许用户将来**明确手动运行**在自己的 `PhotoSearchData`；本卡实现/测试阶段禁止自动访问实际图库。

至少输出：当前**有效可见**人脸数；原 OK/LOW 人数及 SMALL/BLURRY/LOW_DETECTION_SCORE 分布；三档收起脸数及重叠原因、**完整收起的未命名人物分组数**、至少保留一张清楚脸的分组数、人工保护数量；三个原始质量指标 P10/P25/P50/P75，普通/原图细节与历史 spec 分开。可测每档假设影响，但不得提前宣称固定过滤百分比，也不把分位数作为自动波动阈值。

过滤统计按现行有效投影（`_VISIBLE`、有效成员、合法待整理、hold/hidden 等）去重；不能把重检测前后同一张照片里的旧/新脸当作独立当前待整理脸；统计总量、组数、原始脸数含义清晰。使用聚合查询、按需一次完成，不扫描大量图片或向量，不让仪表统计成为每次人物页加载的额外负担。

## 7. 严格边界及变更文件

主要：
- `src/photo_search/persons/albums.py`
- `src/photo_search/api/routes.py`
- `src/photo_search/persons/quality_policy.py`（如需要）
- `src/photo_search/web/persons-albums.js`、`persons.html`、必要的 CSS
- `tools/diagnostics/person_face_quality.py`
- 原有 `tests/integration/test_p4_person_albums.py` 与少量 Node DOM / 质量策略测试

不要改 `face/contract.py`、`face/engine.py`、`face/index.py`、旧迁移、人物归属表、人工锁/撤销协议、数据库 Schema、原人脸向量或图库照片。也不提高底层 detector floor；不能把本卡误写成“降低建库时的 GPU/CPU 识别成本”。

如实现时发现为达目标必须跨入人工事实写入、推理生成版本或大规模 Schema 变更，**停止扩张本卡并反馈**，不得顺手做额外功能。

## 8. 个人范围极简验收

仅三组定向自动测试，合成 SQLite/少量 DOM：

1. **质量/诊断：** 当前 OK 但低于新门限、当前 LOW、三个边界值、缺项安全回退、普通与原图细节版本的独立统计、三档分布与交集、旧替代检测不重复计数；不写数据库。
2. **后端列表：** 未命名分组仅含模糊自动脸/含一张清楚脸/人工确认、已命名和忽略状态、人物照片总数与封面；待整理候选跨图分组、同图多脸、kind=quality/manual、hidden/hold、全局排序和跨游标翻页；旧省略 `quality_view` 行为与原游标不变。
3. **前端 DOM：** 默认清爽、勾选显示全部、切原因找回低质量人脸、统计口径、跨页批量选择清理及竞态保护、已忽略逐脸恢复无影响。

运行三组定向 pytest（JUnit XML）、修改面 Ruff、`node --check`、`git diff --check`。不跑全仓/30万/GPU/模型/正式打包/浏览器矩阵；不访问真实图库/启动现有服务。最后由用户手工用少量已知清楚脸、明显模糊脸、合照小脸和人工指定人物检查实际表现，不能将合成 PASS 冒充实际阈值已标定。

## 9. 模型与 PASS 判据

**推荐 GPT-6 High** 实施及三组定向验证，同会话对直接改动范围做简短复核；仅在触及身份/人工状态/Schema/冻结协议时按 V3.1 升级审核，不默认全仓主审。

**PASS**：现有库无需额外建库便默认以 `clean` 显示整理页；大量低质量自动候选不再占据普通整理列表，`all` 与 `quality` 可找回；已命名、人工确认/忽略/纠错、分组分页、照片集合及原 API 均保真。交付改动清单、三组小测试、三档诊断示例及未验的真实图库质量效果；更新任务卡、执行报告与 `docs/development-status.md`。


## 10. 实施交接（2026-10-09）

实现报告：[P4-PERSON-QUALITY-A-implementation.md](../reports/P4-PERSON-QUALITY-A-implementation.md)。实现文件为 `persons/quality_policy.py`、`persons/albums.py`、`api/routes.py`、`web/persons-albums.js`、`web/persons.html` 与只读工具 `tools/diagnostics/person_face_quality.py`；定向测试新增质量诊断/API与DOM用例，并更新受影响的忽略 UI 请求断言。JUnit 20/0/0/0；Ruff、Node、py_compile、diff-check 通过。

真实图库阈值效果与正常应用现场仍待用户少量试用确认。未访问 PhotoSearchData、未运行诊断 CLI 对真实库、未执行浏览器矩阵/全仓/容量/GPU/打包、未迁移 Schema、未提交或推送。当前进入个人试用，无下一张工程任务卡；发现具体误收/漏收样本后再按实际反馈开卡。
