# P4-LIBRARY-RETENTION-C2-B 执行报告

## 目的与范围

增加跨重启持久的资产清理屏障，并阻止旧任务、派生发布、搜索投影和候选缓存重新发布该资产。C2-B 不执行资产或人工数据删除，也不生产/消费 `DELETE_VECTOR`。

## 实现

- Schema 0039 新增 `asset_retention_barriers`，以 `asset_id` 主键保存 ACTIVE/COMPLETE tombstone，刻意不关联 `assets` 外键，保证资产行删除后 tombstone 仍存在。
- 激活屏障的 SQLite 事务会取消目标资产的 QUEUED、RETRY_WAIT、BLOCKED、RUNNING job 并清空 lease；enqueue/claim/retry/candidate selection 与 scheduler 最终提交均检查屏障。资产 subject 仅使用精确正整数十进制格式绑定到 barrier，`904abc`、`00904` 等 opaque subject 不会被 SQLite CAST 混淆。
- embedding 索引 outbox、face outbox、FTS 文档、智能合集候选、face 候选发布路径在写入/确认边界检查屏障；屏障资产不再贡献搜索投影可执行积压或 smart freshness 未完成状态。物理队列行可留存至后续数据清理阶段。
- 0039 迁移回滚、schema 38 旧库升级和旧库人工收藏保留有隔离测试；地点包兼容审阅上限推进至 schema 39。

## 验证证据

- 最终受影响组合：`test-results/P4-LIBRARY-RETENTION-C2-B-affected-final-r5-20261020.xml`，241 项、0 failed、0 errors、0 skipped；实际 exit 0。包括新库/旧库/失败回滚，scheduler job/replay，FTS summary/catch-up，smart collection、backup、scan、地点包兼容及 1000 条合成屏障写入与主键查询计划。
- 全量 unit/integration：`test-results/P4-LIBRARY-RETENTION-C2-B-full-20261020.xml`，3372 项、31 failed、0 errors、52 skipped。31 个失败项均为 API/UI/search/person 测试名称；相较 C2-A 全量运行的失败集合无新增名称，30 个原失败本轮未复现。该全量运行不是 PASS。最终针对三个 review findings 的修复由最终 241 项组合覆盖，按全量加增量组合证据记录。
- Ruff、Python 3.12 `py_compile`、Node `--check`、`git diff --check` 通过。
- 用户指定 Luna High 独立复核完整受影响发布面。三项 P2 finding（不透明 asset subject 误匹配、屏障队列导致永久 LAGGING、projection summary 漏过滤）均修复；最终跟进无新增 blocker。该复核不是 DS-H。

## 未验证边界与后续

本阶段未写入/删除 Lance 向量，不验证真实 Lance crash replay；没有开放一键或自动清理。C2-C 将实现 DELETE_VECTOR 生产消费及实际 Lance 删除/重复回放/代际隔离。未访问真实照片库、未运行 GPU/模型或 30 万照片测试。父目标仍 **IMPLEMENTED / ACCEPTANCE PARTIAL**；改动未提交、未推送。
