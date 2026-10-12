# Development status

## P4-PERSON-INBOX-COMPARE-A — TODO / 待整理人脸与候选头像并排对照（仅任务卡）

[任务卡](tasks/P4-PERSON-INBOX-COMPARE-A.md)。待整理候选分组摘要与展开逐脸卡并排显示待确认脸/正确候选人物现有头像；组键 person_id 精确对齐、无头像安全退化，不改自动匹配、人工归属、分页或批量管理。原则上只改 `persons-albums.js`、`persons.css`，确有需要才追加有界只读头像投影。推荐 **GPT-6 / Medium**，最少定向 JUnit、Node 语法与 diff 检查 + 用户手测；**尚未实施或测试**，不访问真实图库。

## P4-UI-DIALOG-ESC-A — SCOPED PASS / Esc 退出与照片弹窗关闭优化

[任务卡](tasks/P4-UI-DIALOG-ESC-A.md)、[执行报告](reports/P4-UI-DIALOG-ESC-A.md)。人物批量管理 Esc 退出只清临时选择/snapshot/锚点并保留已加载列表/分页；人物相册整理照片 Esc 等效完成整理；清空选择仍不退出；原生打开的 dialog、写入及 pending write 优先/保护有效。唯一明显退出入口为批量模式切换按钮。定向 JUnit **19/0/0/0**，Node 语法与 diff-check 通过，JUnit 位于 `test-results/P4-UI-DIALOG-ESC-A.xml`。未做真实浏览器手测，遮罩内部空白命中及原生 cancel 效果待用户检查；未访问真实图库/PhotoSearchData，未跑全仓、GPU 或性能测试。


## P4-LIBRARY-VIDEO-EXCLUDE-A — SCOPED PASS / 视频扫描排除与照片资格

[任务卡](tasks/P4-LIBRARY-VIDEO-EXCLUDE-A.md)、[执行报告](reports/P4-LIBRARY-VIDEO-EXCLUDE-A.md)。视频扩展名在共享扫描枚举层过滤；历史视频成功 fast 刷新后通过原有 reconcile 转 `MISSING`，中断不误标。列表、SearchScope 权威候选、人物/合集照片计数及地点相册在分页/排名前应用统一视频路径资格，保留缺失图片、孤儿资产与跨来源共享图片语义。只读历史视频统计脚本已新增。定向 JUnit **25/0/0/0**（含任务合成用例及人物/合集/地点受影响面用例），Ruff、Python 编译、diff-check 通过。未访问真实图库/PhotoSearchData、未跑全仓/GPU/浏览器/规模或打包验收；未独立复核（按用户要求）。开工前 `geo/dataset.py` 改动保留，未提交。

## P4-PERSON-EMPTY-VISIBILITY-A — SCOPED PASS / 用户指定 LUNA High 审查路由例外

[任务卡](tasks/P4-PERSON-EMPTY-VISIBILITY-A.md)、[执行报告](reports/P4-PERSON-EMPTY-VISIBILITY-A.md)、[LUNA High 审查报告](reports/P4-PERSON-EMPTY-VISIBILITY-REVIEW-A.md)。列表资格应用活动资产、`MEMBER_PREDICATE` 与 `photo_asset_sql`；视频专属人物隐藏，同资产具备图片和视频路径仍展示。定向 JUnit **70/0/0/0**，Ruff、diff-check 通过。用户指定 LUNA / High 子代理审查（路由例外，不是 DS-H）；首轮 P2 已修并由修复轮确认 CLOSED，无新增 finding。真实浏览器、图库、全仓、GPU/模型、容量和打包未验。

## P4-RELEASE-020 — READY / v0.2.0 个人预览版任务卡（未实施）

[任务卡](tasks/P4-RELEASE-020.md)。按用户要求只立简化版本冻结卡：0.1.0→0.2.0、更新日志、版本一致性定向测试/最小启动搜索检查；不改 Schema 29、原图、模型、识别/搜索逻辑，不做全量 pytest、GPU/30 万/Windows 打包验收。推荐 **GPT-6 Luna / Medium**，发布代码推送/Tag/GitHub Pre-release 须另经用户确认。当前仅文档提交，**没有升级程序或运行测试**；本卡不取代现有其他任务的后续安排。

## P4-PERSON-SELECT-RANGE-A — SCOPED PASS / 人物整理范围与已加载快捷选择

[任务卡](tasks/P4-PERSON-SELECT-RANGE-A.md)、[执行报告](reports/P4-PERSON-SELECT-RANGE-A.md)。人物相册、人物批量管理、待整理/已忽略脸、人物合集成员支持各自范围锚点、已加载全选/反选和清空；候选组/隐藏区域隔离，超过 500 的选择整体拒绝，保留 person snapshot 与既有逐脸/API语义。合成 UI 定向 JUnit **14/0/0/0**，Node 语法、diff-check 通过。未做真实浏览器手测、真图库、GPU、全仓或性能验收；工作区原有 `geo/dataset.py` 改动保留。

## P4-PERSON-REGROUP-A — SCOPED PASS / 用户指定当前会话复核

[任务卡](tasks/P4-PERSON-REGROUP-A.md)、[执行报告](reports/P4-PERSON-REGROUP-A.md)、[加性协议 ADR-0020](decisions/ADR-0020-selected-face-regroup.md)。仅完成选中脸“移出并重新智能分组”与“整理照片→完成整理”：复用已存向量/候选和门限，源排除、人工控制、OPEN hold、双 CAS、幂等、原子回滚/重启重放及共享 undo 保留，三种移出/拆分语义明确；反馈实际归组/新人物/仍需整理数。新增 FACE_REGROUP、0029/schema 29，未改旧 action/companion。定向 JUnit **133/0/0/0 + 72/0/0/0**，最终忙时退出补充 **8/0/0/0**（去重 197 项），Ruff、编译、Node、diff-check 通过。当前会话受影响面复核，非独立 DS-H/Sol 冻结。按用户授权本地提交，未推送；未访问真实图库，未做本轮真实浏览器/模型/GPU/全仓/性能/实际库升级验收。下一步仅用户少量试用，不自动扩展任务；此前其他图库任务记录与工作树保留。

## P4-LIBRARY-RETENTION-C — SCOPED PASS / USER-DIRECTED LUNA-H REVIEW ROUTE EXCEPTION

[任务卡](tasks/P4-LIBRARY-RETENTION-C.md)、[执行报告](reports/P4-LIBRARY-RETENTION-C.md)。完成登录保护的只读失效资产统计与最多 50 条 keyset 分页、人工/任务/索引引用保护分类、未知引用 fail-closed、空间信息说明及按需展开 UI；首次失效时间仍未知，不推断 30 天资格。定向 JUnit **8/0/0/0**，Ruff、Node 语法、py_compile、diff-check 通过。独立 LUNA / High **PASS / finding=none**；按用户指定记录为**路由例外，不是 DS-H**。未访问真实图库/`PhotoSearchData`，未做真实浏览器手测、全量回归或 GPU 验证。C2 任何业务数据/向量逻辑或物理删除仍需单独授权。

前置 [P4-LIBRARY-SYNC-B](tasks/P4-LIBRARY-SYNC-B.md) 与 [P4-LIBRARY-OPS-A 报告](reports/P4-LIBRARY-OPS-A.md) 已按各自范围完成；其既有工作树改动保留。

更新：2026-10-11。当前规则：主架构需求修订 10、执行策略修订 12 / §9.4个人待审去重。较早逐项记录归档于[本次执行前快照](development-status-archive-2026-10-10-pre-P4-STORAGE-GC-A.md)、[2026-10-10 快照](development-status-archive-2026-10-10.md)、[2026-10-09 快照](development-status-archive-2026-10-09.md)及[2026-09-27 归档](development-status-archive-2026-09-27.md)。


## P4-PERSON-UX-SIMPLIFY-A — SCOPED PASS / 用户指定当前会话复核

[任务卡](tasks/P4-PERSON-UX-SIMPLIFY-A.md)、[执行报告](reports/P4-PERSON-UX-SIMPLIFY-A.md)。人物/相册/待整理脸支持头像上直接跨图跨页勾选；逐脸更正、原子多源合并/拆分、照片定向映射审核、统一历史/撤销/清理已迁入普通入口，旧高级重复 DOM 退场。仅新增按 asset 有界 OPEN 只读查询，身份写协议/人工排除/hold/CAS 保持。定向 JUnit **107/0/0/0 + 38/0/0/0**，Ruff、Python 编译、Node 语法、diff-check 通过；隔离合成图库真实浏览器完成单脸/多图更正、两页人物合并、拆分、映射确认/拒绝及撤销、历史撤销和清理预览。用户明确“这里复核”，记录为**同会话受影响面复核，不是独立 DS-H/Sol**。按用户授权本地提交，未推送；未访问真实图库，未做全仓/模型/GPU/30万性能验收。D 卡已协调完成范围，余下纯视觉目标不追认 PASS。

## P4-PERSON-UX-UNIFY-A — 历史立卡 / 已由 SIMPLIFY-A 替代，勿重复执行

[任务卡](tasks/P4-PERSON-UX-UNIFY-A.md)。2026-10-11 已提交**仅文档方案**：整合旧人物列表、逐脸批量调整/拆分、识别更新按照片直达、撤销历史；沿用现有 assign/unassign/restore_auto、原子 split/merge、mapping confirm/reject/undo 与人工 hold 保护，旧入口仅在替代能力验收后退场。必要时只新增照片定向映射的有界只读接口；保留最近完成的头像复用、全局排序及分区按需加载。与 [P4-UI-PEOPLE-D](tasks/P4-UI-PEOPLE-D.md) 已加避免并行重叠的说明。推荐 GPT-6.1 Sol / High（成本优先可选 GPT-6 Luna / High）；个人项目六组定向合成验收，涉及冻结人工写语义时停止另立高风险卡。**未实施、未测试、未访问真实 PhotoSearchData**；不改变此前 OCR 下一步安排。

## P4-PHOTO-VIEW-CLOSE-RETURN-A — SCOPED PASS

[任务卡](tasks/P4-PHOTO-VIEW-CLOSE-RETURN-A.md)、[执行报告](reports/P4-PHOTO-VIEW-CLOSE-RETURN-A.md)。照片查看对话框支持遮罩点击关闭；从照片人物头像进入人物页后可用“返回查看照片”回到来源，普通入口保留“所有人物”。定向 JUnit **9/0/0/0**，Ruff、Python 编译、相关 Node 语法和 diff-check 通过；真实浏览器/图库未验证。
## P4-PERSON-ORGANIZE-SECTION-PERF-A — SCOPED PASS

[任务卡](tasks/P4-PERSON-ORGANIZE-SECTION-PERF-A.md)、[执行报告](reports/P4-PERSON-ORGANIZE-SECTION-PERF-A.md)。人物/未命名人物页使用有效照片数与 `assets.created_at` 排序及独立三段游标；整理区块独立加载，合集不预加载，待整理 summary 不阻塞卡片；代表脸投影有界批量读取。定向 JUnit **29/0/0/0**，Ruff、`py_compile`、Node 语法、diff-check 通过。真实浏览器、真实图库、Uvicorn/30万性能未验证；不代表个人试用或全仓 PASS。未访问 PhotoSearchData/真实图库。

## P4-STORAGE-UI-A — SCOPED PASS；门闩已按用户确认开放

[任务卡](tasks/P4-STORAGE-UI-A.md)、[执行报告](reports/P4-STORAGE-UI-A.md)。已实现存储诊断 UI、用户点击触发的后台有界扫描、维护预览、可配置备份目录、手动 SQLite catalog 快照及一次性多选维护。用户确认报告通过并授权开闸后，`MAINTENANCE_REVIEWED=True`；启用后 API→启动消费合成闭环及原关闭拒绝路径均通过。P2 缓存版本/逐项实际结果展示已修复；任务定向 JUnit **28/0/0/0**（含 GC-A/GC-B 定向复用和 JS 渲染桩），Ruff、Python 编译/导入、Node 语法、diff-check 通过。状态为 **SCOPED PASS**；不代表真实图库、真实浏览器或全仓 PASS。未访问真实 PhotoSearchData/图库。

后续复核发现并修复两个 P2 UI 交付问题：更新脚本缓存版本，并在批次结果中显示每项实际删除数/释放量。用户确认报告通过并明确授权开放维护门闩；现 `MAINTENANCE_REVIEWED=True`，启用后的合成 API/启动消费闭环定向通过。复核报告：[P4-STORAGE-UI-A-REVIEW](reports/P4-STORAGE-UI-A-REVIEW.md)。真实浏览器、真实图库和全仓回归仍未验证。

## P4-TEST-BASELINE-TAIL-A — SCOPED PASS / 地点测试基线收尾完成

[任务卡](tasks/P4-TEST-BASELINE-TAIL-A.md)、[执行报告](reports/P4-TEST-BASELINE-TAIL-A.md)。修正北京→中国合成层级并用东京→日本保留跨国反例；同名地点两个范围经 `intent_plan` 分别选择后返回独立 GPS 成员；历史 v4 USER/custom 归属升级后可读且退休受保护；当前来源拒绝保持零写入。定向 JUnit **6/0/0/0**，Ruff、py_compile、diff-check 通过。仅改测试；没有产品缺陷扩大项。旧 `needs_place_choice` fallback 无现成直接测试，本卡未宣称覆盖该旧分支。全仓回归未运行，GC-A 的全量数仍是历史结果，OCR 原生运行时 `RUNTIME_UNCONFIRMED`。

## P4-TEST-BASELINE-CONTINUE-A — COMPLETE WITH PARTIALS / 四阶段完成

[总控入口](tasks/P4-TEST-BASELINE-CONTINUE-A.md)；四阶段报告：[LITE-B](reports/P4-TEST-BASELINE-LITE-B.md) **SCOPED PASS**（JUnit 43/0/0/0），[PLACE-RESIDUAL-A](reports/P4-TEST-BASELINE-PLACE-RESIDUAL-A.md) **PARTIAL**（54/0/0/0），[UI-A](reports/P4-TEST-BASELINE-UI-A.md) **SCOPED PASS**（9/0/0/0），[CORE-A](reports/P4-TEST-BASELINE-CORE-A.md) **PARTIAL**（108/0/0/0）。本卡后续报告收口地点同名选择及历史人工归属证据；OCR 原生运行时 `RUNTIME_UNCONFIRMED`，其余历史全量失败未复跑。GC-A 全量历史 2,338 项（70 failures/43 errors/49 skipped）未复跑；LITE-A 的 50 项定向 48/2 仍是历史阶段结果，不推断当前全量余项。全仓门禁没有被标 PASS。

## P4-PERSON-COLLECTION-COUNT-A — PASS / 合集照片数展示

[任务卡](tasks/P4-PERSON-COLLECTION-COUNT-A.md)、[执行报告](reports/P4-PERSON-COLLECTION-COUNT-A.md)。合集列表与照片页显示有效 canonical 成员关联的活动照片总数，按 asset 去重；改名后保留计数。定向 JUnit **10/0/0/0**，Ruff、Node 语法及 diff-check 通过；一次相邻旧 UI 测试在其人物卡初始渲染断言失败，详见报告。未做真实图库/独立浏览器验证；本任务 diff 未提交。

## P4-STORAGE-GC-B — IMPLEMENTED / 任务卡定向验收通过

[任务卡](tasks/P4-STORAGE-GC-B.md)、[执行报告](reports/P4-STORAGE-GC-B.md)。新增停机 LanceDB 活跃图像 generation 诊断/按需标量索引修复/fragment compaction/14 天原生版本清理，以及独立可选 SQLite VACUUM 与 `storage optimize-vectors`、`storage vacuum` CLI。四组 synthetic JUnit 定向测试 **4/0/0/0**；Ruff、`uv lock --check`、Python 编译、CLI help 和 `git diff --check` 通过。合成 fixture 中压缩 3→1 fragments、当前检索等价；版本清理实际释放 0 bytes（保留期内）；VACUUM fixture 实际缩小 9,850,880 bytes。未访问真实 PhotoSearchData/图库/模型/GPU，未跑全量回归。为锁定 LanceDB 0.38.0 的 compaction/cleanup API 启用 `pylance` extra。GC-A 的 GPT-6 Luna/High 路由例外维持原记录，**不是 DS-H PASS**；GC-A 全量历史失败单独保留，不作为 GC-B 验收，也未重跑。

## P4-PERSON-AVATAR-REUSE-A — A+B 定向验收通过（2026-10-11 修复列表渲染回归）；C 后置

[任务卡](tasks/P4-PERSON-AVATAR-REUSE-A.md)、[执行报告](reports/P4-PERSON-AVATAR-REUSE-A.md)。用户指定从 `f140e82` 执行此卡；60 秒内无变化返回复用列表 DOM，人物卡片刷新复用稳定头像 `<img>`，同模式重复点击不重载。2026-10-11 修复真实 DOM `HTMLCollection` 不支持 `.find()` 导致人物卡片渲染中断的问题，并更新脚本版本 URL 以避开旧缓存；Node 测试桩覆盖 DOM 差异并检查版本 URL。任务卡 Node 语法、两份定向 UI pytest（JUnit 2/0/0/0）和 `git diff --check` 通过。真实浏览器图片缓存/用户图库观察未做，C 后置；工作区变更未提交。原 `P4-TEST-BASELINE-LITE-A` 状态和其后续入口保持不变。

## P4-TEST-BASELINE-LITE-A — PARTIAL / 地点 fixture 修复与轻量分类

[任务卡](tasks/P4-TEST-BASELINE-LITE-A.md)、[执行报告](reports/P4-TEST-BASELINE-LITE-A.md)。GC-A 全量历史结果仍为 2,338 项 / 70 failures / 43 errors / 49 skipped，未重跑。地点 ASSIGN fixture 改为使用正式激活的微型合成来源包；搜索权威、人物/地点搜索、备份恢复及来源拒绝零写入共 50 项定向结果为 **48/2/0/0**，两项为冻结哈希与未命名人物列表断言，需后续确认。Ruff、py_compile、diff-check 通过。52 个全量 failure 签名差异尚未规范化；本卡为 **PARTIAL**，不代表全仓通过。此前七阶段完整基线计划仍为 DEFERRED。

## P4-STORAGE-GC-A — IMPLEMENTED / 定向验收通过；GC-B 前置条件按路由例外满足

[任务卡](tasks/P4-STORAGE-GC-A.md)、[执行报告](reports/P4-STORAGE-GC-A.md)。GC-A 相关定向 JUnit 现存结果 **76/0/0/0**，未见直接受影响面新增回归。用户确认独立 GPT-6 Luna / High 复核通过；原 V3.1 路由要求 DS-H 全受影响面主审，本次按用户明确指示记录并接受路由例外。该结论**不是 DS-H PASS**。全量 unit/integration 既有 **2,338 项：2,176 passed、70 failures、43 errors、49 skipped** 原样保留，仍未通过，作为独立历史基线问题跟踪；本次未重跑或修改测试结果。未访问真实 PhotoSearchData、真实图库或用户服务；未改 Schema/依赖。

## P4-PLACE-ALBUM-PERF-A — PARTIAL / 列表已变快，后台预生成已实现，实际首屏待验证

[任务卡](tasks/P4-PLACE-ALBUM-PERF-A.md)、[执行报告](reports/P4-PLACE-ALBUM-PERF-A.md)。地点列表已变快；用户确认首次只有一张封面立即显示、其余需数秒，同批再次刷新明显变快，支持冷缩略图首次生成等待的判断。新增有界后台预生成：应用启动和成功扫描队列排空后，城市/省份/区县/国家各取前 4 张（最多 16 张），写入并复用现有 content-addressed 缩略图缓存，不阻塞扫描；前台与预热共用最多两路生成限制，同一资产并发会合并。定向 JUnit **15/0/0/0**（含缓存复用、后台边界与 scan queue 触发），Ruff、py_compile、Node 语法和 diff-check 通过。真实冷首屏收益待重启实测；真实 PhotoSearchData 未由本次开发测试访问。目录手动加载；相册按匹配数量降序，同数按最近加入图库时间降序。任务仍 PARTIAL，未提交。

## P4-OCR-LIFECYCLE-A — SCOPED PASS / 退役任务收尾与来源进度修复

[任务卡](tasks/P4-OCR-LIFECYCLE-A.md)、[执行报告](reports/P4-OCR-LIFECYCLE-A.md)。集中声明 active/retired processor IDs；通过有界 lease/CAS 安全协调退役任务，接入 Catalog 启动、OCR 入队和运行入口；按当前 SHA 与 distinct asset 统计 OCR 结果、等待、失败、受阻和未尝试，更新来源提示与继续操作条件。任务范围 JUnit **10/0/0/0**，Ruff、Python 编译、Node 语法和 `git diff --check` 通过。用户指定独立 **LUNA H REVIEW PASS，finding=none；不做 DS-H**。既有 `test_p4_02y_l_identity.py` 的 HEAD 第 824 行字面量 `\\n` 导致额外收集 SyntaxError；本任务定向测试中的迟交拒绝/历史数据保护反例已实际执行。未访问真实图库、PhotoSearchData、GPU/模型或浏览器；真实 OCR 状态和来源进度待用户自主实测，不代表全仓或生产运行 PASS。

## 下一步

P4-LIBRARY-RETENTION-C 的 C1 已按用户指定的 LUNA / High 路由例外完成复核并定向验收。C2 删除业务/向量数据未授权；待用户查看只读预览并单独确认清理范围/策略后再规划 C2 卡。[P4-PERSON-UX-SIMPLIFY-A](tasks/P4-PERSON-UX-SIMPLIFY-A.md) 本卡闭环已完成，下一步由用户少量试用人物多图勾选、更正/拆分及照片待确认入口并反馈；无新增算法或安全缺口，不强行新建优化卡。[P4-UI-PEOPLE-D](tasks/P4-UI-PEOPLE-D.md) 余下纯视觉目标与 [P4-PLACE-ALBUM-PERF-A](tasks/P4-PLACE-ALBUM-PERF-A.md) 真实冷首屏验证仍保留；OCR 下一步安排不变。全仓、真实图库和 30 万性能未验证。
