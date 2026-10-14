# P4-UI-PEOPLE-D 实施与验证报告

日期：2026-10-14
结论：**IMPLEMENTED / BROWSER INTERACTION VERIFIED / PERF_200K PENDING**。人物与合集相册已接入共享时间轴，保留旧局部搜图与既有人物整理操作；定向 JUnit 为 **31/0/0/0**，Ruff、Node 语法检查和 `git diff --check` 通过。用户指定的 LUNA High 受影响面复核完成且无剩余实现 findings。合成产品浏览器交互已观察并保存截图；继承的 `PERF_200K` 仍 pending，因此不标整卡 PASS。

用户明确要求在 A/C/CORE/B 的浏览器或性能验收门尚未全部关闭时开始 D。该要求覆盖本卡开始顺序，不改变前置任务的验收状态。早期尝试通过本地文件 URL 打开演示页时被浏览器安全策略拒绝；随后用户明确授权复用 P4-UI-PLACES-E 的本机产品服务与隔离合成数据方法，按该方法完成本次浏览器走查。

## 实施范围

- `src/photo_search/web/persons.html`：人物与合集照片区域增加共享时间轴 host；保留人物局部搜图、查看器及现有整理按钮 ID/入口。加载 CORE 时间轴脚本和样式，并更新本页资源版本。
- `src/photo_search/web/persons.css`：调整人物头像/卡片、合集、整理操作和人物/合集照片时间轴的层级与样式；合集游标失效后隐藏会重复同一旧游标的通用重试入口。
- `src/photo_search/web/persons-albums.js`：人物 scope 使用 canonical person id，合集使用 collection id；通过 `/api/v1/timeline/assets` 复用 CORE 有界分页和日期摘要/定位。预览导航保留最多 400 个已加载 asset id 并同步时间线 `next_cursor`，续页追加顺序 ID；未知/待就绪日期和 `capture`/`file_time` 标签使用中文文案。
- 合集首屏和续页都核对服务端响应的合集 id、instance token、revision。失配时不显示该响应，要求显式重载；重载会刷新合集列表并重新读取目标合集当前实例，清空年月/日期筛选后重挂时间轴。合集被删除时提示返回列表。没有更改后端/API、日期语义、集合成员或人物写协议。
- `tests/unit/test_p4_ui_people_d.py`：新增页面时间轴挂载、旧搜图/整理路径、合集 revision 失配恢复、viewer 游标桥接、有限照片 ID 和日期文案断言。沿用人物预览行为测试、CORE 时间线测试和时间线 API 集成测试。
- `tests/unit/test_person_count_ui.py`：修正既有“暂时忽略”界面文案断言。`tests/unit/test_photo_view_info_ui.py` 的 D 相关缓存版本断言随页面版本更新；其余既有差异保留。

## 验证

任务卡关联 JUnit（生成 XML）：**31 tests / 0 failures / 0 errors / 0 skipped**，文件为 `test-results/P4-UI-PEOPLE-D.xml`。

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
$env:PYTHONUTF8 = '1'
uv run --no-sync pytest -q tests/unit/test_person_hide_selection_ui.py tests/unit/test_person_inbox_hide_ui.py tests/unit/test_person_inbox_create_ui.py tests/unit/test_p4_person_collection_ui.py tests/unit/test_person_count_ui.py tests/unit/test_person_quality_ui.py tests/unit/test_person_preview_navigation_ui.py tests/unit/test_p4_ui_people_d.py tests/unit/test_photo_view_info_ui.py tests/unit/test_p4_ui_timeline_core.py tests/integration/test_p4_ui_timeline_integration.py --basetemp=.pytest-tmp/P4-UI-PEOPLE-D-final --junitxml=test-results/P4-UI-PEOPLE-D.xml
node --check src/photo_search/web/persons-albums.js
node --check src/photo_search/web/persons.js
uv run --no-sync ruff check tests/unit/test_p4_ui_people_d.py tests/unit/test_person_count_ui.py
git diff --check
```

JUnit 主要覆盖既有人物工作流/预览、CORE 时间线 API 与页面集成及新增静态连接断言；它没有代替合成浏览器交互观察。未访问真实照片库或加载 AI 模型。

## LUNA High 受影响面复核

用户指定子代理使用 LUNA High 完整复核人物/合集时间线 scope、合集实例及成员 revision、失败/重载路径、有限查看器 ID 与分页、日期标签、集合“人物”页和原人物操作入口。复核提出并跟踪修复：合集元数据变化时首屏可能静默切到新范围；刷新合集第一页会误判较后页面的合集已删除；查看器续页游标/照片 ID 未同步；`capture`/`file_time` 枚举需转为本地文案。上述均已修复并重新检查。最终结论：**无剩余实现 findings**。这是用户指定的 LUNA High 复核，不是 DS-H，也不构成浏览器或性能验收。

## 合成产品浏览器观察（2026-10-14）

使用隔离数据根 `.tmp/P4-UI-PEOPLE-D/browser/PhotoSearchData` 和本机 loopback 产品服务；合成库含 64 名人物、4 张主要照片及隔离待整理/忽略记录，没有访问真实图库或加载模型。人物列表的第一页选中爸爸，再加载下一页选中末页人物，观察到选择计数从 1 到 2；点“清空选择”后计数回到 0，跨页保留与取消均可见。

打开爸爸相册可见 2 张照片，可信拍摄日期 0、未知日期 0、待就绪 2。打开照片详情并关闭后，人物范围、2 张照片和日期摘要恢复。家庭合集由爸爸/妈妈构成，合集照片页显示 3 张唯一照片（共用合照去重）；打开合集“人物”页可见爸爸和妈妈两名成员。照片预览返回后仍在原合集 3 张范围。

从整理页打开“这是谁？”单脸操作，观察到仅处理所选脸的说明、创建人物入口、分配人物入口及取消按钮；点击取消后待整理列表仍在。已忽略人脸区域可展开，并显示暂时忽略状态和候选参考操作。人物列表另通过界面将一个合成人物暂时忽略，再进入“查看已忽略”选中并恢复；页面显示“已恢复 1 个人物”，恢复列表变空，回到人物列表仍可见该人物。任务范围内抽查的浏览器 console 没有 error/warn。最终人物列表截图为 [P4-UI-PEOPLE-D 浏览器截图](artifacts/P4-UI-PEOPLE-D-browser.jpg)。

`BROWSER_INTERACTION=VERIFIED`，截图工件已保存。该观察只验证小型合成数据的可见交互，不构成性能、真实图库或整卡 PASS。

## 未完成验收门

- `PERF_200K_PENDING`：沿用 CORE 的 20 万性能验收未关闭状态；没有新增大数据夹具或声称性能达标。D 的人物/合集局部大范围性能依赖 CORE 证据与差异场景验证。
- 前置 A/C/CORE/B 的浏览器、性能及各自复核状态继续按各自报告保留，不因用户要求先开始 D 而关闭。

`tests/integration/test_p3_face_correction_lifecycle.py` 与 `docs/reports/P4-UI-TIMELINE-CORE-A.md` 在 D 开始前已是工作区未提交差异；本卡没有修改前者，后者的既有内容也保留。复核未将它们归因于 D。本次提交仅包含 D 验收记录和截图，不推送。浏览器交互门已关闭；`PERF_200K` 仍是 D 的未完成验收门。
