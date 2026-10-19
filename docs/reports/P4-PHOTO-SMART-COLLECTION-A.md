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

原始实施阶段当时没有提交或推送；本轮修复与交付状态见下节。并行未跟踪脚本 `repair_photo_collections_v36.py` 未修改、未纳入本轮提交。

## 2026-10-19 第一轮：核心正确性与性能修复

### 确认的问题与修复

- FILTER 列表原先循环调用 `_place_aware_search_worker` 补足过滤后的页面；每次调用会重复全量 COUNT，排除很多时还会重复扫描并依赖循环上限。现在把 `NOT EXISTS(photo_smart_exclusions)` 合并进权威筛选 SQL，单次请求取 `limit+1`，并关闭 FILTER 统计 COUNT；排除后的 SQL Keyset 直接决定页内容和 `has_more`。
- FILTER `capture_desc` 原先沿用资产 ID 顺序，没有整集合时间排序。现在复用 `idx_asset_metadata_capture` 对 `EXIF_ORIGINAL` 和 `EXIF_DIGITIZED` 做有界 keyset 流合并，再以未知时间 asset_id 降序作为尾部。游标绑定 filter/suggestion revision、完整筛选 fingerprint、集合范围身份、收藏 revision、智能合集 rule/exclusion revision 和排序。EXIF_ROOT 与文件时间按现有可信拍摄时间合同归到未知尾部。
- ranked `capture_desc` 原先把所有非空 `taken_at_sort` 当作可信时间。现在只将可信 EXIF 原始/数字化来源排在前面，其他来源和缺失时间稳定按 asset_id 降序排尾。查询仍只触及已发布、有界的最多 5000 条候选，时间全局排序在 SQL 中完成后再分页。
- `rule_from_search()` 原先只看 `content_query/text`，原文 TEXT 搜索可能被误存为 FILTER，COMBINED 计划也可能未固化。短期搜索凭据现在附带服务端实际执行模式、确认的内容查询和 intent plan。规则分类以该实际执行路径为准：TEXT 保存原始查询，COMBINED 保存确认计划，纯条件保存 FILTER；缺少可信执行凭据或必要计划时明确拒绝。
- 刷新 worker 原先在异常捕获范围外创建 service，factory 失败会留下 durable lease 到期；候选发布也未验证 lease 截止时间。初始化异常现在使用请求已打开的共享 service 尽力释放 owner 自己仍有效的租约。发布事务同时比较 owner、lease_until、rule_revision 和依赖签名；失败保留上次已发布缓存，清理使用 owner 条件避免影响接管者。

### 验证与证据

- 最终受影响组合 JUnit：**41 passed, 0 failed, 0 skipped**，`test-results/smart-final-20261019-190731-722.xml`。命令覆盖智能合集单元/集成、普通照片合集集成、收藏拍摄排序及拍摄时间 API 测试。
- 隔离合成 FILTER 数据共 201 张。分别排除 0%、50%、95%、100%，逐页核对 asset_id oracle；每页 50 张，首续页无重复/遗漏，SQLite trace 未出现 FILTER `SELECT COUNT(*) AS value FROM assets ...`。EXPLAIN 显示排除查询使用 `photo_smart_exclusions` 主键搜索，可信时间流使用 `idx_asset_metadata_capture`。
- 时间排序验证包含 EXIF_ORIGINAL/EXIF_DIGITIZED、EXIF_ROOT、FILE_MTIME、无 metadata、相同时间跨页；另以三个 ranked cache 候选验证可信时间、未知尾部与续页。FILTER 时间游标在筛选 revision 推进后返回 409。
- 分类测试覆盖仅有 original_query 的 TEXT、带确认 intent_plan/content_query 的 COMBINED、无内容纯条件 FILTER，以及缺少确认上下文时拒绝。Lease 测试覆盖初始化失败释放、过期后接管、旧 owner 发布拒绝、规则替换后的旧 worker 发布拒绝，均保留之前 build。
- 受影响 Ruff、`py_compile`、`node --check src/photo_search/web/app.js`、`git diff --check` 均通过，exit 0。没有 schema 或 Python 依赖变化；没有执行浏览器、真实模型、GPU、真实图库或 30 万容量测试。

首轮调试 JUnit `test-results/smart-round1-20261019-190338-509.xml` 保存了 39 项、6 项中间失败：规则重建漏了 Pydantic 必填 request_id，导致两个 FILTER API 用例 500；合成 EXIF_DIGITIZED 置信度不符合 schema；另外两个 LanceDB 失败是 `$TEMP` 子目录未预创建导致测试运行时 panic。创建项目临时目录后 LanceDB 用例通过。后续记录 `smart-round1-20261019-190409-288.xml`（39 项，2 项）暴露 FILTER 默认 `rank` 到内部顺序的接线错误；`smart-affected-20261019-190530-138.xml`（40 项，1 项）发现时间桶布尔排序方向反了。相应代码/fixture 已修，最终 41 项全过；原 XML 均保留未覆盖。

### 当前验收状态与剩余风险

第一轮列出的 FILTER 分页、规则分类、两种 `capture_desc`、worker factory lease 边界和发布 CAS 均已有定向证据。整卡仍为 **IMPLEMENTED / ACCEPTANCE PARTIAL**：用户明确排除的 stale ranked 缓存浏览和 `save-static` 留待后续；刷新浏览器/进程重启、真实向量模型、完整并发规则替换仍未验。根据 V3.1 §2.4，当前高风险改动需要独立 DS-H 全受影响面工程审查，本轮尚未执行该审查，因此不标 PASS。没有访问真实图库。

本轮代码、回归测试和状态记录已组成单独本地 Git 提交；未推送。

## 2026-10-19 地点纯筛选范围续修复

### 复核发现与修复

- 智能 FILTER 详情重放带地点身份的已确认计划时会进入 `place_album`。该分支之前没有消费智能排除、`capture_desc` 或 `filter_statistics=False`；因此地点范围内排除会失效、拍摄排序退回 asset_id 顺序/分页，智能详情仍执行 COUNT。
- 最小修复把智能排除谓词合并进既有地点 `selected_members_sql` 范围，地点 `capture_desc` 复用 `_filter_capture_desc_rows` 的可信 EXIF 流及未知时间尾部 Keyset。保持地点身份与修订、照片资格，不先全库检索；智能读取关闭统计，普通地点默认行为不变。
- 新增 `test_place_filter_smart_exclusion_capture_desc_continuation`：53 张隔离合成照片，排除排序首项，按 50 张首/续页与含相同拍摄时间和未知时间的 oracle 对照；断言 WHERE 同时保留地点范围与排除条件、无 COUNT，EXPLAIN 命中时间索引和排除主键。

### 验证

- 受影响组合 JUnit：**51 passed, 0 failed, 0 skipped**，`test-results/smart-place-affected-20261019-193234-761.xml`。地点范围/游标用隔离候选与成员谓词，真实地点实现的回归由同组 place integration 覆盖。
- Ruff、`py_compile`、`git diff --check` 通过；未跑浏览器、真实模型、30 万库或真实图库。整卡仍 PARTIAL，等待本节后续交付与剩余审查。

## 2026-10-19 stale 缓存浏览、save-static 与恢复续交付

本节更新此前报告中“stale 缓存不可浏览、save-static 未实现、进程恢复未验”的历史状态；此前轮次记录保留原样。

### 修复

- TEXT/COMBINED 缓存 STALE 或 REFRESHING 时，先确认规则版本仍一致，再用冻结规则对应的可信 SearchScope 在 SQL 侧重新核对当前完整范围、照片资格和人工排除；不执行 FTS/向量检索。候选最多 5000，按发布顺序分页 50 张，返回“上次结果·待更新”。规则身份失效时不展示候选。游标绑定 build，发布新 build 后旧游标失效。
- 新增 `POST /api/v1/photo-smart-collections/{smart_id}/save-static`。FILTER 当前权威范围最多读至 5001 并对超 5000 明确拒绝；ranked 只复制已发布候选并按当前完整 SQL 条件复核。STALE、TRUNCATED、INDEXING 缺少确认时返回所需确认项。普通合集创建事务接收 smart/scope revision CAS，幂等键重试复用已有合集；校验或写入失败不会改智能规则/排除或产生部分成员。
- UI 增加普通合集另存入口与 freshness/coverage/刷新错误提示，刷新失败可重试，缓存存在时继续显示旧候选。原有排除、已排除恢复、viewer 和返回路径保留。
- 新增 Catalog 重新打开后的 lease 恢复测试：过期 lease 可接管，旧 worker 和规则被替换后的旧发布均被拒绝，原 build 与人工状态不被失败清理覆盖。

### 验证

- 最终受影响 JUnit：**56 passed, 0 failed, 0 skipped**，`test-results/smart-followup-final-20261019.xml`。运行命令：

  ```powershell
  $env:TMP='D:\SZ\project\aisearch\.tmp\pytest-smart-followup'; $env:TEMP=$env:TMP
  .\.venv\Scripts\python.exe -m pytest -q tests/unit/test_photo_smart_collections.py tests/integration/test_p4_photo_smart_collections.py tests/integration/test_p4_photo_collections.py tests/unit/test_photo_collections.py tests/integration/test_p4_storage_ui.py tests/unit/test_photo_view_info_ui.py --junitxml=test-results\smart-followup-final-20261019.xml --basetemp=.pytest-tmp\smart-followup-final-20261019
  ```

- 同组覆盖 stale SQL 资格复核且阻止 FTS/vector、排除后的有界候选页、旧 build 游标拒绝；另存幂等和普通合集成员顺序、FILTER 第 5001 个结果拒绝、STALE/TRUNCATED/INDEXING 确认、scope/smart revision 并发校验；lease 重新打开/接管及失败保留。
- Ruff（8 个受影响 Python 文件）、py_compile、`node --check src/photo_search/web/app.js`、`git diff --check` 均 exit 0。
- Codex 内置浏览器在 `127.0.0.1:8767` 的隔离合成图库实测 3 条流程：过期缓存浏览→刷新→新结果；另存普通合集并检查静态成员；排除→已排除→恢复，并经刷新与真实应用进程重启确认排除状态不丢失。另检查 excluded viewer 关闭后返回排除列表。TEXT 搜索不需要模型；未访问真实 `PhotoSearchData`，没有导出截图。

### 剩余验收边界

- COMBINED 真实向量模型路径、完整跨进程规则替换压力尚未验证；本轮不做 GPU/真实图库测试。
- 高风险持久状态/并发改动的独立 DS-H 全受影响面工程审查仍 PENDING，因此整卡继续 **IMPLEMENTED / ACCEPTANCE PARTIAL**，不标 PASS。未推送。
