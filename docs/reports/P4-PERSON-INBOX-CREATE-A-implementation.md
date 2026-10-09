# P4-PERSON-INBOX-CREATE-A 实施报告

日期：2026-10-10。状态：**IMPLEMENTED / 任务卡定向验证通过 / 当前会话工程复核通过（非独立 DS-H/Sol） / 个人试用待确认**。

## 基线与范围

- 实施前 `HEAD`：`b7e90614c74b34af8680b6fda2eb95b565e1ab8a`；工作树已有 `.tmp/` 与 2026-10-03 retention 审计材料未跟踪内容，全部保留、未纳入本任务。
- 实际 schema 基线：27；新增连续 migration `0028_create_person_from_face.sql`，支持 schema 28。
- 未提交、未推送。没有读取或改动真实 `PhotoSearchData`，没有运行用户服务、识别、模型、GPU、打包或真实图库扫描。

## 实施

- 新增 `POST /api/v1/faces/{face_id}/create-person`，请求只有专用 `contract_version`、全局 `request_id` 和 `expected_control_revision`。事务先检查规范化 payload 的全局幂等，再按人物收件箱的可见、有效成员、忽略、重检测 hold 规则及照片有效状态检查资格。
- 复用 `_create_person` 的稳定未命名人物语义和 `face_correction.apply_one(action="assign")` 的人工归属矩阵。仅选中脸成为新人物的 `MANUAL/CONFIRMED` 初始成员，头像取该脸；通过原 `publish_derived` 更新 `PERSON_CHANGED` 派生队列，不触发图片索引、embedding 或即时全体重匹配。
- 操作记录使用独立 `CREATE_PERSON_FROM_FACE` 类型，只保存一个 `FACE_CONTROL` 快照和 `target_person_id`。新类型明确不进入可撤销写入集合，旧 undo API 返回 `PERSON_OPERATION_NOT_UNDOABLE` 且零写入。历史列表标记“新建未命名人物 · 不支持撤销”。
- 0028 在扩展闭合操作类型时，保留既有 `FACE_MAPPING_GROUP` 明细、人物别名操作引用及 schema 27 retention 回执。收件箱按钮仅从非批量、非忽略的收件箱脸进入；成功后清除快捷撤销状态，刷新人物和待整理视图并尽量保留展开的候选组。未知网络结果复用同一 `request_id`；4xx 冲突不冒报成功。`kind=all` 不显示已空的未命名人物，但稳定 ID 与历史记录保留。

## 验证

- 后端/API 与旧撤销回归：
  `uv run -- python -m pytest -q tests/integration/test_p4_person_inbox_create.py tests/integration/test_p3_face_correction_undo.py tests/integration/test_p3_person_undo.py tests/integration/test_p3_person_split_undo.py --junitxml=test-results/P4-PERSON-INBOX-CREATE-A-backend.xml --basetemp=.pytest-tmp/P4-PERSON-INBOX-CREATE-A-backend` → **40 passed, 0 failed, 0 errors, 0 skipped**。
- Migration 回归：
  `uv run -- python -m pytest -q tests/unit/test_catalog_migrations.py --junitxml=test-results/P4-PERSON-INBOX-CREATE-A-migrations.xml --basetemp=.pytest-tmp/P4-PERSON-INBOX-CREATE-A-migrations` → **13 passed, 0 failed, 0 errors, 0 skipped**。另有 0027→0028 保真夹具覆盖 retention 回执、别名引用、`FACE_MAPPING_GROUP` 明细与新操作类型。
- 前端轻量 DOM：
  `uv run -- python -m pytest -q tests/unit/test_person_inbox_create_ui.py --junitxml=test-results/P4-PERSON-INBOX-CREATE-A-frontend.xml --basetemp=.pytest-tmp/P4-PERSON-INBOX-CREATE-A-frontend` → **1 passed, 0 failed, 0 errors, 0 skipped**。覆盖“这是谁？”/“选其他人”共用入口、单脸请求字段、快速重复点击、未知网络结果复用 request ID、成功清除撤销条与刷新、409 保留对话框，以及历史页旧撤销接线。
- 受影响 Python 文件 Ruff、两个人物页面 `node --check`、受影响 Python `py_compile`、`git diff --check`：全部通过。

## 复核修复与验证

- 当前会话复核发现 0028 遇到旧 `UNDO` 自引用链时无法 DROP 操作表。修复后 migration 暂时断开旧表自引用并从备份恢复；回归夹具现覆盖 RENAME→UNDO 父子引用。
- 修复回归 + migration 单元组：16 passed, 0 failed, 0 errors, 0 skipped；JUnit：`test-results/P4-PERSON-INBOX-CREATE-A-review-fix.xml`。Ruff、py_compile、diff-check 通过。完整复核记录：[当前会话复核报告](P4-PERSON-INBOX-CREATE-A-review-current-session.md)。

## 未验证与下一步

- 未运行全仓套件、浏览器矩阵、真实 catalog 升级或真实人物照片试用；不声称整库/发行 PASS。用户仍需按任务卡少量个人试用确认。
- 本实现触及人物身份、人工归属、幂等、事务回滚、操作历史和 schema。用户指定当前会话复核已通过，未冒称独立 DS-H/Sol；真实小样本试用仍待用户确认。
