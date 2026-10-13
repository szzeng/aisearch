# P4-PERSON-IGNORED-CANDIDATE-A 执行报告

日期：2026-10-13。结论：**SCOPED PASS / 任务卡 API 与合成 DOM 验收通过**。

## 范围与改动

- 基线：`a3f915c`；执行前工作区已含时间轴页面及报告/测试变更，全部保留。
- `src/photo_search/web/persons-albums.js`：忽略人脸复用现有 `suggestions[]` 显示候选头像和名称；有候选可确认或打开现有人物搜索选择器，无候选可进入人物选择；确认归属后刷新人物和忽略视图，不阻塞待整理列表分页。保持忽略仍是无写入的默认状态。
- `src/photo_search/web/persons.html`：更新人物脚本缓存键。
- `tests/integration/test_p4_person_albums.py`、`tests/unit/test_person_inbox_confirm_ui.py`：覆盖忽略人脸候选投影、候选确认/列表刷新、人工选择现有人物并提交归属、无候选显示和保持批量选择语义。
- 未改后端归属协议、候选顺序、人物/人脸数据结构或识别/相似度计算。当前公开人脸投影不含候选分数；相似度不显示、不推导。

## 验收

```powershell
$env:UV_CACHE_DIR = (Join-Path (Get-Location) '.uv-cache')
$env:PYTHONPATH = (Get-Location).Path
uv run --locked --no-sync pytest -q tests/integration/test_p4_person_albums.py::test_inbox_face_visibility_is_atomic_persistent_and_scoped_to_selected_faces tests/unit/test_person_inbox_confirm_ui.py --junitxml=test-results/P4-PERSON-IGNORED-CANDIDATE-A.junit.xml --basetemp=.pytest-tmp/p4-person-ignored-candidate-a
node --check src/photo_search/web/persons-albums.js
uv run --locked --no-sync ruff check tests/unit/test_person_inbox_confirm_ui.py tests/integration/test_p4_person_albums.py
git diff --check
```

- 定向 pytest：**2 passed / 0 failed / 0 skipped / 0 errors**，退出码 0；JUnit：`test-results/P4-PERSON-IGNORED-CANDIDATE-A.junit.xml`。
- Node 语法检查、Ruff、`git diff --check` 均通过，退出码 0。
- 首次 DOM 执行中 API 用例通过；修正测试 harness 的 DOM 定位与表单提交事件挂接后，同一命令通过。

## 未验证与交接

- 没有运行真实浏览器、真实图库、模型/GPU、全仓测试或打包验收；本结论只覆盖本卡所需 API/DOM 小样本行为。
- 继续遵守 V3.0 §8.3.4 的 face_id 级人工归属与人工锁保护；没有重跑推理。依赖 `P4-PERSON-IGNORE-STATE-A` 已通过。
- 下一必要闭环仍为已存在的 [P4-UI-TIMELINE-INTEGRATION-VERIFY-A](../tasks/P4-UI-TIMELINE-INTEGRATION-VERIFY-A.md)：该卡关闭时间轴集成浏览器验证阻断，建议 LUNA High。完成前不启动 CORE-A 后续阶段。
