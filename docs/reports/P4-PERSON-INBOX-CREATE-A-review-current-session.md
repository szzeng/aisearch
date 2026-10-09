# P4-PERSON-INBOX-CREATE-A 当前会话受影响面复核

日期：2026-10-10。复核范围：当前工作树中的 P4-PERSON-INBOX-CREATE-A 完整变更面；状态：**当前会话工程复核通过 / F1 已修复并定向回归通过**。本结论按用户要求在当前会话完成，**不是独立 DS-H 或 Sol 结论**。

## 范围与证据

核对了 API request/response 与路由、PersonService 和单脸创建用例、face correction 资格及控制矩阵、inbox eligibility SQL、person 操作族/undo/history/retention、schema 27→28 migration、人物列表空组过滤、人物页入口与 unknown-outcome 重试、三组 JUnit XML 与任务报告。

现有 XML 汇总：backend regression 40 tests / 0 failures / 0 errors / 0 skipped；migration 13/0/0/0；frontend 1/0/0/0。前一实施报告中 Ruff、Node、py_compile、diff-check 均通过；本次没有机械重跑已通过套件。

本次另外执行了一次只使用内存 SQLite、加载 migration 0001–0027 的最小迁移探针：插入一个已提交 `RENAME` 父操作及一条 `UNDO` 子操作（`undo_of_operation_id` 指向父项），启用 `PRAGMA foreign_keys=ON` 后执行 0028。执行在 `DROP TABLE person_operations` 失败，错误为 `sqlite3.IntegrityError: FOREIGN KEY constraint failed`。

## 阻断发现

### F1 — 0028 无法升级含有历史 UNDO 链的 schema 27 数据库（已修复）

严重度：**P1 / 升级阻断**。

位置：[0028_create_person_from_face.sql](../../src/photo_search/catalog/migrations/0028_create_person_from_face.sql) 第 11 行；当前合成迁移覆盖位于 [test_p4_person_inbox_create.py](../../tests/integration/test_p4_person_inbox_create.py) 第 372 行起。

迁移复制表后先删除 `person_operation_items`、retention，再执行 `DROP TABLE person_operations`。`person_operations` 存在指向同一表的 `undo_of_operation_id` / `undone_by` 外键；当旧库有 undo 子行时，SQLite 在启用外键的情况下拒绝删除父表。项目 schema migration 使用启用外键的连接，因此这不是仅限直接脚本执行的差异。

最小反例：schema 27 中有 `RENAME` 操作 A 及 `UNDO` 操作 B，B.`undo_of_operation_id` = A.`operation_id`。加载实际 migration 0001–0027 并插入这两行后运行 0028，失败点为 `DROP TABLE person_operations`。现有名为 `...preserves_retention_receipt_alias_and_undo_refs` 的测试只写入 RENAME 与映射操作、retention 和 alias 引用，没有实际 `UNDO` 子行，故漏过该失败路径。

修复已落在 migration 0028：先在待删除的旧表暂时将 state 归为 COMMITTED 并清空两列自引用，之后从无约束备份完整恢复原值。迁移集成夹具现在包含 RENAME 父项（UNDONE）与 UNDO 子项，并断言 `undo_of_operation_id`、`undone_by`、state、alias、retention、mapping item 均保留且 `PRAGMA foreign_key_check` 为空。`tests/integration/test_p4_person_inbox_create.py` + `tests/unit/test_catalog_migrations.py` → **16 passed, 0 failures, 0 errors, 0 skipped**，JUnit：`test-results/P4-PERSON-INBOX-CREATE-A-review-fix.xml`；Ruff、py_compile、diff-check 通过。

## 其余复核结论

- 单脸身份边界、全局 request_id 幂等优先级、expected revision、可见/有效成员/hidden/hold/资产状态资格，以及原子回滚调用顺序与任务卡一致。
- `apply_one(assign)` 只处理指定 face_id；低质量显式选择与 MANUAL_UNASSIGNED 路径有定向覆盖。operation 使用唯一 FACE_CONTROL item，派生发布纳入同一写事务。
- 新操作类型明确不在 undoable 集合中，undo 入口先处理请求幂等后拒绝该类型；history label 隐去 undo 控件；retention replay 回执路径有集成覆盖。
- 人物页两个待整理入口传入单脸；未知网络失败保留 request_id，成功撤掉旧 undo 状态并刷新列表；这些路径有轻量 Node DOM 测试。

以上仅为代码与合成证据。真实 catalog 升级、浏览器矩阵、真实人物照片试用未验证；没有访问 PhotoSearchData 或用户服务。F1 已关闭；当前会话复核没有发现其它阻断项。此结论不是独立 DS-H/Sol 结论，也不代表真实 catalog 升级、浏览器矩阵或个人照片验收。修复只改 migration SQL 与其合成迁移夹具。
