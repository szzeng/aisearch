# P4-UI2-SEARCH-SCOPE-UNIFY-A 实施与验证报告

日期：2026-10-14
状态：**IMPLEMENTED / ACCEPTANCE PARTIAL / LUNA HIGH REVIEW COMPLETE**
范围：统一首页、人物、地点、搜索页的页头搜索动作和一次性范围交接；未新增后端路由。

## 实施内容

- 新增 `search-scope-entry.js`：页头范围 CTA 在点击时读取当前有效身份，将范围、同一关键词和 `execute=true` 写入一次性 sessionStorage 交接并进入 `/search`。搜索页消费后恰好发起一次查询；空词仍显式执行 FILTER_ONLY。无效/缺失 token 给出错误，不退化为全库搜索。
- 首页照片浏览在选定单一来源时提供“搜索此图库”，沿用 `source_id`；人物和合集状态（合集照片/成员 tab）以及有效地点详情状态更新同一页头按钮。退出、切换或身份失效时清理/更新按钮。人物标题旧 CTA 与地点标题旧 CTA 已移除。
- 搜索页固定范围在继续提交时保留。取消范围会作废旧请求/分页/快照并清空旧结果，不自动全库重搜；source 范围释放 `source_id`。已限定时以图搜图会得到明确阻止提示。
- 人物范围查询会把进入固定身份前的 `person_match` 留在 `intent_plan.person_match_before_fixed_scope`；固定人物仍按既有 all 关系执行与其它显式人物条件求交，连续提交先用保存的原模式校验解析计划。取消人物时移除固定 person ID 并恢复原 any/all 模式。这里只在现有 plan 字典中保留 UI 取消所需状态，没有新 API 路由或检索语义。
- 浏览器走查发现地点范围响应会把同一地点回显成第二个 place-aware / intent 条件。取消固定地点时现按匹配的结构化 `place_kind/place_key` 一并清除这两个重复回显条件，保留其它独立条件，并等待用户主动提交。定向回归与修复后浏览器复验通过。
- UI2.0 交互 demo 同步改为点击立即提交和取消后不自动扩大全库结果。人物页旧 `#pa-search-photos` 局部文字表单暂时保留：本次只有地点路径获得真实浏览器验证，按任务卡条件不提前移除人物旧入口。

## 定向验证

最终 JUnit：[`P4-UI2-SEARCH-SCOPE-UNIFY-A-junit.xml`](artifacts/P4-UI2-SEARCH-SCOPE-UNIFY-A-junit.xml)，**28 tests / 0 failures / 0 errors / 0 skipped**。覆盖新交接/失效 token、人物/合集/图库/地点身份取消状态、同范围连续提交和人物 any/all 模式保持、人物入口返回、地点选择/取消、搜索状态与旧 scope 返回、既有搜索自然语言状态、页面资源打包。单独集成结果也保存在[`integration JUnit`](artifacts/P4-UI2-SEARCH-SCOPE-UNIFY-A-integration.xml)。

命令：

```powershell
$env:UV_CACHE_DIR='.uv-cache'
$env:PYTHONPATH='.'
uv run pytest -q tests/unit/test_p4_ui_search_entry.py tests/unit/test_search_person_album_link_ui.py tests/unit/test_search_place_nl_ui.py::test_place_cancel_and_ambiguous_choice_use_the_current_content tests/unit/test_p4_ui2_closeout_flow.py tests/unit/test_p4_search_nl_ui.py tests/unit/test_api_frontend.py::test_home_loads_rootless_summaries_and_source_scoped_browse tests/unit/test_api_frontend.py::test_search_page_restores_results_without_searching_again tests/unit/test_api_frontend.py::test_the_search_page_assets_are_packaged tests/unit/test_api_frontend.py::test_the_places_page_assets_are_packaged tests/unit/test_api_frontend.py::test_the_persons_page_assets_are_packaged tests/integration/test_p4_search_collection.py::test_collection_connectors_keep_scope_content_and_refuse_multi_collection_or --junitxml=docs/reports/artifacts/P4-UI2-SEARCH-SCOPE-UNIFY-A-junit.xml
```

`node --check` 对 `app.js`、`persons-albums.js`、`places-albums.js`、`search.js`、`search-scope-entry.js`、demo `app.js` 和 `model.js` 全部通过；`python -m py_compile src/photo_search/api/routes.py` 与 `git diff --check` 通过。浏览器发现的重复地点条件新增了 DOM 状态回归，所在测试为 `test_scope_entry_carries_query_and_executes_once_without_fallback`。

曾在初始实现阶段额外运行一个较宽的 UI/API 文件组合，记录为 138/12/0/0；其中暴露的 2 个人物测试 mock 和 1 个地点测试 DOM fixture 问题已修复，并纳入最终通过的定向套件。其余旧源码词汇/静态结构断言不是本卡最终定向套件门槛；原 XML 保存在 [`P4-UI2-SEARCH-SCOPE-UNIFY-A-broad-exploratory.xml`](artifacts/P4-UI2-SEARCH-SCOPE-UNIFY-A-broad-exploratory.xml)，不作为本卡 PASS 证据。本卡未运行全仓测试。

## 产品浏览器冒烟

- 使用既有隔离合成数据根 `.tmp/P4-UI2-CLOSEOUT-FLOW-A/place-browser-data`，活动包为 `synthetic-mini（TEST-FIXTURE）`，4 张合成资产；最终复验启动产品服务于 `127.0.0.1:18836`，不带 `--open-browser`。应用新生成的一次性 session URL 在本机 Codex 内置浏览器中仅打开一次，session exchange 为 303，首页/地点/搜索资源及 API 请求返回 200。没有触碰用户真实图库或模型/GPU。
- 地点目录显示“东京 · 日本 · 4 张照片”；进入详情后页头“搜索此地点”可见。点击后直接到 `/search`，提交按钮显示“搜索当前范围”，固定 chip 为“东京 · 日本”；空词显示 4 个结果和 `FILTER_ONLY` 说明，没有生成查询向量。
- 点击固定地点 chip 的取消按钮后，范围 chip、重复地点过滤 chip、旧结果均消失；提交按钮恢复“全库搜索”，页面提示“已取消范围，点击全库搜索重新查询”。未自动发起范围扩大后的查询。
- `BROWSER_INTERACTION=PARTIALLY_VERIFIED`：仅地点详情空词入口与取消流程已在产品浏览器复验。非空词、人物 CTA/Enter、合集两个 tab、图库来源 CTA、scope 翻页/预览返回、图片搜索屏蔽、token 失效、IME、窄屏/暗色/真实 200% 均未在本次浏览器走查。`BROWSER_SCREENSHOT=PENDING`：本次没有落盘截图；交互文本与服务请求证据记录于本报告。
- 搜索语义模型在隔离数据根缺失，本次浏览器证据仅证明 FILTER_ONLY，不证明非空词的 FTS/语义搜索。

## 复核与未完成项

- LUNA High 对完整变更面的独立复核提出并推动修复两项取消状态问题：清除 person/collection/source/place 回显身份；人物至少两个其它 ID 时恢复原 any/all，而非保留 fixed scope 强制的 all。最终复核确认原模式验证、固定人物 all 约束、取消恢复和连续 scoped 请求一致，没有剩余可执行 finding。此为 LUNA High，不是 DS-H。
- 整卡保持 `ACCEPTANCE PARTIAL`：还需按任务卡完成其它人物、合集、地点非空词、图库范围、取消竞态和预览返回等浏览器关键路线。由于新人物 scoped 流程尚未浏览器验证，保留人物旧局部搜索表单。
- 未执行全仓、真实图库、模型/GPU、性能规模、200%缩放、打包或发布验证；没有 commit。
