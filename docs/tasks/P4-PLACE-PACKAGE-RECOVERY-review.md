# P4-PLACE-PACKAGE-RECOVERY — DS-H 独立审查卡

执行模型：DS-H；对地点包注册、校验、激活和恢复的完整受影响面进行独立审查。模型切换由用户手动完成。

## 必读

1. `AGENTS.md`、`docs/development-status.md`、实现卡与实现报告。
2. `docs/decisions/ADR-0005-place-facts-assignment-and-search-contract.md` 的数据包/生命周期相关章节、`docs/reports/P2-03B-offline-name-dataset-and-nearby-city-suggestions.md` §3。
3. `src/photo_search/places/datasets.py`、`src/photo_search/geo/dataset.py`、`tools/places/manage_dataset.py`、`tests/integration/test_p2_dataset_lifecycle.py`。

## 审查问题

- 同规范路径判断是否覆盖 Windows 大小写、相对路径与解析路径，且没有绕过数据根安全边界？
- 跳过复制时是否仍完整校验包内容，并保持原子写入 catalog、VALIDATED/ACTIVE 转换和激活失败回滚语义？
- 针对只读、现存包恢复的回归证据是否充分，是否有同路径/链接/损坏包反例？
- 是否存在必须先停服务或额外保护用户数据的操作前提？

## 结果要求

审查完整改动和本卡验证证据，报告 PASS/FAIL/阻塞及具体证据。发现状态机或事务风险时扩展审查范围；不要重跑全量套件，除非发现有证据的影响面需要它。
