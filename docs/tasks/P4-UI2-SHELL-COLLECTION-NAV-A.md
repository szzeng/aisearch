# P4-UI2-SHELL-COLLECTION-NAV-A — 跨页面保留照片合集导航

**状态**：IMPLEMENTED / SCOPED PASS
**日期**：2026-10-21
**优先级**：P2（导航可达性）
**范围**：公共应用侧栏的照片合集入口在照片、搜索、人物、地点和地图页面一致可见。

## 目标与验收

用户从左侧进入人物、地点等独立页面时，“照片合集”仍应留在左侧主要导航，并可点击返回合集目录。导航持久可见指入口在各页面模板持续存在；当前路由仍可标记自己的 `aria-current`。不改变普通/智能合集数据、内容状态或路由语义。

- 五个公共 shell 页面均包含 `data-shell-page="photo-collections" href="/?view=photo-collections"`：`index.html`、`search.html`、`persons.html`、`places.html`、`map.html`。
- 静态导航回归覆盖五页，定向 UI shell JUnit 通过。
- Codex 内置浏览器使用本机隔离合成数据点击“人物→照片合集”和“地点→照片合集”，确认入口在各页可见且点击后打开合集目录；若浏览器/服务受限，诚实记录为 PENDING。

## 性能边界

**链路与风险：** 独立页面模板导航渲染；入口遗漏会阻断合集页面返回。
**做法与边界：** 仅补静态锚点，复用既有合集路由；不增加请求、脚本、存储或业务状态。
**验证与停止：** 五页 DOM 合同与两条浏览器导航点击；确认无新增数据/API 工作后停止，不做图库/模型/性能测试。

## 交付约束

只改相关 HTML、静态模板测试及本卡报告/状态摘要。保留并行工作树改动，不触碰真实图库，不提交或推送。低风险展示改动按要求进行有界 Luna H 独立复核。

## 实施与验证结果（2026-10-21）

原因是独立 shell 页有各自的静态侧栏：仅 `index.html` 原本包含照片合集入口；搜索、人物、地点、地图页缺少对应锚点。现将同一入口补到这四页，入口指向已有 `/?view=photo-collections` 页面。`tests/unit/test_p4_ui_shell.py` 增加五页一致性断言。

- 定向 JUnit：`test-results/P4-UI2-SHELL-COLLECTION-NAV-A-r1.xml`，**4 passed / 0 failed / 0 errors / 0 skipped**。
- Codex 内置浏览器：启动绑定 `127.0.0.1` 的应用，使用项目内隔离的空合成数据目录；已认证 API 返回 schema 43 / `ok`。人物页和地点页均显示“照片合集”，点击两页侧栏入口均进入 `/?view=photo-collections`，可见合集目录。测试服务已关闭；未使用真实照片库。浏览器输出为 AX 可访问性树，未保存截图文件。
- `git diff --check` 通过。改动只有静态链接，无新增请求、模型调用或数据写入。
- 用户已在自己的环境实测并确认导航恢复正常。
- Luna H（gpt-6-luna/high）有界独立复核 **PASS**；五页链接/路由无风险。`index.html` 和 `search.html` 的资源缓存版本变更属于并行改动，不纳入本任务。
