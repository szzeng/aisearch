# P4-PERSON-INBOX-CREATE-A-REVIEW — 独立受影响面工程复核

日期：2026-10-10。状态：**当前会话工程复核通过 / F1 已修复**。复核报告：[当前会话复核](../reports/P4-PERSON-INBOX-CREATE-A-review-current-session.md)。该复核不是独立 DS-H 结论。实施报告：[P4-PERSON-INBOX-CREATE-A-implementation.md](../reports/P4-PERSON-INBOX-CREATE-A-implementation.md)。

## 目标与门槛

独立复核 P4-PERSON-INBOX-CREATE-A 的完整变更面与任务卡验收证据，确认该新人物写操作在 schema 28 上保持单脸身份、全局幂等、SQLite 原子回滚、人工状态保护、retention 兼容和永久不可撤销。按 V3.1 §2.4 做完整受影响面审查；这不是全仓审查。

推荐：**DeepSeek V4.1 Flash / High（DS-H）**。用户需在 Codex 中手动切换模型和推理档。普通单点问题不升级 DS-MAX；仅当 V3.1 §2.2 六项中某项有具体证据时升级并写明未决问题。

## 复核范围

完整阅读并核对当前工作树中的任务新增差异、调用链和证据：

- DB/API：`src/photo_search/catalog/migrations/0028_create_person_from_face.sql`、`schema.py`、`api/models.py`、`api/routes.py`。
- 人物协议/事务：`persons/create_from_face.py`、`service.py`、`face_correction.py`、`operations.py`、`contracts.py`、`albums.py`、`read.py`。
- UI：`web/persons-albums.js`、`web/persons.html`、`web/persons.js`。
- 测试：`tests/integration/test_p4_person_inbox_create.py`、`tests/unit/test_person_inbox_create_ui.py`、`tests/unit/test_catalog_migrations.py`；核验报告所列既有纠错/撤销回归测试及 JUnit 产物。
- 数据语义重点：request_id 全局占用和先幂等后状态检查；仅 face_id 入新人物；`MANUAL_UNASSIGNED` 与低质量显式选择；已归属/忽略/旧替代脸/OPEN hold/stale revision 的零写拒绝；`apply_one` before/after 记录和统一 `operation_id`；派生发布失败整体回滚；新类型 undo 零写拒绝；压缩回执后的重放；0028 对 schema27 映射明细/别名/retention 引用的保真；空未命名人物从 `kind=all` 选项中隐藏；前端未知网络重试 ID、成功清除旧撤销状态与409失败提示。

## 工作方式与输出

1. 已在当前会话审阅改动文件、直接调用链、协议与任务卡要求，并交叉核对三个 JUnit XML 及实施报告中的 Ruff、Node、py_compile、diff-check 证据。
2. F1 已修复：0028 删除旧表前临时断开自引用，并从备份恢复；新增 RENAME→UNDO 外键开启迁移夹具。定向 migration + integration JUnit 16/0/0/0，Ruff、py_compile、diff-check 通过，详见复核报告。
3. 报告覆盖范围、结论、未决项、升级判断和 `SOL_NOT_REQUIRED` 是否适用。若发现协议/数据安全未决或按 V3.1 §11 需要最终冻结，单独提出 Sol High 聚焦复核建议；不要把 DS-H 结论冒称 Sol 结论。
4. 不访问真实 `PhotoSearchData`、不启动服务、不跑全仓/30万/GPU/模型/打包/浏览器矩阵，不提交或推送。

用户要求在当前会话复核；本结论是当前会话工程复核通过，不冒称独立 DS-H 或 Sol。未访问真实 catalog/人物照片；浏览器与真实个人试用仍由用户决定。
