# P4-PERSON-IGNORE-STATE-A 执行报告

**日期：** 2026-10-13
**状态：** SCOPED PASS / 用户指定 LUNA High 审查完成（不是 DS-H）
**任务卡：** [P4-PERSON-IGNORE-STATE-A](../tasks/P4-PERSON-IGNORE-STATE-A.md)

## 实施

- 新增 schema 0030，在 `person_hidden` 增加 `ignore_state`（`TEMPORARY` / `EXCLUDED`）；既有忽略记录默认迁为 `TEMPORARY`，保留人物和人脸关联。迁移回归覆盖失败回滚和成功升级后的记录保留。
- 人物隐藏写 API 支持明确忽略状态，并可按状态筛选已忽略人物列表；恢复操作继续移除忽略标记。页面提供暂时忽略、明确排除两种选择及对应列表。
- `TEMPORARY` 保留候选链接和归属流程；`EXCLUDED` 从人物整理候选投影、suggestion 可用性及后续自动人脸匹配候选中排除。没有修改人脸识别算法、重算相似度或删除人物/人脸数据。
- 增加迁移、API、状态语义和自动匹配候选覆盖；修正一个 UI 合成测试使用了当前页面不存在的按钮 ID。

## 任务卡定向验证

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
uv run pytest -q tests/integration/test_p4_person_ignore_state.py tests/integration/test_p4_person_albums.py tests/integration/test_p4_person_collections.py tests/integration/test_p4_person_match_b.py::test_explicitly_excluded_person_is_not_an_automatic_face_candidate tests/unit/test_person_hide_selection_ui.py tests/unit/test_person_inbox_confirm_ui.py --junitxml=test-results/P4-PERSON-IGNORE-STATE-A.junit.xml --basetemp=.pytest-tmp/p4-person-ignore-state-a
```

- pytest：**34 tests / 0 failures / 0 errors / 0 skipped**，退出码 0。JUnit：`test-results/P4-PERSON-IGNORE-STATE-A.junit.xml`。
- Ruff：本任务修改的 Python 源码与测试文件通过。
- `node --check`：`web/app.js`、`web/persons-albums.js` 通过；修复后重新执行 `persons-albums.js` 语法检查。
- `py_compile`：本任务修改的 Python 源码与测试文件通过。
- `git diff --check`：通过。

## 复核与未验证

- LUNA High 完成全影响面复核，发现的唯一 P2（排除后 inbox 仍显示旧建议）已修复并通过修复复核；无其他发现。复核与修复记录见 [审查报告](P4-PERSON-IGNORE-STATE-REVIEW-A.md)。此路由按用户指定采用 LUNA High，不代表 DS-H PASS。
- 未运行全仓测试、真实浏览器手工验收、真实图库、模型/GPU 或打包验证；未读取或修改真实照片库。
- `app.js`、`index.html`、`photo-timeline.css` 及 `test_p4_ui_timeline_integration.py` 有工作区既存的时间线相关改动，本任务未纳入或覆盖这些改动。
