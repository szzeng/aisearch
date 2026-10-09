# P4-PERSON-INBOX-CONFIRM-A 执行报告

日期：2026-10-09
状态：**PASS / PERSONAL-SCOPED REVIEW（用户指定当前会话简审）**
基线：`65bec73 feat(persons): group inbox faces by suggested person`

## 结果

候选单脸卡片现在直接显示“确认是妈妈”，调用既有 `PUT /faces/{face_id}/assignment` 单脸 `assign` 操作，并携带候选 `person_id`、卡片的 `control_revision`、request ID 和 CSRF。保留“选其他人”处理误建议；没有建议的卡片仍显示“这是谁？”。成功后使用原操作反馈、撤销条和人物/收件箱刷新路径。

没有修改后端、数据模型、身份判定或人工写入语义。确认仍只针对该 `face_id`，合照中的其他脸不变。

## 验证

命令：`uv run --no-sync pytest -q tests/unit/test_person_rematch_ui.py tests/integration/test_p4_person_albums.py::test_single_face_correction_leaves_neighbour_alone_and_undo_restores --basetemp=.pytest-tmp/p4-person-inbox-confirm-final --junitxml=test-results/P4-PERSON-INBOX-CONFIRM-A-final.xml`

结果：**2 passed，0 failures/errors/skips**。JUnit：`docs/reports/P4-PERSON-INBOX-CONFIRM-A.xml`。

`uv run --no-sync ruff check tests/unit/test_person_rematch_ui.py`、`node --check src/photo_search/web/persons-albums.js`、`git diff --check` 均退出码 0。

## 审查边界

本会话按用户指定的个人项目简审，核对新增按钮、请求字段与复用的单脸纠错/撤销用例；未跑浏览器、全仓测试、真实图库/服务或模型。结果不代表这些范围验收。未更改前序任务状态。后续仅在日常使用遇到具体反馈时再开新任务。
