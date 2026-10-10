# P4-SEARCH-INTENT-UNIFIED-A-FIX1 修复报告

日期：2026-10-10。结果：**代码修复完成 / 定向验证通过**。实现起点为 `f417c51`（`feat(search): unify personal search intent`）。

## 根因

统一解析对同一人物的正式名和别名分别遍历。如果二者文本相同，就会为同一 `person_id`、同一原文区间创建两条完全相同的 person candidate。随后重叠检测把这两条候选当作两个条件，错误返回 `needs_choice`。路由为每条候选各建一个按钮，于是出现两个相同的“人物：SZ”。

另一个有效歧义情形是不同对象身份（例如不同人物通过共享别名、两个地点同名但父级不同、两个图库来源同名）匹配到相同文本。这些情况必须保留选择，但统一选择 UI 丢掉了此前展示的人物 ID、地点父级/来源等区分信息，并无条件使用“该名称同时对应不同范围”的标题，造成按钮相同且文案不准确。香港问题属于这类：同名地点身份不同，不能把它们合并成一个地点。

现场究竟是哪种人物/别名记录未检查：没有读取真实图库或运行中服务。两种触发方式都用合成目录复现并覆盖。

## 修复

- 在冲突判断之前按 `(intent type, identity, original span)` 去重候选；同一身份重复命中只保留一项，不同 `person_id` 不合并。
- 人物范围按钮追加 `#person_id`；重复来源/合集标签追加对应 ID。地点标签展示 `parent` 与 `source`，这些信息仍重复时再追加 place key。
- 人物、地点、来源各自单类歧义显示准确提示；跨类型选择仍显示不同范围提示。

## 验证

- 定向 JUnit：`test-results/P4-SEARCH-INTENT-UNIFIED-A-FIX1-r8.xml`，**20 passed / 0 failed / 0 errors / 0 skipped**。
- Ruff、`node --check src/photo_search/web/search.js`、`git diff --check` 均通过。
- 覆盖统一解析、搜索 UI 和合集/人物/地点/来源 API；API 回归确保重复候选去重、不同身份保持独立、香港同名地点显示父级/来源，父级和来源仍相同时显示地点 key/kind，且同名来源显示 ID。
- 未运行全仓、真实浏览器、GPU/真实模型或打包验证；未读取真实图库或现有服务。未提交/推送。

复验命令：

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
$env:PYTHONPATH = 'src;tests;.'
uv run --no-sync pytest -q `
  tests/unit/test_p4_search_intent_unified.py `
  tests/unit/test_p4_search_nl_ui.py `
  tests/integration/test_p4_search_collection.py `
  --basetemp=.pytest-tmp/P4-SEARCH-INTENT-UNIFIED-A-FIX1-r8 `
  --junitxml=test-results/P4-SEARCH-INTENT-UNIFIED-A-FIX1-r8.xml
uv run --no-sync ruff check `
  src/photo_search/api/routes.py `
  src/photo_search/search/unified_intent.py `
  tests/unit/test_p4_search_intent_unified.py `
  tests/unit/test_p4_search_nl_ui.py `
  tests/integration/test_p4_search_collection.py
node --check src/photo_search/web/search.js
git diff --check
```
