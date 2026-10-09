# Development status

更新：2026-10-09。当前规则：主架构需求修订 9、执行策略修订 11。当前简要交接；较早逐项记录归档于[2026-10-09 快照](development-status-archive-2026-10-09.md)及[2026-09-27 归档](development-status-archive-2026-09-27.md)。

## 最近完成：P4-PERSON-SORT-A — PASS（本地 A 级范围与当前会话审查）

[执行卡](tasks/P4-PERSON-SORT-A.md)、[报告](reports/P4-PERSON-SORT-A.md)。保留旧 `people1` 游标并为照片数排序使用独立前缀；排序聚合计数复用于当前页摘要。定向JUnit 18/0/0/0；完整单元/集成 2154/2012 passed/50 failures/43 errors/49 skipped，失败/错误与2152项基线一致、无新增；Ruff/Node/diff-check通过。查询计划使用 `idx_face_person_links_person`；243人合成热缓存中位数 ID/照片数排序0.353/1.238 ms。按用户要求在本会话直接审查；自动归组并发期间无快照保证，详情见报告。未访问PhotoSearchData或真实浏览器。

## 前置完成：P4-PERSON-ALBUM-PERF-A — PASS（个人相册基础功能）

[执行卡](tasks/P4-PERSON-ALBUM-PERF-A.md)、[报告](reports/P4-PERSON-ALBUM-PERF-A-implementation.md)。按用户限定完成合成相册基本浏览与定向验证；不追认全仓、真实图库或发行通过。全量基线2152/50 failures/43 errors/49 skipped保留。

## 前置人物交接：P4-PERSON-DETAIL-B — 工程与Sol聚焦审查已通过

[报告](reports/P4-PERSON-DETAIL-B-review.md)、[Sol聚焦终审卡](tasks/P4-PERSON-DETAIL-B-sol-review.md)。既有人脸身份/人工状态审查闭环通过；未验真实图库、现场catalog升级、GPU/吞吐。人物列表逐人物查询属于未测量的另线问题，不自动接续。

## 待处理闭环

- P4-PERSON-AVATAR-FIX-A：[卡](tasks/P4-PERSON-AVATAR-FIX-A.md)，需源码服务重启后现场确认。
- P4-SEARCH-NL-UX-A：[卡](tasks/P4-SEARCH-NL-UX-A.md)，认证浏览器交互待补。
- P4-PHOTO-VIEW-A：[卡](tasks/P4-PHOTO-VIEW-A.md)、[报告](reports/P4-PHOTO-VIEW-A-review.md)，主卡完整浏览器验收待补。
- P4-PERSON-DETAIL-A 与 P4-PERSON-UX-A：独立审查未关闭，分别见各自 review 卡；PERSON-DETAIL-B审查不追认它们整卡PASS。
- P4-PLACE-UNIFIED-A、P4-PLACE-CN、P4-PLACE-MAP-A、P4-02Y-L、P4-02Y-W、P4-EMBEDDING-ARCH：既有独立审查/现场验收未关闭，详见归档中的对应任务卡链接；不随本次人物排序任务自动接续。
- P4-PLACE-RECOVERY-B 与 P4-SEARCH-PLACE-NL-A：真实空库恢复/服务重启后的个人复测由用户手动完成；没有访问PhotoSearchData。
- P4-TEST-BASELINE 与地点基线卡：DEFERRED。除非用户重新要求修复，不自动修全仓历史失败。

## 人物下一步

个人相册与人物列表基础体验已完成当前授权范围，转入日常使用与具体反馈；不自动续开性能优化。其他未结事项保留原状态，不因本卡通过而关闭。本次排序为只读列表功能，SOL_NOT_REQUIRED。
