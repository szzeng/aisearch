# Development status

## Current: 搜索质量实测与改进（完成）

2026-10-16，以远端 `200c891` 为基线，分支 `codex/search-quality`。PR #5 已合并，此次同步的额外上游内容仅为任务卡文档。当前 [搜索质量任务卡](tasks/P4-SEARCH-QUALITY.md)完成：修复中文专名被删字、OCR/跨字段命中来源遗漏，并优化全文查询计划；模型默认值及存储格式未变。

受影响后台验证 538 通过、19 条件跳过，exit 0（约 5 分钟；跳过因隔离数据根缺少真实 OCR 权重）；三套工具测试共 39 通过，30 万条基准 2 场景通过，静态检查及受影响面审查完成。此为受影响验证，不是一次最终全仓回归。公开中文图片实验和自然句查询的取舍见 [本轮报告](reports/P4-SEARCH-QUALITY.md)。原始数据/JUnit/指纹位于忽略的 `test-results/search-quality-20261016/`、`.tmp/search-quality-20261016/`，不访问真实图库。

后续候选是扩大图文/OCR联合质量集、验证双视图与向量维度、分阶段定位 Qwen 冷加载；本轮不自动改模型或重建索引。上游 [人物整理草案](tasks/P4-PERSON-ORGANIZE-UX-CLEANUP.md)保持 DRAFT，未启动。

上一轮的真实双页面版本冲突浏览器验收仍因原生弹窗工具限制为 PENDING，保留在 [合流余项卡](tasks/P4-UPSTREAM-REVIEW-REMAINDER.md)；本轮不冒充关闭该项或宣布正式发布通过。
