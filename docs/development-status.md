# Development status

更新：2026-10-10。当前规则：主架构需求修订 10、执行策略修订 12 / §9.4个人待审去重。当前简要交接；较早逐项记录归档于[2026-10-10 快照](development-status-archive-2026-10-10.md)、[2026-10-09 快照](development-status-archive-2026-10-09.md)及[2026-09-27 归档](development-status-archive-2026-09-27.md)。

## P4-PERSON-COLLECTION-MANAGE-A — IMPLEMENTED / 当前会话受影响面工程审查通过；用户浏览器测试已确认

[实施报告](reports/P4-PERSON-COLLECTION-MANAGE-A.md)、[当前会话审查报告](reports/P4-PERSON-COLLECTION-MANAGE-A-review.md)、[实施卡](tasks/P4-PERSON-COLLECTION-MANAGE-A.md)。人物 `/persons/browse` 默认响应增加 canonical 合集标签批量投影；`collection_state=none` 在 SQL 计数/排序/分页前排除属于现存合集的人物，并把合集实例/revision 摘要绑定到新游标。筛选覆盖命名、未命名、已忽略列表，和原查询条件组合；筛选变化清空选择但保留批量模式；加入、改名、删除合集后刷新标签并通知其他人物页。任务卡相关定向 JUnit 27/0/0/0，Ruff、Python 编译、Node 语法及 diff-check 通过。用户确认浏览器功能测试 OK。本会话受影响面工程审查通过；按用户要求在当前会话完成，未切换模型，结论不称独立 DS-H。未访问真实图库或运行服务；未提交/推送。

## 新增实施：P4-SEARCH-INTENT-NL-COVERAGE-FIX-A — 已实现 / 定向验证与当前会话工程复核通过；独立审查与现场试用待完成

[任务卡](tasks/P4-SEARCH-INTENT-NL-COVERAGE-FIX-A.md)、[执行报告](reports/P4-SEARCH-INTENT-NL-COVERAGE-FIX-A.md)。新增 37 条解析红灯后按根因修复合集范围连接词/多合集拒绝、时间与地点上下文、人物同框关系、来源句式及前端 request generation；`all` 人物 chip 全取消后可继续检索。当前会话复核另补月份区间边界，反向/跨年范围明确拒绝，重复年份同年范围完整执行。任务卡六个测试文件 JUnit 102/0/0/0，Ruff、Node、diff-check 通过。后续补修 `filter-only` 与地点相册续页累加显示，定向 JS 测试 1/0/0/0。当前会话工程复核通过（非独立 DS-H）；独立 DS-H High 复核和现场少量真实数据复测待完成。未跑全仓/浏览器矩阵/GPU/真实模型/打包，未访问真实 PhotoSearchData 或运行服务，未提交/推送。与 P4-PERSON-SEMANTIC-REPAIR-A 独立。

## 当前实施：P4-PERSON-SEMANTIC-REPAIR-A — 已实现 / 工程复核通过；验证待完成

[实施与复核报告](reports/P4-PERSON-SEMANTIC-REPAIR-A.md)、[实施卡](tasks/P4-PERSON-SEMANTIC-REPAIR-A.md)。已修复 CLI 冷启动导入循环和人物相册语义索引空状态，并覆盖合成改名 → 正式 drain → 范围搜索恢复。定向 JUnit 3/0/0/0 及静态检查通过。用户要求当前会话复核，工程复核通过 / SOL_NOT_REQUIRED。必要的 B/D 宽回归仍失败（2253 tests / 70 failures / 43 errors / 49 skips；记录一次 ONNX fatal）；未将失败断言为既有问题。该任务在该验证门槛解决前不标为 PASS。未访问真实照片数据或服务。

## 当前：P4-SEARCH-INTENT-UNIFIED-A-FIX2 — GeoNames 同名父子候选选择修复完成 / 定向验证通过

[修复卡](tasks/P4-SEARCH-INTENT-UNIFIED-A-FIX2.md)、[修复报告](reports/P4-SEARCH-INTENT-UNIFIED-A-FIX2.md)、[前序修复](reports/P4-SEARCH-INTENT-UNIFIED-A-FIX1.md)。同名 GeoNames 国家/城市候选可在相同文本跨度同时命中；此前只按来源优先，两个候选同为 GeoNames 时仍要求选择。现在仅当活动目录的父记录 ID、来源和数据包均证明同名城市直接隶属同名国家时，才默认国家范围；不相关同名地点继续保留歧义，内部父身份不进入解析器返回选项。定向 JUnit 41/0/0/0，Ruff、diff-check 通过。根据按钮父级文字推断现场匹配此类情况，未访问真实图库/活动目录确认具体 GeoNames 行；未跑全仓/浏览器矩阵/GPU/模型/打包，未提交/推送。

## FIX1：P4-PLACE-UNIFIED-A-FIX1 — IMPLEMENTED / 定向验证通过；现场复测待完成

[修复卡](tasks/P4-PLACE-UNIFIED-A-FIX1.md)、[报告记录](reports/P4-SEARCH-PLACE-NL-A-implementation.md)。自然语言同名片段默认优先当前候选中的 OSM；无 OSM 时 GeoNames 回退；已验证的区域父链约束优先，多个不同 OSM 同名仍要求选择。JUnit 28/0/0/0，Ruff、diff-check通过。未访问真实图库或服务，现场刷新/重启后效果待用户确认；未提交/推送。

## 当前：P4-SEARCH-COLLECTION-A — IMPLEMENTED / 语义合集 500 回归已修复 / 定向验证通过

[实施卡](tasks/P4-SEARCH-COLLECTION-A.md)、[报告](reports/P4-SEARCH-COLLECTION-A-implementation.md)。合集自然语言查询接入统一 scope。修复两个现场反馈：`query_hash()` 不再收到不支持的合集参数；入口支持“合集名 + 的 + 内容词”（如“AI的裙子”），将“裙子”作为语义词并绑定 AI 合集。任务卡限定 JUnit 81/0/0/0，两个关键回归节点各 1/0/0/0，Ruff、Node、diff-check 通过。之前宽受影响面尝试的 4 个旧地点包兼容上限失败及 1 个缺失表夹具失败详见报告；未扩大修复无关夹具。未访问真实图库/运行中服务，现场刷新后试用待确认；不代表真实库验收。未做全仓/GPU/30万/打包/发行验证。

## 前序小修：P4-PERSON-SMALL-FIX-A — IMPLEMENTED / 定向验证通过；现场试用待确认

[任务卡](tasks/P4-PERSON-SMALL-FIX-A.md)、[报告](reports/P4-PERSON-SMALL-FIX-A.md)。排序、大小写搜索、改名/合集刷新、移出恢复可见及待整理确认打勾已修。同日补齐待整理/未命名写后刷新保留已加载页数、展开组人脸页和浏览位置，最新游标重取成功才替换；新筛选仍从第一页。最新定向 JUnit 11/0/0/0，Ruff、Node、diff-check通过；前轮证据详见报告。schema仍28，未改身份/归属/人工锁/撤销协议；当前会话自检非独立DS-H/Sol。用户正常重启刷新后少量试用；旧已移出且隐藏人物用“已忽略人物”恢复。未访问真实库/现有服务、未跑全仓/GPU/模型/打包/浏览器矩阵、未提交/推送。

## P4-PERSON-INBOX-CREATE-A — 定向验证与当前会话工程复核通过；个人试用待确认

[任务卡](tasks/P4-PERSON-INBOX-CREATE-A.md)、[实施报告](reports/P4-PERSON-INBOX-CREATE-A-implementation.md)、[复核卡](tasks/P4-PERSON-INBOX-CREATE-A-review.md)、[当前会话复核报告](reports/P4-PERSON-INBOX-CREATE-A-review-current-session.md)。schema27→28，新建人物仅归属所选单脸，新操作永不可撤销。后端与旧撤销 JUnit 40/0/0/0；原迁移组 13/0/0/0；DOM 1/0/0/0；复核修复回归与迁移组 16/0/0/0。Ruff、Node、py_compile、diff-check 通过。用户要求当前会话复核，受影响面工程复核通过（非独立 DS-H/Sol）；少量真实照片试用仍待确认。未跑全仓/浏览器矩阵/GPU/模型/打包，未访问 PhotoSearchData 或现有服务；未提交/推送。
## P4-PERSON-OPLOG-GC-A — IMPLEMENTED / 定向验证通过 / 同会话复核通过

[任务卡](tasks/P4-PERSON-OPLOG-GC-A.md)、[实施报告](reports/P4-PERSON-OPLOG-GC-A-implementation.md)、[同会话复核记录](reports/P4-PERSON-OPLOG-GC-A-same-session-review.md)。schema27 手动预览/确认清理，严格同时满足 30 天与不在最近200条；每批最多检查100候选并可续批；预览展示原快照 JSON 字节及跳过原因。定向 JUnit 5/0/0/0，Ruff、Node、py_compile、diff-check通过。按用户要求在当前会话完成复核；未执行独立 DS-H，不冒称该结论。未跑全仓/真实图库或现有服务迁移、清理；未运行浏览器矩阵。实施时 HEAD `4440f74`；之后上游只新增 Inbox-create 文档提交至 `d628a87`，已保留。
## 本次完成：P4-PERSON-CAPTION-A — PASS / 定向 DOM 验证通过

[任务卡](tasks/P4-PERSON-CAPTION-A.md)、[执行报告](reports/P4-PERSON-CAPTION-A.md)。人物相册照片卡移除“这张照片里的这个人”，保留脸部缩略图和合集说明。定向 JUnit 1/0/0/0，Node、Ruff、diff-check通过；未访问真实图库/服务。下一步仍为少量个人试用，无后续工程卡。
## 本次完成：P4-PERSON-QUALITY-A — IMPLEMENTED / 定向合成验证通过；个人试用待确认

[任务卡](tasks/P4-PERSON-QUALITY-A.md)、[实施报告](reports/P4-PERSON-QUALITY-A-implementation.md)。整理页默认 clean（均衡 0.70/56/40），旧 API 默认 all；质量入口、分组和分页一致，人工控制与忽略状态保真。只读三档 SQLite 诊断工具已新增。定向 JUnit 20/0/0/0，Ruff、Node、py_compile、diff-check 通过。真实图库阈值效果仍待用户少量试用；未访问 PhotoSearchData/用户服务，未运行真实诊断、浏览器矩阵、全仓/容量/GPU/模型/打包，未迁移；实现随后本地提交为 `eae58ea`，未推送。当前转个人试用，无下一张工程任务卡。
## 前序完成：P4-PERSON-COLLECTION-A — FOCUSED REVIEW PASS / F1–F6 CLOSED；用户试用待确认

[任务卡](tasks/P4-PERSON-COLLECTION-A.md)、[实施报告](reports/P4-PERSON-COLLECTION-A-implementation.md)、[聚焦审核报告](reports/P4-PERSON-COLLECTION-A-focused-review.md)。v24→v26合集迁移及跨升级旧实例保护已完成；迁移前已删除 ID 即使复用，旧人物/照片游标和 ID-only 请求也不能操作新实例。修复轮定向 JUnit 22/0/0/0，用户要求的受影响面复核 JUnit 13/0/0/0；限工程/合成范围，真实页面试用仍待确认。不宣称指定 Sol 模型或全量验收；不访问真实图库，未推送。
## 前序完成：P4-PERSON-INBOX-HIDDEN-GROUP-A — IMPLEMENTED / 定向验证通过

[任务卡](tasks/P4-PERSON-INBOX-HIDDEN-GROUP-A.md)、[报告](reports/P4-PERSON-INBOX-HIDDEN-GROUP-A.md)。已忽略人脸按当前首选有效 SUGGESTED 候选聚合，组卡与组内均分页；原隐藏平铺 API 兼容，恢复仍是逐 face_id。定向 JUnit 2/0/0/0，Ruff/Node/diff-check通过，用户已确认真实页面功能可用。未访问PhotoSearchData或现有服务，未提交/推送。
## 新增实现：P4-PERSON-INBOX-HIDE-A — IMPLEMENTED / 定向验证通过

[任务卡](tasks/P4-PERSON-INBOX-HIDE-A.md)、[报告](reports/P4-PERSON-INBOX-HIDE-A.md)。schema23 新增按 face_id 独立隐藏标记、待整理查询过滤、受保护原子批量 API、已忽略人脸分页恢复及人物页跨页逐脸选择。任务卡两组定向测试 2/0/0/0，Ruff/Node/diff-check通过。用户个人前端操作待确认；未跑全仓/30万/GPU/模型/打包/浏览器矩阵，未访问PhotoSearchData或现有服务，未提交/推送。
## 新增完成：P4-PERSON-COUNT-A — PASS / 定向 API 与 DOM 回归

[任务卡](tasks/P4-PERSON-COUNT-A.md)、[报告](reports/P4-PERSON-COUNT-A.md)。人物页分别显示已命名、有效未命名、已忽略人物的服务端完整数量；计数按搜索/类型/可见性过滤，不随分页或排序重复累计。定向 API＋DOM 3/0/0/0，Ruff、Node、diff-check通过。未运行全量回归或真实浏览器；现场显示待用户使用确认。
## 新增完成：P4-PERSON-HIDE-UI-FIX-A — PASS / 定向 DOM 回归

[任务卡](tasks/P4-PERSON-HIDE-UI-FIX-A.md)、[报告](reports/P4-PERSON-HIDE-UI-FIX-A.md)。多页批量选择、取消与模式切换改为原位更新人物卡片，不再重载首屏/清游标；120 人选择、第4页追加、失败保留和成功恢复刷新用例 1/0/0/0，Ruff/Node/diff-check通过。滚动以 DOM 节点/请求/游标不变作合成替代验证，未做真实浏览器观察；不访问PhotoSearchData或用户服务。
## 人物合并重复反馈：P4-PERSON-MERGE-DEDUPE-A — PASS

[任务卡](tasks/P4-PERSON-MERGE-DEDUPE-A.md)、[报告](reports/P4-PERSON-MERGE-DEDUPE-A.md)。正式合并 API 隔离合成回归覆盖共享合照＋A/B单人照、`limit=1`逐页、旧 ID 解析和正式撤销；合并后 3 个唯一 `asset_id`，双脸保留，撤销后双方各恢复 2 张。定向集成 14/0/0/0，Ruff/diff-check通过。用户随后在个人相册复测并反馈没问题，现场验收关闭；未改产品代码。未访问PhotoSearchData或现有服务。
## 新增实现：P4-PERSON-HIDE-A — IMPLEMENTED / PERSONAL-SCOPED IMPACT REVIEW PASS

[实现卡](tasks/P4-PERSON-HIDE-A.md)、[报告](reports/P4-PERSON-HIDE-A-implementation.md)。schema22 独立隐藏标记、可见性 SQL 过滤/游标隔离、受保护批量 API 和人物相册多页选择/恢复已实现。定向 JUnit 25/0/0/0，Ruff/Node/diff-check通过；按用户要求未执行全量回归。同会话个人范围影响面复核通过；真实应用重启与浏览器操作待用户试用。未触碰PhotoSearchData、模型、服务或其它现有未跟踪文件。
## 新增完成：P4-PERSON-INBOX-CONFIRM-A — PASS / PERSONAL-SCOPED REVIEW

[任务卡](tasks/P4-PERSON-INBOX-CONFIRM-A.md)、[执行报告](reports/P4-PERSON-INBOX-CONFIRM-A.md)。候选单脸可直接“确认是XX”，复用逐脸人工归属接口和 revision/CSRF/撤销路径；保留“选其他人”与无候选时的“这是谁？”。定向 UI＋单脸纠错/撤销 2/0/0/0，Ruff、Node、diff-check通过。按用户要求在当前会话简审；未跑浏览器、全仓、真实图库/服务或模型。
## 新增完成：P4-PERSON-INBOX-GROUP-SORT-A — PASS / PERSONAL-SCOPED REVIEW

[执行报告](reports/P4-PERSON-INBOX-GROUP-SORT-A.md)。候选收件箱服务端按不同待确认照片数全局排序并分组，旧 inbox/summary 兼容；显式从头补匹配保留原批次续批位置。用户指定个人范围与当前会话简审；相关定向23/0/0/0，Ruff/Node/diff-check通过，31脸合成页约23.2ms，本会话复核未发现本任务阻断缺陷。全量回归另记录既有语法错误、48失败、43错误、49跳过及一次ONNX Runtime CUDA DLL fatal exception；不作为本次个人范围验收门槛，也不声称全仓通过。不访问真实图库/服务、不提交/推送。
## 当前总览：五组个人范围工程审核已闭环

[收口核对](reports/P4-PERSONAL-REVIEW-closeout.md)：人物、地点、搜索与查看、OCR、模型切换均有工程PASS记录；本次核对报告、已有JUnit与后续直接产品差异，没有新跑pytest或重复主审。模型组为用户指定个人短审，非DS-H/Sol结论。原真实能力/现场PENDING、全量B级未转绿继续保留。五组之外[人物合并重复反馈卡](tasks/P4-PERSON-MERGE-DEDUPE-A.md)仍READY / 未实施，不计已修复。总计划与旧审查索引已同步，未把原任务批量标总PASS。
## 本次完成：P4-02Y-L-IDENTITY-A-FIX — ENGINEERING REVIEW PASS / F1–F7 CLOSED

[独立复核报告](reports/P4-02Y-L-review-dsh.md)、[修复卡](tasks/P4-02Y-L-IDENTITY-A-fix.md)、[实现报告](reports/P4-02Y-L-IDENTITY-A.md)。当前审核会话独立关闭 F6/F7：普通终态不可恢复、OCR handoff 专用 CAS、provider-unverified 持久拒绝均核对通过；F1–F5 保持关闭，未见新增阻塞问题。独立定向 JUnit 60/60 passed、0 failures/errors/skips，Ruff/diff-check 通过；实现轮 65/65 证据已核对。ENGINEERING REVIEW PASS / SOL_NOT_REQUIRED，仅限工程与合成范围；真实 weights/session/GPU 仍 PENDING，不标 OCR 总 PASS。不另跑整轮 DS-H/Sol，不自动启动其他任务；未加载模型、访问真实图库/服务、提交或推送。
## 前序完成：P4-PERSONAL-REVIEW-SEARCH-A — ENGINEERING REVIEW PASS / SOL_NOT_REQUIRED

[执行报告](reports/P4-PERSONAL-REVIEW-SEARCH-A.md)、[任务卡](tasks/P4-PERSONAL-REVIEW-SEARCH-A.md)。按用户指定在当前会话完成W/W-Q/NL全受影响面工程复核，结论ENGINEERING REVIEW PASS / SOL_NOT_REQUIRED；没有声称切换DS-H。修复不同长度及重叠人名识别、失败查询残留旧条件标签；排序对照测试改用当前统计策略注入点。定向JUnit 12/0/0/0，Ruff/Node/diff-check通过；65张合成浏览器筛选/翻页/缩略图/人物相册往返通过。Qwen真实网页效果待验。现在只交接OCR卡，不自动开始；未运行全量/30万/GPU/打包，未访问真实图库或用户服务；按用户指示本地提交，未推送。
## 本次完成：P4-PERSONAL-REVIEW-PLACE-A — ENGINEERING REVIEW PASS / SOL_NOT_REQUIRED

[地点组审查报告](reports/P4-PERSONAL-REVIEW-PLACE-A.md)、[执行卡](tasks/P4-PERSONAL-REVIEW-PLACE-A.md)。按用户要求在当前会话完成工程审查，没有自动切换模型，故不声称本次为DS-H会话。逐项覆盖CN/UNIFIED/schema21兼容/包恢复与RECOVERY-B/MAP/地点NL；恢复缺前驱窄例外补隔离小包正例，地图API与UI定向JUnit 7/7，合并定向JUnit 12/12，Ruff通过；10张合成照片浏览器确认地点建议批量归属、地点词＋文本词搜索/取消条件保留、地图→详情→返回。原任务现场/正式数据事项仍按报告标记PENDING；未访真实图库、未重启用户服务、未运行全量/30万/GPU/PBF。代码差异仅恢复测试断言与新增专用正例；保留先前工作区文档改动与.tmp材料，未提交/推送。
## 前序完成：P4-PERSONAL-REVIEW-PERSON-A — ENGINEERING REVIEW PASS

[人物组审查报告](reports/P4-PERSONAL-REVIEW-PERSON-A.md)、[执行卡](tasks/P4-PERSONAL-REVIEW-PERSON-A.md)。逐原卡审查UX/DETAIL-A/MATCH-A+B/IMAGE/AVATAR/CLEANUP/COVER/PREVIEW新增面；复用DETAIL-B已审共享边界和可核对JUnit，补做当前版本隔离合成浏览器封面与前后导航。未重跑pytest。AVATAR正常重启/刷新现场确认仍PENDING，由用户决定正常更新服务后观察；不影响转交地点组，未追认其整卡或真实图库PASS。
## 前序完成：待审任务个人范围优化（仅文档）

[共同规则与覆盖表](plans/P4-PERSONAL-REVIEW.md)、[调整报告](reports/P4-PERSONAL-REVIEW-optimization.md)。22张原主卡统一为五组；旧review卡转索引，复用已独立通过的共享面，证据缺口才补小型基础验证；不默认全量/30万/GPU/打包/固定多轮Sol。AGENTS、策略、task-launcher已同步。该文档优化本身没有新增产品任务PASS。
## 最近完成：P4-PERSON-SORT-A — PASS（本地 A 级范围与当前会话审查）

[执行卡](tasks/P4-PERSON-SORT-A.md)、[报告](reports/P4-PERSON-SORT-A.md)。保留旧 `people1` 游标并为照片数排序使用独立前缀；排序聚合计数复用于当前页摘要。定向JUnit 18/0/0/0；完整单元/集成 2154/2012 passed/50 failures/43 errors/49 skipped，失败/错误与2152项基线一致、无新增；Ruff/Node/diff-check通过。查询计划使用 `idx_face_person_links_person`；243人合成热缓存中位数 ID/照片数排序0.353/1.238 ms。按用户要求在本会话直接审查；自动归组并发期间无快照保证，详情见报告。未访问PhotoSearchData或真实浏览器。
## 前置完成：P4-PERSON-ALBUM-PERF-A — PASS（个人相册基础功能）

[执行卡](tasks/P4-PERSON-ALBUM-PERF-A.md)、[报告](reports/P4-PERSON-ALBUM-PERF-A-implementation.md)。按用户限定完成合成相册基本浏览与定向验证；不追认全仓、真实图库或发行通过。全量基线2152/50 failures/43 errors/49 skipped保留。
## 前置人物交接：P4-PERSON-DETAIL-B — 工程与Sol聚焦审查已通过

[报告](reports/P4-PERSON-DETAIL-B-review.md)、[Sol聚焦终审卡](tasks/P4-PERSON-DETAIL-B-sol-review.md)。既有人脸身份/人工状态审查闭环通过；未验真实图库、现场catalog升级、GPU/吞吐。人物列表逐人物查询属于未测量的另线问题，不自动接续。
## 待审/待验入口（按当前状态）

| 入口 | 当前缺口与复用 |
|---|---|
| [人物组](tasks/P4-PERSONAL-REVIEW-PERSON-A.md) | ENGINEERING REVIEW PASS；AVATAR正常重启/刷新现场确认仍单项PENDING。 |
| [地点组](tasks/P4-PERSONAL-REVIEW-PLACE-A.md) | ENGINEERING REVIEW PASS / SOL_NOT_REQUIRED；正式CN包导入/交付、实际catalog与真实照片现场效果仍按地点组报告保留PENDING。 |
| [搜索与查看组](tasks/P4-PERSONAL-REVIEW-SEARCH-A.md) | ENGINEERING REVIEW PASS / SOL_NOT_REQUIRED；Qwen真实页面效果待验。PHOTO-VIEW复用用户已接受报告并复核基础查看路径；2026-10-09补充分辨率显示，详情宽高缺失时仅读当前打开照片图像头，JUnit 26/0/0/0、合成浏览器首页/搜索预览显示正确。 |
| [OCR](tasks/P4-02Y-L-review.md) | ENGINEERING REVIEW PASS / F1–F7 CLOSED；独立定向 60/60。真实 weights/session/GPU 仍 PENDING。 |
| [模型切换](tasks/P4-EMBEDDING-ARCH-review.md) | 用户指定的当前会话个人短审ENGINEERING REVIEW PASS / SOL_NOT_REQUIRED（非DS-H）；Qwen阈值变更后的真实页面效果仍PENDING。 |

P4-TEST-BASELINE及地点基线修绿卡保持DEFERRED；历史全量失败照实保留，不默认修全仓。基础关键失败不得跳过。已有PASS任务不重开，不自动继续性能优化。
## UI 优化规划 — v0.1讨论稿 / 未实施

[总计划](plans/P4-UI-PERSONAL.md)、[规划报告](reports/P4-UI-PERSONAL-planning.md)、[布局草图](mockups/P4-UI-PERSONAL.html)、[逐页效果图](mockups/README.md)。用户要求先本地提交讨论版本，后续继续交流，未定稿。9张PNG已保存，六张卡均要求执行前实际看对应图、实现后截图对照。已补[逐页现状盘点](plans/P4-UI-INFORMATION-CHECKLIST.md)，119项仅作备份参考；按用户澄清撤回全量保留/逐项验收要求，只有此前明确强调的必要项需落实。照片/人物/地点为日常入口，图库管理与高级工具次级；首批业务 API/schema/Python 业务代码零改动。候选卡优先 A/B/C；首张 [P4-UI-SHELL-A](tasks/P4-UI-SHELL-A.md)，推荐 GPT-6.1 Sol / High，用户手动选择。仅规划，不自动替换下方个人试用交接；用户明确指定后开始。未改产品代码或真实图库。

## 下一步

下一步按 P4-PERSON-SMALL-FIX-A 修复卡正常重启/刷新后少量试用四项反馈。当前为实施与定向验证通过，不冒称独立 DS-H/Sol 或现场验收；不自动续开性能/工程卡。
