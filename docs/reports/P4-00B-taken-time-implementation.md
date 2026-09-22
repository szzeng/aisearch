# P4-00B 报告 — 拍摄时间迁移、增量抽取、API/UI 与打包闭环

- 任务卡：[docs/tasks/P4-00B.md](../tasks/P4-00B.md)
- 冻结契约：[ADR-0011](../decisions/ADR-0011-taken-time-timezone-and-order-contract.md)（Accepted）、
  `docs/decisions/contracts/p4-time-contracts.v1.json`
- 收口状态：**IMPLEMENTATION + ACCEPTANCE COMPLETE / INDEPENDENT DS-H ENGINEERING REVIEW PENDING**
- 起点：`8e48fc7` + P4-00A 未提交契约交付物（工作树 6 modified / 6 untracked，全部保留）

本卡实现 ADR-0011，未改 ADR/companion/`0001`～`0014`/依赖/`uv.lock`/`PhotoSearch.spec`，
未扫描或访问任何真实照片库。**本卡不标 PASS**：按 V3.1 审查路由，通过与否由另开的独立
**DS-H / DeepSeek V4.1 Flash / 高推理**会话做全量工程主审后确定；本卡未生成
`docs/tasks/P4-00C.md`。

## 1. 改动文件

新增：

- `src/photo_search/catalog/migrations/0015_taken_time.sql`
- `src/photo_search/taken_time/{__init__,contract,exif,extract,read,service,cli}.py`
- `tests/p4_time_fixtures.py`
- `tests/unit/test_p4_taken_time.py`（59 例）
- `tests/integration/test_p4_taken_time_migration.py`（5 例）
- `tests/integration/test_p4_taken_time_extract.py`（10 例）
- `tests/integration/test_p4_taken_time_api.py`（9 例）
- `tests/integration/test_p4_taken_time_faults.py`（8 例 = ADR-0011 §10 的 8 项 D 级）
- `tools/diagnostics/taken_time_smoke_fixture.py`
- 本报告

修改：

- `src/photo_search/catalog/schema.py`（`SUPPORTED_SCHEMA_VERSION` 14 → 15）
- `src/photo_search/catalog/errors.py`（新增 `INVALID_TIME_RANGE`）、`src/photo_search/api/errors.py`（422 映射）
- `src/photo_search/api/files.py`（新增 `resolve_present_locations`，复用同一 P1-04 边界做多位置确定性遍历）
- `src/photo_search/api/models.py`、`api/queries.py`、`api/routes.py`（列表/详情/搜索的加性键、条件性状态块）
- `src/photo_search/search/{contracts,retrieval,service,sessions}.py`（时间条件进同一 AND 集合与同一摘要）
- `src/photo_search/scheduler/engine.py`（`taken_time_extract` 的提交钩子，延迟导入）
- `src/photo_search/web/{index.html,app.js,styles.css,search.html,search.js,search.css}`（拍摄时间范围、`capture_desc`、文件时间标注、诚实状态）
- `tests/unit/test_api_frontend.py`（+7 个 P4-00B 静态契约用例）、
  `tests/unit/test_p4_time_contracts.py`（把 3 条「实现前」断言改成「实现后」断言，只加键、不削弱）、
  `tests/unit/test_p3_person_search{,_scope}.py`、`tests/integration/test_p3_person_search_filter.py`
  （live echo 键集扩展为 V2：ADR-0009 的 11 键 + 2 = 13，只加键）、
  `tests/unit/test_catalog_migrations.py`、`tests/integration/test_p2_scheduler_recovery.py`
  （表集合加入 `asset_metadata`）
- `tools/diagnostics/packaged_api_smoke.py`（打包后的 15/schema/规则版本/索引断言 + bundle 内迁移逐个 SHA-256 比对）
- `tools/places/examples/mini-cities.{spec,manifest}.json`（`compatible_catalog_schema.maximum` 14 → 15，见 §9.4）
- `docs/diagnostics/frontend-smoke-checklist.md`（新增 §9 拍摄时间范围与 `capture_desc`）
- `docs/development-status.md`

## 2. 迁移与抽取状态机

**`0015_taken_time.sql`**：只建 `asset_metadata`（主键 + 六列）+ 表级 CHECK + 一条
`(time_source, taken_at_sort, asset_id)` 索引，并在 `metadata` 写库级 `taken_time_rules_version=1`。
表级 CHECK 冻结：五组 `(time_source, time_confidence)`、`taken_at_sort` 为 NULL 当且仅当
`FILE_MTIME`、`FILE_MTIME` 的 `timezone_offset` 恒为 NULL、`taken_at_sort ∈ [0, 315537897599999]`、
`timezone_offset ∈ [-720, 840]`、`camera` 为 NULL 或 1..128 字符。迁移不写任何资产行；
建表与版本推进仍在 `migrate._apply_migration` 的同一 `BEGIN IMMEDIATE` 事务里，失败整段回滚；旧版本程序
读 15 库得 `SCHEMA_TOO_NEW` 且零写。SQLite 不接受 CHECK 里的行值 `IN` 列表（"subqueries prohibited
in CHECK constraints"），因此五组许可组合写成等价 OR 等式。

**抽取任务**（`taken_time_extract` / `taken-time-exif@1`，`non_model` + `CPU_PREFERRED` + 纯 Python
runtime，与 GPS 抽取器同一 `photo-search-runtime@1` 包）：

1. 输入修订 = `<资产 sha256>:<file_locations 观察摘要 16 hex>`，观察摘要覆盖该资产**全部**位置的
   `(location_id, availability, size, mtime_ns)`。这是本卡的关键裁定：同一内容在录制文件时间变化后是
   **新输入**而不是同一输入的第二次产物，因此它是普通的新逻辑作业（ADR-0003 §8），不会与旧产物
   冲突（见 §9.2）。
2. 按 `location_id` 升序遍历 `PRESENT` 位置（复用 P1-04 的四层受控边界，逐位置做读前 `(size, mtime_ns)`
   观察守卫；读后再验一次）。任一位置的坏值只被丢弃：`EXIF_ORIGINAL`（0x9003 + 0x9011/0x9291）→
   `EXIF_DIGITIZED`（0x9004 + 0x9012/0x9292）→ `EXIF_ROOT`（0x0132 + 0x9010）→ `FILE_MTIME`
   （全位置含 MISSING 的最小 `mtime_ns`）。禁止扫描时间/当前时间/`created_at` 兜底；只读文件头不解码像素。
3. `taken_at_sort` 只按墙上字段折算（原点 `0001-01-01T00:00:00.000`，非 instant；偏移永不参与），亚秒取前
   3 位。`EXIF_ORIGINAL` 有偏移为 HIGH、无偏移降为 MEDIUM；只有 `EXIF_ORIGINAL/EXIF_DIGITIZED`
   的 `label=capture`。
4. 提交事务内写入 `asset_metadata` 行，并在**同一**事务、**至多一次**地把 `catalog_filter_revision`
   推进 `CAPTURE_TIME_CHANGED`；与旧行完全一致即 no-op（不重写、不推 revision），这正是重启/强制重抽的
   幂等来源。不新增 projection queue cause、不动向量命名空间与 `search_projection` 8 键。
5. 规则版本门禁：库级 `taken_time_rules_version` 与程序不一致时，时间条件谓词恒不可满足（`0`）、
   状态计数规范化为 `(total, 0, 0)` ⇒ `PENDING`，详情对象返回 `null`（不发布不可信派生事实）。行仍在库里。

## 3. B 级（自动化）证据

```
uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration \
  --basetemp=.pytest-basetemp-p400b-full3 --junitxml=test-results/p4-00b-full.xml
```

- `test-results/p4-00b-full.xml`：**1598 tests / 0 failures / 0 errors / 48 skipped**（196.7 s；
  48 个 skip 全为未安装 pinned 权重的环境跳过，与 P3-08D 基线同类）。
- 专项 `test-results/p4-00b-focused.xml`：**268 / 0 / 0 / 0**（`test_p4_taken_time.py` 59、
  `test_p4_time_contracts.py` 39、迁移 5、抽取 10、API 9、D 级 8、前端静态 92、P3 契约 46）。
- `uv run --no-sync ruff check .`：All checks passed。
- `git diff --check`：clean；`uv lock --check`：Resolved 95 packages（依赖未变）。
- 覆盖：新库 1→15 与有数据 14→15、表级 CHECK 闭集、回滚、旧程序拒绝 15、来源闭集/子 IFD/偏移/亚秒/
  坏日历/多位置/MISSING/读中变化/幂等重启/规则版本失配、civil key 与范围端点、`capture_desc` 三桶/游标/
  stale/分页、`asset_id`+`time_desc` 全回归、assets/search/detail 信封、V2 echo、单一 revision/hash、
  四态纯函数、错误/隐私闭集。

## 4. D 级（故障注入，逐条）

| # | 夹具与命令 | 结果证据 |
|---|---|---|
| 1 | `build_v14_catalog` 造真 v14 库（2 资产/2 位置/revision 7/person op）→ `migrate_database` | `applied=(15,)`、backup 仍是 14 且无 `asset_metadata`；8 张既有表逐行不变、revision 仍 7、`asset_metadata` 0 行、`verify_schema(15)` 通过、FK check 空 |
| 2 | (a) 在未提交事务里执行 0015 后直接关闭连接（= 进程被杀）；(b) 0015 追加失败语句 | 两者都仍是 14：无 `asset_metadata`、无半张表/无 parking 表、既有行不变；失败路径得 `MIGRATION_FAILED` |
| 3 | 4 资产 enqueue 后只 `run_queue(limit=2)`，再“重启”跑完剩余 | 第一段 2 QUEUED/2 SUCCEEDED，第二段 2 步；4 行齐、无重写、无跳过；每条 `CAPTURE_TIME_CHANGED` 的 `changed_count=1` |
| 4 | 读取 EXIF 时把文件 mtime 前移（读中变化）→ 再 rescan + 强制重抽 | 第一次不采纳该位置、写 `FILE_MTIME`、revision +1、仅 1 条产物；rescan 后输入修订改变 ⇒ 新作业 ⇒ 重抽得 `EXIF_ORIGINAL`、revision +2、2 条产物（旧产物保留） |
| 5 | (a) 整个来源根被删除（不复扫，行仍 PRESENT）；(b) 文件消失后复扫（行 MISSING） | 两种情况都 `SUCCEEDED` + `FILE_MTIME`，时间取录制 `mtime_ns` 的 UTC ISO；读文件失败从不失败作业 |
| 6 | 同内容两位置（两来源），删掉 `location_id` 最小那份 | 仍取 `EXIF_ORIGINAL`，产物里的 `location_id` 是第二个可读位置；同观察下再跑一次结果逐字段相同、revision 不动 |
| 7 | 库级规则版本改成 2 | 行仍在（2 行）、状态 `(PENDING, 2, 0, 0)`、详情 `null`、谓词 `("0", [])`；`capture_desc` 仍确定性返回全部行（见 §9.3） |
| 8 | 先取 `capture_desc` 游标 → 抽取写入 → 复游标；并对照 `time_desc`/`asset_id` | 复游标 409 `CATALOG_FILTER_REVISION_STALE`（details 仅既有键）；丢游标从第一页重开不重不漏；`time_desc` 续页仍是 P1-05 的插入时间顺序、`asset_id` 续页正常 |

## 5. C 级（真实冻结包）证据

1. 真实重建：`uv run --no-sync python tools/packaging/build_onedir.py` → `dist/PhotoSearch`（PyInstaller onedir，
   21 个 NVIDIA DLL 平铺）。bundle 内 `photo_search/catalog/migrations/0001`～`0015` **15 个脚本逐个 SHA-256
   与源码一致**（例如 `0001_initial.sql 4010535c…c10`、`0015_taken_time.sql 4e2cfa09…d89`）；`git status` 证明
   `0001`～`0014` 未被改动（只有 0015 是新增文件）。
2. `tools/diagnostics/packaged_api_smoke.py --bundle dist/PhotoSearch --work-dir "…\打包 中文 空格"`：
   `status=ok`、`problems=[]`；打包 data root 实测 `packaged_taken_time_schema={schema_version:15,
   has_asset_metadata:true, has_closed_sources:true, has_capture_index:true, rules_version:1}`，
   `bundled_migrations.matches_source=true`，person 词表断言改为 `schema_version>=13` 且四项仍在。
3. 缺件最小反例：在**独立可丢弃的 bundle 副本**里删掉 `_internal/photo_search/catalog/migrations/0015_taken_time.sql`
   （剩 14 个），用全新 data root 启动该副本并对 `/api/v1/stats` 发真实 HTTP 请求 →
   `409 SCHEMA_INCONSISTENT`，details 为 `versions=[1..14]` vs `expected_versions=[1..15]`；事后该 data root 只有
   `runtime/session.json` 与 4 KiB 空库（`sqlite_master` 0 个对象）⇒ **零写**，没有静默停在 14。反例结束后
   只清理该副本目录。

## 6. 记录式真实浏览器（Codex 内置浏览器，非 Playwright/Selenium PASS）

夹具：`uv run --no-sync python tools/diagnostics/taken_time_smoke_fixture.py --reset --root <项目内临时目录>`
（8 张合成 JPEG：2 张 EXIF 原图同墙钟时间、一张带 `+08:00` 一张不带；1 张只有数字化时间带 `-05:30`；
1 张只有 EXIF 根时间；4 张只有文件时间；已扫描、已抽取、1 个已归类地点、1 个已激活 `SearchProfile`）。
脚本用生产 `asset_page`/`search_request` 记录期望：`capture_range_2022=[1,8]`、
`capture_desc_order=[8,1,2,3,7,6,5,4]`、`capture_desc_tail=[7,6,5,4]`、`file_time_assets=[3,4,5,6,7]`、
`empty_range_assets=[]`、`search_hits_2022=[1,8]`、`#8` 偏移 480 / `#1` 偏移 `null`。

服务端：`python -m photo_search --port 18991 --data-root "<夹具 data_root>"`，用启动器打印的一次性链接建会话。

| # | 观察 | 实测 |
|---|---|---|
| 1 | 范围 `2022-01-01`~`2023-01-01` 逐页浏览 | 浏览页回显 `capture_from=2022-01-01T00:00:00.000`、`capture_to_exclusive=2023-01-01T00:00:00.000`，恰好 `#8`、`#1`（起点含、终点不含），状态块「拍摄时间已抽取完成（已抽取 8/8，其中有拍摄时间 3）」；`/search` 同条件命中 `#1`、`#8`（FILTER_ONLY） |
| 2 | `capture_desc` 无范围 | 顺序 `8,1,2,3,7,6,5,4`；尾桶 `#7..#4` 每张显示「文件时间（文件修改时间）」；`#3`（只有 EXIF 根时间）显示「文件时间 2019-01-02T03:04:05.000（EXIF 根时间（文件时间））」 |
| 3 | 有/无偏移显示 | `#8`：`2022-07-05T18:31:07.000（+08:00）`、HIGH、相机 `SmokeMake SmokeModel`；`#1`：同一墙钟时间、无任何时区、显示「文件未记录（不会显示或推断为 UTC）」、MEDIUM、无相机；两者 civil key 同为 `63792642667000` |
| 4 | 空结果四态提示 | 抽取完成时空范围 ⇒「这个时间范围内确实没有命中的照片（拍摄时间已抽取完成。）」；把库级规则版本改成 99（`--rules-version 99`，不重建）后同一查询 ⇒「拍摄时间尚未抽取…并不代表没有这些照片（已抽取 0/8）」且详情显示「未抽取（或本程序不信任当前规则版本的既有结果）」 |
| 5 | 第二标签页改地点后旧续页 | 第二标签页在 `/places` 新建并改名地点（目录 revision 9→10→11，写入 `PLACE_NAME_CHANGED`）；回到搜索页点「加载下一页」⇒ 页面提示「人物、地点或可见性在翻页期间发生了变化…请重新搜索」+ `[CATALOG_FILTER_REVISION_STALE]`，访问日志同一请求记 `409 Conflict`；capture_from/to 与每页数量均保留，「按当前条件重搜」随后成功 |
| 6 | 上传搜图不带 capture 字段 | 上传表单里没有 capture 输入，`sendImageSearch` 从不追加这两个字段（前端静态契约 + 服务端 multipart 闭集）；空文件提交只触发页面自身的「请先选择一个本地图片文件」提示，不发请求；打包 smoke 的真实 multipart 上传仍是 `RUNTIME_CAPABILITY_UNAVAILABLE / IMAGE_ENCODER_UNAVAILABLE`（而不是「未定义字段」422） |
| 7 | 键盘与窄宽 | `Tab` 依次可达 `query-capture-from → query-capture-to → capture-clear → 人物控件 → query-page-size → 搜索 → 清空筛选 → 重搜 → 上传文件 → 用这张图搜索`，`Enter` 在「搜索」上正常提交并回显；内置浏览器实际视口 `clientWidth=380`，`scrollWidth=380`（无横向溢出） |
| 8 | DOM/URL/日志隐私 | 浏览页与搜索页正文均无 `taken_at_raw`、无 `2022:07:05 18:31:07`、无 `PhotoSearchData`/盘符路径、无坐标；URL 只有 `/` 与 `/search`（无 token、无人物/地点 id）；访问日志 147 个请求全部落在 `/`、`/places`、`/search`、`/web/*`、`/api/v1/*`，`/geo` 命中 **0**，唯一凭证出现处是启动器打印的链接（访问日志记 `token=<redacted>`），状态码分布 200×95 / 201×1 / 303×1 / 304×49 / 409×1 |

## 7. 隐私与错误闭集

- 公开面只有规范化墙钟串、来源/置信度枚举、偏移分钟、civil key、`label` 与（仅详情）`camera`；`taken_at_raw`
  只存在于 `asset_metadata` 与结构化产物内部，契约测试与静态契约测试分别扫描 companion 与 `web/**`。
- 新增唯一错误码 `INVALID_TIME_RANGE`（422），details 只用既有 `reason`/`field`；四个 reason 与
  `capture_bound_*`/`capture_range_not_ascending` 逐条实测；`capture_desc` 续页复用既有 409
  `CATALOG_FILTER_REVISION_STALE` 且**不带**会话键（该列表没有会话）。其它端点信封不变。

## 8. 开发过程中的真实失败与修复

1. **CHECK 里的行值 IN**：`(time_source, time_confidence) IN ((...),(...))` 被 SQLite 判为
   “subqueries prohibited in CHECK constraints” ⇒ 迁移失败（`MIGRATION_FAILED`）。改成等价 OR 等式后通过；
   失败的那次恰好成为 D2 回滚证据。
2. **产物逻辑键冲突**：第一版把输入修订只用 sha256，读中变化后重抽会与旧结构化产物
   `ARTIFACT_CONFLICT`。根因是事实同时依赖内容与录制观察 ⇒ 把观察摘要并入输入修订（§2.1、§9.2）。
3. **观察摘要前缀比较**：首版用完整摘要与 16 位前缀比较，导致每次抽取都被判 `STALE_INPUT_REVISION`
   （D 级与抽取用例一次全红）。改为 `observation_matches()` 后通过。
4. **纯 Python 运行时能力**：CLI 首版沿用 `snapshot_from_runtime()`，它记录的是 ONNX Runtime 包，
   与处理器声明的 `photo-search-runtime@1` 不匹配 ⇒ 作业被 BLOCKED 且队列无法排空。改为按处理器
   声明的运行时包记录健康 CPU 能力（provider `python-stdlib`）后 `steps=1`。
5. **前端「条件变更丢重载」**：范围两个输入快速变更（或一次加载在途时再改）会让第二次 `loadBrowse`
   因 `loading` 直接返回，网格停在旧条件。新增 `state.browse.owed` 与诚实空结果措辞后，
   浏览页在录制浏览器里稳定得到「2 张 / 0 张 + PENDING」三种口径。
6. **地名包示例兼容上限**：schema 升到 15 后，仓库自带示例 spec 的
   `compatible_catalog_schema.maximum=14` 让 `manage_dataset` 与两条 P2-03B 用例失败。0015 不触碰任何
   地点数据/导入路径，因此把示例的 maximum 提到 15（第三方包若仍声明 14，工具按包声明继续拒绝，
   见 §9.4）。

## 9. 裁定与设计取舍（契约未逐字覆盖处）

1. **规则版本失配时的详情**：ADR-0011 §2 只规定「不得被信任或参与过滤」、§8.1 规定计数规范化，
   没有写详情。本卡选择「失配 ⇒ 详情 `null`」（不可信派生事实不发布；行仍在库里，状态块说明
   `PENDING`），而不是把不可信行当作事实返回。D7 与浏览器步骤 4 都固定了该行为。
2. **输入修订 = 内容哈希 + 录制观察摘要**：ADR-0003 §8 的五字段逻辑作业身份只写「input_revision」，
   ADR-0011 §2 明确“文件路径、可用性、文件时间的观察值以 `file_locations` 为准”。本卡据此把观察并入输入
   修订（同一内容 + 不同录制 mtime ⇒ 新输入 ⇒ 新作业），否则「留待重抽」在产物层面不可实现（§8.2）。
   提交事务内再次校验内容与观察，任一变化 ⇒ `STALE_INPUT_REVISION` 且零写。
3. **失配时的 `capture_desc` 排序**：ADR-0011 §2 的原文是「行仍在库里，只是不参与**状态与过滤**」，
   §5.1 的 SQL 未加门禁。本卡据此只门禁过滤/计数/详情，保留 `capture_desc` 的确定性排序（仍返回全部行、
   游标只绑定既有 `catalog_filter_revision`）。替代方案（失配时把所有行当尾桶）会让同一 revision 下的
   游标语义随规则版本变化，风险更高；该取舍记录于此，供主审裁定。
4. **示例地名包上限 14→15**：见 §8.6。工具仍尊重包声明；本卡不放宽 `manage_dataset` 的校验语义。
5. **列表的逐行时间来自详情接口**：ADR-0011 §8.1 只给列表加两个回显键与条件性状态块（不带逐行时间投影），
   而任务卡要求界面显示「文件时间」。本卡让浏览页在拍摄时间模式下按可见卡片懒加载该资产**详情**的闭集对象
   （不做本地解析、不读库）；代价是拍摄时间浏览是 N+1 读，30 万规模下的延迟列入未验证项。
6. **`Non-model` 处理器的能力快照**：CLI 记录 `photo-search-runtime@1` + `python-stdlib` 健康 CPU 能力，
   与 GPS 抽取器同一运行时包；不使用模型运行时包来资格判定非模型处理器（§8.4）。

## 10. 未验证项（不得当 PASS）

- 30 万资产下 `capture_desc`/时间过滤的延迟、最终索引列序与全库抽取耗时；
- 多进程并发抽取、`0015` 在真实库上的实测迁移耗时；
- 内部存储格式的真实覆盖率（大小写、坏相机样本）与 GTX 1660 实机；
- 浏览器窄宽为内置浏览器真实视口（380 CSS px），非设备级模拟；上传路径在无图像编码器时只能观察到结构化
  拒绝（「不带 capture 字段」由静态契约 + multipart 闭集 + 打包 smoke 共同判定）；
- 本卡**未**做独立全量工程主审（下一步）；也**未**生成 `docs/tasks/P4-00C.md`。

## 11. 真实 Git 状态与下一步

- 工作树：P4-00A 交付物 + 本卡全部改动均为**未提交**（本卡不加 commit）；`0001`～`0014` 未改、
  companion 未改、依赖与 `uv.lock` 未改、`PhotoSearch.spec` 未改（`.spec` 在 `.gitignore` 内）。
- 测试/打包临时目录（`.pytest-basetemp-*`）与 `test-results/` 均在 `.gitignore` 内；诊断脚本自建的
  `.p4-00b-tmp/` 已删除；缺口反例使用的 bundle 副本已清理。
- **停止点**：本卡状态为 **IMPLEMENTATION + ACCEPTANCE COMPLETE / INDEPENDENT DS-H ENGINEERING REVIEW
  PENDING**。下一步请在新会话用 **DS-H / DeepSeek V4.1 Flash / 高推理** 审完整 diff（含未提交文件）、
  迁移/事务/revision/重启状态、API/search/UI 依赖面、测试真实性、bundle 资源清单与 ADR-0011/任务卡一致性；
  主审通过后才标 P4-00B PASS、更新 `docs/development-status.md` 并生成下一任务卡。模型切换始终由用户在
  Codex 中手动完成。

## 12. 主审后补充（2026-09-22）

- 独立 **DS-H 全量工程主审**已完成并判 **P4-00B PASS**（`SOL_NOT_REQUIRED`）：见
  [主审报告](P4-00B-dsh-engineering-review.md)。主审独立重跑完整套件 **1598 / 0 / 0 / 48**、聚焦
  **353 / 0 / 0 / 0**、真实重建 onedir 并重跑打包 smoke 与缺件反例；本报告 §5 的 C 级证据在**当时**的包上
  成立，但该包早于最后一次 `web/app.js` 修改，主审已重建后重新验证（主审报告 §5 F1）。
- 下一任务 **P4-00C（HEIC）** 按用户要求重新规划为两段式：**P4-00C-A 业务实现（DS-H，B + D）** 与
  **P4-00C-B 环境/正式包兼容（C1，只做环境，C 级）**；本报告原先提到的 `docs/tasks/P4-00C.md` 已由
  [P4-00C-A](../tasks/P4-00C-A.md) 与 [P4-00C-B](../tasks/P4-00C-B.md) 取代。
