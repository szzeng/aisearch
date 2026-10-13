# P4-RELEASE-020 — 执行报告

日期：2026-10-13。状态：**SCOPED PASS / PUBLICATION PENDING**。

## 冻结内容

- 开工 HEAD：`b0b56aff0362f81ae800fe9ee537ed1ff7e28906`，工作树干净。
- 版本代码候选（已验收）：`8027b0c37de56ae30539b3d87546a9a8262a5b02`。
- `pyproject.toml`、API `APP_VERSION`、OpenAPI、页面能力接口和 `uv.lock` 均为 `0.2.0`；`SUPPORTED_SCHEMA_VERSION` 仍为 **29**，无 migration 或图库数据修改。
- 变更文件：`pyproject.toml`、`src/photo_search/api/routes.py`、`tests/integration/test_api_access.py`、`uv.lock`、`CHANGELOG.md`、本报告和开发状态入口。
- 候选 HEAD 比 R2 参考 HEAD `bc07f25f` 多出已提交的待整理人脸建议头像 UI 改动（`60442d3`）。其三项定向合成 UI 测试通过，真实人物页外观仍待用户观察；更新日志明确此边界。UI 2.0 与统一时间轴仍为规划。
- 远端实时 `master` 为 `73f917e7`；相对本地跟踪点 `6946d3b` 仅多两项 UI mockup/任务卡文档提交，没有产品代码变化，未并入本候选。

## 验证

- `uv run --locked --no-sync pytest -q tests/integration/test_api_access.py::test_openapi_document_is_behind_the_session tests/integration/test_api_frontend_routes.py::test_the_search_page_is_public_while_its_api_stays_protected tests/integration/test_p2_search_endpoint.py::test_an_explicitly_bound_text_search_answers_one_reproducible_page --junitxml=test-results/P4-RELEASE-020.xml` — **3 passed，JUnit 3/0/0/0**。覆盖 OpenAPI 与页面能力接口版本一致、搜索页可访问/API 仍受保护，以及隔离合成图库文本搜索。
- `$env:UV_CACHE_DIR='.uv-cache'; uv lock` — 成功，同步本地项目版本；出现既有 `transformers==4.57.0` yanked 警告，未升级依赖。
- `$env:UV_CACHE_DIR='.uv-cache'; uv lock --check` — 通过。
- `$env:UV_CACHE_DIR='.uv-cache'; uv run --locked --no-sync ruff check src/photo_search/api/routes.py tests/integration/test_api_access.py` — 通过。
- `git diff --check` 和提交前 `git diff --cached --check` — 通过。

JUnit 文件：`test-results/P4-RELEASE-020.xml`。

## 未验证及边界

- 未访问真实图库或 `PhotoSearchData`。在已正常试用的图库中启动并执行简单搜索：**NOT VERIFIED，需用户自行手测**。
- 人物页是否合理隐藏空人物：**NOT VERIFIED，需用户自行手测**。
- 新视频不会入库/显示，以及历史视频需成功刷新后才转为失效：**NOT VERIFIED，需用户自行手测**。
- Esc 退出人物批量管理且不影响已保存操作：**NOT VERIFIED，需用户自行手测**。
- 建议头像布局、真实人物图片缺失时的视觉表现：**NOT VERIFIED，需用户自行手测**。
- 未运行全量 pytest、GPU/OCR/人脸专项、30 万容量/性能、备份恢复实操、PyInstaller 或 GTX 1660 验收。历史全仓失败基线未重跑、不据此扩大任务或声称全仓通过。
- 内置 catalog 备份不含原图和模型权重；本任务未为用户实际图库执行备份，也未做 Schema 升级。正式 Windows 发行仍需独立执行 P4-07/P4-08 门槛。

## 发布状态

本地与远端均未发现 `v0.2.0` Tag；`gh release view v0.2.0` 返回 Not Found。没有推送代码、创建 Tag 或创建 GitHub Pre-release。版本代码提交为 `8027b0c37de56ae30539b3d87546a9a8262a5b02`；包含本报告和状态记录的当前 HEAD 是完整本地候选，后续 Tag 应指向该完整记录提交。所有远端发布操作待用户另行确认。
