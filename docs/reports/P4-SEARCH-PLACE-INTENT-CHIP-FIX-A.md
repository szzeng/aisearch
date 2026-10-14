# P4-SEARCH-PLACE-INTENT-CHIP-FIX-A 验收报告

**日期：** 2026-10-14
**状态：** IMPLEMENTED / ACCEPTANCE PARTIAL / LUNA HIGH REVIEW COMPLETE
**任务卡：** [P4-SEARCH-PLACE-INTENT-CHIP-FIX-A](../tasks/P4-SEARCH-PLACE-INTENT-CHIP-FIX-A.md)

## 实施摘要

- `natural_search.py` 对已确认与普通视觉内容冲突的“太阳”做窄范围意图门控：裸词、`太阳的照片`、`照片里的太阳`保留为内容查询；明确拍摄地点语境仍可选择该地点。裁剪地名短语后保留有效内容词，不清空 `content_query`。
- `search.js` 按结构化地点身份合并固定范围、意图计划和服务端回显；相同 `kind + key` 只显示一个 chip，不同身份仍分别保留。
- 统一地点取消状态迁移：使旧请求/游标/会话失效、清理地点计划和结果、保留其它条件，且不自动触发全库查询。碰撞词可回退为普通内容搜索；纯地点条件取消后提示输入内容。
- 未改后端协议、权威地点成员语义或数据库结构。更新 `search.js` cache-bust。

## 任务卡定向验证

- 修复前先运行相关测试，裸词 `太阳` 与 `太阳的照片` 有 2 项复现失败，解析成地点。Luna High 首轮指出取消后的在途响应缺少迟到回归断言；现已增加 deferred-fetch 用例，证明取消后旧成功响应不能覆盖状态。
- 最终 bounded JUnit：**98/0/0/0**，JUnit XML：`test-results/P4-SEARCH-PLACE-INTENT-CHIP-FIX-A-final-r5.xml`。覆盖指定的地点自然语言、统一意图、搜索 UI、地点集成用例。
- 最终定向命令（PowerShell，成功退出；XML 读取为 98 tests、0 failures/errors/skipped）：

```powershell
$env:UV_CACHE_DIR='.uv-cache'; $env:PYTHONPATH='src;tests;.'
uv run --no-sync pytest -q `
 tests/unit/test_search_place_nl_ui.py `
 tests/unit/test_p4_search_intent_unified.py `
 tests/unit/test_p4_search_nl_ui.py `
 tests/unit/test_p4_ui_search_entry.py `
 tests/integration/test_p4_place_search_flow.py::test_pure_place_search_and_explicit_place_cancellation `
 tests/integration/test_p4_place_search_flow.py::test_place_aware_api_parses_daily_phrasing_and_keeps_unsafe_queries_unfiltered `
 --basetemp=.pytest-tmp/P4-SEARCH-PLACE-INTENT-CHIP-FIX-A-final-r5 `
 --junitxml=test-results/P4-SEARCH-PLACE-INTENT-CHIP-FIX-A-final-r5.xml
```

- `uv run --no-sync ruff check src/photo_search/places/natural_search.py tests/unit/test_search_place_nl_ui.py tests/unit/test_p4_search_intent_unified.py tests/unit/test_p4_ui_search_entry.py`、`node --check src/photo_search/web/search.js`、`python -m py_compile src/photo_search/places/natural_search.py` 和 `git diff --check`：通过。
- 未运行全仓测试、性能、真实图库、模型/GPU、打包或发布检查。

## 浏览器观察

使用 Codex 内置浏览器、本机 `127.0.0.1` 产品服务和隔离合成数据（synthetic-mini，4 张照片）。地点东京的“搜索此地点”路线显示一个固定地点 chip；取消后 chip 与结果清除，按钮回到“全库搜索”，没有自动提交新查询。服务使用新生成的一次性 session link，仅在该浏览器打开一次；会话凭据未写入报告。服务已停止。

`太阳` 浏览器查询因该隔离库没有 active search profile，接口返回 `VALIDATION_ERROR`，未能形成真实产品浏览器查询证据。故 `BROWSER_SOLAR_QUERY=PENDING`；解析/回退行为由上述定向 JUnit 覆盖。浏览器不支持本次截图导出，未保存截图。`REAL_DATASET_NOT_INSPECTED`；只检查了隔离合成数据，没有访问真实图库。

## 独立复核与验收边界

用户指定的 Luna High 独立复核已完成。首轮 P2 finding 要求补充取消后在途响应迟到的回归断言；新增 deferred-fetch 测试后，复核代理确认 finding 关闭且无新可执行问题。此次没有改服务端地点计划身份/校验协议，所以不触发任务卡规定的 DS-H 升级路径。

实现、定向测试和 Luna High 复核均已完成；只剩在可用 search profile 的合成产品会话中验证 `太阳` 浏览器路线。
