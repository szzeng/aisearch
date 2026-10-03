# P4-PLACE-PACKAGE-RECOVERY — DS-H 独立审查卡

执行模型：DS-H；对地点包注册、校验、激活和恢复的完整受影响面进行独立审查。模型切换由用户手动完成。

## 必读

1. `AGENTS.md`、`docs/development-status.md`、实现卡与实现报告。
2. `docs/decisions/ADR-0005-place-facts-assignment-and-search-contract.md` 的数据包/生命周期相关章节、`docs/reports/P2-03B-offline-name-dataset-and-nearby-city-suggestions.md` §3。
3. `src/photo_search/places/datasets.py`、`src/photo_search/geo/dataset.py`、`src/photo_search/ingestion/service.py`、API lazy service factory、`tools/places/manage_dataset.py`、`tests/integration/test_p2_dataset_lifecycle.py`、`tests/integration/test_place_package_startup_recovery.py`。
4. `docs/decisions/ADR-0015-place-package-catalog-recovery.md`；以第二轮自动启动恢复证据为准，不能只复核第一轮手动导入。

## 审查问题

- 同规范路径判断是否覆盖 Windows 大小写、相对路径与解析路径，且没有绕过数据根安全边界？
- 跳过复制时是否仍完整校验包内容，并保持原子写入 catalog、VALIDATED/ACTIVE 转换和激活失败回滚语义？
- 针对只读、现存包恢复的回归证据是否充分，是否有同路径/链接/损坏包反例？
- 是否存在必须先停服务或额外保护用户数据的操作前提？
- 注册表全空时唯一包自动恢复的边界、已有未激活选择、多包/链接/缺件/不兼容拒绝和并发检查是否正确？真实 API 冷启动、合成删除 catalog/vectors 与事务故障是否完整覆盖？

## 结果要求

审查完整改动和本卡验证证据，报告 PASS/FAIL/阻塞及具体证据。发现状态机或事务风险时扩展审查范围；不要重跑全量套件，除非发现有证据的影响面需要它。

本轮有新的启动自动激活语义；DS-H 先收敛 ADR-0015、原子注册/物化和损坏/多包的边界证据。正式冻结恢复语义前生成 Sol high 聚焦终审卡，只审这些不变量和 DS-H 未决项。不得把实现者自检当作独立审查或提前标总任务 PASS。
