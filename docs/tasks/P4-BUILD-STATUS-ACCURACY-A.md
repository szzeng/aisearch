# P4-BUILD-STATUS-ACCURACY-A

状态：IN PROGRESS（2026-11-27）；基线 master e9f9b4e。

按用户任务说明修复语义照片确认数、OCR 互斥状态、Worker 存活与真实进度时间、候选模型状态隔离。仅使用合成数据；不改变任务身份、租约、确认协议、调度、恢复或数据库 schema。发现必须改变上述边界时停止。

架构及策略：任务卡缺失，启动时参照 V3.0、V3.1 全文；重点 V3.1 §2.4、§3.2/3.4。直接检查 refresh、API、前端和受影响测试。

## 性能设计

- PERF-BOUNDED：来源统计与后台每批统计可能随图库及任务扇出增长；避免逐照片查询和轮询新增聚合。
- 复用来源卡的批量统计，最多每编码批次一次；按既有索引和预算检查查询路径，保持 scope、revision、generation 及状态不变量。
- 定向合成测试、受影响 JUnit、Node、真实浏览器关键流程；若出现明显规模风险再补工作量检查。不新增规模或发行性能结论。

## 验证计划

逐项复现、修复并验证 AC1–AC4；联合验证 API 兼容、暂停/恢复及状态时间；运行受影响 pytest（JUnit）、Node、Ruff、compileall、diff-check。无依赖、迁移或全量发布变更，不自动触发全套回归。UI 真实浏览器和 DS-H 独立完整影响面审查为必需，缺失保持 PENDING。报告及原始结果分别放 docs/reports/P4-BUILD-STATUS-ACCURACY-A.md 和 test-results/。不自动提交、推送、发布。

## 本次执行结果

IMPLEMENTED / VALIDATION PENDING。语义、OCR、Worker 与候选任务显示改动已实施；最终定向 34 passed / 1 skipped，受影响组合 126 passed / 3 failed（3 项在 HEAD 原始 pipeline 同样失败）。独立 DS-H 完整影响面审查及受影响状态的真实浏览器检查未完成，保留 PENDING；未实测发布包、真实模型或真实图库。详见 [执行报告](../reports/P4-BUILD-STATUS-ACCURACY-A.md)。未提交、推送或发布。


## 独立复核与后续修复

用户指定的独立 Sol 全影响面复核及修复后复审通过；2 项 P2、1 项 P3 与后续发现的异步竞态已修复。最终受影响组合 160 passed / 1 skipped，退出 0；前述 3 项原始基线失败未包含在本次重跑中且仍保留记录。原卡要求的 DS-H 审查、实际任务状态浏览器及窄窗口验收未完成，状态仍为 IMPLEMENTED / SOL REVIEW PASS / VALIDATION PENDING。详见同一报告。
