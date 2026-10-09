# Development status

更新：2026-10-09。当前规则：主架构需求修订 9、执行策略修订 12 / §9.4个人待审去重。当前简要交接；较早逐项记录归档于[2026-10-09 快照](development-status-archive-2026-10-09.md)及[2026-09-27 归档](development-status-archive-2026-09-27.md)。

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
| [搜索与查看组](tasks/P4-PERSONAL-REVIEW-SEARCH-A.md) | ENGINEERING REVIEW PASS / SOL_NOT_REQUIRED；Qwen真实页面效果待验。PHOTO-VIEW复用用户已接受报告并复核基础查看路径。 |
| [OCR](tasks/P4-02Y-L-review.md) | ENGINEERING REVIEW PASS / F1–F7 CLOSED；独立定向 60/60。真实 weights/session/GPU 仍 PENDING。 |
| [模型切换](tasks/P4-EMBEDDING-ARCH-review.md) | 用户指定的当前会话个人短审ENGINEERING REVIEW PASS / SOL_NOT_REQUIRED（非DS-H）；Qwen阈值变更后的真实页面效果仍PENDING。 |

P4-TEST-BASELINE及地点基线修绿卡保持DEFERRED；历史全量失败照实保留，不默认修全仓。基础关键失败不得跳过。已有PASS任务不重开，不自动继续性能优化。

## 下一步

五组工程报告按用户授权的个人范围收口，转日常使用与具体反馈，不默认新增审核/性能/全量基线修绿/发行任务。模型组已记录ADR-0003/0013适用性解释及SOL_NOT_REQUIRED，当前不另立Sol卡；未来run驱动激活仍须遵守冻结门禁。真实模型/GPU、Qwen页面、头像、CN包及其它原现场项由用户另行决定何时核对；人物合并重复反馈卡未自动启动。本次只做报告/状态/索引核对与同步，未操作真实图库、服务或模型，未提交/推送。
