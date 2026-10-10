# P4-PHOTO-LOCAL-OPEN-A 执行报告

日期：2026-10-10。基线：`75ccbe3`。状态：**IMPLEMENTED / 任务卡合成验证通过；Windows 现场手动验收待用户执行**。

## 完成内容

- 公共 `photo-info.js` 对每条 `PRESENT` 位置显示“系统打开原图”和“定位文件位置”，按该行的 `location_id` 发起带 CSRF 的 POST。请求中按钮禁用；关闭或重挂面板后的迟到响应不更新新 UI。五个共用页面更新静态资源版本参数，避免继续使用旧缓存。
- 新增 `POST /api/v1/assets/{asset_id}/local-action`，请求只接受 `action=open|reveal` 与严格正整数 `location_id`；额外字段（包括 path）被拒绝。
- 后端仅在 Windows、服务配置为 loopback 且真实 ASGI 对端是 loopback 时继续。精确匹配 `(asset_id, location_id)`，要求位置 PRESENT 且来源 ONLINE，并复用登记路径、重解析点、根目录包含及 size/mtime 校验；不回退到其他副本。
- `open` 只允许图片扩展名白名单，通过 Windows 文件关联请求打开；`reveal` 只用 `explorer.exe /select,...` 和 `shell=False`。系统调用放在线程中，响应只表示已接受启动请求。未改 schema、原图、预览、查看器状态机或依赖。

## 任务卡验证

- 定向 JUnit：`uv run --cache-dir .uv-cache pytest -q tests/integration/test_p4_photo_local_open.py tests/unit/test_photo_view_info_ui.py --junitxml=test-results/P4-PHOTO-LOCAL-OPEN-A.xml` — **9 passed, 0 failures, 0 errors, 0 skipped**。
- Python 编译：`python -m py_compile src/photo_search/api/files.py src/photo_search/api/local_actions.py src/photo_search/api/models.py src/photo_search/api/routes.py tests/integration/test_p4_photo_local_open.py` — 通过。
- Ruff：上述四个后端 Python 文件与 `tests/integration/test_p4_photo_local_open.py` — 通过。
- 前端语法：`node --check src/photo_search/web/photo-info.js` — 通过。
- `git diff --check` — 通过。
- 合成测试覆盖：双来源同资产精确选中、中文/空格路径、open/reveal adapter 参数、跨资产 location、路径越界、额外恶意 path、缺失/变化文件、OFFLINE 来源、未登录、缺失/错误 CSRF、非 loopback 服务配置/对端、非 Windows 拒绝、UI 重复点击和关闭后迟到响应。

## 未验证项与停止边界

- 未实际调用 Windows 文件关联或启动 Explorer；用户可在正常 Windows 应用中选择一张照片，分别手动试点两个按钮。成功提示仅代表操作系统启动请求已提交。
- 未访问真实 PhotoSearchData、真实照片库或正在运行的用户服务；未跑全仓、浏览器矩阵、GPU/模型、容量或打包验证，未提交/推送。
- 下一已有任务卡为 `P4-STORAGE-GC-A`（推荐 GPT-6 High）；本任务在此停止，不自动开始下一卡。
