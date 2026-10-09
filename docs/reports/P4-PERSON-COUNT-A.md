# P4-PERSON-COUNT-A 执行报告

日期：2026-10-09。基线：`2ade131`。结论：**PASS / 定向合成验证通过**。

## 结果

- `/api/v1/persons/browse` 增加 `total`，按现有 `kind`、`q`、`visibility` 候选 SQL 过滤后做单次 `COUNT(*)`。它不受 cursor 和排序影响，未加载所有人物，也没有逐人物计数查询。
- 人物页显示“人物 N 人”“未命名人物 N 人”“已忽略 N 人”。未命名人物沿用有效成员且非空的现有口径；待整理人脸摘要保持独立。
- 页面计数每次由响应整体覆盖，翻页不会累加；搜索、视图切换和隐藏视图复用 API 当前筛选条件。

## 验证

- `uv run pytest -q tests/integration/test_p4_person_albums.py::test_people_page_total_matches_filters_without_pagination_or_sort tests/unit/test_person_count_ui.py tests/unit/test_person_hide_selection_ui.py --junitxml=test-results/p4-person-count-a.junit.xml`：**3 passed**。覆盖已命名/未命名/已忽略、搜索过滤、跨页计数一致及三种页面文字；同时复跑隐藏选择 DOM 回归。
- `uv run ruff check src/photo_search/persons/albums.py tests/integration/test_p4_person_albums.py tests/unit/test_person_count_ui.py`：通过。
- `node --check src/photo_search/web/persons-albums.js`、`git diff --check`：通过。
- 初次试跑时，合成夹具重复注册重叠 source，修正为在同一合成资产追加人脸后通过。初次命令还包含相邻的 `test_person_sort_ui.py`；该既有 UI 桩没有定义 production script 使用的 `window`，在启动时失败，与本改动无关，未纳入最终定向命令。计数排序行为由 API 定向用例直接验证。

未运行全量回归、真实浏览器、真实图库或用户服务；页面现场显示待用户使用确认。
