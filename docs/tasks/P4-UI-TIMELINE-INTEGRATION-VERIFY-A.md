# P4-UI-TIMELINE-INTEGRATION-VERIFY-A

## 目标

关闭 [P4-UI-TIMELINE-INTEGRATION-A](P4-UI-TIMELINE-INTEGRATION-A.md) 当前唯一阻断：在隔离合成图库中真实打开首页，验证时间轴按钮、浏览/定位/详情返回及亮暗主题。当前实现与 API 定向 JUnit 已通过；不得把 HTTP/API smoke 当成浏览器验收。

## 入口与状态

- 开工读取 `AGENTS.md`、`docs/development-status.md`、本卡、[实现报告](../reports/P4-UI-TIMELINE-INTEGRATION-A.md)及当前 Git 状态。
- 上游 CORE-A 报告：[P4-UI-TIMELINE-CORE-A](../reports/P4-UI-TIMELINE-CORE-A.md)。它仍有独立的 `PERF_200K` 和完整受影响面审查缺口，本卡不关闭 CORE-A。
- 接入任务的基线提交为 `1925fef`。接入代码尚未提交；只操作本卡涉及文件，保留人物忽略状态等其他任务的工作树改动。
- 使用隔离临时数据和合成照片。严禁连接、扫描或修改真实图库及用户服务，不下载模型，不改 OS 主题/权限设置。

## 修改范围

- 首选只验证，不改产品代码。
- 用户已明确授权修复实测的 `insertBefore` DOMException：将时间轴分页 section 追加到其所属 `groups` 容器。若后续发现其他接入缺陷，只允许修改 `src/photo_search/web/app.js`、`src/photo_search/web/index.html`、`src/photo_search/web/photo-timeline.css` 和本卡定向测试；不改 Timeline API、查询、schema、修订协议或人物/地点/搜索实现。
- 更新 `docs/reports/P4-UI-TIMELINE-INTEGRATION-A.md` 与开发状态；保留其他任务当前状态。

## 浏览器验收

在本地隔离服务及至少 3 张内容不同的合成照片上，逐项记录可见结果：

1. 首页出现“时间轴浏览”；点击后挂载共享 `PhotoTimeline`，列表切换可返回旧列表。
2. 默认按可信拍摄时间分组；切换日/月/年可见对应组标题，同日照片不重复标题。
3. 使用“图库筛选”进入 source 范围，确认查询请求带该 `source_id`；翻页不重复/漏掉合成照片，使用 cursor 续页。
4. 选取实际存在的年份和月份并点击“定位到年月”，确认首屏照片落在选定范围；没有月份时显示明确空状态。
5. 点击一张照片打开现有详情；返回后仍处于时间轴，之前照片/锚点位置可恢复。再切回列表并确认列表原筛选/分页工作。
6. 通过浏览器的 `prefers-color-scheme` 仿真检查浅色和暗色：文字、控制、卡片、分组标题和状态可读，组件继续使用共享 UI tokens。
7. 确认 `/search`、人物、地点、合集入口未被此首页接线改写；按页面入口 smoke 可访问，不做这些页面的完整视觉回归。

浏览器动作只访问本地回环地址和合成内容。优先使用隔离浏览器会话；不要在不能确认 URL 的情况下操作已有用户 Chrome 窗口。若环境阻止本地页面访问，不绕过安全边界，记录工具返回的具体阻断并保持 `BROWSER_PENDING`。

## 验收命令

若产品代码有变更，使用项目 Python 3.12.10 / `uv`：

```powershell
$env:UV_CACHE_DIR = (Join-Path (Get-Location).Path '.uv-cache')
uv run --no-sync pytest -q tests/integration/test_p4_ui_timeline_integration.py tests/unit/test_p4_ui_timeline_core.py --basetemp=.pytest-tmp/P4-UI-TIMELINE-INTEGRATION-VERIFY-A --junitxml=test-results/P4-UI-TIMELINE-INTEGRATION-VERIFY-A.xml
uv run --no-sync ruff check tests/integration/test_p4_ui_timeline_integration.py
node --check src/photo_search/web/app.js
node --check src/photo_search/web/photo-timeline.js
git diff --check
```

若产品代码未变，可复用实现报告对应 JUnit/静态检查证据，仅执行实际浏览器闭环并确认报告文件/代码 SHA 未漂移。不要运行全仓测试、20 万性能或真实图库测试；CORE-A 的规模门槛由后续 CORE 专项单独关闭。

## 审查路由

- 本卡是低风险首页展示/接线验证；由独立 **LUNA High** 按 V3.1 §2.4 有界复核完整接入 diff、浏览器证据、时间轴组件 adapter、详情返回和与页面 token 的一致性。
- 若发现 API 查询/分页协议、共享 revision、人工状态或其他高风险状态语义变化，立即扩大为 **DS-H 完整受影响面主审**；只有已证实的 V3.1 §2.2 条件才升级 DS-MAX。无高风险不变量变化时 `SOL_NOT_REQUIRED`。
- 模型路由仅为建议，由用户在 Codex 手动切换；不得声称已切换。

## 完成记录与停止点

- 报告分别记录 `FUNCTIONAL`、`BROWSER`、`REVIEW`，保留 JUnit XML、浏览器截图/视口和失败信息。没有实际浏览器验证就不得写整卡 PASS。
- 更新状态后停止。本卡通过也不自动开始 CORE-A、B/D/E、SEARCH-MAP 或其他 Timeline 页面任务；下一候选仍先关闭 CORE-A 的性能、浏览器和工程审查门槛。
- 若本机浏览器无法打开 localhost，写 `IMPLEMENTED / BROWSER_PENDING` 并交接环境阻断，不用静态检查代替点击验收。

## 推荐模型

执行/有界复核：**LUNA High**。本卡限于页面接线与用户可见浏览行为，不改协议、数据语义或查询逻辑。
