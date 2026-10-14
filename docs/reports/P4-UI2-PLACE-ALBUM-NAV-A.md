# P4-UI2-PLACE-ALBUM-NAV-A 执行报告

日期：2026-10-14
起始 HEAD：`51d46b576127e1f21a000ffb3b94cc683ebb2480`
结论：**IMPLEMENTED / ACCEPTANCE PARTIAL**。用户明确要求不做浏览器测试，记为 `BROWSER_NOT_RUN_USER_REQUEST`；未声明产品浏览器或视觉 PASS。

## 实施摘要

- 地点相册列表与详情改为互斥视图；详情直接显示地点标题、返回入口和共享照片时间轴。
- 以 `album_kind + album_key` 驱动深链、卡片进入、Back/Forward 和安全返回；目录状态轻量保存在 history state，时间轴只保存当前及少量近期 scope 的年月/分桶/锚点/照片 ID/revision 摘要。
- 详情内更正与异步刷新不通过重建相册导航；管理 hash 优先保留原管理入口，详情模式延迟管理区加载。
- 照片时间轴挂载时比较恢复快照 revision 与当前首屏响应。revision 不匹配会清空旧筛选、锚点和选中照片，按当前 scope 重取权威第一页，并显示更新提示。
- 未改后端、SQL、人工归属写接口或数据库协议；未访问真实图库。

## 定向行为与测试

新增地点相册导航 Node DOM 行为覆盖卡片进入/返回/Back/Forward、精确 CN 深链、非法 scope、乱序响应、管理 hash 优先级、更正刷新不 remount；时间轴回归覆盖匹配与过期 revision 的跨 mount 恢复。

运行命令：

```powershell
$env:UV_CACHE_DIR='.uv-cache'
uv run pytest -q tests/unit/test_p4_ui2_place_album_nav_a.py tests/unit/test_p4_place_unified_ui.py tests/unit/test_p4_place_cn_ui.py tests/unit/test_p4_ui_timeline_core.py tests/unit/test_p4_ui_photos.py --basetemp=.pytest-tmp/P4-UI2-PLACE-ALBUM-NAV-A --junitxml=test-results/P4-UI2-PLACE-ALBUM-NAV-A.xml
node --check src/photo_search/web/photo-timeline.js
node --check src/photo_search/web/places-albums.js
node --check src/photo_search/web/places.js
git diff --check
```

结果：**34 passed / 0 failed / 0 errors / 0 skipped**。JUnit：`test-results/P4-UI2-PLACE-ALBUM-NAV-A.xml`。三份 JS 语法检查与 `git diff --check` 通过。

## 独立复核

用户指定 Luna High。完整前端导航状态/深链/返回/旧入口复核发现一项：快照保存的 revision 没有在跨 mount 恢复时验证，导致旧筛选和锚点重放。已增加 revision 比较、权威第一页回查、可见退化提示及定向回归。第一次 repair-only 复核发现 guard 在首次 revision 匹配后未消费；现已修正为只校验初次跨 mount 恢复，并增加“匹配恢复→revision 增长→用户筛选仍生效”的回归。Luna High 二次 repair-only 复核确认 finding 关闭，此修复边界无新 finding（不是 DS-H）。

## 未验证范围

- `BROWSER_NOT_RUN_USER_REQUEST`：没有真实应用浏览器交互、截图、computed style 或视觉检查；任务卡浏览器验收仍 pending，因此不标记整卡 PASS。
- N05 同名上级及成员数据真值、N08 实际更正写入/失败与撤销行为、N09 原生 200%/浅暗色/键盘目视，以及更多规模滚动路径没有在本轮真实浏览器验证。
- 未跑全仓、真实图库、`PERF_200K` / `CHECK_300K`、模型/GPU、打包或发布检查。此前的规模门禁继续 pending。
- 本任务变更已本地提交，未推送。
