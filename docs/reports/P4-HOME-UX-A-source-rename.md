# 首页修改图库名称（2026-10-04）

用户补充：“首页增加可以修改图库名字的功能”。沿用同会话的个人使用简单验证要求，作为 P4-HOME-UX-A 的一个补充小功能完成；其他未完成的首页/搜索验收状态保持。

## 修改与使用

每张首页图库卡片直接显示“修改名称”。点击后在卡片内编辑，支持保存和取消，拒绝空白名称和超过200字符；保存中禁止重复提交，失败时保留输入并允许重试。成功后更新卡片标题、图库筛选与来源明细缓存，下一次浏览使用新名称。建库运行中也可以改名。

新增 `PATCH /api/v1/sources/{source_id}`，请求仅允许 `{display_name: string}`，成功返回200及轻量 `SourceSummary`，不返回根路径、统计或进度。沿用全局 Session/Host/Origin/CSRF 保护。首尾空白被去除；不存在的来源返回 `SOURCE_NOT_FOUND`，空白名称沿用 `INVALID_PATH`，形状/长度不合法沿用 `VALIDATION_ERROR`，均为现有错误信封。

`CatalogService.rename_source` 在既有短写事务中仅更新 `source_roots.display_name`；同名保存不重复更新。ADR-0002 §3.2 明确该字段仅展示、不参与身份。本功能不扫描或检查原目录，不修改来源ID、根路径、扫描/建库状态、资产、位置或任务，不要求重新建库。离线图库也可改名，名称持久保存。首页脚本及样式版本改为 `p4-source-rename-a-1`，避免旧缓存隐藏新入口。

未读取/更改真实图库或真实 PhotoSearchData；保留上轮建库排队改动及已有临时目录审计文件，未提交或PUSH。

## 简单验证

Python3.12.10，现有 uv 环境，无依赖变更。最终命令及退出码：

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
uv run --no-sync pytest -q tests/unit/test_api_source_rename_ui.py tests/unit/test_api_frontend.py tests/unit/test_api_refresher_queue.py tests/integration/test_api_sources_assets.py tests/integration/test_api_access.py tests/integration/test_p4_02y_library_refresh.py --junitxml=test-results/p4-home-source-rename.xml --basetemp=.pytest-tmp/p4-home-source-rename
uv run --no-sync ruff check src tests
node --check src/photo_search/web/app.js
git diff --check
```

JUnit `test-results/p4-home-source-rename.xml`：**147 tests / 0 failures / 0 errors / 0 skipped**，14.964s，退出码0。Ruff、Node语法与diff-check退出码0。

- 小型合成图库：改名/同名重复保存/重新打开数据库仍保留；来源字段仅名称改变；资产、位置、扫描、任务及metadata完整快照不变；合成原图字节不变；摘要和照片详情来源标签返回新名称。
- 覆盖空/空白/超长名称、禁止额外root_path字段、未知来源、离线来源及Session/CSRF/Origin拒绝。
- 活动刷新＋排队期间修改新图库名称成功，原FIFO来源ID与执行顺序不变；沿用直接队列回归。
- Node VM运行实际完整 `app.js`（仅抑制boot并暴露测试入口），以最小DOM/fetch替身验证取消不提交、空名称提示、PATCH与CSRF头、重复提交保护、保存后卡片/筛选/缓存更新，以及失败后保留输入并恢复按钮。该行为测试纳入同一JUnit。

首次沙箱运行因项目内 `.pytest-tmp` 创建目录 `WinError5` 产生46个环境错误；按仓库规则走正常权限路线后通过，未调整ACL。最终运行没有deselect或skip，不把首次环境错误当产品失败或通过证据。

未做真实浏览器/真实图库验证，未跑全量、性能、模型、容量或打包测试。源码需要重启应用并刷新首页才生效，之后修改名称无需重建图库。本补充功能定向验证完成，停止扩展，不生成后续优化卡。
