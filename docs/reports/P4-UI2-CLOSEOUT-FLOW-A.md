# P4-UI2-CLOSEOUT-FLOW-A 实施与验证报告

日期：2026-10-14
结论：**IMPLEMENTED / ACCEPTANCE PARTIAL**。修复 FILTER_ONLY 与有效地点范围返回时错误走语义 session 时间校验的问题：返回时按保存的原始完整请求重新请求权威首屏，不复用旧 hits/cursor；零结果查询也保存导航快照。定向 JUnit **26/0/0/0**，Node、Ruff、Python 编译和 `git diff --check` 通过。LUNA High 复核无可执行 correctness finding；本轮按 P4-UI-PLACES-E 的本机浏览器流程补齐有效地点 scope、明确全局重搜、合成合集成员变化、首页 Escape、多页续页返回与 GPS bbox 浏览器证据并落盘截图。F07 竞态及其他 P0 子路线仍待验，故不标 `FLOW_SCOPED_PASS`。

## 复现与原因

`P4-UI-VIEWER-F` 的人物 FILTER_ONLY 搜索往返后仅恢复 chip/查询条件、清空结果。`restoreSearchSnapshot()` 统一调用 `/search/sessions/{id}/time?limit=1`；`filter-only` 与 `place-album:*` 没有语义候选 session，因而被误判为不可验证。另 `saveSearchSnapshot()` 要求当前有 hits，零结果时导航条件也不会写入快照。

## 修复

- [search.js](/D:/SZ/project/aisearch/src/photo_search/web/search.js)：保存原始首请求体（包含 scope、过滤条件及请求身份），零结果时也保存快照。返回 FILTER_ONLY 或地点相册范围时，以新 request id 提交保存的完整条件，移除旧 session/cursor，调用既有 `/search/place-aware`，只渲染服务端返回的第一页；不尝试复用旧结果或分页游标。
- 有语义候选 session 的原时间修订验证路径保留。请求失败时清除结果/session/cursor，保留查询与 scope 并显示可理解的重新查询提示；恢复成功/失败均由 request generation 防止迟到响应覆盖新状态。
- [search.html](/D:/SZ/project/aisearch/src/photo_search/web/search.html)：更新脚本 cache-bust。调整既有版本/UI 文案断言，并新增 `test_p4_ui2_closeout_flow.py` 源码回归断言。
- 未改变后端/API、身份、权限、revision 或 cursor 合同。

## F01–F10 验收结果

| 项 | 结果 | 证据 / 缺口 |
|---|---|---|
| F01 人物 scope 空词、预览、跨人物、返回 | **BROWSER_VERIFIED（核心路径）** | 隔离 PEOPLE-D 合成库：Dad scope 明确空词搜索得到 2 个 FILTER_ONLY 结果；预览后进入人物页并浏览器返回，scope 与结果恢复。服务日志确认返回时发起新的 `/api/v1/search/place-aware` 请求。人物 scope 取消后全局重搜未在本轮复测。 |
| F02 合集 scope、第二页、预览返回 | **BROWSER_VERIFIED（主要路径）** | 合集 3 个唯一照片；空词查询、加载第二页、固定合集 chip 保留、第二页成员预览/关闭后结果仍在。旧证据观察到取消 chip 清空旧结果；本轮另验证变更后拒绝旧结果（见 F06）。 |
| F03 有效活动地点 scope 搜索及往返 | **BROWSER_VERIFIED** | 按 P4-UI-PLACES-E 本机流程启动 `127.0.0.1` 产品服务，经启动器打印的一次性链接进入 Codex 应用内浏览器。活动合成包为 ACTIVE/TEST-FIXTURE，东京 4 个有效成员；「搜索此地点」后固定 `东京 · 日本` scope、空词 FILTER_ONLY 返回 4 张。搜索请求为 HTTP 200，无 stale；预览关闭后保留 scope 与 4 张结果。截图：`artifacts/P4-UI2-CLOSEOUT-FLOW-A-place.jpg`、`artifacts/P4-UI2-CLOSEOUT-FLOW-A-place-search.jpg`。 |
| F04 三种 scope 取消后明确全局重搜 | **PARTIAL** | 地点 scope 取消固定相册 chip 后旧结果清空；明确点击搜索后返回该隔离库全库 4 张。合集路线也复验：取消 chip 清空旧结果，明确重搜得到全库 69 张并显示续页。人物 scope 取消后重搜仍待验。取消地点结构化条件时先出现一次 stale 提示；随后取消固定地点 scope并明确重搜正常。 |
| F05 未变、空结果、不可验证 session 三种返回 | **PARTIAL** | 人物/地点 FILTER_ONLY 未变返回已有浏览器证据。本轮人物 scope 使用 2050–2051 日期条件查询 0 张，返回后仍保留 chip、条件与空结果提示（截图 `artifacts/P4-UI2-CLOSEOUT-FLOW-A-empty.jpg`）。真实浏览器的 session 不可验证返回仍未执行。 |
| F06 成员/活动地点/时间规则真实变更后返回 | **PARTIAL** | 仅在隔离 PEOPLE-D fixture 副本执行合集成员变化：原合集 2 人/3 张；移出“爸爸”后为 1 人/2 张。返回旧合集 FILTER_ONLY 搜索快照时保留范围提示、清空旧结果并显示 `CATALOG_FILTER_REVISION_STALE`，未继续显示旧照片。地点活动包及时间规则变化子路线仍待验；副本位于 `.tmp/P4-UI2-CLOSEOUT-FLOW-A/stale-test-data`。 |
| F07 旧请求迟到且用户切换范围 | **PENDING** | request generation 保护由源码断言覆盖；本轮未执行浏览器异步竞态。 |
| F08 首页/来源/人物/合集/地点详情关闭或 Esc | **PARTIAL** | 本轮首页时间轴卡片打开资产 #69 后按 Esc 回首页，再打开资产 #19 后 Esc 仍回首页；焦点落在 main 容器。人物搜索跨页返回与合集/地点范围路线见 F01–F03。来源页及人物/合集/地点各自从时间轴点卡、打开一次、触发按钮焦点恢复仍未成套验证。截图：`artifacts/P4-UI2-CLOSEOUT-FLOW-A-home.jpg`。 |
| F09 多页时间轴及日期桶往返 | **PARTIAL** | 69 张合成首页时间轴均处于“日期待就绪”桶。访问日志先请求 limit=50，再带 cursor 请求第二页；打开后页资产 #19 后 Esc 返回，69 张范围仍显示。可信日期/未知/待就绪跨桶、年月定位及来源/人物/地点多页日期锚点仍待验。 |
| F10 GPS 地图详情返回与 bbox 变化 | **BROWSER_VERIFIED** | 隔离 GPS 合成库显示 2 个成员。初始 bbox（z8）约 `119.9146,30.0332,121.7521,31.4284`；缩放到 z9 后 bbox 变为 `120.374,30.384,121.293,31.082`，列表变为 0；缩回 z8 恢复 2。照片详情显示合成 GPS `(31.2167,121.5)`，浏览器返回恢复地图 2 张。地图 error/warn 为空，截图 `artifacts/P4-UI2-CLOSEOUT-FLOW-A-map.jpg`。 |

## 2026-10-14 按 P4-UI-PLACES-E 流程补充的产品浏览器验收

- 每个 localhost 服务实例均使用应用启动器新生成的一次性会话链接，在 Codex 应用内浏览器建立会话；地点 4 图活动 mini 包、PEOPLE-D 副本和 GPS 样本均放在项目 `.tmp/P4-UI2-CLOSEOUT-FLOW-A/` 下。未访问真实 `PhotoSearchData`，未关闭认证或绕过浏览器安全策略。
- 地点 scope、明确全局重搜、合集成员变化/stale 返回、首页 Escape、第二页续页返回、地图 bbox/详情返回均为真实产品 UI 操作。访问日志确认对应 search/timeline/map 请求与响应；本轮地点搜索、人物/合集操作和地图标签的浏览器 `error`/`warn` 均为空。
- 保存了首页、人物、合集、搜索、空结果、地点时间轴、地点 scope 结果、地图等页面截图，路径在 `docs/reports/artifacts/`；截图不含启动器会话 token。
## 定向验证

JUnit XML：`test-results/P4-UI2-CLOSEOUT-FLOW-A.xml`，**26 tests / 0 failures / 0 errors / 0 skipped**。

```powershell
$env:UV_CACHE_DIR='.uv-cache'
$env:PYTHONPATH='.'
uv run --no-sync pytest -q `
  tests/unit/test_search_person_album_link_ui.py `
  tests/unit/test_photo_view_info_ui.py `
  tests/unit/test_p4_ui2_closeout_flow.py `
  tests/integration/test_p4_place_search_flow.py `
  tests/integration/test_p4_search_collection.py `
  --basetemp=.pytest-tmp/P4-UI2-CLOSEOUT-FLOW-A `
  --junitxml=test-results/P4-UI2-CLOSEOUT-FLOW-A.xml
node --check src/photo_search/web/search.js
node --check src/photo_search/web/photo-timeline.js
uv run --no-sync ruff check tests/unit/test_p4_ui2_closeout_flow.py tests/unit/test_photo_view_info_ui.py
uv run --no-sync python -m py_compile tests/unit/test_p4_ui2_closeout_flow.py tests/unit/test_photo_view_info_ui.py
git diff --check
```

全部命令通过。JUnit 中新增 UI 断言主要检查源码连接点，并未运行浏览器 JS 事件循环；不能单独证明零结果恢复、真实 stale UI 或请求竞态。浏览器检查使用本机产品服务和隔离合成数据根；未访问 `PhotoSearchData` 私人图库，未加载模型/GPU，未执行大规模性能或全仓测试。本轮临时服务已关闭并核对端口 18821–18825 无监听。

## LUNA High 复核

用户指定的 LUNA High 复核已检查改动搜索恢复路径、请求体及 scope 保存、FILTER_ONLY/地点分支的 session/cursor 作废、零 hits 快照、失败清理、request generation、静态资源版本和测试对齐。结论：**无可执行 correctness finding**。复核指出新增测试以源码断言为主，不能取代实际 JS/浏览器 stale、空结果、race 场景证据；该限制已保留在验收状态中。本次是 LUNA High，不是 DS-H。

## 未验证与停止边界

- `BROWSER_SCREENSHOT_ARTIFACT=CAPTURED`：本机产品页面截图已保存至 `docs/reports/artifacts/`，最终检查需确认工件存在且有效。
- `SEMANTIC_BROWSER=PENDING`：合成环境未加载编码器，浏览器证据仅为 FILTER_ONLY/范围 SQL。
- F04/F05/F06/F07/F08/F09 列明的子路线仍有 P0 待验；后续若继续，应从本卡剩余浏览器门开始，不重做已通过 JUnit。
- `PERF_200K`、视觉/200%/暗色模式、真实图库、模型/GPU、打包和全仓测试均未执行，维持各自 pending。
- 未提交；未启动性能或视觉专项。
