# P4-UI2-SHELL-COLLECTION-NAV-A 执行报告

**日期：** 2026-10-21
**状态：** IMPLEMENTED / SCOPED PASS
**范围：** 照片合集入口在公共侧栏的五个 shell 页面持续可见。

## 原因与修复

左侧“人物”“地点”会导航到独立 HTML 页面，每个页面都有独立的静态侧栏。首页 `index.html` 已经列出“照片合集”，但 `search.html`、`persons.html`、`places.html` 和 `map.html` 漏了这个链接。浏览这些页面时侧栏仍在，但入口不在 DOM 中，因此用户感觉合集消失。合集数据并未删除或隐藏在首页内部。

在这四个页面补入统一链接 `/?view=photo-collections`，并扩展 shell 单元测试，要求照片、搜索、人物、地点、地图五页都含该导航项。路由和合集数据语义未改。

## 验证

- 定向 JUnit：`uv run --cache-dir .uv-cache --python 3.12.10 pytest -q tests/unit/test_p4_ui_shell.py --junitxml=test-results/P4-UI2-SHELL-COLLECTION-NAV-A-r1.xml --basetemp=.pytest-tmp/P4-UI2-SHELL-COLLECTION-NAV-A-r1`，**4 passed / 0 failed / 0 errors / 0 skipped**。
- Codex 内置浏览器使用绑定 `127.0.0.1` 的本机服务与项目目录 `.tmp/P4-UI2-SHELL-COLLECTION-NAV-A/PhotoSearchData` 中的隔离空合成数据。认证 API 返回 schema 43 / `ok`。打开 `/persons` 和 `/places` 均观察到“照片合集”侧栏链接；分别点击后都进入 `/?view=photo-collections`，并显示合集目录区域。测试服务随后关闭。
- `git diff --check` 通过。该改动只有静态导航项，不增加 API、存储、模型调用或写入。
- 用户随后在自己的环境实测并确认导航恢复正常。
- Luna H（gpt-6-luna/high）有界独立复核 **PASS**：五页入口及路由一致，无导航风险。`index.html` 的 `app.js` 和 `search.html` 的 `search.js` 缓存版本变更是并行内容，未计入本次修改。
- 未访问真实照片库。使用浏览器 AX 树检查，无截图导出文件。
