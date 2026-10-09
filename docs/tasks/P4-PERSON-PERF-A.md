# P4-PERSON-PERF-A — 消除人物批次每张照片的重复索引收尾

日期：2026-10-09。状态：**IMPLEMENTED / 定向验证通过 / 随DETAIL-B独立审查闭环通过**。HEAD 基线 `d4f7720`。用户明确指定完成本任务；当前 worktree 含无关搜索/地点/照片信息修改，已保留。结果见[实现报告](../reports/P4-PERSON-PERF-A.md)及[DETAIL-B独立审查报告](../reports/P4-PERSON-DETAIL-B-review.md)。

## 目标与范围

主要瓶颈：`FaceDetailRunner._run_batch` 对每个资产先执行一次 `FaceIndexCoordinator.process_all`，随后调用 `reconcile`；`reconcile` 在归人写入变更时已自行 `process_all` 发布新候选，但 runner 仍无条件再调用一次 `process_all`。这次额外调用在空 outbox 上仍执行查询和 generation-ready 写事务，每张照片都付一次固定 SQLite 收尾成本。删除这次重复调用，保持每个资产发布待处理候选、完成归人后才能进入下一个资产的顺序。

不改识别模型、解码、阈值、人物身份/人工保护、任务队列协议、schema、索引契约、批大小或 GPU 调度。不读/扫描/写入真实图库、`PhotoSearchData` 或私人照片；只使用现有合成测试夹具。无依赖变更。

## 基线与实现

基线 HEAD：`d4f7720`。执行前确认 runner 和人物文件无其他未提交差异。现有 `P4-PERSON-DETAIL-B` 公开样片 JSON 仅用于上下文，未有批次 SQLite 开销计时；本卡不把静态调用次数当成端到端吞吐提升，不声明 30 万处理性能 PASS。

只修改 `src/photo_search/api/face_detail_runner.py` 及其直接集成测试 `tests/integration/test_p4_person_detail_b.py`，并生成本卡报告 `docs/reports/P4-PERSON-PERF-A.md`。在小型合成批次记录每资产 `process_all` 调用次数修复前后及批次状态/候选确认/归人结果；必要时调整实际修改面。不得覆盖其他会话文件。

## 必须保持

- 每资产首次 reconcile 前发布并确认该资产的所有人脸候选。
- `reconcile` 若产生新人物/关系，其内部发布仍在处理下一资产前完成；保持其 publication limit、顺序及 revision/profile 门禁。
- 失败、暂停、重试、恢复及人员人工控制语义不变。
- 输出必须明确此优化只消除重复的每资产 SQLite 收尾；小型合成回归不代表模型/GPU/30 万吞吐或真实图库表现。

## 验收

1. 给现有合成批次回归加入 `process_all` 调用计数，确认移除每个资产末尾的空队列收尾调用；首轮部分失败和失败重试后都检查批次终态及每资产推理次数。所选原有DETAIL-B测试继续覆盖候选发布、跨profile归人及人工门禁。
2. 执行：

```powershell
$env:UV_CACHE_DIR='.uv-cache'
uv run --no-sync --python 3.12.10 pytest -q tests/integration/test_p4_person_detail_b.py tests/integration/test_p4_person_source_detail.py --basetemp .pytest-tmp/person-perf-a --junitxml test-results/P4-PERSON-PERF-A.xml
uv run --no-sync --python 3.12.10 ruff check src/photo_search/api/face_detail_runner.py tests/integration/test_p4_person_detail_b.py
 git diff --check -- src/photo_search/api/face_detail_runner.py tests/integration/test_p4_person_detail_b.py docs/tasks/P4-PERSON-PERF-A.md docs/reports/P4-PERSON-PERF-A.md docs/development-status.md
```

JUnit 必须由 XML 计数及退出码确认，失败则只查失败项。仅在本任务必需验证成功后记录 `IMPLEMENTED / targeted validation passed`；不得标人物独立审查、人物模型/GPU或个人试用整体 PASS。

## 审查交接

本轮runner变更已纳入 `P4-PERSON-DETAIL-B-review` 的DS-H完整受影响面主审及Sol high聚焦终审，审查闭环 **PASS**。详见[主审报告](../reports/P4-PERSON-DETAIL-B-review.md)与[聚焦终审卡](P4-PERSON-DETAIL-B-sol-review.md)。PASS仅覆盖人物工程审查闭环，不扩大本任务的定向验证范围。

本轮结果：重复的runner归人后索引收尾已删除；source-detail批次定向JUnit **16/0/0/0**、Ruff、scoped diff-check通过。具体范围、schema21夹具调整、失败重跑记录和未验证边界见[实现报告](../reports/P4-PERSON-PERF-A.md)。30万人物批次吞吐及真实试用仍未验证；按原卡进入独立审查。
