# P4-PERSON-HIDE-UI-FIX-A 实现与验证

日期：2026-10-09。实际基线：`8a4bfc5299460e4e15fe4028c5aceb2e5d554bf2`（任务卡计划基线 `3fda522`）。结论：**PASS / 个人范围 DOM 回归通过**。

## 修复

- `togglePersonSelection()` 原先每次勾选/取消都会调用 `loadPeople(false)`；批量管理按钮开启/退出时也会重载首屏。这两处现在只同步现有卡片，不触发请求、清空游标或增加列表序号。
- 每张人物卡关联稳定的 person ID，并持有独立选择提示节点。`syncPeopleCards()` 就地更新 `aria-pressed`、选择提示和高亮；取消选择/退出批量模式会立即清除当前所有卡片状态。卡片节点、头像、焦点和已展开页面保持原样。
- 选择提示绝对定位，不增加卡片行高；新增脚本与样式 URL 版本，浏览器会请求更新后的资源。
- 下一页响应到达时，通过当前 `batchMode` 与 `selectedPeople` 同步新卡片；无额外缓存或整页预取。成功忽略/恢复仍清除选择并重载首屏；确认取消和请求失败保留选择及当前页。

## 验证

- 新增 Node DOM 回归：`tests/unit/test_person_hide_selection_ui.py`，**1 passed**。模拟加载 120 人后选择第85/20/110人并取消第20人，确认列表节点、120张卡片、游标、列表序号和请求数不变；继续加载第4页后验证退出/重进与取消清理；已忽略视图验证确认取消、写请求失败保留选择，成功恢复后才刷新首屏。
- 因测试 DOM 不模拟浏览器布局，滚动保持以卡片容器未替换、无请求、游标/序号不变作为卡片允许的替代证据；未做真实浏览器滚动观察。
- `uv run pytest -q tests/unit/test_person_hide_selection_ui.py --junitxml=test-results/p4-person-hide-ui-fix-a.junit.xml`：通过。JUnit：`test-results/p4-person-hide-ui-fix-a.junit.xml`。
- `uv run ruff check tests/unit/test_person_hide_selection_ui.py`、`node --check src/photo_search/web/persons-albums.js`、`git diff --check`：通过。

未运行全仓测试、后端测试、真实图库、浏览器矩阵或模型/GPU。仅修改人物相册选择 UI、资源版本和一项定向测试。
