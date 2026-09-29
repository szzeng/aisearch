# P4-02Y — 一键快速刷新图库：增量扫描 → 增量派生 → 可搜索

基线 `bc84fd3`（master，工作树干净），schema 仍 16，`0017` 未实施。模型/推理切换由用户在
Codex 中手动完成；本报告只记录实际执行过的验证。

## 1. 范围与结论

实现「刷新图库」：完整枚举全部来源，但只对**无法复用既有完整 SHA-256 绑定**的位置重新校验内容；
扫描阶段产生 `content_delta` / `location_delta` 并一路传给派生阶段；semantic/search projection
完成即进入 **SEARCH_READY**，thumbnail / taken-time / GPS / face / OCR 作为**不阻塞**可搜索的增强阶段。
**不实现** watcher、USN Journal、定时刷新、NTFS file-id、source remap、P4-03B、A3/ANN/FAISS、
新后端、VACUUM/artifact GC、全库预览预生成、多来源并行扫描。

结论：实现与卡面验收均达标（见 §6/§7/§8），`full scan` 完整校验语义、MISSING/离线/中断语义、
public 信封均为加性改动。**独立 DS-H 窄受影响面主审尚未进行**（按卡面由用户另开新会话）。

## 2. 产品改动

| 文件 | 改动 |
| --- | --- |
| `ingestion/scanner.py` | `execute_scan(mode=...)`：`full`（默认，逐位置全量 SHA-256、逐文件提交，语义不变）/ `fast`（P4-02Y）；快速模式的 content pass 只遍历 `asset_id IS NULL` 的已见位置 + 本轮 `MISSING→PRESENT` 恢复位置；`unchanged_reused` / `hash_targets` / `hash_bytes` / `new_assets` / `rebound_assets` / `locations_added` / `content_delta_count` / `location_delta_count` / `delta_overflow`；有界 delta 集合（`_note_content`/`_note_location`，上限 `DELTA_LIMIT`，溢出则回退全量 selector 且计数仍精确）；枚举按 `observation_batch`（128/256）成批提交 |
| `catalog/store.py` | `LocationObservation` 加性字段 `renamed` / `restored` / `asset_id`（P1-02 只读 `location_id`/`created`/`changed`，语义未动）；新增 `iter_unbound_seen_locations`（keyset 分页，`asset_id IS NULL` ∪ 显式恢复位置）；`reconcile_missing` 语义不变，新增 `reconcile_missing_detail`（同一条 `UPDATE` + `RETURNING asset_id`，返回本次刚标记 MISSING 的资产样本） |
| `catalog/errors.py` | 新增错误码常量 `INVALID_SCAN_MODE`（仅用于拒绝未知 `mode`） |
| `refresh/pipeline.py`（新） | 刷新流水线：A 快速扫描 → B search projection catch-up → C semantic（SEARCH_READY 门禁）→ D..G 增量增强；每阶段有界、可跳过、失败不阻断 |
| `refresh/state.py`（新） | 进程内只读状态：`IDLE/SCANNING/INDEXING/SEARCH_READY/ENRICHING/COMPLETE/PARTIAL_FAILED/FAILED`、`search_ready_basis`、分阶段计数、`failed_stages`；只暴露计数与阶段名，**不含 asset_id 列表** |
| `api/refresher.py`（新） | 单后台线程刷新管理器：一次一个 active refresh、重复请求返回当前状态、进程被杀只丢显示进度（持久幂等由产品路径保证）；每次运行在自己的线程内开/关连接 |
| `api/routes.py` + `api/models.py` | 加性入口 `POST /api/v1/library/refresh`（202，可选 `source_ids`）与只读 `GET /api/v1/library/refresh`（`LibraryRefreshRequest/Status`） |
| `web/app.js` + `web/index.html` | 「来源与扫描」页新增 **刷新图库** 按钮、每来源 **快速刷新此来源**、刷新状态行（区分 可搜索 / 增强中 / 完成 / 部分完成）；既有「扫描」保留并改文案为「完整校验扫描已排队」 |
| `workers/query_encoder.py` | 补齐 ADR-0003 适配器的两个方法 `health()` / `encode_image(path)`：API 进程能复用**同一个** pinned worker 为刷新补嵌入（否则刷新无法在新空间外另起模型） |
| `app.py` / `api/context.py` / `__main__.py` | 注入 `refresh_manifest_path` / `refresh_device_policy` / `refresh_batch_size` / `refresher_executor`，真实启动用与查询侧同一 manifest |

## 3. 快速刷新语义（§2/§3）

位置只有同时满足「该 path_key 已存在 + 上次 PRESENT + asset_id 非空 + 当前 size/mtime_ns 与记录一致」
才允许跳过重新 hash；`size/mtime` 只是 freshness hint。必须重新完整 SHA-256 的情形：新路径、size 变化、
mtime 变化、asset_id 为空、MISSING→PRESENT 恢复、以及任何无法证明满足复用条件的情况。
`fast` 仍要求 `count_unbound_locations == 0` 才允许 `SUCCEEDED`，`reconcile_missing` 仍只在完整成功
的最终事务里执行。已知限制（卡面要求明确记录）：**同一路径内容被外部工具替换且 size+mtime_ns 完全伪装
不变时，快速刷新可能发现不了；必须靠「完整扫描」重新 hash 全库覆盖**（`tests/integration/test_p4_02y_fast_scan.py` 的
disguised-replacement 用例同时验证 fast 漏检与 full 纠正）。

## 4. Delta 与 SEARCH_READY（§4/§6）

* `content_delta` = 本轮完整确认并绑定成功的内容资产；`location_delta` = 位置/可用性/观察发生变化的
  资产（新建、改名/移动、删除→MISSING、恢复、size/mtime 变化，含 `content_delta` 与 reconcile 刚标记
  MISSING 的资产）。两者都是刷新内部实现细节，HTTP/CLI 只看到计数（+`delta_overflow`）。
* 有界：小增量走内存集合，超上限置 `delta_overflow` 并回退各处理器**既有全量 selector**，计数始终精确。
* 各阶段只消费 delta：thumbnail 用 `asset_ids=content_delta`（已发布同 revision 直接复用）；
  taken-time 用 `location_delta ∪ content_delta`；GPS/place resolver 用 `content_delta`；
  face/OCR 用 `content_delta`；semantic 用既有 `semantic_selection`（=「当前 revision 缺 CONFIRMED」
  真实工作集），`content_delta` 超过上限时同样退回该 selector 并循环 `build_index` 的 5000 有界批次。
* **SEARCH_READY 由证据决定**：search projection 队列清空 **且** 本轮变化内容（仅「本构建可索引的图像」
  计入 required；文档/视频/无法识别文件记 `out_of_scope`）在**当前服务的 generation** 里全部 CONFIRMED
  或已终态拒绝。basis 只能是 `semantic-confirmed` / `semantic-up-to-date`。**没有可用语义通道时不予
  宣告**（`MANIFEST_UNCONFIGURED`/`MODEL_UNAVAILABLE` → `search_ready=false`、终态 `FAILED`），因为本
  构建的搜索路径依赖语义通道，谎报「可搜索」比失败更糟。
* face/OCR/GPS/place resolver 不可用 → `SKIPPED_UNAVAILABLE` 并保留 `SEARCH_READY`；有内容被编码器
  终态拒绝 → `PARTIAL_FAILED` + `failed_stages=["semantic-content"]`，**不**回滚 catalog/semantic。
  新内容被**全部**拒绝时（`confirmed == 0`）不宣告可搜索。

## 5. 实现过程中发现并修复的缺陷（本卡内）

1. **`WorkerQueryEncoder` 缺 `health()`**：刷新用 API 侧适配器补嵌入时必须读它的运行事实（device/
   provider/runtime package）来记录 capability/qualification 证据，否则 `AttributeError` 使 refresh 直接
   失败（第一次 30 万 no-op 运行即复现）。已补齐。
2. **`WorkerQueryEncoder` 缺 `encode_image(path)`**：`indexing.semantic` 的 executor 调的是
   `encode_image`（单数、路径），API 适配器只有上传用的 `encode_images`（复数、已解码图像）。缺失时
   每个 embedding job 都以 `AttributeError` 失败（首次 30 万 + 小增量运行复现：`encoded=310` 但
   `confirmed=0 / failed_assets=310`）。补齐后同一次运行 `required=310 / confirmed=310 / failed=0`。

## 6. 必测 A：30 万 no-op 快速刷新

夹具：`tmp/p402f-阶段0/scale-300000-wave`（只读；工具把 `data/` 复制到 `tmp/p4-02y/scale-300k`，**不触碰**
夹具、图库与向量）。工具 `tools/diagnostics/p4_02y_refresh_scale.py --mode noop` 走**真实产品入口**
（本机 uvicorn + `POST/GET /api/v1/library/refresh`，真实 pinned 模型为搜索侧），并按 §10 只比较
128/256、交错重复少量测量。证据：`test-results/p4-02y-noop-{128,128b,256,256b}.json`。

| 档 | refresh wall | scan wall | enumerated | unchanged_reused | hash_targets | hash_bytes | 实际打开原图做 hash | 打开原图解码 | catalog 逐项 | 并发合法搜索 | 拒绝 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 128 | 30 290 ms | 26 878 ms | 300 000 | 300 000 | **0** | **0** | **0** | **0** | 逐行摘要相同 | 7 | **0** |
| 128（重复） | 30 955 ms | 26 745 ms | 300 000 | 300 000 | **0** | **0** | 0 | 0 | 相同 | 7 | 0 |
| 256 | 30 984 ms | 27 394 ms | 300 000 | 300 000 | **0** | **0** | 0 | 0 | 相同 | 7 | 0 |
| 256（重复） | 30 219 ms | 26 761 ms | 300 000 | 300 000 | **0** | **0** | 0 | 0 | 相同 | 7 | 0 |

* 全部四次 `state=COMPLETE`、`search_ready=true`、`search_ready_basis=semantic-up-to-date`；
  `content_delta_count=0`、`location_delta_count=0`、`new_assets=0`；semantic `encoded=0`；
  thumbnail/taken-time/GPS/face/OCR/place-resolver 全部 `SKIPPED_NO_TARGETS`（0 新工作）。
* `originals_hashed`/`originals_decoded` 由工具对 `scanner.sha256_file` 与 `media.decode.load_normalized`
  计数得出：**30 万 no-op 没有打开任何一张原图**。
* catalog 逐项等价用 assets+locations 全行（含 availability/asset_id/size/mtime）摘要对比，前后相同。
* **128 vs 256 差异在噪声内**（scan 26.745/26.878 vs 26.761/27.394 s）⇒ 按卡面默认保留 **128**
  （单事务写锁时间更短）。停止扫参。
* 说明（夹具事实，非产品缺陷）：wave 夹具的 300 000 条 embedding 由 P4-02F 容量工具以**合成的
  `artifacts.input_revision`** 发布，按冻结的「CONFIRMED 且 artifact revision == 当前 sha256」selector
  会被判为全部待重建。工具因此在**工作副本**内把 `artifacts.input_revision` 指向所属资产的当前
  `sha256`（`stale_before=300000 → stale_after=0`，300 000 条），使副本内部自洽；夹具文件本身零改动、
  产品代码零改动。这也是把真实模型接上该夹具前必须做的事：否则刷新会去重编码 30 万张合成图。

## 7. 必测 B：30 万 + 几百张变化（真实 pinned 模型）

同一夹具副本 + 一个新增来源 `day-library`：先建立（10 张与库内内容相同的副本 + 20 张「稳态」图），
再制造一天的变化：**+300 真新照片**（建立之后才写入）、**rename 10**、**delete 10**。
证据：`test-results/p4-02y-delta.json`。

建立轮：`state=COMPLETE`、`search_ready=true (semantic-confirmed)`、30 个位置全部 hash、20 新资产
（10 副本复用既有资产）、semantic `targets=encoded=20`、`required=30 / confirmed=30 / failed=0`、
start→SEARCH_READY **10 463 ms**。

变化轮（本卡核心）：

| 指标 | 实测 |
| --- | --- |
| enumerated / unchanged_reused | 300 320 / 300 010 |
| hash_targets / hash_bytes | **310** / 1 489 797 B |
| 实际打开原图做 hash（工具计数） | **310**（= 300 新 + 10 改名新路径） |
| new_assets / rebound_assets | 300 / 10（改名与副本复用同一资产，不新建 identity） |
| locations_missing | 20（10 删除 + 10 改名旧拼写） |
| content_delta / location_delta | 310 / 330 |
| semantic targets / encoded / required / confirmed / failed | 300 / 300 / 310 / 310 / 0 |
| thumbnail generated / 复用已发布 | 300 / 10（改名资产同 revision 直接复用） |
| taken-time targets / GPS targets | 320 / 310 |
| face / OCR / place resolver | `SKIPPED_UNAVAILABLE`（本机无权重/无数据集），**不阻塞** SEARCH_READY |
| **start → SEARCH_READY wall** | **50 195 ms**（scan 29 469 ms） |
| 终态 | `COMPLETE`，`search_ready=true (semantic-confirmed)`，`failed_stages=[]` |
| 刷新期间并发合法搜索 | 9 次，**0 拒绝**，全部 `state=OK` |
| SEARCH_READY 时真实 HTTP 搜索 | `text="day-00000"` → `state=OK`、20 hits，**新增照片 asset 300021 为第 1 位** |

结论：30 万档下未退化成 30 万重算 —— 只有 310 个变化位置被重新读取/校验，只有 300 张新内容进入
AI 派生；进入 SEARCH_READY 时新照片**已经**能被真实 HTTP 搜到；face/OCR/GPS 被移出可搜索硬前置。
体验目标（30 万 no-op 1–3 分钟级、+约 300 张 2–5 分钟进入 SEARCH_READY）在本机均满足。

## 8. 测试与回归

* 新增 `tests/integration/test_p4_02y_fast_scan.py`（20 例，§14 的 1–10 与 13–14 扫描面）：no-op 0 hash、
  新文件只 hash 新文件、changed size/mtime 重算、MISSING→PRESENT 重算、重复副本复用资产、rename/move
  保资产、删除仅完整成功后 MISSING、同路径替换不继承派生、full scan 仍全量 hash、**same-size/same-mtime
  伪装替换被 fast 漏检而 full 纠正**、128/256/逐文件三档 catalog 逐项等价、批内 PATH_KEY_COLLISION 与
  changed 清空 asset_id、批量下中断-恢复、delta 上限溢出。
* 新增 `tests/integration/test_p4_02y_library_refresh.py`（20 例，§14 的 11–22 与 §12）：真实 HTTP 202/只读
  状态/未认证 401、双击不启动第二次刷新（门控执行器确定复现）、会话+CSRF、首次刷新即可搜索、
  **新增照片在 SEARCH_READY 时已被真实 HTTP 搜到**、无模型时不谎报可搜索、no-op 全阶段 0 新工作
  （含 generation/profile/entries/jobs 前后全等）、no-op 搜索结果一致、小 delta 只消费 delta（含二次
  刷新 0 新工作）、单张被拒照片不阻断其余（`PARTIAL_FAILED`）、非图片变更记 `out_of_scope` 非失败、
  来源限定刷新不扫其他来源、离线来源不阻断其他来源且不产生错误 MISSING、中断后重试幂等、
  刷新期间 9–12 次合法搜索 0 拒绝、Web 资产含入口与阶段名。
* B 级回归：`uv run --no-sync pytest tests -q`（JUnit 实读）**1846 / 0 failures / 0 errors / 48 skipped**，
  262.823 s（`test-results/p4-02y-full.xml`）；`ruff check .`、`uv lock --check`、`git diff --check` 全绿
  （沙箱内 pytest basetemp `WinError 5` 为既有环境限制，按正常权限路由重跑，未改 ACL）。

## 9. 未验证 / 限制（不授予 PASS 的项目）

* **独立 DS-H 窄受影响面工程主审未进行**（按卡面由用户另开新会话）。
* Web 端到端浏览器计时未做（本环境无可靠浏览器自动化，同 P4-02W 的 `BROWSER_TIMING BLOCKED`）；
  本轮只验证了 HTTP 层：真实 202/只读状态/真实搜索，以及被服务的 `app.js`/`index.html` 确实携带入口与阶段名。
* 30 万档的语义/增强阶段用的是**本机真实 pinned Chinese-CLIP（CUDA）**，但 300 000 条历史向量来自
  容量工具的合成向量（见 §6 说明），因此「30 万历史内容的相关性/质量」不在本卡结论内。
* face / OCR 权重与 place resolver 数据集在本机不可用 ⇒ 两阶段全程 `SKIPPED_UNAVAILABLE`；
  它们的 `PARTIAL_FAILED` 路径只在集成测试里用替身覆盖（真实权重路径未验证）。
* 未测：GTX 1660、冷缓存、40 万档、soak、打包产物内刷新、真实个人图库、多来源并行扫描；
  **不自动进入 P4-02Y-T**。
* 已知设计边界（非缺陷）：本构建无可用语义通道时刷新**不会**宣告 SEARCH_READY（终态 `FAILED` +
  `MANIFEST_UNCONFIGURED`/`MODEL_UNAVAILABLE`），因为搜索本身依赖该通道。

## 10. 复现命令

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path
uv run --no-sync pytest tests/integration/test_p4_02y_fast_scan.py tests/integration/test_p4_02y_library_refresh.py -q
uv run --no-sync python tools/diagnostics/p4_02y_refresh_scale.py --mode noop  --work tmp/p4-02y/scale-300k       --reuse-work --repair-revisions --batch 128 --output test-results/p4-02y-noop-128.json
uv run --no-sync python tools/diagnostics/p4_02y_refresh_scale.py --mode delta --work tmp/p4-02y/scale-300k-delta --repair-revisions --batch 128 --photos 300 --duplicates 10 --renames 10 --deletes 10 --output test-results/p4-02y-delta.json
```

## 11. 独立 DS-H 窄受影响面主审（2026-09-29）

结论 **`ENGINEERING REVIEW PASS / SOL_NOT_REQUIRED`**，主审报告
[P4-02Y-dsh-narrow-affected-surface-review](P4-02Y-dsh-narrow-affected-surface-review.md)（无阻断问题，
F1–F3 非阻断）。主审按指令未重跑 30 万性能测试与 B 级回归，另在当前工作树独立复跑本卡定点 suite
**40/0/0/0**（`test-results/p4-02y-review-focused.xml`）与 `ruff`/`uv lock --check`/`git diff --check`。

主审补充口径（不改变本卡任何判定）：

* §1/§7 引用的「start → SEARCH_READY 50 195 ms」是诊断工具的 `search_ready_wall_ms`（轮询中**首次
  观测到** `search_ready=true` 的时间，与整轮 `refresh_wall_ms` 相同）；refresh 自身记录的口径是
  `status.counters.search_ready_ms = 39 538 ms`。即 **39.5 s 为流水线口径、50.2 s 为工具观测上界**，
  两者都满足 §11 的「2–5 分钟进入 SEARCH_READY」体验目标（F1）。
* `p4-02y-delta.json` 的 `search` 探针在 refresh 进入终止态之后执行，因此「SEARCH_READY 时已可搜索」
  由 `refresh/pipeline.py` 的门禁顺序（projection `SYNCED` + delta 在当前 generation `CONFIRMED`
  之后才 `mark_search_ready`，其后只剩不改变可搜索性的增强阶段）与本卡定点集成测试共同支撑（F2）。
* 单轮变化超过 `DELTA_LIMIT = 50 000` 时本轮会清空 delta 集，D..G 增强各报 `SKIPPED_NO_TARGETS`
  且后续 refresh 不再捡起；catalog / projection / semantic / SEARCH_READY 与新增内容可搜索性不受
  影响，缩略图仍可由按需入口与 `catalog thumbnail` 补齐（F3，超出本卡「几百张变化」验收范围）。
