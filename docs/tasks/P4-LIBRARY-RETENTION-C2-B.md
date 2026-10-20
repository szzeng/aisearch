# P4-LIBRARY-RETENTION-C2-B — 持久清理屏障与派生发布拒绝

**状态**：PASS（限定 C2-B，组合证据）；父目标 `P4-LIBRARY-RETENTION-C2` 仍 IMPLEMENTED / ACCEPTANCE PARTIAL。
**基线**：承接 C2-A 工作树变更；不提交；不访问真实图库。
**目标**：建立可跨进程重启读取的资产清理屏障，使旧任务、派生流水线和搜索投影不能在清理开始后重新发布目标资产。当前阶段只建立屏障与阻断，不删除任何数据。

## 必读范围

- V3.0 §5.1–5.2、§6、§7、§10.2、§11.2/11.4、§12.4–12.5；V3.1 §2.4、§3.2/3.4、§4.2、§11。
- `P4-LIBRARY-RETENTION-C2-A.md` 与 C2-A 报告。
- 接口/发布路径：`jobs/queue.py`、`scheduler/engine.py`、`artifacts/store.py`、`indexing/semantic.py`、`indexing/store.py`、`search/documents.py`、`media/pipeline.py`、`media/cache.py`、`ocr/processor.py`、`geo/extract.py`、`face/processor.py`、`face/index.py`、`persons/mapping.py`、`photo_smart_collections.py`，以及其直接 worker/repair/重试入口和对应 tests。

## 本卡冻结语义

1. 持久 barrier 的身份为全局 `asset_id`；必须独立于将来可能删除的 `assets` 行，并在正常应用重启/任务重放后继续阻断发布。
2. 任何 barrier 写入只会发生在下一阶段获准执行清理时；本阶段通过测试显式建立 barrier，并拒绝其后的 enqueue、claim、artifact/机器派生事实发布、FTS/search projection 和候选缓存写入。
3. barrier 建立前已有的运行中任务和旧 outbox/repair 也必须在提交/确认边界再次检查；只在 HTTP/CLI 入口检查不够。
4. barrier 检查不使用进程内锁作为唯一保障；一个 SQLite 事务内的 barrier 状态读取与目标发布必须原子化。外部存储写入前后无法原子化的部分须留下可安全重放状态，具体向量删除/回执合同在 C2-C 冻结。
5. 清理/删除事件的未来通行规则由 C2-C 明确；本阶段不创建 `DELETE_VECTOR` 生产者，也不得通过宽泛豁免绕过普通派生发布拒绝。

## 性能设计

- 链路与风险：扫描/排队、worker 领取、artifact/outbox、搜索投影与 face/OCR/GPS 发布都可能重新引入目标资产，风险是热写路径逐目标额外查询及事务竞态。
- 做法与边界：barrier 按 asset_id 主键点查并复用既有最终事务；不得全库扫 barrier 或在 Python 中加载资产集合；不做向量表遍历。
- 验证与停止：定向覆盖各发布边界、运行中 worker 迟到提交、重放、重启；1000 条合成屏障写入后确认资产主键点查计划，出现屏障漏点或全表扫描即停止，不做全库/30 万基准。已覆盖 1000 条合成写入与 `INTEGER PRIMARY KEY` 点查计划；此证据不构成延迟保证。

## 验证与审查

- 增加连续 migration（若新表/触发器需要）；迁移遵循 database-migrations.md 的新库、冻结旧库升级、失败回滚检查。
- 全量评估被屏障影响的共享消费者；若 migration 修改数据语义，按 V3.1 §3.2/3.4 执行 unit/integration 完整回归，并保留 JUnit XML 和实际退出码。
- 保留原有纯机器处理、任务幂等、修复/重放行为；屏障仅对明确的资产目标生效。
- Ruff、Python 3.12 编译、Node `--check`（如触及 UI）、`git diff --check`。
- 用户指定 Luna High 子代理独立复核完整受影响发布面、事务竞态和所需证据；finding 修复并跟进复核。

## 结果

- Schema 0039 新增无 `assets` 外键的持久 tombstone，资产行删除后仍保留；屏障激活会在同一事务取消目标资产的排队/重试/阻塞/运行任务并清空租约。屏障表位于 schema 39；地点包兼容审阅推进至 39，未来版本仍受上限约束。
- enqueue、claim、retry、scheduler 最终提交、索引/face outbox、FTS 文档和 smart/face 候选发布在对应 SQLite 边界检查 tombstone。任务 ID 只接受精确正整数十进制字符串与资产 ID 关联；不透明 subject 和带前导零字符串不与其他资产混淆。
- 屏障资产的搜索投影队列物理行可留待后续清理，但不再计入可执行队列深度、catch-up、搜索/备份/调度摘要或 smart 集合 freshness；普通投影仍按原合同处理。
- 新库迁移、冻结 schema 38 旧库升级、0039 故障回滚通过；旧库资产与人工收藏完整保留，回滚后无屏障表/外键不一致。
- 最终受影响组合 JUnit：`test-results/P4-LIBRARY-RETENTION-C2-B-affected-final-r5-20261020.xml`，**241 项，0 failed，0 errors，0 skipped**；实际 exit 0。覆盖 migrations、retention、scheduler、search projection、smart collection、backup、scan、place compatibility 与 storage UI。
- 完整 `tests/unit tests/integration` 运行：`test-results/P4-LIBRARY-RETENTION-C2-B-full-20261020.xml`，3372 项、31 failed、0 errors、52 skipped。31 个失败名均属于 API/UI/search/person 现有范围；与 C2-A 全量运行的失败集合相比没有新增失败名，30 个此前失败项本次未复现。此全量执行不是 PASS。随后修复的三个 Luna H finding 已由 241 项受影响组合覆盖，采用全量基线加最终受影响增量证据。
- 最终 Ruff、Python 3.12 编译、Node `--check`、`git diff --check` 均通过。Luna High 完整受影响面复核指出 opaque ID 混淆、残留队列误报积压、projection summary 漏过滤三个 P2 finding；全部修复，最终跟进确认无新增 blocker。

## 排除范围

- 本卡不删除 `assets`、人工记录、OCR/GPS/face、FTS、Artifact、缓存或任何文件；不修改源照片。
- 不生产或消费 `DELETE_VECTOR`；不修改 LanceDB，也不声称旧 UPSERT 的向量回放已被删除事件修复。
- 不开放用户清理 UI/按钮或自动清理调度；由 C2-C 及后续阶段继续完成端到端闭环。

## 后续闭环

进入 C2-C：实现 `DELETE_VECTOR` 生产者/消费者与 LanceDB 实际删除；覆盖 vector write 成功而 SQLite 确认前崩溃、重复回放、代际隔离及 tombstone 的幂等安全。随后继续 C2-D 至 C2-F。父目标保持 **IMPLEMENTED / ACCEPTANCE PARTIAL**。
