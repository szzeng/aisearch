# P4-PHOTO-SMART-COLLECTION-A 执行报告

**日期**：2026-10-19
**状态**：IMPLEMENTED / ACCEPTANCE PARTIAL
**范围**：schema 0037、智能合集 FILTER/TEXT/COMBINED 规则与缓存、API、搜索保存入口、详情/排除 UI；仅使用隔离合成图库，未访问真实图库。

## 实施

- 新增 schema 0037 智能合集、人工排除、排名缓存、规则替换幂等记录和向量发布修订表；同步 schema 上限、保留/GC 分类、备份恢复表清单与地点数据兼容审核上限。
- 新增 `photo_smart_collections` 服务与鉴权 API：搜索创建、列表/详情、改名/删除、规则替换、TEXT/COMBINED 有界候选刷新、FILTER 动态读取、缓存排序、排除与可恢复列表。
- 创建、替换和刷新在检索前后比较依赖签名，发布事务再次核对；签名覆盖 FTS tokenizer/generation、查询/排序合同、相关 profile 字段、过滤/地点/时间/向量发布修订。搜索会话的 projection、pending semantic、FTS/向量截断写入 coverage 状态。
- 修复主应用 view 路由未注册智能合集详情、搜索替换状态作用域、排除后 UI 缓存缺失 rule，以及排除列表缩略图 API 路径重复前缀。
- 0037 不改变地点数据/投影合同；地点包兼容桥审核边界显式扩到 schema 37。

## 验证

- 最终受影响组合 JUnit：**119 passed, 0 failed, 0 skipped**，报告：`test-results/smart-focused-final2-20261019.xml`。命令：

  ```powershell
  $env:TMP='D:\SZ\project\aisearch\.tmp\pytest-smart'; $env:TEMP=$env:TMP
  .\.venv\Scripts\python.exe -m pytest -q tests/unit/test_photo_smart_collections.py tests/integration/test_p4_photo_smart_collections.py tests/integration/test_p4_photo_collections.py tests/unit/test_photo_collections.py tests/unit/test_catalog_migrations.py tests/integration/test_p2_scheduler_recovery.py tests/integration/test_place_dataset_schema17.py tests/integration/test_p2_dataset_tool.py tests/integration/test_p4_storage_ui.py tests/unit/test_photo_view_info_ui.py tests/unit/test_search_text_mode_ui.py --junitxml=test-results\smart-focused-final2-20261019.xml --basetemp=.pytest-tmp\smart-focused-final2-20261019
  ```

- 完整 `tests/unit tests/integration` 执行 JUnit：3269 项，3212 passed、6 failed、51 skipped、0 errors，`test-results/smart-full-final-20261019.xml`。五项失败属于并行 UI 文件版本断言/历史 query digest 断言；第六项 storage UI app.js 版本断言是在运行中同步缓存版本造成，最终受影响重跑已通过。该执行不记作全量 PASS。
- Ruff 受影响 Python 文件通过；相关 Python `py_compile`、`node --check`（app.js/search.js/photo-info.js）及 `git diff --check` 通过。
- Codex 内置浏览器在 127.0.0.1 的隔离图库实测搜索→创建 TEXT 智能合集→详情读取→排除→查看已排除→恢复，3 张合成照片均可重新出现。文本检索未使用语义模型。浏览器日志发现排除列表缩略图 URL 重复 `/api/v1` 前缀；路径已修复且单测断言通过，但修复后的排除缩略图未再次浏览器目视验证。未导出截图。

## 独立复核与未关闭项

Luna High 两轮只读复核。最终复核确认签名前后核对、事务内发布校验、session coverage 的实际来源及“规则已提交但缓存发布失败时明确返回已提交并提示刷新”处理有效；未发现本轮新修复引入的问题。仍不能将整卡标 PASS：

- stale/refreshing ranked 缓存目前返回空页，没有按当前完整规则资格复核后继续浏览旧快照。
- 任务卡要求的 `save-static` API/UI 尚未实现。
- 刷新 worker 的 service factory 在异常处理块外；初始化失败时 durable lease 最迟在租约过期后才能重试。
- 真实浏览器 refresh/进程重启恢复、模型/真实向量路径、规则替换并发及 5000 截断另存流程仍待验证；不作 30 万规模、GPU 延迟或真实图库结论。

本任务没有提交或推送；工作树包含并行 UI 改动，保持原样。
