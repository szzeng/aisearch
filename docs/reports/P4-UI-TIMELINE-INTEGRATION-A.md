# P4-UI-TIMELINE-INTEGRATION-A 实施与验证报告

日期：2026-10-13
结论：**IMPLEMENTED / VALIDATION PENDING**。首页已接入共享时间轴并保留列表入口；定向 shell/API smoke 与查询回归通过。实际浏览器点击、年月定位和亮/暗模式视觉检查未能完成，因此不标整卡 PASS。

## 实施

- 首页加载既有 `photo-timeline.js` 和 `photo-timeline.css`，添加时间轴/列表切换入口。
- 通过 `PhotoTimeline.mount` 复用 CORE-A 的页、period 查询和 cursor 翻页；图库筛选传入现有 source scope。没有新增照片查询或改写 API。
- 时间轴卡片使用现有照片详情路由。详情返回时记录并恢复时间锚点；旧列表过滤条件不会伪装成时间轴筛选，页面有明确说明。
- 时间轴样式继续使用 F1 的全局语义色与系统暗色变量。人物、地点、搜索结果、合集页面未改。

## 验证

用户报告的真实时间轴渲染错误已修复：分页 section 追加到 `groups` 容器，不再使用属于 `host` 的 sentinel 作为插入锚点。定向 JUnit：**15 passed / 0 failed / 0 skipped / 0 errors**，包括本卡首页静态资源/API cursor smoke 与 CORE-A 的查询回归。

```powershell
$env:UV_CACHE_DIR = (Join-Path (Get-Location).Path '.uv-cache')
uv run --no-sync pytest -q tests/integration/test_p4_ui_timeline_integration.py tests/unit/test_p4_ui_timeline_core.py --basetemp=.pytest-tmp/P4-UI-TIMELINE-INTEGRATION-VERIFY-A --junitxml=test-results/P4-UI-TIMELINE-INTEGRATION-VERIFY-A.xml
uv run --no-sync ruff check tests/integration/test_p4_ui_timeline_integration.py
node --check src/photo_search/web/app.js
node --check src/photo_search/web/photo-timeline.js
git diff --check
```

JUnit XML：[test-results/P4-UI-TIMELINE-INTEGRATION-VERIFY-A.xml](../../test-results/P4-UI-TIMELINE-INTEGRATION-VERIFY-A.xml)。Ruff、两个 JS 语法检查与 diff-check 通过。

首次测试夹具生成了三张相同 JPEG，扫描按内容 SHA 正确去重，导致预期页数断言失败；修正为内容各异的合成照片后，同一测试命令通过。

## 尚未验证

- **BROWSER_PENDING**：用户实测复现 `insertBefore` DOMException 后，已将分页 section 改为 `groups.append(section)`；本次定向测试通过。Codex in-app browser 打开本地 smoke 服务曾返回 `ERR_BLOCKED_BY_CLIENT`；Windows Chrome Computer Use 因无法安全确认当前窗口 URL 而停止。修复后的真实浏览器点击、年月定位、详情返回及主题检查仍未完成，不能以 API/JUnit 通过替代。
- 因浏览器阻断，未实测首页按钮点击/分组渲染、年月跳转、照片详情返回、暗色模式或搜索/人物/地点页面的视觉回归。HTTP shell/static asset 检查和 API 两页 cursor 验证不替代这些证据。
- 本次定向变更为共享时间轴组件的一行 DOM 插入修复，按用户明确要求超出原验证卡限定文件；没有修改 API、查询、schema 或修订协议。未做独立模型审查、全仓测试、真实图库、20 万性能或 CORE-A 完整验收。CORE-A 的 `PERF_200K`、浏览器和独立工程复核仍按原报告保持 pending。

未提交变更。工作区中的人物忽略状态任务改动及其他既有内容均保留。

## 下一步

先完成 [P4-UI-TIMELINE-INTEGRATION-VERIFY-A](../tasks/P4-UI-TIMELINE-INTEGRATION-VERIFY-A.md) 的合成浏览器闭环；其后仍须按开发状态关闭 CORE-A 自身的性能、浏览器和审查门槛，不能据此自动启动后续页面任务。
