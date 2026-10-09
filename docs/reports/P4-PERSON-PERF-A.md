# P4-PERSON-PERF-A — 人物批次重复索引收尾优化

日期：2026-10-09。状态：**IMPLEMENTED / 定向验证通过 / 随DETAIL-B独立审查闭环通过**。基线 HEAD `d4f7720`。保留工作区中无关的搜索、地点与照片信息改动；此次提交仅收录人物性能实现及审查闭环。

## 变更

人物 source-detail batch runner 每张资产处理后会先执行 `FaceIndexCoordinator.process_all`，再调用 `reconcile`，随后无条件再次执行 `process_all`。归人产生更新时，`reconcile` 已在内部发布这些更新；没有更新时也没有新 outbox。runner 的最后一次调用因此是每张资产一次空队列检查和 generation-ready 写事务。移除此重复收尾调用，保留进入 `reconcile` 前的候选发布以及 reconcile 本身对已产生候选的发布。

回归测试为原有的失败继续/只重试失败用例加了 `process_all` 调用计数：首轮3张资产（2成功、1失败）调用3次，失败项重试完成后累计4次；批次依然从 `PARTIAL_FAILED` 到 `COMPLETE`，失败资产只重新推理一次。每张照片只剩一个必需的 reconcile 前发布调用。此计数证实了无效调用被移除，不是端到端处理吞吐测量。

同一直接测试文件里的schema升级夹具原先只从schema20倒退到schema19，但当前HEAD已支持schema21。将夹具回退时一并移除0021新增表，并验证升级从19到当前21且两张表恢复；这是让现有升级回归与当前主线一致的测试维护，不改产品迁移。

## 验收

- Python 3.12.10 定向集：`tests/integration/test_p4_person_detail_b.py` 与 `tests/integration/test_p4_person_source_detail.py`；JUnit `test-results/P4-PERSON-PERF-A.xml`：**16 tests / 0 failures / 0 errors / 0 skipped**，退出码0。
- Ruff：`src/photo_search/api/face_detail_runner.py`、`tests/integration/test_p4_person_detail_b.py` 通过，退出码0。
- scoped `git diff --check` 通过，退出码0。
- 调试中新增调用数预期首次写错，按 isolated 重跑确认实际当前调用数为3/4后修正；初次全定向运行另发现schema19夹具仍断言schema20，已按schema21修正夹具。失败XML留在 `test-results/P4-PERSON-PERF-A-count.xml`、`...-failures*.xml`，最终证据以最终XML为准。

## 边界与交接

只使用合成测试库，没有打开真实 catalog、`PhotoSearchData`、私人照片或模型；没有测30万张批次吞吐、CUDA、长期运行或浏览器，因此不宣称人物识别速度或个人试用门槛达标。此次优化仅保证少掉一轮固定 SQLite 发布收尾调用/资产；实际耗时收益需由真实试用或与该路径同形的规模基准确认。

既有高风险身份、跨profile候选及人工保护已按[P4-PERSON-DETAIL-B-review](../tasks/P4-PERSON-DETAIL-B-review.md)完成独立DS-H完整受影响面主审及Sol high聚焦终审，审查 **PASS**。两阶段均覆盖本次runner删除；证据和限制见[主审报告](P4-PERSON-DETAIL-B-review.md)及[聚焦终审卡](../tasks/P4-PERSON-DETAIL-B-sol-review.md)。
