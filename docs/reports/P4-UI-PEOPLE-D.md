# P4-UI-PEOPLE-D 实施与验证报告

日期：2026-10-14
结论：**IMPLEMENTED / VALIDATION PENDING**。人物与合集相册已接入共享时间轴，保留旧局部搜图与既有人物整理操作；定向 JUnit 为 **31/0/0/0**，Ruff、Node 语法检查和 `git diff --check` 通过。用户指定的 LUNA High 受影响面复核完成且无剩余实现 findings。任务卡要求的合成产品浏览器交互/截图，以及继承的 `PERF_200K` 仍 pending，因此不标整卡 PASS。

用户明确要求在 A/C/CORE/B 的浏览器或性能验收门尚未全部关闭时开始 D。该要求覆盖本卡开始顺序，不改变前置任务的验收状态。尝试通过本地文件 URL 打开演示页时，浏览器安全策略拒绝访问并明确禁止绕过；未使用本地服务或其他浏览器路径规避此限制，因此没有产品浏览器观察或截图。

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

## 未完成验收门

- `BROWSER_PENDING`：没有合成产品浏览器中的人物/合集点击与切换、选择/取消、忽略恢复、待整理单脸对话框、预览返回和截图。尝试打开 `file://` 演示页面被浏览器安全策略拒绝；按策略未绕过。
- `PERF_200K_PENDING`：沿用 CORE 的 20 万性能验收未关闭状态；没有新增大数据夹具或声称性能达标。D 的人物/合集局部大范围性能依赖 CORE 证据与差异场景验证。
- 前置 A/C/CORE/B 的浏览器、性能及各自复核状态继续按各自报告保留，不因用户要求先开始 D 而关闭。

`tests/integration/test_p3_face_correction_lifecycle.py` 与 `docs/reports/P4-UI-TIMELINE-CORE-A.md` 在 D 开始前已是工作区未提交差异；本卡没有修改前者，后者的既有内容也保留。复核未将它们归因于 D。没有提交或推送。完成本卡后停止，后续先补 D 的浏览器与性能验收门。
