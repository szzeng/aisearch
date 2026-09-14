# Development status

## Current state

**P0 is complete. P1-01 is PASS (C2 final short re-review). P1-02 is PASS. P1-03 is PASS. P1-04 is PASS
(B level plus real-port/C-level packaged smokes and the C1 Windows-path/local-access review). P1-05 is PASS
(B level + C-level packaged smoke + a recorded real-browser smoke); its non-blocking follow-up P1-05B
(front-end verification base) is also PASS. P1-06 is PASS (D level: 16 new adversarial acceptance cases for
duplicate / move / replace / offline / remap / restart, with no production change). **P2-01 and P2-01A are PASS
(A-level protocol closures, no production/schema/dependency change). P2-02 is PASS (D level: a continuous
`0001 → 0002` migration, the persistent scheduler with scheduler-owned device qualification, and named
fault-injection/recovery cases). P2-03 is PASS (D level: schema 3 artifact/outbox/index confirmation and real
LanceDB replay/repair). **P2-03A is PASS** (D level: the 0004 place core, GPS facts, the place
directory, manual assignment/unassign/restore_auto with batch and compensating undo, and the pure
SQLite place filter API; 48 new cases). **P2-03B is PASS** (D level: the `0005` dataset/resolution
migration, the offline name-package lifecycle, the `non_model + CPU_PREFERRED` nearby-city resolver,
suggestion-only candidates with an independent suggestion revision, and the explicit local dataset
tool; 65 new cases). **P2-03C is PASS** (B level + a recorded real-browser smoke + the C-level
packaged smoke proving the page is inside the frozen bundle). **P2-04 is PASS** (B level + the D-level
projection fault injection + a C-level packaged smoke: the `0006` derived search layer, the versioned
tokenizer/controlled MATCH, the effective-place text projection and rename reprojection; 35 new cases,
schema_version 5 → 6). **P2-05 is PASS** (B level + the deterministic retrieval comparison + a replay
against the P2-03 crash window: the two bounded recall channels, the pure RRF fusion, the authoritative
candidate-ID restriction and the pending-projection top-up; 19 new cases, no migration, schema_version
stays 6). **P2-06A is now READY / CURRENT TASK**; the planned P2-06 was split into
**P2-06A** (search protocol, `POST /api/v1/search`, sessions/cursors, `SearchProfile` binding and atomic
activation) and **P2-06B** (explicit relevance feedback and the rerank adapter), without changing the
overall route.

P1-01 history (frozen contract `docs/decisions/ADR-0002-catalog-identity-migration-scan-contract.md`):

- Implementation `92ca7566`, three C2 review rounds (`83adb65` FAIL, `8393583` FAIL) and two corrective passes
  (`ce6f56c5`, `f0b4f402`), final C2 short re-review PASS in [docs/reports/P1-01-c2-final-pass.md](reports/P1-01-c2-final-pass.md).
  Accepted closing baseline: `e39a40c`.
- Persistent positive writes are submission-qualified, retry/lease exhaustion commits job/scan_run terminal state in one
  transaction, schema compatibility cannot be bypassed, stale hashes cannot produce success, and same-scan recovery no
  longer inherits observations from an older execution.

P1-02 (streaming scan, stability waiting, hash before/after stat, missing rules) is recorded in
[docs/reports/P1-02-streaming-scan-stability-and-missing.md](reports/P1-02-streaming-scan-stability-and-missing.md).

P1-03 (EXIF orientation, thumbnails/previews, sharded rebuildable cache, bad-media isolation) is recorded in
[docs/reports/P1-03-orientation-thumbnails-cache-and-isolation.md](reports/P1-03-orientation-thumbnails-cache-and-isolation.md).
It ships `catalog thumbnail` / `catalog preview` / `catalog media-status`, keeps `schema_version = 1` (no new table, no
new dependency) and treats the atomically published artifact file itself as the READY marker.

P1-04 (sources/assets API, controlled file access, local session protection) is recorded in
[docs/reports/P1-04-api-controlled-files-and-local-session.md](reports/P1-04-api-controlled-files-and-local-session.md).
It adds `src/photo_search/api/**` (`/api/v1/{health,session,capabilities,stats,sources,assets,jobs}` plus
thumbnail/preview/original), a loopback-only binding policy with a single-use bootstrap credential, HttpOnly
session + double-submit CSRF + Host/Origin checks, four-layer controlled path resolution, and one background
scan executor that reuses the production `catalog scan` path. `schema_version` is still 1, ADR-0002 is unchanged,
and the only dependency edit is declaring the already-resolved `pydantic` range explicitly.

P1-05 (sources page, insertion-time browsing, detail view, offline hints) is recorded in
[docs/reports/P1-05-local-frontend-sources-browse-detail-offline.md](reports/P1-05-local-frontend-sources-browse-detail-offline.md).
It ships a dependency-free static front end (`src/photo_search/web/**` served from package data by
`src/photo_search/api/web.py`), an additive `order=time_desc` keyset with its own cursor space, an additive
`next=` redirect on the session exchange, and a launcher URL that lands on the page. `schema_version` is still 1,
ADR-0002 is unchanged, `pyproject.toml`/`uv.lock` are untouched, and every existing P1-04 test still passes
unmodified.

P1-05A 的服务端 Windows 路径脱敏补强为 **PASS**：完整 B 级测试（303 passed、1 skipped）、专项回归、
真实端口损坏图片场景、Ruff 与差异检查均通过；详见
[P1-05A 报告](reports/P1-05A-local-path-redaction.md)。

P1-06 (the duplicate / move / replace / offline / remap / restart acceptance closed loop) is recorded in
[docs/reports/P1-06-identity-lifecycle-acceptance.md](reports/P1-06-identity-lifecycle-acceptance.md). 它新增
一个测试文件 `tests/integration/test_catalog_identity_lifecycle.py`（16 个用例），**未改任何生产代码**：
`schema_version` 仍为 1，ADR-0002、`0001_initial.sql`、`pyproject.toml`、`uv.lock` 均未改动，既有测试
全部原样通过。该任务完成后主线依次进入 P2-01、P2-01A 与 P2-02。

Current architecture: `个人图片搜索系统_V3.0_开发主架构.md`, requirement revision 4 (2026-09-13 actual-place planning).
Current execution strategy: `个人图片搜索系统_V3.1_Codex_DeepSeek_开发执行策略.md`, revision 3 (2026-09-13 location task chain and current-state reconciliation).

## Latest planning closure — P2-00 (2026-09-13)

地点规划已完成（文档交付，**地点功能尚未实现，P2-01 尚未执行**）。范围已由用户确认：国家／城市／景点／
自定义地点，GPS 与手动归类。GPS 事实、机器建议、人工归属分离；取消后的人工锁保留；首版提供离线附近城市
建议供确认，不把最近点当准确行政区。沿用静态前端，schema_version 仍为 1。

决策：[ADR-0004](decisions/ADR-0004-place-classification-plan.md)。
全项目规划：[P2-00 路线图](plans/P2-00-location-project-roadmap.md)。
核对与验证：[P2-00 报告](reports/P2-00-location-project-planning.md)。
本次文档闭环 PASS：既有迁移测试 11/11 通过、Ruff／diff-check clean、7 份文档链接通过；
测试临时目录 ACL 限制经隔离目录和工具提权解决，完整命令在报告。未执行地点功能测试或 P2-01 契约测试。

下一任务仍为 **P2-01 / C2 / GPT-5.6 Sol / high**；增加无模型 CPU／非向量产物和权威筛选版本外壳。
随后 **P2-01A → P2-02 → P2-03 → P2-03A/B/C → P2-04～08**。地点实现不提前到本次规划。
拍摄时间、HEIC 与 remap 产品欠项分别排入 P4-00A/B、P4-00C、P4-05A/B。

## Latest contract closure — P2-01 (2026-09-13)

P2-01 已冻结 [ADR-0003](decisions/ADR-0003-p2-processing-runtime-and-search-profile-contract.md)：
JobRequest/JobResult 五字段身份映射、三种 device_policy 与禁止隐式 fallback、RuntimeCapabilities、
三类 ProcessorSpec、适配器边界、不可变 SearchProfile/原子切换、space 隔离、非向量成功结果和
CatalogFilterRevision 独立版本。机器可读伴随物为
[p2-contracts.v1.json](decisions/contracts/p2-contracts.v1.json)，报告见
[P2-01 报告](reports/P2-01-processing-runtime-search-profile-contract.md)。

本任务没有改生产代码、表/migration、schema_version、ADR-0001/0002、依赖或既有测试。
A 级验证：新契约测试 11 passed；Ruff 与 diff-check clean。环境无第三方 JSON Schema validator，
测试实际执行伴随文件所用关键字子集的实例正反例，但不声称运行时拒绝已经实现。

其后续契约任务 **P2-01A** 也已完成；当前进入 P2-02。

## Latest place contract closure — P2-01A (2026-09-13)

P2-01A 已冻结 [ADR-0005](decisions/ADR-0005-place-facts-assignment-and-search-contract.md)：GPS 原始事实、
附近城市机器建议、人工有效归属/取消锁三层分离；稳定 place_id、首版四类地点、同名与祖先层级；
批量 500、幂等/revision、操作历史与补偿撤销；地名包 manifest/原子激活/回退；增量 DDL、`/api/v1`
与结构化错误；SQLite 权威筛选、suggestion revision 和 CatalogFilterRevision 触发矩阵。

机器可读伴随物为 [p2-place-contracts.v1.json](decisions/contracts/p2-place-contracts.v1.json)，报告见
[P2-01A 报告](reports/P2-01A-place-contract.md)。本任务未改生产代码、migration、schema_version、
依赖或既有测试。A 级验证：地点契约专项 21 passed，Ruff 与完整暂存差异检查 clean；schema/样例及
显式跨项语义检查不等于数据库/API/运行时拒绝已经实现。

下一任务是 **P2-02 / DS-H / DeepSeek V4.1 Flash / 高推理**，任务卡
[docs/tasks/P2-02.md](tasks/P2-02.md)。它实现持久调度、设备资格、暂停/退避/优先级与阻塞恢复，
地点只用确定性 non-model CPU 假执行器验收，不提前实现 GPS 或地点表。

## Latest scheduler closure — P2-02 (2026-09-13)

P2-02 已实现持久调度闭环并通过 D 级验收，报告
[P2-02 报告](reports/P2-02-persistent-scheduler-and-device-qualification.md)。
起点 HEAD `bf1a652`（P2-01A），工作树干净。

新增 `src/photo_search/scheduler/**`（`p2-contracts.v1` 契约实现、不可变 runtime capability 快照、
处理器专项 evidence、唯一设备资格矩阵、持久暂停/公平窗口/指数退避、claim→dispatch→submit→
cancel/recover 闭环、只读诊断投影）和连续增量迁移
`catalog/migrations/0002_scheduler_runtime.sql`：**`schema_version` 推进到 2**，
`0001_initial.sql` 未改，五字段逻辑唯一键、七态状态机、lease/attempts/迟到提交语义不变。
`POST /api/v1/scheduler/{pause,resume}`、`GET /api/v1/jobs`（派发摘要）与
`GET /api/v1/capabilities`（scheduler/runtime 健康摘要）已接入既有 session/Origin/CSRF/Host 边界。
本任务未修改 `workers/**`、打包入口或依赖，因此未触发 C 级；未产生 artifact/outbox/LanceDB/向量行。

| Check | Result |
|---|---|
| `uv run pytest tests/unit tests/integration` | exit 0，`415 passed, 1 skipped in 131.90s` |
| `uv run pytest -q tests/unit/test_p2_scheduler.py` | exit 0，`29 passed` |
| `uv run pytest -q tests/integration/test_p2_scheduler_recovery.py` | exit 0，`10 passed` |
| `uv run pytest -q tests/integration/test_p2_scheduler_api.py` | exit 0，`8 passed` |
| `uv run ruff check .` / `git diff --check` | clean / clean |
| `uv lock --check` | exit 0，`Resolved 95 packages`（依赖未变） |
| v1→v2 迁移 | 真实 0001 库 + 中文/空格位置 + 活动 lease 行：`applied=(2,)`、备份内 `schema_version=1` 且只有 6 张 v1 表、19 列逐行不变、新列仅 `requested_device='auto'`、旧 lease 三元组原样保留 |
| 中断/缺件/过新 | 注入 0003 失败后整体回滚（版本仍 2、无 `probe_note`）；`DROP scheduler_state` 后 `SCHEMA_INCONSISTENT` 且主库字节不变；过新库 `SCHEMA_TOO_NEW` 且无 `-wal`/`-shm`（库与 API 两条路径） |
| 调度/资格 | GPU 缺失/不健康/无 evidence → `GPU_REQUIRED` 阻塞且不耗 attempts、显式 GPU 不转 CPU，同队列 `CPU_PREFERRED` non-model 假任务正常 SUCCEEDED；新健康 revision 后只恢复合格任务，`DEVICE_POLICY_CONFLICT` 不复原 |
| 重启/退避/公平 | 进程退出后过期 lease 接管 `attempts+1`、旧 worker 心跳与迟到结果被拒且零副作用；退避 `11000→13000→17000→21000`（8000 被 4000 上限截断）；pause 跨重启仍暂停而既有 RUNNING 合法完成；10 个公平窗口后旧 backlog 追平并被选中 |
| 结果资格 | `JobResult` provider/device/precision 不匹配 → `RUNTIME_RESOLUTION_MISMATCH` 且 job 行逐字节不变；`artifacts=[]` 与结构化非向量结果均正常；协议不兼容记确定性 FAILED、执行器异常记可重试且只记异常类型 |
| 诊断工具 | `frontend_smoke_fixture.py --too-new` 改为由 `SUPPORTED_SCHEMA_VERSION` 推导；顺带修掉该工具 `--help` 的历史崩溃（argparse 二次 `%` 格式化） |

## Latest artifact/index closure — P2-03 (2026-09-13)

P2-03 已完成通用 artifact/outbox/LanceDB/index_entries 可恢复闭环，报告见
[P2-03 报告](reports/P2-03-artifact-outbox-index-submission.md)。新增连续 migration
`0003_artifact_outbox_index.sql`，**schema_version 推进到 3**；0001/0002、冻结 ADR/JSON schema、
依赖、worker 与打包入口均未改。

`Scheduler.submit` 现在先核对 lease/revision/processor/device，再受控发布 structured/binary/embedding
产物，并在一个 SQLite 事务写 artifact、embedding outbox、PENDING_REPAIR index entry 与 job 终态。
LanceDB 0.38.0 使用原生 merge 幂等提交；只有 SQLite 确认后才显示 READY。删除确认、Lance 已写而
SQLite 未确认、晚到旧 projection、artifact 回收/再执行和引用保护都有具名恢复用例。
`schedule_projection_refresh` 已为 P2-03A 的 CatalogFilterRevision 同事务投影刷新提供稳定实现入口。

| Check | Result |
|---|---|
| `uv run pytest -q tests/unit tests/integration` | exit 0，433 collected，432 passed / 1 skipped |
| P2-03 专项 | exit 0，17 passed（artifact、提交/重放、migration/repair） |
| `uv run ruff check .` / `git diff --check` | clean / clean |
| `uv lock --check` | exit 0，`Resolved 95 packages` |
| C 级 | 未触发：未改 worker、正式环境、打包入口、依赖或新 package-data 目录 |

## Latest place-core closure — P2-03A (2026-09-13)

P2-03A 已实现地点核心闭环并通过 D 级验收，报告
[P2-03A 报告](reports/P2-03A-place-core-gps-assignment-api.md)。起点 HEAD `b9b5312`（P2-03），
工作树干净。

新增连续 `catalog/migrations/0004_place_core.sql`：**`schema_version` 推进到 4**，0001/0002/0003
未改；新增 `places`/`place_aliases`/`place_external_refs`/`asset_geo_facts`/
`asset_place_assignments`（+只追加 history）/`asset_place_exclusions`/`place_operations`
（+items）/`catalog_filter_revision`（+events）/`place_suggestion_revision`，并把 SQLite 能表达的
PK/FK RESTRICT/枚举/范围/模式组合全部下沉（含“禁止物理删除地点”“历史行只允许
ACTIVE→SUPERSEDED”“`SOURCE_UNAVAILABLE` 不得成为当前事实”）。

新增 `src/photo_search/places/**`（目录、层级矩阵与环校验、别名、人工 assign/unassign/restore_auto、
排除、幂等/批量 500/补偿撤销、CatalogFilterRevision 与投影刷新、纯 SQLite 筛选/覆盖）与
`src/photo_search/geo/**`（版本化 `non_model + CPU_PREFERRED` GPS 提取器、`VALID/ABSENT/INVALID/
UNSUPPORTED_CRS` 分支、受控读取 + SHA/stat 双重复核、确定性 structured GeoFact）。
`Scheduler.submit` 在既有事务内落当前事实；`/api/v1` 新增 10 条地点路径（OpenAPI 14 → 26），
`GET /assets` 支持 `place_ids/include_descendants/place_state`。本任务未改 ADR-0001～0005、
冻结 JSON schema、依赖、`workers/**` 或打包入口。

| Check | Result |
|---|---|
| `uv run --no-sync pytest -q -p no:cacheprovider tests/unit tests/integration` | exit 0；`--collect-only` 计 481 例；进度行 1 个 `s`，即 480 passed / 1 skipped |
| 本任务专项（5 个新增 + 3 个版本耦合文件） | exit 0，72 passed（新增 48 例：place_directory 14、geo_exif 12、geo_extract 12、place_api 5、place_migration 5） |
| `uv run ruff check .` / `git diff --check` / `uv lock --check` | clean / clean / exit 0（95 packages，依赖未变） |
| v3→v4 迁移 | 备份内 `schema_version=3` 且无地点表；单事务推进到 4；旧行逐列不变；注入损坏 0004 整体回滚；缺件 `SCHEMA_INCONSISTENT`；过新库零写 |
| GPS | `(0,0)` 合法；南/西半球；非法分母/NaN/Inf/越界/未知 datum 分支；离线与读中变化 → `BLOCKED` 且**不覆盖**上一条事实；迟到 lease 零事实副作用 |
| 人工控制 | assign→unassign→重启→restore_auto→再 assign 的锁/origin/排除/revision 逐项正确；无任何自动写路径；批量 500 全原子、501/0/重复被拒 |
| 撤销与版本 | 无冲突 undo 递增 revision；目标被后续编辑整批 `PLACE_OPERATION_NOT_UNDOABLE` 且历史不删；触发矩阵与同事务 outbox 投影刷新实测；注入失败全回滚 |
| 筛选与隐私 | 后代展开、同名、无地点、manual_unassigned、重复位置去重、与 source AND；`suggested` 在无解析器时确定为空；精确坐标只在会话保护 `/geo`，日志/错误/诊断/outbox/Lance 无坐标 |
| C 判定 | 按卡**不触发**（未改 workers/打包入口/依赖/新增 package-data 目录）；仍额外执行真实 onedir 构建 + `packaged_api_smoke.py`（`status=ok`、`problems=[]`、startup 1.8 s），并审计 bundle：`photo_search.places.*` 与 4 个 migration 已入包，`geo.*`/`scheduler.engine` 未入包（应用尚未接调度器，见报告 §9） |

## Latest dataset/resolution closure — P2-03B (2026-09-14)

P2-03B 已实现离线地名包与 suggestion-only 附近城市解析并通过 D 级验收，报告
[P2-03B 报告](reports/P2-03B-offline-name-dataset-and-nearby-city-suggestions.md)。
起点 HEAD `d7229be`（P2-03A），工作树干净。

新增连续 migration `catalog/migrations/0005_place_dataset_resolution.sql`：**`schema_version`
推进到 5**，0001～0004 未改。新增 `geo_datasets`/`geo_dataset_files`/`active_geo_dataset`
（单例，未安装时 `dataset_id IS NULL`）/`place_resolutions`（逻辑输入唯一）/
`place_resolution_candidates`（固定 `SUGGESTION_ONLY` + `NEARBY_CITY_POINT` + `DISTANCE_ONLY`）；
包内容不可变、`STAGED→VALIDATED→ACTIVE→RETAINED`（含 RETAINED→ACTIVE 回退）与机器产物只增不改
都由 trigger 固定。新增 `geo/dataset.py`（manifest/校验/确定性 builder/距离）、
`geo/resolver.py`（`geo-nearby-city@1`、`non_model + CPU_PREFERRED`、结构化 artifact 承载、
`persist_resolution`）、`places/datasets.py`（导入/校验/激活/回退/清理保护 + 数据集地点物化）、
`places/suggestions.py`（可见候选与独立 suggestion revision）；`Scheduler.submit` 在既有事务内落
resolution；`/places/datasets`、`/assets/{id}/place` 建议状态、`/assets` 的
`suggestion_revision` 绑定与 `origin=CONFIRMED_SUGGESTION` 已接入既有 session/Origin/CSRF 边界。
显式本地工具 `tools/places/manage_dataset.py` + 可验证的 `tools/places/examples/**`（不联网、
不改依赖、未改 `workers/**`、未改打包入口、未新增 package-data 目录）。

| Check | Result |
|---|---|
| `uv run --no-sync pytest -q -p no:cacheprovider tests/unit tests/integration` | exit 0；`--collect-only` 计 546 例；进度行 1 个 `s`，即 545 passed / 1 skipped |
| 新增专项 6 个文件 | 65 passed（manifest 23、lifecycle 13、resolver 15、place-dataset-api 7、migration 5、tool 2） |
| `uv run ruff check .` / `git diff --check` / `uv lock --check` | clean / clean / exit 0（95 packages，依赖未变） |
| v4→v5 迁移 | 备份内 `schema_version=4` 且无 5 张新表；单事务推进到 5；旧行逐列不变；注入损坏 0005 整体回滚；缺件 `SCHEMA_INCONSISTENT`；过新库零写；非法状态跳转/内容改写/产物改写全部被拒 |
| 包生命周期 | 源包→数据根 staged 目录（含 fsync）→VALIDATED→ACTIVE；提交前崩溃回滚（指针/状态/revision 不变）；被篡改的已校验包激活被拒且旧指针不变；删/改活动包 → `missing`/`invalid` 且解析 `PLACE_DATASET_MISSING`/`PLACE_DATASET_INVALID`；rollback 重新校验目标包并成功；活动/被 resolution 引用/作为回退目标的包清理返回 `PLACE_REFERENCED`；无引用 staged 包可删除 |
| 解析分支 | `VALID`→候选（`(distance,dataset_place_id)` 排序、上限 3/10、边界含等号）；`ASSUMED_WGS84`→候选但 `assumed_datum=true`/`requires_confirmation=true`；`ABSENT`/`INVALID`/`UNSUPPORTED_CRS`/海域/`(0,0)`→成功空结果；同名城市可区分且不合并；无包/缺包只 BLOCKED，人工创建/归属/筛选/操作历史继续可用 |
| 人工优先与版本 | 无任何机器路径写 `asset_place_assignments`；晚到解析在人工加锁后提交仍不改 assignment（`control_revision_seen=1`）；可见集合变化才 +1 suggestion revision（重放/空结果不变），`catalog_filter_revision` 全程不受数据包与解析影响；`origin=CONFIRMED_SUGGESTION` 的归属在重扫、切包与回退后保留 |
| 故障注入 | 解析事务中断 → resolution/候选 0 行、revision 仍 0、job 仍 RUNNING；`artifacts=[]` 的合格结果仍可提交；GPU 缺失（capability 只有 cpu）时解析/建议/人工写全部正常 |
| API 与隐私 | `/places/datasets` 无包时 `unavailable` 且不伪造 ACTIVE；`/assets/{id}/place` 只在 `/geo` 之外无坐标、无绝对路径；`place_state=suggested` 携带过期 `suggestion_revision` → 409 `PLACE_SUGGESTION_REVISION_STALE`；无 session 全部 401、跨 Origin 403 `ORIGIN_NOT_ALLOWED`、缺 CSRF 403 `CSRF_INVALID` 且零写入 |
| C 判定 | 按卡**不触发**（未改 workers/打包入口/依赖/新增 package-data 目录）；因“继承项：接入解析执行器必须复核导入图”仍执行真实 onedir 构建 + `packaged_api_smoke.py`（`status=ok`、`problems=[]`、startup 1.0 s，中文/空格路径），并审计 bundle：`geo.dataset`/`geo.exif` 与 `places.*` 已入包、0001～0005 共 5 个 `.sql` 在包内；`geo.extract`/`geo.resolver`/`scheduler.engine` 仍未入包（无常驻调度线程，见报告 §9） |
| 环境事件 | 打包 smoke 会把约 3.4 GB bundle 复制到 `%TEMP%`，C 盘被填满导致第二次复核以 `WinError 112` 失败；随后只清理本任务创建的 3 个 `%TEMP%\P2-03B 打包*` 目录（约 3.9 GB）并确认 C 盘恢复 3.76 GB 可用，C 级证据取自第一次成功运行 |

## Latest place-page closure — P2-03C (2026-09-14)

P2-03C 已实现静态地点整理页并通过 B 级、真实浏览器 smoke 与 C 级打包验收，报告
[P2-03C 报告](reports/P2-03C-place-page-and-manual-curation.md)。起点 HEAD `f4959a2`（P2-03B），
工作树干净。

新增依赖-free 页面 `GET /places`（`src/photo_search/web/{places.html,places.js,places.css}`，
由 `api/web.py` 从 package data 提供，不遮蔽 `/api/v1/places`），外壳导航新增入口；页面覆盖目录
keyset 分页与 `CATALOG_FILTER_REVISION_STALE` 提示、同名地点的完整祖先链 + 稳定 `place_id`、
创建/改名/别名（`expected_revision` 与 `PLACE_ASSIGNMENT_CONFLICT` 的 `current_revision` 提示）、
建议列表绑定 `suggestion_revision`、`SUGGESTION_ONLY` 候选的单条确认
（`origin=CONFIRMED_SUGGESTION`）与 ≤500 分批的原子批量动作（逐批结果、冻结 `asset_id` 快照）、
操作日志与撤销（含 `PLACE_OPERATION_NOT_UNDOABLE`/`PLACE_IDEMPOTENCY_CONFLICT` 提示）、
数据包状态面板与缺包降级。未改 ADR、migration、`schema_version`（仍为 5）、触发矩阵、错误码、
依赖或 `workers/**`；本轮未新增后端能力。

| Check | Result |
|---|---|
| `uv run --no-sync pytest -q -p no:cacheprovider tests/unit tests/integration` | exit 0，148.6 s；`--collect-only` 计 564 例，进度行 1 个 `s`，即 563 passed / 1 skipped |
| 专项（前端静态契约 + 页面路由 + 页面请求形状） | exit 0，68 passed（P2-03C 新增 18 例） |
| `uv run ruff check .` / `git diff --check` / `uv lock --check` | clean / clean / exit 0（95 packages，依赖未变） |
| 真实浏览器 smoke（内置浏览器 + 127.0.0.1:8732 + `%TEMP%` 合成夹具 14 张照片/14 GPS/14 resolution/12 建议） | 10 步逐条记录：落地导航、创建/改名/过期 revision、同名消歧与后代过滤、单条确认（`origin=CONFIRMED_SUGGESTION` 留存于结果区、列表 11→10）、批量确认（1 批 10 项原子提交、空目标前端拒绝且不发请求）、跨标签页 `PLACE_SUGGESTION_REVISION_STALE`、撤销成功 + `PLACE_OPERATION_NOT_UNDOABLE`、缺包 `missing`/`PLACE_DATASET_MISSING` 且人工路径可用、Tab/Enter 可达、页面文本无路径无坐标、访问日志 145 条全在 `/api/v1/*` 或 `/web/*` 且 `geo-hits = 0` |
| smoke 期间自修并复核 | 改名后编辑标题未刷新、后代过滤保留名称条件、单条确认结果随列表刷新消失（3 处，均在最终代码上重新核对） |
| C 级（页面是发行资源） | `build_onedir.py` exit 0（67.6 s，bundle 3,541,244,472 B，21 个 NVIDIA DLL）；bundle 内 `photo_search/web/` 含 `places.html`(12,329) `places.js`(61,297) `places.css`(2,558) 与既有三个资产、migrations 仍为 0001～0005；`packaged_api_smoke.py` exit 0 `status=ok`/`problems=[]`：`/places` 200 `text/html` 无外链无本机路径、`/web/places.js` 200、`/web/places.css` 200、外壳链接 `/places`、startup 1.0 s |
| 磁盘与清理 | 只删除本任务创建的 `%TEMP%\P2-03C 打包页面验收`（3,541,874,021 B），C 盘 2.56 GB → 5.87 GB；其中应用用 `icacls` 收紧过 DACL 的 `runtime\session.json`（约 1 KB）在沙箱内无法删除而残留 |
| C 判定 | 依赖/`workers/**`/打包入口未变、未新增 package-data 目录（按卡不触发）；因页面是发行资源仍执行真实 onedir 构建 + 打包 smoke + bundle 审计 |

未验证项（报告 §9）：本次内置浏览器的 `viewport` 覆盖没有改变实际窗口，640×900 像素级证据用
「0 个元素内部横向溢出 + `min(100%, …)` 网格」代理；批量接口没有 `origin` 字段，批量确认记录
`MANUAL`（要保留 `CONFIRMED_SUGGESTION` 需先更新契约）；`PLACE_OPERATION_NOT_UNDOABLE` 的
「目标被后续编辑」分支在页面上不可达（由 API 测试覆盖）；页面不触发后台解析；无 DOM 自动化；
`GET /assets` 与建议列表仍非快照隔离读。

## Latest search/fts closure — P2-04 (2026-09-14)

P2-04 已实现文件名／有效地点 FTS 与改名重投影并通过 B 级、D 级故障注入与 C 级打包验收，报告
[P2-04 报告](reports/P2-04-fts-and-rename-reprojection.md)。起点 HEAD `6cc75a9`（P2-03C），工作树干净。

新增连续 `catalog/migrations/0006_search_fts.sql`：**`schema_version` 推进到 6**，0001～0005 未改。
新增 `src/photo_search/search/**`（`tokens.py` 版本化分词与受控 MATCH、`documents.py` 派生文档与
受影响集队列、`query.py` 绑定谓词与滞后摘要）。`search_documents` 以 `asset_id` 为主键，六列文本
按主架构 §4.3 分开，本任务只写 `filename_text`（PRESENT 位置的 basename）与 `place_text`
（`MANUAL_ASSIGNED` 的有效地点 + 完整祖先链 + ACTIVE 别名，含数据包别名）；FTS5 是外部内容表、
`rowid = asset_id`，由触发器在同一事务维护（级联删除也清理索引）。受影响集由触发器在事实变更的
同一短事务写入 `search_projection_queue`（文件名、归属、地点改名/退休、别名增改，含递归子树展开），
重投影按批派生，`GET /assets?text=` 先做有界 catch-up 再 MATCH 并返回
`search={state SYNCED|LAGGING, queued_assets, processed_assets, documents, tokenizer_version,
fts_generation_id, terms, match_expression}`；`GET /capabilities` 新增只读 `search` 投影摘要。
地点条件仍完全由 SQLite 权威过滤，文本命中不证明拍摄地。

| Check | Result |
|---|---|
| `uv run --no-sync pytest -q -p no:cacheprovider tests/unit tests/integration` | exit 0；`--collect-only` 计 **599** 例，进度行 1 个 `s`，即 **598 passed / 1 skipped** |
| 新增 5 个测试文件 | **35 passed**（tokenizer 11、migration 7、document 8、projection 5、api 4） |
| `uv run ruff check .` / `git diff --check` / `uv lock --check` | clean / clean / exit 0（95 packages，依赖未变） |
| v5→v6 迁移 | 备份内 `schema_version=5` 且无 7 张新表；单事务 `applied=(6,)`；旧行逐列不变；注入损坏 0006 整体回滚；缺 `search_projection_queue` → `SCHEMA_INCONSISTENT`；过新库 `SCHEMA_TOO_NEW` 零写 |
| FTS 一致性 | 插入/更新/删除后无孤儿行；改名后旧词条 0 命中、新词条 1 命中；删除资产级联清空索引；`integrity-check` 通过；重扫改名只留新名称 |
| 重投影与恢复 | 改名在同一事务持久记录子树受影响集、`catalog_filter_revision` 恰好 +1、`suggestion_revision` 不变；批次中途注入异常 → 整批回滚、重跑无重复无遗漏；`LAGGING/SYNCED` 信号与权威 SQLite 过滤同时正确 |
| 候选隔离与 MATCH 边界 | 只有候选的资产 `place_text` 为空且候选地名 0 命中；空/特殊字符/超长查询 → 422 `VALIDATION_ERROR`；中英数字混排、全半角、显式短语、停用词按报告 §3 策略逐条断言 |
| C 级（改了应用导入图 + 新 migration） | `build_onedir.py` exit 0（约 59 s，bundle 3,541,272,714 B）；bundle 内 migrations 含 0001～0006，归档含 `photo_search.search{,.documents,.query,.tokens}`；`packaged_api_smoke.py` exit 0 `status=ok`/`problems=[]`，新增 `GET /assets?text=照片` → 200、命中 `[1]`、`state=SYNCED`、`fts_generation_id=fts-v1`、startup 1.0 s |
| 磁盘与清理 | smoke 工作目录放在 D 盘（`dist\p2-04-smoke`）以避开 C 盘压力；结束后只删除本任务创建的该目录（约 3.5 GB），其中 `runtime\session.json`（约 1 KB，应用收紧过 DACL）在沙箱内无法删除而残留 |

未验证项（报告 §10）：单次请求最多追赶 2000 个资产（更大规模改名需后续请求或 P2-05 后台追赶）；
`rebuild_all` 尚未接 CLI/调度；`filename_text` 只取 PRESENT 位置（整库离线资产按名称检索不到）；
同一资产多 PRESENT 名称之间存在短语跨界理论命中；停用词表为固定小表且「只含停用词」按 422 拒绝；
`search` 是加性响应字段（不带 `text=` 时只有显式 `"search": null`）；候选链路为直接写行（解析器由
P2-03B 验收）；前端尚未接 `text=`（无需浏览器 smoke）；`GET /assets` 仍非快照隔离读。

## Latest retrieval closure — P2-05 (2026-09-14)

P2-05 已实现双通道并行召回、RRF 融合、权威候选预过滤与投影滞后补查，并通过 B 级完整测试与
确定性检索对照验收，报告
[P2-05 报告](reports/P2-05-vector-fts-fusion-and-authoritative-topup.md)。起点 HEAD `fdfd13b`
（P2-04），工作树干净。

新增 `src/photo_search/search/**`（`fusion.py` 纯函数 RRF、`embedding.py` 查询侧
`EmbeddingAdapter` 切片、`retrieval.py` 权威谓词/两通道/补查、`service.py` 服务层编排）。
文本通道复用 P2-04 的 `build_match` + `search_documents_fts`（`bm25, asset_id`），向量通道复用
P2-03 的 `LanceVectorIndex`（新增加性 `search`/`search_candidates`，`asset_id IN` 下推预过滤）；
每通道有界 `DEFAULT_TOP_K=200`，向量候选按 CONFIRMED `index_entries` ∩ 权威条件每块 1000 个
keyset 扫描后精确归并。融合公式 `Σ weight/(60+rank)`、权重相等、去重、排序
「score 降序 → 命中通道数降序 → asset_id 升序」。查询必须显式绑定 `space_id`+`generation_id`+
维度（否则结构化拒绝），文本只编码一次；`catalog_filter_revision` 变化在返回前复核并标注
`FILTER_REVISION_CHANGED`。补查路径把「绑定 generation 下没有已确认投射」的权威资产显式列进
`pending_semantic`，因此改名/新归类后第一次查询即命中，向量未确认时 `matched_by` 只记 `fts` 且
`semantic_ready=false`。未改 migration（`schema_version` 仍 6）、ADR/冻结 JSON schema、依赖、
`places/**`、`ingestion/**`、`workers/**`、打包入口、前端；未接 API，故按卡不触发 C 级。

| Check | Result |
|---|---|
| `uv run --no-sync pytest -q -p no:cacheprovider tests/unit tests/integration` | exit 0；`--collect-only` 计 **618** 例，进度行 1 个 `s`，即 **617 passed / 1 skipped** |
| 新增 4 个测试文件（+ 共享夹具） | **19 passed**（fusion 6、channels 5、authority 5、topup 3） |
| `uv run ruff check .` / `git diff --check` / `uv lock --check` | clean / clean / exit 0（95 packages，依赖未变） |
| 确定性通道与融合 | 假 `EmbeddingAdapter` + 真 LanceDB generation：FTS `[sunset]`、向量 `[sunset, IMG_0001, 西湖]`（距离 `0.0/0.02/2.0`）、融合 `[sunset, IMG_0001, 西湖]`；同一查询两次 `json.dumps` 字节一致；查询文本只编码一次 |
| 过滤正确性 | 后代展开/`include_descendants=false`/`place_ids` OR/`place_state` 与原语义一致；`place_ids=[空地点]` 时两通道均 0 行（全库最近邻不可达，证明非「Top-K 后过滤」）；非法 `where()` 列 → 结构化 `FILTER_UNSUPPORTED` 且不回退未过滤检索；空候选集不打开引擎 |
| 补查与绑定 | 文本命中但向量未确认的资产仍出现并被 `pending_semantic` 指名（`PENDING_REPAIR`/`pending_events=1`/`in_ranked_hits`）；未知 generation/foreign space/错误维度/半绑定/非法 `top_k` 全部结构化拒绝；旧 READY generation 仍可查，新 PENDING generation 向量通道为空且不宣称新照片已入语义检索 |
| P2-03 崩溃窗口联测 | `after_lance` 注入后引擎有行、outbox 零 CONFIRMED、向量通道为空；重启重放同一逻辑事件成功且不产生第二条逻辑向量，之后才进入向量通道 |
| 既有不变量与隐私 | 两次查询前后 `schema_version=6`、两个 revision、`outbox`/`entries`/`documents` 行数与每个 `assets.created_at` 逐项不变；响应无本机路径、无坐标、无 `request_id`；既有测试原样通过 |

未验证项（报告 §11）：候选分块扫描的规模/延迟只在 ≤4 资产夹具上实测 1 个块（`MAX_VECTOR_BLOCKS=1000`
只是安全阀，触发时如实报 `vector_scan.truncated`）；无真实模型与人工标注，不声称召回质量；查询向量
归一化由 processor 负责；混入外来 space 的拒绝路径未构造真实注入；中查询并发变更只标注状态、不自动
补新命中；纯筛选查询给有界权威列表（≤1000 个 id，`top_k` 只约束通道）；`pending_projection_summary`
尚未接入 `/capabilities`。`catalog/errors.py` 新增了一个 extra 码常量 `FILTER_UNSUPPORTED`（本文件
外唯一改动，见报告 §11.1），暂无 HTTP 映射，P2-06A 接入 API 时补齐。

## Latest accepted verification

P1-06 的完整 D 级验收（`docs/tasks/P1-06.md` 要求的基础等级 + 6 类故障注入/恢复场景）：

| Check | Result |
|---|---|
| `uv run pytest -q tests/integration/test_catalog_identity_lifecycle.py` | exit 0, `16 passed`（本次新增用例） |
| `uv run pytest -q tests/unit tests/integration` | exit 0, `tests=337 failures=0 errors=0 skipped=1`（336 passed / 1 skipped，127.111 s） |
| `uv run ruff check .` / `git diff --check` | clean / clean |
| `uv lock --check` | exit 0, `Resolved 95 packages`（无依赖变更） |
| 场景：重复内容（同来源 3 份含中文/空格、>220 字符长路径、只读原文件 + 跨来源 1 份） | 1 个 `asset_id`、4 个位置、1 个内容寻址缩略图；删除同来源 1 份后其余 `PRESENT`、来源内 `assets` 仍为 1、产物字节/时间戳不变；删除另一来源唯一位置后资产行仍保留 |
| 场景：移动（`a.jpg` → `2024 年/照 片 一.jpg`） | 新位置 `PRESENT` + 旧位置 `MISSING`、`asset_id` 不变、位置数 +1、资产数不变、缩略图路径与 `(size, mtime_ns)` 完全不变（未重算） |
| 场景：替换（4 种） | 不同长度覆盖 → 新内容身份、旧资产仍 `ACTIVE`；覆盖成既有位置的内容 → 重绑该既有资产、不新增；同尺寸 + 还原 mtime → 仍重新 hash 识别；两位置互换 → 各自跟随内容身份；覆盖后中断 → 位置 `PRESENT` 但 `asset_id IS NULL`（待确认）、`RUNNING`、无缺失、`last_successful_scan` 不变 |
| 场景：断盘/离线（成功后拔盘 + 扫描中掉盘 + 枚举↔确认窗口） | 一律 `BLOCKED`、`locations_missing == 0`、`last_successful_scan` 不变；2 个已发布缩略图字节不变且 `media-status` 仍 `published == 2`；未生成的 preview 报 `source_missing`/`SOURCE_FILE_MISSING`；原文件仅被夹具移动、字节未变 |
| 场景：重映射（真实改名挂载点 + 单写事务更新 `root_path`） | 位置行零改写（`location_id`/`path_key`/`asset_id`/`size`/`mtime_ns`/`availability` 投影逐项不变）、重扫 0 缺失、资产数与产物不变；产品 remap 入口实测不存在（OpenAPI 14 条路径，无 `remap`） |
| 场景：重启/中断恢复 | 关闭服务 → 停机期间替换 1 个已绑定文件 + 删除 1 个未确认文件 → 新进程接管**同一** `scan_run`（`scans == 1`、`jobs == 1`）；`attempts == 1`、无重复位置、被替换位置绑定新内容、旧内容资产仍 `ACTIVE`、被删位置 `MISSING` 且 `asset_id IS NULL`；多次接管 `attempts` 0→1→2 单调 |
| 边界 | 0 文件/空子目录、1 文件、1001 文件（1001 位置 / 1001 资产 / `path_key` 全不重复 / 0 缺失）、只读原文件、中文/空格/长路径 |

Produced by DS-L on the P1-05 implementation and its P1-05B follow-up (see the P1-05 and P1-05B reports for
per-scenario evidence).

### P1-05B (front-end verification base, A level)

| Check | Result |
|---|---|
| `uv run pytest -q tests/unit/test_api_frontend.py` | exit 0, `46 passed` (42 existing + 4 new contract cases) |
| `uv run ruff check .` / `git diff --check` | clean / clean |
| `uv run python tools/diagnostics/frontend_smoke_fixture.py --photos 60 --bulk 2000 --corrupt --reset` | exit 0, 2061 synthetic files under `%TEMP%`, prints the source root, the real-port start command and the 10 expected observations |
| `--too-new` / `--offline` branches (separate `%TEMP%` dir) | verified: deletes the named original, reports the missing ones, builds a `schema_version = 2` catalog |
| Production code | untouched (`src/photo_search/**` unchanged); the only test edit is 4 added cases in `tests/unit/test_api_frontend.py` |
| New artifacts | `tools/diagnostics/frontend_smoke_fixture.py`, `docs/diagnostics/frontend-smoke-checklist.md` |

| Check | Result |
|---|---|
| `uv run pytest -q tests/unit tests/integration` | exit 0, 304 collected, `303 passed, 1 skipped` |
| focused P1-05 suites | exit 0, `50 passed` (`test_api_frontend.py` 42, `test_api_frontend_routes.py` 4, `test_api_time_order.py` 4) |
| `uv run ruff check .` / `git diff --check` | clean / clean |
| `uv lock --check` | exit 0, `Resolved 95 packages` (no dependency change) |
| P1-05 API/page scenarios 1-9 (public shell + static assets with traversal refusals, session `next` 303 + unsafe-`next` 422 without burning the credential, `time_desc` 26-asset paging with tie-break stability and cross-cursor `INVALID_CURSOR`, sources/scan flow, detail/offline with `SOURCE_FILE_MISSING`, bad-media codes, empty/single/multi-page/unicode/read-only boundaries, higher `schema_version`) | all executed on `%TEMP%` fixtures; mapped to named tests in report §5 |
| Recorded real-browser smoke (Codex in-app browser, real port, 2061-file `%TEMP%` fixture) | 10 steps observed: 303 landing, source registration without rendering absolute paths, live `RUNNING 14% → 65% → SUCCEEDED 100%`, 50 → 100 grid paging with lazy thumbnails, detail with 7 locations, `原文件不可用` badge with the thumbnail still shown, real `409 SOURCE_FILE_MISSING` from `/original`, `DECODE_FAILED` with the path withheld, narrow 640×900 viewport + Tab/Enter, and the `SCHEMA_TOO_NEW` notice (no empty list, no `-wal`/`-shm` written) |
| C-level packaged smoke (`tools/packaging/build_onedir.py` + `tools/diagnostics/packaged_api_smoke.py`) | build exit 0 (~62 s, 34,020,895 B); bundle copied to `%TEMP%\P1-05 打包页面验收\PhotoSearch 中文 空格`; `status=ok`/`problems=[]`: `GET /` 200 `text/html` `no-cache` with no external reference or local path, `/web/app.js` 200 (33,516 B) and `/web/styles.css` 200 inside the frozen bundle, unknown asset 404, printed link → 303 `Location: /`, plus the P1-04 session/scan/thumbnail/offline scenarios; startup 1.0 s |
| New finding (carried forward) | the media failure records embed a **repr-escaped** path (`C:\\Users\\…`) that `errors.Redactor` does not match, so `GET /assets/{id}` messages can still contain an absolute path; the page now withholds such messages (unit-asserted) and a C1/C2-reviewed server-side fix is recommended (report §8.1) |

P1-03 shipped contract: thumbnail `catalog-thumbnail@1` (long edge 320, JPEG q85, LANCZOS, never enlarged) and preview
`catalog-preview@1` (long edge 1600, JPEG q88, lazy). Layout
`<data_root>/{thumbnails,previews}/<processor_id>/<sha[:2]>/<sha[2:4]>/<sha>.jpg`, failure records under
`<data_root>/cache/media/...`, atomic same-directory write + fsync + `os.replace`. No schema change (still
`schema_version = 1`), no new dependency.

## Carried-forward limits / follow-ups

- **P1-06**: 对抗性验收**未发现**与 ADR-0002 冲突的真实缺陷，故无生产代码改动。三项残留边界已实测记录：
  (a) 若内容被替换但字节数不变且 `mtime` 被还原，扫描层的变化提示不会触发，此时若扫描被中断，位置会
  在**下一次完整扫描**前保留上一次观测（旧 `asset_id`/旧 size/mtime）；ADR-0002 §5.1 明确 size/mtime
  只是快速提示，且每次完整扫描都重算完整 SHA-256（`test_same_size_replacement_window_*` 已实测纠正），
  该窗口内 `last_successful_scan` 不前进、不会静默成功；若要缩短到「中断窗口内立即解除绑定」，属于改变
  缺失/身份判定边界，必须先交 C2 更新 ADR-0002。
  (b) 「断盘」用真实重命名/移走来源根目录模拟，**不是**真实外接盘/网络盘的卷级故障
  （`ERROR_NOT_READY`、USB 意外拔出、SMB 断链仍未在真实硬件上验证）。
  (c) 重映射只验证到夹具层（受控 `source_roots.root_path` 更新 + 重扫的身份/位置/产物不变）；
  `POST /sources/{id}/remap` 的**产品能力**（新根校验、来源重叠复查、映射审计、API 契约）仍缺，
  该项产品层入口标记为 `VALIDATION PENDING — 缺产品能力，不缺验收能力`。
  跨进程写者约束沿用 P1-02 记录，本任务未新增承诺。
- **P2-03B**: 真实 GeoNames/公开地名包**未使用**，只用极小合成子集验证格式、生命周期与数值分支，
  因此不声称任何真实城市覆盖/命中率；最近点解析是城市点线性扫描（无 RTree/网格索引），
  冷缓存会整包重算 SHA-256，大包规模与吞吐未测；切包/回退不自动重算既有 resolution
  （旧包产物仍作历史机器产物保留，`place_state=suggested` 只匹配活动数据集），自动刷新流程留给后续任务；
  坏包时既有建议仍可读、只有新解析阻塞；回退链只依赖包声明的 `previous_dataset_id`，工具导入不自动
  串联；数据集别名只新增不覆盖。`photo_search.geo.extract`/`geo.resolver`/`scheduler.engine`
  仍不在冻结包内（无常驻调度线程），接入后台解析的任务必须重跑 C 并复核导入图。
  C 级 smoke 会把约 3.4 GB bundle 复制到 `%TEMP%`，执行前需确认 C 盘余量（见 P2-03B 报告 §9）。
- **P1-04**: the local session lives in memory for the process lifetime (no TTL, no logout, no LAN auth); the
  bootstrap credential file's Windows DACL could not be verified here (`icacls` was denied in this sandbox, so
  the tightening stays best-effort); the API and the scan worker use two SQLite connections in one process and only
  a sequential-overlap window was stress-tested; media generation still runs on the event loop thread; `GET /stats`
  is O(assets). A C1 review of the packaging/migration-check fix is recommended; see report §6 and §8.
- No file-id move acceleration: a move is only proven by full SHA-256, so it leaves "new location PRESENT + old position
  MISSING" with the same `asset_id`. Explicitly allowed by the P1-02 card; revisit only with a sound identity argument.
- A file that disappears between enumeration and content confirmation still classifies as `FILE_READ_ERROR` → `BLOCKED`
  (conservative: never converted into a deletion conclusion). A re-run of the same `scan_run` completes it.
- Files whose `mtime` is later than the wall clock (clock skew, wrong camera date) skip the quiet-period check and rely on
  the stat pair around the hash only.
- Throughput is bounded by per-file short write transactions (2 observe/bind writes + throttled progress): ~375 files/s on
  4 KB synthetic files in this environment. Batching commits is a future tuning item that must not break ADR-0002 §8.
- Single-writer coordinator is in-process; cross-process writers use WAL + `BEGIN IMMEDIATE` + busy timeout but have not
  been stress-tested. Recovery always re-enumerates the whole source (no directory cursor), as ADR-0002 §5.3 allows.
- `assets.width/height/format` are still NULL: P1-03 delivers the orientation-normalised size and source format in the
  batch summary and the derived facts can be read from the artifact; persisting them needs a documented additive
  migration and a C2 decision (P1-03 report §2/§8).
- P1-03 refuses TIFF/RAW/HEIF/AVIF as `FORMAT_UNSUPPORTED` even though Pillow can read TIFF; multi-page, RAW and colour
  semantics are unverified, so no support may be claimed before a dedicated task verifies it.
- No colour management in the media path: ICC is not applied, CMYK/16-bit inputs are converted naively to RGB and
  transparency is composited on white.
- Media generation decodes one asset at a time on one thread and uses JPEG DCT-scaled decode (intermediate always at
  least the target long edge). A bounded decode pool per architecture §6.4 is still a P2 item.
- `catalog media-status` is an O(assets) diagnostic (one reachability probe per pending asset plus a walk of the current
  cache version); no incremental status index yet. Media batches are untested under cross-process concurrency (the
  catalog is never written by the media path; identical content-addressed artifacts rely on atomic replace).
- GTX 1660 6 GB remains **unverified on real hardware** (P4-07/P4-08 release gate). Model GPU execution is verified on the
  current RTX 4060 Laptop GPU; earlier P0 limits for InsightFace/RapidOCR, cold CUDA start and human-labelled semantic
  quality remain carried forward.
- Two historical P0 record inconsistencies remain recorded but unedited; see the P0-07 report §3.1.
- **P1-05 historical redaction gap is closed by P1-05A** (`b708847`); its earlier report remains historical evidence.
  The front end has no automated DOM tests (no Node/npm chain introduced); its behaviour
  rests on the static contract assertions consolidated by P1-05B in `tests/unit/test_api_frontend.py` plus the
  repeatable manual checklist `docs/diagnostics/frontend-smoke-checklist.md`. Narrow-viewport/200% zoom was
  proxied by a 640×900 viewport (no horizontal overflow,
  3-column reflow), not by real OS zoom. `time_desc` sorts by insertion time only (`taken_at` would need an
  ADR-0002 §7 additive migration + C2). A fully offline asset shows no relative path in the grid
  (`current_location` is null once every position is MISSING); the detail view still lists every position.

  两个历史非阻塞后续项均已完成，保留任务卡／报告供追溯（不再作为下一任务）：

  - `docs/tasks/P1-05B.md` — 前端静态契约断言集中化 + `%TEMP%` 夹具脚本 + 浏览器 smoke 核对清单
    （不引入 Node/npm），验收等级 A。**状态：PASS**，见
    [docs/reports/P1-05B-frontend-verification-base.md](reports/P1-05B-frontend-verification-base.md)。
  - `docs/tasks/P1-05A.md` — 本机路径脱敏补强（repr 转义与多拼写 + 未登记路径兜底），推荐
    **C1 / GPT-5.6 Terra / medium**，验收等级 **B**；唯一允许改动的生产文件是
    `src/photo_search/api/errors.py` 的 `Redactor`，页面守卫保留并加回归断言。**状态：PASS**；见
    [P1-05A 报告](reports/P1-05A-local-path-redaction.md)。
  - `taken_at`（EXIF 拍摄时间）排入 **P4-00A（C2 契约）→ P4-00B（DS-H 实现）**；
    通过 ADR-0002 §7 的增量迁移路径，不改变现有加入时间排序。

## GPU status

Model GPU execution is verified on this machine (RTX 4060 Laptop GPU / 8188 MiB / driver 616.56 / CUDA UMD 13.4):
Chinese-CLIP image and text towers run on `CUDAExecutionProvider` with CPU fallback disabled, the worker defaults to CUDA,
ViT-L/14 also ran fully on CUDA, and the packaged app reproduced the CUDA path from a Chinese/space path offline.

Still open for later phases: real GTX 1660 6 GB validation; bounded decode pool and thumbnail/subsample prep (P1-03);
scheduler handling of cold GPU start; explicit OCR device decision; InsightFace CPU-operator re-check; and a
human-labelled semantic-search holdout.

## Current task / next execution step

**P1-06 — 重复、移动、替换、断盘、重映射、重启验收：PASS.** 见
[P1-06 报告](reports/P1-06-identity-lifecycle-acceptance.md)。

**P2-01 — 通用处理任务、运行时与搜索配置契约：PASS.** 见
[P2-01 报告](reports/P2-01-processing-runtime-search-profile-contract.md)。

**P2-01A — 地点事实、人工控制、数据包与搜索契约：PASS.** 见
[P2-01A 报告](reports/P2-01A-place-contract.md)。

**P2-02 — 持久调度、设备资格与阻塞恢复实现：PASS.** 见
[P2-02 报告](reports/P2-02-persistent-scheduler-and-device-qualification.md)。

**P2-03 — 通用产物、outbox、幂等索引提交与修复：PASS.** 见
[P2-03 报告](reports/P2-03-artifact-outbox-index-submission.md)。

**P2-03A — 地点增量迁移、GPS 事实、目录、人工归属/撤销与纯筛选 API：PASS.** 见
[P2-03A 报告](reports/P2-03A-place-core-gps-assignment-api.md)（D 级，48 个新增用例）。

**P2-03B — 离线地名包、GPS 附近城市建议、版本/缺包/升级恢复：PASS.** 见
[P2-03B 报告](reports/P2-03B-offline-name-dataset-and-nearby-city-suggestions.md)
（D 级，65 个新增用例）。`schema_version = 5`，migration 连续 0001～0005；
依赖/`workers/**`/打包入口未改，`geo.extract`/`geo.resolver`/`scheduler.engine` 仍不在冻结包内。

**P2-03C — 静态地点页与人工整理交互：PASS.** 见
[P2-03C 报告](reports/P2-03C-place-page-and-manual-curation.md)（B 级 563 passed / 1 skipped + 真实
浏览器 smoke 10 步 + C 级打包 smoke）。`schema_version` 仍为 5，migration/ADR/依赖未改。

**P2-04 — 文件名/有效地点 FTS、受控 MATCH 与改名重投影：PASS.** 见
[P2-04 报告](reports/P2-04-fts-and-rename-reprojection.md)（B 级 598 passed / 1 skipped + D 级
投影故障注入 + C 级打包 smoke，35 个新增用例）。`schema_version` 推进到 6，migration 连续
0001～0006；ADR/冻结 JSON schema/依赖/`places/**`/`ingestion/**`/`workers/**`/打包入口未改。

**P2-05 — 图文 + FTS 并行召回、RRF 融合、地点候选预过滤与权威补查：PASS.** 见
[P2-05 报告](reports/P2-05-vector-fts-fusion-and-authoritative-topup.md)（B 级 617 passed / 1 skipped +
确定性检索对照 + P2-03 崩溃窗口联测，19 个新增用例）。`schema_version` 仍为 6（无新 migration）；
ADR/冻结 JSON schema/依赖/`places/**`/`ingestion/**`/`workers/**`/打包入口/前端未改，未接 API（按卡不触发 C）。

**P2-06A — 搜索协议、`POST /api/v1/search`、搜索会话/游标与 Profile 绑定.**

- Status: **READY / CURRENT TASK**. P2-05 的服务层检索（`search.search_assets`：双通道 + RRF +
  权威候选约束 + `pending_semantic`）已 PASS，P1-04 的 session/Host/Origin/CSRF 边界与
  P2-02 的能力/资格事实已可用。计划中的 P2-06 拆为 **P2-06A**（本卡）与 **P2-06B**
  （显式相关性反馈与精排适配器接入），不改变总体路线。
- Task card: `docs/tasks/P2-06A.md`.
- Recommended execution model: **DS-H / DeepSeek V4.1 Flash / 高推理**（协议/绑定歧义再报 C2）。
- Goal: `SearchRequest`/`SearchResult` 协议、`POST /api/v1/search`、进程内搜索会话与游标
  （≤1000 有序 ID、默认 10 分钟、绑定 profile/generation/`catalog_filter_revision`）、
  `search_profiles`/`active_search_profile` 迁移与原子激活；**不得提前实施搜索页面（P2-07）**。
- Validation level: **B + C（接入 API/打包）+ D（迁移与原子激活故障注入）**。

**P2-06B — 显式相关性反馈与精排适配器接入（依赖 P2-06A）.** Task card: `docs/tasks/P2-06B.md`。

P1-05A 已关闭脱敏缺口；P1-04 打包/迁移检查的建议复核保留到发行检查。P1-06 §5 product remap
排入 P4-05A/B，拍摄时间排入 P4-00A/B，HEIC 排入 P4-00C；均不是 P2-03C 的前置依赖。
P2-02 未验证项（无常驻调度线程、真实模型设备对接、跨进程并发、evidence 撤销、真实端口 smoke）
记录在 P2-02 报告 §10；P2-03A 未验证项（`GET /assets` 页面非快照隔离读、改名未接 undo）
记录在 P2-03A 报告 §9/§10；P2-03B 未验证项（真实 GeoNames 包未使用、最近点线性扫描、
冷缓存整包重校验、切包不自动重算、坏包时旧建议仍可读、`geo.resolver` 尚未进入冻结包）
记录在 P2-03B 报告 §10；P2-03C 未验证项（内置浏览器 `viewport` 覆盖未生效致 640×900 像素级证据
只到代理、批量接口无 `origin` 致批量确认记 `MANUAL`、`PLACE_OPERATION_NOT_UNDOABLE` 的「目标被后续
编辑」分支页面上不可达、页面不触发后台解析、无 DOM 自动化、非快照隔离读）记录在 P2-03C 报告 §9；
P2-04 未验证项（单次请求最多追赶 2000 资产的滞后、`rebuild_all` 未接 CLI/调度、
`filename_text` 只取 PRESENT 位置、多 PRESENT 名称的短语跨界、停用词固定小表、`search` 为加性字段、
候选链路直接写行、前端未接 `text=`、非快照隔离读）记录在 P2-04 报告 §10；P2-05 未验证项（候选分块
扫描的规模/延迟未测、`MAX_VECTOR_BLOCKS` 安全阀未在大库触发、查询向量归一化由 processor 负责、
混入外来 space 的拒绝路径未真实注入、中查询并发变更只标注不补命中、纯筛选查询的权威列表上界 1000、
`pending_projection_summary` 未接 `/capabilities`、`FILTER_UNSUPPORTED` 暂无 HTTP 映射）记录在
P2-05 报告 §11。

Model switching remains manual; the repository only records the recommended model per stage.
