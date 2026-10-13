# P4-UI-SHELL-A 执行报告

日期：2026-10-13。状态：**IMPLEMENTED / VALIDATION PENDING**。

## 实施范围

- 五个现有页面共用照片、人物、地点主导航；搜索、地图、图库管理保留为可工作的辅助入口。人物导航由现有 `/capabilities/page` schema 门控。
- 地图页面原先没有人物入口；独立复核发现该缺口后，补入同一隐藏入口，并让现有 `map.js` 只读读取已有 `/api/v1/capabilities/page` 后按 schema 门控显示。没有新接口、写请求或权限语义。
- 首页 `/` 默认显示照片浏览；`/?view=sources` 显示原图库管理视图。有效 `#/asset/{id}` 深链先打开详情，已有详情返回上下文仍优先恢复照片浏览状态。
- 保留旧 `/search` 页面、原 `search-form`、人物相册局部搜索及地点/地图互链；没有添加页头占位搜索框或搜索监听。地图人物入口仅复用现有能力只读 API。
- `styles.css` 新增范围化外壳样式与窄屏布局；地图页改用外壳布局和页面范围选择器。所有改动静态资源的版本参数已更新。
- 新增 `tests/unit/test_p4_ui_shell.py` 覆盖五页入口、首页/管理/未知参数路由、详情深链与返回优先级、人物能力门控及不触发 API 请求。

## 验证

任务卡定向命令：

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
uv run --no-sync pytest -q tests/unit/test_p4_ui_shell.py tests/unit/test_api_frontend.py tests/integration/test_api_frontend_routes.py tests/unit/test_photo_view_info_ui.py --basetemp=.pytest-tmp/P4-UI-SHELL-A --junitxml=test-results/P4-UI-SHELL-A.xml
node --check src/photo_search/web/app.js
node --check src/photo_search/web/map.js
uv run --no-sync ruff check tests/unit/test_p4_ui_shell.py
git diff --check
```

- JUnit：**101 passed / 5 failed / 0 errors / 0 skipped**；文件 `test-results/P4-UI-SHELL-A.xml`。新增路由/导航测试通过，`test_api_frontend_routes.py` 与详情返回行为测试通过。
- 5 个失败与本卡差异无关：4 个既有人物页断言要求未由当前代码提供的映射面/API 文本/错误码；1 个断言期待旧 `persons-albums.js` 版本号。任务开工前这些产品文件和断言均未在工作树改动；本卡未扩大到人物任务修复。
- 新增路由定向复跑：**4 passed**（新测试、人物导航门控、首页详情返回）。
- `node --check src/photo_search/web/app.js`：通过。
- `node --check src/photo_search/web/map.js`：通过。
- `ruff check tests/unit/test_p4_ui_shell.py`：通过。
- `git diff --check`：通过。
- 隔离浏览器服务使用 `.tmp/P4-UI-SHELL-A/data`（1 张脚本生成的合成 JPEG）和独立 `127.0.0.1:8001`；另用空的 `.tmp/P4-UI-SHELL-A/empty-data`、`127.0.0.1:8002` 检查无图库入口。两服务均未加载模型，验收后已关闭。
- 浏览器真实检查五路由 `/`、`/search`、`/persons`、`/places`、`/map`，及 `/?view=sources`。在 1440×900、1024×768、640×800 和 720×450 视口下，当前导航项均正确、无横向溢出；旧搜索表单、人物/地点局部筛选、地图互链和管理入口可见。空图库管理详情可展开到“注册来源”；合成缩略图可打开 `#/asset/1` 并通过“返回浏览”恢复首页。键盘 Tab 可聚焦首页/主导航链接并显示实线焦点轮廓。页面控制台无错误。
- 720×450 仅作为 1440×900 在 200% 缩放下的有效 CSS 视口近似；浏览器缩放快捷键未改变 `visualViewport.scale`（仍为 1），所以真实 200% 缩放未验。当前浏览器 `prefers-color-scheme` 为 light，接口没有暗色仿真控制，暗色可读性未验。桌面和窄视口截图已在会话中查看，但没有持久化为项目截图文件。
- 用户后来提供的 `http://127.0.0.1:8000/` 是真实图库服务。该页面只返回会话要求提示，受保护的图片接口响应 `[SESSION_REQUIRED]`；未提交真实会话凭据、未加载任何照片。立即关闭该标签，后续所有浏览器检查仅访问上面的隔离合成服务。此后未读取/修改真实图库或 `PhotoSearchData`。
- 独立 LUNA High 首轮发现的地图人物入口与缓存版本问题已修复；修复轮确认 CLOSED、无新增 finding。按用户指定路线记录为 LUNA High review PASS，不是 DS-H。
- 合成库仅登记并扫描脚本生成的 `synthetic-ui.jpg`，并生成一张合成缩略图；未对真实图库执行建库、刷新或识别。未启动 AI 模型；两个隔离服务已停止。

## 结论与交接

代码改动已实施；LUNA High 独立复核 PASS，浏览器五路由与隔离合成流程已完成。但任务卡 pytest 命令仍有 5 项与本卡无关的既有失败，真实 200% 缩放、暗色视觉和截图落盘也未完成。因此本卡保持 **IMPLEMENTED / VALIDATION PENDING**，不标 PASS。下一候选仍为 [P4-UI-SEARCH-C](../tasks/P4-UI-SEARCH-C.md)；须等本卡验收闭环后再开始。
