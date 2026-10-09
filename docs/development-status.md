# Development status

更新：2026-10-09。当前规则：主架构需求修订 9、执行策略修订 12 / §9.4个人待审去重。当前简要交接；较早逐项记录归档于[2026-10-09 快照](development-status-archive-2026-10-09.md)及[2026-09-27 归档](development-status-archive-2026-09-27.md)。

## 本次完成：待审任务个人范围优化（仅文档）

[共同规则与覆盖表](plans/P4-PERSONAL-REVIEW.md)、[调整报告](reports/P4-PERSONAL-REVIEW-optimization.md)。22张原主卡统一为五组；旧review卡转索引，复用已独立通过的共享面，证据缺口才补小型基础验证；不默认全量/30万/GPU/打包/固定多轮Sol。AGENTS、策略、task-launcher已同步。没有执行新工程审查或pytest，没有因此新增产品任务PASS。

## 最近完成：P4-PERSON-SORT-A — PASS（本地 A 级范围与当前会话审查）

[执行卡](tasks/P4-PERSON-SORT-A.md)、[报告](reports/P4-PERSON-SORT-A.md)。保留旧 `people1` 游标并为照片数排序使用独立前缀；排序聚合计数复用于当前页摘要。定向JUnit 18/0/0/0；完整单元/集成 2154/2012 passed/50 failures/43 errors/49 skipped，失败/错误与2152项基线一致、无新增；Ruff/Node/diff-check通过。查询计划使用 `idx_face_person_links_person`；243人合成热缓存中位数 ID/照片数排序0.353/1.238 ms。按用户要求在本会话直接审查；自动归组并发期间无快照保证，详情见报告。未访问PhotoSearchData或真实浏览器。

## 前置完成：P4-PERSON-ALBUM-PERF-A — PASS（个人相册基础功能）

[执行卡](tasks/P4-PERSON-ALBUM-PERF-A.md)、[报告](reports/P4-PERSON-ALBUM-PERF-A-implementation.md)。按用户限定完成合成相册基本浏览与定向验证；不追认全仓、真实图库或发行通过。全量基线2152/50 failures/43 errors/49 skipped保留。

## 前置人物交接：P4-PERSON-DETAIL-B — 工程与Sol聚焦审查已通过

[报告](reports/P4-PERSON-DETAIL-B-review.md)、[Sol聚焦终审卡](tasks/P4-PERSON-DETAIL-B-sol-review.md)。既有人脸身份/人工状态审查闭环通过；未验真实图库、现场catalog升级、GPU/吞吐。人物列表逐人物查询属于未测量的另线问题，不自动接续。

## 待审/待验入口（均未因文档调整关闭）

| 入口 | 当前缺口与复用 |
|---|---|
| [人物组](tasks/P4-PERSONAL-REVIEW-PERSON-A.md) — 下一任务 | UX/DETAIL-A及MATCH/媒体/整理/封面/预览未审新增面一次核对；复用DETAIL-B工程＋Sol保护。AVATAR正常重启/刷新后的现场确认保留。 |
| [地点组](tasks/P4-PERSONAL-REVIEW-PLACE-A.md) | CN/UNIFIED/MAP/包恢复/兼容/地点NL合并；MAP已修短审复用，RECOVERY-B缺前驱运行时基础证据在隔离小包核对。真实现场重启由用户自行决定，不清空私库验收。 |
| [搜索与查看组](tasks/P4-PERSONAL-REVIEW-SEARCH-A.md) | W/W-Q/NL未审面与缺失基础浏览器；PHOTO-VIEW已有用户接受的独立GPT-6复核及六入口证据，不重开独立审查或全浏览器矩阵。 |
| [OCR](tasks/P4-02Y-L-review.md) | 独立工程证据核对；旧Sol冻结与CPU/GPU实验复用，不重复冻结/测速。 |
| [模型切换](tasks/P4-EMBEDDING-ARCH-review.md) | 独立工程面待审；维护select-profile与UpgradeRun门禁适用性若仍未裁决，保留一次Sol短裁决。 |

P4-TEST-BASELINE及地点基线修绿卡保持DEFERRED；历史全量失败照实保留，不默认修全仓。基础关键失败不得跳过。已有PASS任务不重开，不自动继续性能优化。

## 下一步

只交接[人物组](tasks/P4-PERSONAL-REVIEW-PERSON-A.md)：推荐独立DS-H/高推理，由用户在Codex手动选择后开始；本轮停止在文档优化。逐原卡列证据覆盖与剩余，仅缺口补测试；完成后交接地点组，不自动执行其他组。若已有实际使用错误，可由用户指定先审对应组。
