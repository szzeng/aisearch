# P4-02Y 独立 DS-H 窄受影响面工程主审（2026-09-29）

**结论：`ENGINEERING REVIEW PASS / SOL_NOT_REQUIRED`。阻断问题：无。非阻断：F1–F3。**

## 0. 被审对象与方式

任务卡 [P4-02Y](../tasks/P4-02Y.md)、实现报告 [P4-02Y-library-refresh](P4-02Y-library-refresh.md)。
被审改动 = 工作树相对 `bc84fd3` 的全部 P4-02Y diff：`ingestion/scanner.py`、`catalog/store.py`、
`catalog/errors.py`、新增 `refresh/{__init__,pipeline,state}.py`、新增 `api/refresher.py`、
`api/{routes,models,context}.py`、`app.py`、`__main__.py`、`workers/query_encoder.py`、
`web/{app.js,index.html}`、新增 `tests/integration/test_p4_02y_{fast_scan,library_refresh}.py`、
新增 `tools/diagnostics/p4_02y_refresh_scale.py`。

事实澄清（不影响判定）：P4-02Y 的**产品与测试改动全部仍未提交**；`d59bcd0`/`277b1d3`/`4f6e737`
只改任务卡，`3b70df6`/`bc84fd3` 是 P4-02Y-T 立卡。工作树相对 HEAD 的 diff 才是本卡实现本身。

已有证据（逐项实读，不只引用报告文字）：`test-results/p4-02y-focused.xml`（40/0/0/0）、
`test-results/p4-02y-full.xml`（1846/0/0/48，262.823 s）、
`test-results/p4-02y-noop-{128,128b,256,256b}.json`、`test-results/p4-02y-delta.json`。

主审另做（唯一新增动作）：

* 在当前工作树**独立复跑**定点 suite = **40 passed / 0 failed / 0 errors / 0 skipped**（10.2 s，
  `test-results/p4-02y-review-focused.xml`）；沙箱内 `.pytest-tmp` 的 `WinError 5` 是既有环境限制，
  按正常权限路由重跑，未改 ACL。
* 重跑 `ruff check .`、`uv lock --check`、`git diff --check` 全绿。
* 为一个可疑点写只读探针 `tmp/p4-02y-review/scratch_restored_unbound.py`（`tmp/` 已 gitignore）。
* 按指令**未**重跑 30 万 no-op/delta 性能测试，**未**重跑 B 级全量回归。

## 1. 逐条核对

### 1.1 fast refresh 只跳过已完整确认且 size/mtime 未变的位置（点 1）

`catalog/store.py::observe_location_detailed` 的判定仍是「存储 size/mtime 与本次 stat 不同 ⇒
changed」，changed 时同一条 `UPDATE ... SET asset_id = NULL, availability = 'PRESENT'`；未变化时
只推进 `relative_path`/`last_seen_scan_id`/`availability`，并**回传**了仍指向既有资产的 `asset_id`。
`scanner.py::_ScanRun._note_observation` 只在 `not created and not changed and not restored and
asset_id is not None` 时计入 `unchanged_reused`，而 content pass 只遍历
`iter_unbound_seen_locations`（`asset_id IS NULL` ∪ 本轮显式恢复位置）；新位置在
`INSERT` 时 `asset_id = NULL`。绑定的唯一入口 `bind_location_content` 仍要求完整 SHA-256，
`upsert_asset(sha256=...)` 才是资产的产生方式；`_process_file` 的 SHA-256 + hash 前后 stat
守卫逐行未改。⇒ **size/mtime 仍只是 freshness hint，没有成为内容身份**；new / size 变 /
mtime 变 / asset_id 空 / MISSING→PRESENT 恢复 一律重新完整 hash。
覆盖：`test_fast_no_op_*`、`..._rehashes_a_changed_size`、`..._rehashes_a_changed_mtime_only`、
`..._reconfirms_a_recovered_position`、`..._binds_a_duplicate_copy_to_the_existing_asset`、
`..._keeps_the_asset_across_a_rename_and_reports_both_truths`、`..._marks_a_deletion_missing_...`、
`..._rebinds_a_same_path_replacement_without_inheriting_derivations`、
`test_full_scan_corrects_a_replacement_that_disguises_its_size_and_mtime`。

### 1.2 full scan 完整 SHA-256 校验能力保留（点 2）

`execute_scan(mode=SCAN_MODE_FULL)` 是默认值，既有调用点（`ingestion/service.py::scan` ← CLI、
`api/runner.py` → `run_scan`、`POST /sources/{id}/scan`）都不传 `mode`；full 模式的 content pass
仍用 `iter_seen_locations`（本次执行的**全部**已见位置），`count_unbound_locations == 0` 的
成功守卫与「仅完整成功后 reconcile MISSING」的原子边界未改。`test_full_scan_still_hashes_every_seen_position`
直接断言全量重 hash；伪装替换用例同时证明 fast 允许漏检、full 能纠正。⇒ 完整校验能力未弱化，
快速刷新是**新增模式**而非替换。

### 1.3 content_delta / location_delta 正确且下游不重扫（点 3）

* `content_delta` = 本次执行完整确认并绑定成功的资产；`location_delta` = created/changed/renamed/
  restored 位置所属资产 ∪ 最终对账刚标记 MISSING 的资产（`reconcile_missing_detail` 与原
  `reconcile_missing` **同一条 UPDATE 谓词**，只加 `RETURNING asset_id`）。
* 两者都有界（`DELTA_LIMIT = 50 000`）且**不进入公开信封**：`ScanOutcome.as_dict()` 显式不含
  id 列表，HTTP/CLI 只看到计数 + `delta_overflow`（`test_a_double_click_never_starts_a_second_refresh`、
  `test_a_delta_larger_than_the_cap_is_reported_as_an_overflow` 都断言了这一点）。
* 下游逐段只吃 delta：thumbnail ← `content`；taken-time ← `content | location`；GPS/place resolver/
  face/OCR ← `content`；search projection 走既有的 durable queue（`documents.catch_up`）；semantic 走
  冻结的 `semantic_selection`（=「当前 revision 缺 CONFIRMED」真实工作集），无 delta 时
  `targets=0 & coverage_complete=True` ⇒ 连模型都不加载。
* 30 万证据：enumerated 300 320 中只 hash **310**（`new_assets=300`、`rebound_assets=10`）、
  thumbnail `generated=300` + 复用 10、taken-time 320、GPS 300 新 job、semantic `encoded=300` /
  `confirmed=310`；no-op 侧全部阶段 `SKIPPED_NO_TARGETS`，refresh wall 30.29 s 而 scan 26.878 s
  ⇒ 没有 30 万级重算或重扫。
* 口径说明（非缺陷）：两个计数器统计的是**位置事实条数**，同一资产可贡献多条（rename = 新位置
  content 事实 + 旧位置 MISSING 事实），故 `location_delta_count=330 > content_delta_count=310`；
  路由集合本身去重，报告与 JSON 在这点上自洽。

### 1.4 batching 保持 asset/MISSING/lease/中断语义（点 4）

枚举阶段按 `_batched`（默认 128，只允许 128/256）成批处理，**每个批一个短写事务**，事务内先
`maybe_extend` + `execution.require`（每批一次的 lease/submission 资格），再逐条走既有的
`observe_location_detailed`，最后在**同一事务内**写进度；文件枚举/stat/hash 仍在事务之外。
`PATH_KEY_COLLISION` 仍由 `observe_location_detailed` 抛出（整批回滚、扫描照旧失败 ⇒ 不产生
错误 MISSING）；changed 在同一事务内清空 `asset_id`；MISSING 仍只由最终那一次原子对账产生；
hash pass 保留自己的逐文件 require 与 stat 守卫；中断后同一 `scan_id` 可恢复且只补未确认位置。
覆盖：`test_batched_observation_is_equivalent_to_the_per_file_baseline`（batch 1/128/256 的
`hash_targets`/`unchanged_reused`/`locations_missing`/delta 集合/catalog 状态逐项相同）、
`..._still_detects_a_path_key_collision`、`..._clears_a_changed_binding_in_the_same_transaction`、
`test_an_interrupted_fast_refresh_recovers_the_same_scan`、以及 30 万 no-op 的 catalog 全行摘要
前后相同（`catalog_identical=true`）。

### 1.5 SEARCH_READY 时新增照片已真实可搜索，face/OCR/GPS 不是硬前置（点 5）

`refresh/pipeline.py::_search_ready` 要求「projection `SYNCED`」且「semantic ∈ {OK,
SKIPPED_NO_TARGETS}」且「delta 的 `unconfirmed_content == 0`」且「`required > 0` 时 `confirmed > 0`」；
`mark_search_ready` 在 D..G 增强阶段**之前**调用，thumbnail/taken-time/GPS/place/face/OCR 只可能是
`OK` / `SKIPPED_UNAVAILABLE` / `SKIPPED_NO_TARGETS`，不影响该判定；被编码器终态拒绝的内容记
`PARTIAL_FAILED` + `failed_stages=["semantic-content"]` 但保留可搜索；本构建无可用语义通道时
终态 `FAILED` 且 `search_ready=false`（不谎报）。
证据：`p4-02y-delta.json` = `state=COMPLETE`、`search_ready=true (semantic-confirmed)`、
`semantic.required=310 / confirmed=310 / unconfirmed_content=0 / failed=0`、真实 HTTP
`text="day-00000"` → 20 hits 且**第 1 位就是新增资产 300021**；9 次并发合法搜索 0 拒绝、全 `OK`。
覆盖：`test_a_new_photo_is_searchable_when_the_refresh_says_search_ready`、
`test_a_build_without_a_semantic_channel_never_claims_search_ready`、
`test_one_refused_photo_never_blocks_the_rest`、
`test_a_failing_face_or_ocr_processor_never_blocks_search_ready`。
（取证时机见 F2。）

### 1.6 no-op = 0 hash + 0 AI；小增量只处理增量（点 6）

四份 no-op JSON（128/128b/256/256b）逐项一致：`enumerated=300 000`、`unchanged_reused=300 000`、
`hash_targets=0`、`hash_bytes=0`、工具计数 `originals_hashed=0`、`originals_decoded=0`、
`content_delta_count=0`、`location_delta_count=0`、`new_assets=0`、semantic `encoded=0`、
thumbnail/taken-time/GPS/face/OCR/place-resolver 全 `SKIPPED_NO_TARGETS`（0 新工作）、
assets+locations 全行摘要在前后**相同**、并发合法搜索 0 拒绝。
128 与 256 的 scan wall（26.745/26.878 vs 26.761/27.394 s）差异在噪声内 ⇒ 按卡面只在这两档内
比较并保留 128，未继续扫参。小增量侧 300 新 + 10 副本 + rename10 + delete10 只 hash 310 个位置、
只派生 300 张新内容（副本/改名复用既有 asset 与已发布 artifact）。

### 1.7 ADR / schema / 既有 API 契约变化（点 7）

无。`SUPPORTED_SCHEMA_VERSION` 仍 16，迁移、`pyproject.toml`、`uv.lock`、`docs/adr/**` 零 diff。
新增 `POST/GET /api/v1/library/refresh` 是加性入口（同一 session + CSRF 边界，未认证 401 已被
`test_the_refresh_requires_a_session_and_a_csrf_token` 覆盖），既有 `POST /sources/{id}/scan` 仍由
路由自己拼那 5 个字段的冻结信封；`LocationObservation`（`renamed`/`restored`/`asset_id`）与
`ScanOutcome` 的新字段都带默认值、纯加性；新错误码 `INVALID_SCAN_MODE` 只用于拒绝此前不存在的
未知模式；`WorkerQueryEncoder` 补 `health()`/`encode_image(path)` 是把 ADR-0003 适配器面补全
（此前缺失，所以刷新永远补不上嵌入，属实现期发现的真实缺陷）。
一处需记录的语义澄清（非契约变化）：`ScanOutcome.files_seen` 由 `processed` 改为 `enumerated`——
full 模式下两者相等（content pass 遍历的正是本次枚举到的行，且每次执行都
`reset_execution_coverage`），fast 模式下 `enumerated` 才是「本次看到多少位置」的诚实口径，
真正重 hash 的条数由新增的 `content_confirmed` 承载。

## 2. 阻断问题

无。

## 3. 非阻断问题

**F1（口径 / 上界，建议改报告一行）**：实现报告 §7 与 §1 引用的「start → SEARCH_READY wall
50 195 ms」是 `tools/diagnostics/p4_02y_refresh_scale.py::refresh_through_api` 的
`search_ready_wall_ms`，它与整轮 `refresh_wall_ms` 同为 50 195 ms（轮询首次看到 `search_ready=true`
即终止轮），而 refresh 自己记录的口径是 `status.counters.search_ready_ms = 39 538 ms`。
即 39.5 s 是流水线口径、50.2 s 是工具观测上界，二者都满足「2–5 分钟进入 SEARCH_READY」的体验目标，
判定不受影响；建议把该数标注为观测上界或直接引用内部计数。已在实现报告 §11 追加说明。

**F2（取证时机）**：`p4-02y-delta.json` 的 `search` 探针在 refresh 进入终止态**之后**才发 HTTP 搜索，
因此「SEARCH_READY 当刻已可搜索」严格来说不是由该探针在半途暂停取证的，而是由
`refresh/pipeline.py` 的门禁顺序（projection `SYNCED` + delta 在当前 generation `CONFIRMED`
之后才 `mark_search_ready`，其后只有不改变可搜索性的增强阶段）与定点集成测试
（先断言 `search_ready is True` 再做真实 HTTP 搜索命中新增资产）共同支撑。卡面 §11-B 的意图成立，
无需改产品；若后续要更强的证据，可在 `ENRICHING` 期间暂停并直接搜索一次。

**F3（边界行为，超出本卡验收范围）**：当单轮变化位置数超过 `DELTA_LIMIT = 50 000`（例如首次就对
>5 万文件的大库直接点「刷新图库」，或一次性导入 >5 万张）时，`refresh_library` 会清空两个 delta 集，
该轮 thumbnail / taken-time / GPS / place / face / OCR 一律 `SKIPPED_NO_TARGETS`，而后续 refresh
因不再有 delta 也不会重新捡起这些增强。catalog 绑定、search projection、semantic 与 SEARCH_READY
都不受影响（新增内容仍可搜索），缩略图也有既有按需入口 `GET /assets/{id}/thumbnail`（生成一次）与
`catalog thumbnail`（全库 pending 选择器）可补齐。卡面 §4 只**允许**（非要求）退回既有全量 selector，
实现选择了「本轮不做、留给既有维护入口」，本卡「几百张变化」的验收不受影响；建议后续卡或文档
明确这一取舍。

## 4. 未验证边界（不因本次 PASS 而通过）

浏览器端到端计时（`BROWSER_TIMING BLOCKED`）、face/OCR 真实权重、place resolver 数据集、
GTX 1660、冷缓存、40 万档、soak、打包产物内刷新、真实个人图库；30 万历史向量为容量工具的合成向量，
其相关性不在本卡结论内。主审按指令未重跑 30 万性能测试与 B 级全量回归：B 级 JUnit 产生于
13:13–13:17，而两个新集成文件与诊断工具在 13:14 被再次改写（`src/**` 自 12:26 起未再改动，且
JSON 字段集与当前工具完全一致），故其余 1806 个既有用例对应现状 `src/**`，而这两个新文件已由主审
在当前工作树独立复跑 40/40。

## 5. 收口

无 ADR / schema / 既有 API 契约变化 ⇒ **`SOL_NOT_REQUIRED`**；按卡面 P4-02Y 可收口 PASS，
下一动作是 P4-02Y-T（已立卡，本轮按指令不启动）。
