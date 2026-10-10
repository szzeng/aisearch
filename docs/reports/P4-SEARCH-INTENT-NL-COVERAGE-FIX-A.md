# P4-SEARCH-INTENT-NL-COVERAGE-FIX-A 执行报告

日期：2026-10-10。结果：**实现完成 / 任务卡定向验证通过 / 当前会话工程复核通过；独立 DS-H High 复核与现场试用待完成**。

## 根因与修复

- 合集解析只接受部分连接写法，解析候选跨度与内容擦除规则也不一致。现按登记名称识别 `里/里的/里面/里面的/中/中的/合集里` 等明确范围结构；多合集 AND/OR 直接 `unsupported`，不执行单侧范围。普通 `AI技术`、`AI风格`、`AI生成` 和“手机拍”仍保留内容查询，不推断合集或来源。
- 时间解析缺少相对年份＋月份与连续月份区间；地点解析会把已确认的时间/人物前缀当作非句首内容，且对部分普通内容词触发无目录地点提示。现在支持左闭右开的相对月、绝对月及同年连续月范围；已确认的时间/人物词仅作为地点解析上下文。多个年份 OR 拒绝为 `unsupported`，不返回首个年份作为可执行时间条件。
- 人物关系解析未把“同框/一起拍”识别为 `all` 并擦除关系框架词；现在按明确关系返回双人物 `all`，无同框依据的“张三和李四的照片”保留 `needs_choice`。验证器允许用户把 `all` 人物 chip 全部取消后继续沿用原句中的其它已确认条件。
- 前端在途请求会因 `loading` 吞掉新提交；现在以单调 request generation 隔离新旧响应，旧成功、错误和消歧都不能覆盖最新搜索状态。
- 复核发现反向月份范围会退化成单月过滤；现在同年递增范围支持完整区间，重复写年份也完整支持，反向或跨年范围明确 `unsupported`，不暴露部分日期条件。

## 支持与拒绝边界

- 40 条参数化解析合同样例全通过，包含合集/内容、时间、地点、人物、来源、视觉文字和不支持组合。
- `2024年10月到12月` 及 `2024年10月到2024年12月` 支持连续月范围，右界为 `2025-01-01`；反向月份范围、跨年显式月份范围、`2024年或2025年`、多合集 AND/OR、多地点 OR 和否定条件不执行部分过滤。
- 纯合集范围走 SQLite 条件分页；`AI里的裙子` 解析为 AI 合集＋“裙子”内容词。既有内容检索仍在合集 scope 内执行；语义 Top5000、普通 `/api/v1/search`、Schema、FTS/Vector 成员契约均未更改。

## 验证

先将 37 条解析样例落地为红灯测试：20 失败 / 17 通过。初轮任务卡指定的六个测试文件：99 passed。当前会话复核修复后，六个文件：**102 passed / 0 failed / 0 errors / 0 skipped**。

JUnit：`test-results/P4-SEARCH-INTENT-NL-COVERAGE-FIX-A.xml`。

通过的附加检查：指定文件 Ruff、`node --check src/photo_search/web/search.js`、`git diff --check`。

## 后续补充：纯筛选结果续页显示

用户现场反馈地点与人物搜索点击下一页后旧页被替换。根因是搜索页对 `filter-only` 和 `place-album:` 会话主动清空结果卡片及预览列表；后端游标仍正常返回下一页。现在续页沿用普通关键词/语义结果的累加显示，累计已加载数，并让预览和详情返回快照覆盖所有已加载结果。纯地点、人物、合集、时间、来源及其无内容词组合均走此规则；查询条件、游标和排序未改。

定向验证：`tests/unit/test_p4_search_nl_ui.py::test_filter_pages_append_and_saved_snapshot_keeps_loaded_results`，覆盖 `filter-only` 与 `place-album:` 各三页，检查 DOM、预览列表、返回快照均保留前页；**1 passed**。JUnit：`test-results/search-pagination-append.xml`。另 `node --check src/photo_search/web/search.js` 与 `git diff --check` 通过。未运行浏览器或真实图库验证。

## 当前会话工程复核

按用户要求在本会话复核新增解析规则、范围擦除、搜索计划安全校验、请求代次和既有回归用例；发现并修复反向月份区间可能静默缩成单月的问题，补齐反向/跨年拒绝和重复年份同年范围三条样例。修复后重跑上列完整定向命令，102/102 通过，Ruff、Node、diff-check 通过。本结论是当前会话工程复核，不是独立 DS-H 审查，也未发生模型切换。

定向测试实际命令：

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
$env:PYTHONPATH = 'src;tests;.'
uv run --no-sync pytest -q `
  tests/unit/test_p4_search_intent_unified.py `
  tests/unit/test_p4_search_nl_parse.py `
  tests/unit/test_search_place_nl_ui.py `
  tests/unit/test_p4_search_nl_ui.py `
  tests/integration/test_p4_search_collection.py `
  tests/integration/test_p4_place_search_flow.py `
  --basetemp=.pytest-tmp/P4-SEARCH-INTENT-NL-COVERAGE-FIX-A `
  --junitxml=test-results/P4-SEARCH-INTENT-NL-COVERAGE-FIX-A.xml
```

验证覆盖包括：AI 合集纯范围分页无重复且不调用语义入口；AI 合集＋内容词保留 scope；多合集 OR 不触发搜索；人物 `all` chip 全部取消不产生 409；快速提交期间旧成功、旧错误、旧消歧不能覆盖新结果；来源、时间、地点及现有 FIX1/FIX2 回归。

## 变更与边界

变更文件：

- `src/photo_search/search/natural_intent.py`
- `src/photo_search/search/unified_intent.py`
- `src/photo_search/search/personal_search.py`
- `src/photo_search/places/natural_search.py`
- `src/photo_search/web/search.js`
- `tests/unit/test_p4_search_intent_unified.py`
- `tests/unit/test_p4_search_nl_ui.py`
- `tests/unit/test_search_place_nl_ui.py`
- `tests/integration/test_p4_search_collection.py`

未运行全仓/浏览器矩阵/GPU/真实模型/打包验证；未读取或更改真实 `PhotoSearchData`，未启动用户服务。没有提交或推送。独立 DS-H High 完整受影响面复核尚未执行，因此不标任务整体 PASS；用户按任务卡少量现场复测也待完成。

## 用户现场少量复测

刷新并使用用户自己选择的真实数据，比较 `AI的照片` 与 `AI里的照片`；搜索 `AI里的裙子`、`今年10月的照片`、`2024年济南的照片`；快速连续提交两次查询；取消条件 chip 后翻页。确认后续结果继续保留其余条件。
