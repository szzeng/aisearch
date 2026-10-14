# P4-UI-PLACES-E 实施与验证报告

日期：2026-10-14
结论：**IMPLEMENTED / ACCEPTANCE PARTIAL**。地点目录相册接入共享时间轴；新增地点成员 SQL scope，保留地点人工整理入口和地图查询协议。定向 JUnit **32/0/0/0**，Ruff、Python 编译、四个相关 JS 的 `node --check`、`git diff --check` 通过。用户指定的 LUNA High 受影响面复核完成，无剩余阻断代码 finding；该复核不是 DS-H。有效地点成员规模性能与可落盘的浏览器截图仍未完成，因此不标整卡 PASS。

用户直接要求开始 E，因此按该指示实施；这不关闭 A/C/CORE/B/D 已记录的浏览器或性能门。用户要求复用原 30 万夹具且不建新夹具，本轮遵循此范围。

## 实施内容

- `src/photo_search/places/timeline.py` 新增地点成员 SQL predicate：非 CN 路径保留人工归属优先、有效地点包候选/排序资格、人工排除、照片与 PRESENT location 资格；CN 城市/省份/区县复用 `cn_facets.members_sql`。结果仍由共享时间轴按日期 seek/keyset 和 LIMIT 读取，不收集地点资产 ID。游标 revision 组合 catalog filter、地点 suggestion 与活动包 revision。省级外部 ref 按 800 条批量查询；从包 refs 读取的 place 主键经过 `int` 归一后内联为数字 IN 列表，避免大量省内城市在 manual/automatic 分支累积超过 SQLite bind 总数。
- `api/timeline.py` 与 `/api/v1/timeline/assets`、`periods` 接收 `place_kind/place_key`，日期摘要和资产页使用同一成员 scope；分页游标失效时返回既有 409 revision 错误。
- `photo-timeline.js` 添加地点身份参数及地点包/suggestion revision 比对。`places-albums.js` 用共享时间轴替换地点相册的旧 48 条 offset 页面；保留修正/查看器流程和目录返回。未显示尚未连通的“搜索此地点”按钮。
- `places.html/css` 调整目录卡片、相册标题、地点工具和日期卡片视觉；旧工具 DOM ID 保留。`map.html/css` 只更新视觉和资源版本，`map.js` 与地图成员/viewport 行为未改。地点相册脚本更新为 E 版 cache key，避免继续命中依赖已移除 `#album-more` 的旧脚本。
- 小样本成员等价覆盖复用既有测试 fixture：人工 city 成员、CN district 成员/人工更改与排除后的失效、已有 auto-flow fixture 中自动候选、GPS 异常/缺失、重复文件/距离边界的 city 和 Zhejiang province 等价，以及地点更新后的游标过期。新增 1,200 个城市主键 SQL 谓词测试，确认 predicate 执行不消耗 SQLite bind slots。

## 定向验证

JUnit XML：`test-results/P4-UI-PLACES-E.xml`，**32 tests / 0 failures / 0 errors / 0 skipped**。

```powershell
$env:PYTHONPATH = (Get-Location).Path
$env:UV_CACHE_DIR = '.uv-cache'
uv run --no-sync pytest -q tests/unit/test_p4_place_unified_ui.py tests/unit/test_p4_place_cn_ui.py tests/unit/test_p4_place_map_review_ui.py tests/unit/test_photo_view_info_ui.py tests/unit/test_p4_ui_timeline_core.py tests/integration/test_p4_ui_timeline_integration.py tests/integration/test_p4_place_cn.py::test_cn_current_members_manual_exclusion_and_cursor tests/integration/test_p4_place_auto_flow.py::test_place_album_flow_groups_deduplicates_pages_and_respects_manual_control --basetemp=.pytest-tmp\P4-UI-PLACES-E --junitxml=test-results\P4-UI-PLACES-E.xml
.\.venv\Scripts\ruff.exe check src/photo_search/places/timeline.py src/photo_search/api/timeline.py src/photo_search/api/routes.py tests/unit/test_p4_ui_timeline_core.py tests/unit/test_photo_view_info_ui.py tests/integration/test_p4_ui_timeline_integration.py tests/integration/test_p4_place_cn.py tests/integration/test_p4_place_auto_flow.py
.\.venv\Scripts\python.exe -m py_compile src/photo_search/places/timeline.py src/photo_search/api/timeline.py src/photo_search/api/routes.py
node --check src/photo_search/web/places.js
node --check src/photo_search/web/places-albums.js
node --check src/photo_search/web/photo-timeline.js
node --check src/photo_search/web/map.js
git diff --check
```

## 合成浏览器观察

使用隔离数据根 `.tmp/P4-UI-PLACES-E/browser/PhotoSearchData`，其中 4 张照片由任务内 synthetic fixture 生成并分配到测试城市“东京”；未访问真实照片库。地点卡进入相册后共享时间轴显示 **4 张有效照片，其中 4 张在日期待就绪桶**；打开照片详情后关闭，原地点相册、年月控件与摘要恢复。地点工具折叠区可以展开，原补齐入口和目录/建议/批量/日志区域仍可访问。地图页加载了新版内部卡片样式，并准确显示当前库 **0 张带位置照片**和空状态；本样本不含 GPS，未覆盖地图中有成员时的照片详情往返。

本地产品浏览器截图已通过 CUA 捕获观察，但 CUA 输出未提供写入项目目录的截图文件路径，故任务卡要求的可复查截图工件记为 **BROWSER_SCREENSHOT_PENDING**；浏览器闭环是已观察，不将其提升成截图/完整视觉 PASS。浏览器初次请求使用的本地一次性 session token 已由应用访问日志过滤，不把凭证写入报告。

### 2026-10-14 浏览器复测

复用上述 `.tmp/P4-UI-PLACES-E/browser/PhotoSearchData` 和 4 张合成照片，通过 Codex in-app browser 在本机 loopback 页面重新走查；启动服务未使用 `--open-browser`，一次性 session 凭证未写入报告。实际观察结果：

- `/places` 显示东京 / 日本 / 4 张照片。进入后显示 **4 张有效照片**，其中可信拍摄日期 0、日期未知 0、日期待就绪 4；点击「日期待就绪」筛选后仍显示 4 张。
- 打开首张照片显示详情，关闭后回到东京时间轴，4 张范围和待就绪桶仍可见；随后重新进入并单独选择「日期待就绪」筛选，结果仍为 4 张。
- 展开「地点工具」后「补齐照片地点」入口可见。
- `/map` 显示 **0 张带位置照片**和“当前图库没有带位置的照片”空状态；截图观察到地图画布及缩放控件正常呈现。样本没有 GPS，未覆盖有成员时的 viewport 照片详情往返。
- 本次访问日志中过滤凭证后，session、Places、静态资源、timeline periods、第一页、pending bucket、缩略图和 map 请求均为 HTTP 200 或缩略图缓存 304；未检查浏览器 console。

CUA 返回了可供当场观察的截图，但没有项目内可链接的持久文件路径；仍记 **BROWSER_SCREENSHOT_PENDING**。本次复测补实浏览器交互证据，不替代持久截图工件、真实 GPS 地图成员或规模性能门。

## 原 30 万夹具复用与性能边界

复用既有 CORE 隔离副本 `.tmp/P4-UI-TIMELINE-CORE-A/scale-300k/catalog/catalog.db`，没有创建新的规模夹具或修改源 fixture。其 catalog 有 300,000 个 assets 和 300,000 个 PRESENT locations，但 `asset_metadata=0`、`places=0`、`asset_place_assignments=0`。因此只对空的 city scope 执行了诊断查询，5 次样本返回均为 0：耗时 **2985.45、1927.77、1957.59、1959.21、1927.69 ms**，P50 **1957.59 ms**、max **2985.45 ms**。EXPLAIN 包含 assets 扫描、关联资格查询及临时排序 B-tree。它只能说明这个空 scope 查询的成本，不能代表高成员地点页、人工归属或 CN scope 性能。

用户指定不新建夹具；原 30 万库没有地点成员。任务卡的 20 万高成员城市/稀疏城市/CN 区县 ≥30 次暖机验证未完成，30 万有代表性的高成员地点查询也没有成立。因此 `PERF_200K=PENDING`、`CHECK_300K=PENDING`。不据空 scope 结果声称地点性能通过。

## LUNA High 受影响面复核

子代理只读检查了地点 timeline SQL、旧相册成员链、CN facet、HTTP 参数、共享时间轴游标与前端 dispose、地图 JS/事件路径、旧地点工具入口和测试证据。首轮发现并修复：

1. `places-albums.js` 仍使用旧 cache key；旧脚本在已移除的 `#album-more` 上注册监听器会使页面初始化报错。更新为 `p4-ui-places-e-1`，并同步查看器静态断言。
2. 省级成员查询逐 city 读取 ref 且分块绑定值仍会累计超过旧 SQLite 总变量限制。现改成最多 800 个外部 ID 一批查 ref，并以内联的 int 规范化 place 主键构成 IN predicate；新增 1,200 ID SQL 回归。
3. 旧 city/province 比较此前覆盖较窄。现复用已有 auto-flow fixture 对自动 city、人工排除后的 city 与 Zhejiang province 逐资产对比旧成员 API/helper 和新 timeline。

修复复核结论：无新的阻断代码 finding。评审明确属于用户指定的 **LUNA High**，不是 DS-H，也未独立重跑测试。复核指出非 CN 排除等价仍不是所有组合的全覆盖；对应现有小样本加入了人工排除比较，但未建立新的大规模地点夹具/性能证据。

## 未完成项与交接

- `BROWSER_SCREENSHOT_PENDING`：地点页面闭环已在合成产品中观察，截图尚未成为项目内可链接文件。
- `PERF_200K=PENDING`、`CHECK_300K=PENDING`：旧 30 万 fixture 没有地点成员；按用户指示不新增规模夹具。
- 未运行全仓套件、真实图库、模型/GPU、真实地点包构建或地图 PBF。
- 前置 A/C/CORE/B/D 的未验收门仍按各自报告保留。本报告不追认这些门通过。
- 下一候选 SEARCH-MAP 卡已存在；E 的性能和截图验收门关闭前不自动开始它。

本地提交只收录本任务文件及对应状态摘要；初始工作树中原已有未提交的 `docs/development-status.md`、`docs/reports/P4-UI-TIMELINE-CORE-A.md`、`tests/integration/test_p3_face_correction_lifecycle.py` 差异；保留这些内容，未把 CORE 报告或 face lifecycle 测试改动归因于 E。
