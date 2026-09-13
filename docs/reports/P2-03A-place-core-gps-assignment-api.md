# P2-03A — 地点增量迁移、GPS 事实、目录、人工归属/撤销与纯筛选 API 报告

日期：2026-09-13。状态：**PASS（D 级：完整 unit/integration + 具名故障注入/恢复）**。

契约由 ADR-0005 与 `contracts/p2-place-contracts.v1.json` 冻结；本任务只实现，不重新定义状态、
错误码或事务语义。所有数据库、图片与夹具都在 pytest / `%TEMP%` 目录内，没有读取、移动或修改真实
图库，没有网络下载，也没有引入依赖。

## 1. 基线与交付边界

| 项 | 实际值 |
|---|---|
| 起始 HEAD | `b9b531291214355748493e650426c59d7e9cb765`（P2-03），开始时工作树 clean |
| 前置 migration | 0001/0002/0003，`SUPPORTED_SCHEMA_VERSION = 3` |
| 新 migration | `catalog/migrations/0004_place_core.sql`；schema_version 3 → 4 |
| 新生产包 | `src/photo_search/places/**`、`src/photo_search/geo/**` |
| 调度接入 | `Scheduler.submit` 在既有事务内把 structured GeoFact 落成当前事实 |
| API | `/api/v1` 新增 10 条地点路径（OpenAPI 14 → 26 条），`GET /assets` 增加地点筛选参数 |

未修改：ADR-0001～0005 与冻结 JSON schema、0001/0002/0003、五字段 job 身份、lease/attempts/迟到
提交、资产/位置/缺失判定、`pyproject.toml`、`uv.lock`、`workers/**`、打包入口、模型 manifest。
没有实现地名包、PlaceResolution/候选、`geo_datasets`、`place_resolutions`、地点页面、FTS、
语义融合或 SearchProfile 切换。

## 2. 0004 地点核心 migration

新增 12 张表与派生索引：`places`（稳定身份、父级 `ON DELETE RESTRICT`、`parent != self`、
country 无父）、`place_aliases`（同地点规范化活动别名唯一、软退休）、`place_external_refs`
（`(provider, external_id)` 唯一）、`asset_geo_facts`（每资产一条当前事实）、
`asset_place_assignments`（每资产一条控制行 + 模式组合 CHECK）、
`asset_place_assignment_history`（只追加）、`asset_place_exclusions`、
`place_operations`/`place_operation_items`、`catalog_filter_revision`（+ events）、
`place_suggestion_revision`。

关键设计点与理由：

- `places.normalized_name` 是**派生比较键**，与 ADR-0002 §4 的 `file_locations.path_key` 同构：
  显示拼写保留用户输入（NFC），比较与别名唯一性使用 NFKC+casefold 形式。
- `asset_geo_facts.status` 的枚举**不含 `SOURCE_UNAVAILABLE`**：离线/读中变化只能是一次阻塞诊断，
  永远不能成为“当前事实”（ADR-0005 §3），因此由 DDL 直接禁止而不是靠约定。
- `asset_place_assignments`/`history` 的 CHECK 固定三种模式组合：只有
  `MANUAL_ASSIGNED` 可以带 `place_id`，`AUTO_ALLOWED`/`MANUAL_UNASSIGNED` 必须
  `place_id IS NULL` 且 `manual_lock` 符合模式；`actor` 只能是 `LOCAL_USER`。
- `place_operation_items.exclusion_deltas_json` 记录该 item 的排除变化数组
  （`{place_id,before,after}`）。一次 `assign A→B` 可能同时“排除 A”和“解除 B 的排除”，
  单一 place 列无法精确补偿。
- 触发器：`places` 禁止物理删除；历史/操作/操作项禁止删除；历史行**只允许** ADR 明确的
  `record_state ACTIVE → SUPERSEDED` 迁移，其余列必须逐项不变。
- 单例 `catalog_filter_revision`/`place_suggestion_revision` 初始化为 0；本任务不递增
  suggestion revision，也不创建任何假 ACTIVE 地名包。

### 迁移验收（`tests/integration/test_p2_place_migration.py`）

| 场景 | 实测结果 |
|---|---|
| v3→v4 正常升级 | 备份先生成（备份内 `schema_version=3`、无任何地点表）；单事务推进到 4；`applied=(4,)`；目标 schema 复核通过 |
| 旧行不变 | assets/jobs（含 RUNNING lease 三元组）/file_locations/outbox 快照逐列逐行不变 |
| 重复迁移 | `applied=()`、无备份、`previous_version=4` |
| 注入损坏 0004 | `MIGRATION_FAILED`；版本仍 3；`places_half`/探测键/地点表全部未出现；快照不变 |
| 缺目标对象 | 删 `place_operation_items` 后 `SCHEMA_INCONSISTENT` 且 `missing_objects` 指名该表 |
| 过新库 | 版本 5 → `SCHEMA_TOO_NEW`（`migrate_database` 与 `Catalog.open` 两条路径），主库字节与目录列表不变 |
| 身份不可物理删除 | `DELETE FROM places` 被触发器拒绝，行数不变 |

## 3. GPS 事实（`geo/exif.py` + `geo/extract.py`）

- 版本化 `non_model + CPU_PREFERRED` processor：`geo-exif-gps@1` / `geo_extract` /
  `geo-fact.v1`，envelope 中没有 `model_id`/`weights`/`dimension`/`metric`/`space_id`。
- 输入边界复用 P1-04 已验收的受控位置解析（`api.files.resolve_present_location`）：只经由已登记
  `file_locations` 行读取，含 path_key 合法性、根内包含、reparse point/realpath 与
  `(size,mtime_ns)` 一致四层检查。随后再流式 SHA-256 并与 `assets.sha256`（job input revision）
  比对，最后复核一次 stat，因此“读中变化”不可能提交旧事实。
- 状态分支：`VALID`（有限、范围 [-90,90]/[-180,180]，`(0,0)` 合法）、`ABSENT`（无 GPS 块，成功空结果）、
  `INVALID`（非法分母、非有限、负分量、越界、方向错误）、`UNSUPPORTED_CRS`（未知 datum 保留原始
  证据但不产生内部可检索坐标）。datum 明确 WGS84 → `WGS84`；未声明 → `ASSUMED_WGS84`（只给建议）；
  未知 → `UNSUPPORTED`。
- 读取失败/离线/权限/读中变化一律 **`BLOCKED` + `blocking_reason=SOURCE_UNAVAILABLE`**，不写事实、
  不当作 ABSENT，也不覆盖上一条事实。
- structured artifact 是**确定性**文档（键集合被 `frozenset` 严格校验），因此同内容重放不会触发
  `ARTIFACT_CONFLICT`；时间戳只存在于 SQLite 行，避免内容寻址产物抖动。
- 精确坐标只写入受保护 catalog/受保护 artifact，并由会话保护的 `GET /assets/{id}/geo` 返回；
  日志、错误、`/stats`、`/capabilities`、outbox、LanceDB 都不含坐标（有断言）。

## 4. 地点目录与人工控制

- 四类 `country|city|attraction|custom`；父级类型矩阵（country 无父；city⊂country；
  attraction⊂country/city；custom 可挂任意 ACTIVE 地点）、ACTIVE 父级、自环/祖先环、深度上限
  都在同一短事务内校验；同名可并存且返回完整祖先链与稳定 ID。
- `PATCH` 只开放改名/别名（活动别名为集合语义，被移除的别名软退休）；`parent_place_id` 是显式拒绝
  字段，避免“未知字段”式的模糊校验错误。
- 退休为软删除，存在 ACTIVE 子节点、有效归属或有效排除时返回 `PLACE_REFERENCED`。
- 人工控制三种模式；`assign A→B` 激活 A 的排除，显式 `assign B` 只解除 B 的排除并返回解除清单；
  `unassign` 排除原有效地点并进入 `MANUAL_UNASSIGNED`；`restore_auto` 保留全部排除且不采用任何建议。
- 每 asset 一条当前控制行 + 只追加历史；没有任何自动路径能写这张表（DDL CHECK 亦有反例）。
- 幂等：`request_id` 绑定 `payload_sha256`；同请求同载荷返回原 operation/结果且不再递增 revision，
  不同载荷 `PLACE_IDEMPOTENCY_CONFLICT`。
- 批量：1～500 去重 asset；0/重复 → `VALIDATION_ERROR`（422），>500 → `PLACE_BATCH_TOO_LARGE`；
  任一项 revision 冲突或目标非法则整批零写。
- 撤销：新补偿 operation，要求原操作 `COMMITTED` 且每个目标仍等于记录的 after revision/state；
  成功恢复 before 语义并递增 revision，冲突整批 `PLACE_OPERATION_NOT_UNDOABLE`，历史不删。
  before 为“无控制行”时写入等价的 `AUTO_ALLOWED`（revision 单调、历史只追加，语义等价）。

## 5. CatalogFilterRevision 与投影刷新

`catalog_filter_revision` 单例成为权威值；`artifacts/store.py` 的读取改为先读单例、再回退旧的
`metadata` 键（兼容尚未升级的库）。触发矩阵实测：

| 动作 | 实测 |
|---|---|
| assign/批量/undo 的有效状态变化 | 同事务 +1，写 `catalog_filter_revision_events`，并调用 P2-03 `schedule_projection_refresh` 为该资产的**已确认**向量排入 outbox 事件 |
| 同名载荷 replay / 实际 no-op | revision 与事件数都不变 |
| 无确认向量的资产 | revision 递增但不产生 outbox 事件（首索引会读当时 SQLite） |
| 改名/别名/生命周期变化 | +1 `PLACE_NAME_CHANGED`/`PLACE_HIERARCHY_CHANGED`，受影响资产=该地点及其后代的有效归属集 |
| GeoFact 新增/刷新 | **不变**（有 geo 断言） |
| suggestion | 不递增（本任务不写候选） |

事务注入失败（`bump_filter_revision` 抛错）后 assignment/history/items/operation/event/outbox
全部回滚、revision 保持 0，证明权威写入与投影刷新同事务。

## 6. API

新增路径（全部沿用 loopback Host、内存 session、Origin、double-submit CSRF、统一错误信封与
Redactor）：

```text
GET    /api/v1/places                     POST  /api/v1/places
PATCH  /api/v1/places/{place_id}          GET   /api/v1/places/coverage
GET    /api/v1/places/datasets            GET   /api/v1/assets/{asset_id}/geo
GET    /api/v1/assets/{asset_id}/place    PUT   /api/v1/assets/{asset_id}/place-assignment
POST   /api/v1/place-assignments/batch    GET   /api/v1/place-operations
POST   /api/v1/place-operations/{operation_id}/undo
GET    /api/v1/assets?...place_ids/include_descendants/place_state
```

- `/places` keyset 分页游标绑定目录 revision，过期返回 `CATALOG_FILTER_REVISION_STALE`；
  支持 name/alias、kind、parent、ancestor、lifecycle。
- `/places/datasets` 只返回显式 `state="unavailable"` 的只读投影（P2-03B 才建包），不伪造 ACTIVE 包。
- 冻结错误码全部映射到 ADR-0005 §9 的 HTTP 状态，且 `details` 只使用冻结允许的键
  （`asset_ids/place_id/operation_id/dataset_id/expected_revision/current_revision/max_batch_size`）；
  校验类细节走 `VALIDATION_ERROR`，不污染地点错误详情形状。
- `PlaceFilter`：`place_ids` 内 OR 且默认展开后代、与 source/availability 等条件 AND；
  `place_state` 的 five 值语义分别实测；给定 `place_ids` 时只允许 `any/assigned`（否则
  `INVALID_PLACE_FILTER`）；`suggested` 在无解析器时确定为空集（绝不伪装成 assigned）。
- `/geo` 是唯一返回精确坐标的会话保护入口（无 session → 401）；无事实 → 404 `PLACE_NOT_FOUND`；
  一般资产响应只给 `gps_state` 粗粒度状态。

## 7. D 级场景映射（任务卡 §D 级验收 1–10）

| # | 实测闭环 | 主要测试 |
|---|---|---|
| 1 | v3→v4 备份/中断回滚/缺件/过新/旧行不变/触发器保护 | `test_p2_place_migration.py`（5 例） |
| 2 | 无字段/(0,0)/南西半球/合法/ASSUMED/非法分母/NaN/Inf/越界/未知 datum；离线非 ABSENT；读中变化与迟到 lease 零事实副作用 | `test_p2_geo_exif.py`、`test_p2_geo_extract.py` |
| 3 | 四类、同名不同层级、深层祖先、名称/别名搜索、类型矩阵、自环/祖先环、退休引用保护、PATCH 不改父级且 ID 稳定 | `test_p2_place_directory.py`、`test_p2_place_api.py` |
| 4 | assign→unassign→重启→restore_auto→再 assign；锁/origin/排除/revision 逐项正确；无自动写路径 | `test_p2_place_directory.py` |
| 5 | 幂等 replay 与载荷冲突；0/重复/500/501、逐项 revision 冲突整批零写 | `test_p2_place_directory.py`、`test_p2_place_api.py` |
| 6 | undo 补偿成功且 revision 增大；目标被后续编辑时整批拒绝且历史不删 | 同上 |
| 7 | 触发矩阵、同事务 outbox 投影刷新、注入失败全回滚 | `test_p2_place_directory.py` |
| 8 | 后代/同名/无地点/manual_unassigned/重复位置去重；suggested 不伪装 assigned；与 source AND | 同上 + `test_p2_place_api.py` |
| 9 | session/Host/Origin/CSRF、错误码、分页 revision、字段脱敏；坐标只在 `/geo`，日志/错误/诊断/outbox/Lance 无坐标 | `test_p2_place_api.py`、`test_p2_geo_extract.py` |
| 10 | GPU 缺失只阻塞 GPU 任务、CPU GPS 与人工写继续可用；`artifacts=[]` 与 structured 结果都正常 | `test_p2_geo_extract.py` |

## 8. 验收命令与结果

| 命令 | 结果 |
|---|---|
| `uv run --no-sync pytest -q -p no:cacheprovider tests/unit tests/integration` | exit 0；`--collect-only` 计 481 个用例；进度行出现 1 个 `s`，即 480 passed / 1 skipped（沿用 P2-03 的既有 skip） |
| 本任务专项 8 个文件（5 个新增 + 3 个版本耦合） | exit 0，72 passed |
| 新增 5 个文件 | `test_p2_place_directory.py` 14、`test_p2_geo_exif.py` 12、`test_p2_geo_extract.py` 12、`test_p2_place_api.py` 5、`test_p2_place_migration.py` 5（共 48 例） |
| `uv --cache-dir <unique-%TEMP%> run --no-sync ruff check .` | exit 0，`All checks passed!` |
| `git diff --check` | exit 0，无输出 |
| `uv --cache-dir <unique-%TEMP%> lock --check` | exit 0，`Resolved 95 packages`（依赖未变） |
| 版本耦合测试更新 | `test_catalog_migrations.py`、`test_p2_index_repair.py`、`test_p2_scheduler_recovery.py`、`test_api_sources_assets.py`、`test_catalog_scan.py` 的 3→4 期望值与新增地点表集合 |

本环境的两个已记录工具事实（P2-00/P2-01A 已出现的同类现象）：

1. `uv` 默认缓存在沙箱内 `%LOCALAPPDATA%\uv\cache\sdists-v9\.git` 返回 access denied，改用唯一
   `%TEMP%` uv cache。
2. pytest 在沙箱内创建 `--basetemp` 后其 ACL 拒绝 `iterdir`（`PermissionError`），因此测试在沙箱外
   运行；同环境下 pytest 的**终端摘要行**不落盘，用例数改用 `--collect-only` 统计，进度行的 `s`
   作为跳过标记，退出码作为成败依据（不据猜测填写 passed 数）。

## 9. C 级判定（实际执行了额外打包验证）

任务卡把 C 级触发条件限定为修改 `workers/**`、打包入口、依赖或**新增** package-data 目录；本任务
修改的是既有 `catalog/migrations` package-data 目录（与 0002/0003 一致），没有触碰前三者，因此
**按卡不触发 C**。为降低“新子包未被打进冻结包”的风险，仍额外运行了真实打包与 smoke：

| 检查 | 结果 |
|---|---|
| `uv run --no-sync python tools/packaging/build_onedir.py` | exit 0（约 61 s），产物 `dist/PhotoSearch`（已 gitignore） |
| `pyi-archive_viewer -r -l` 审计 | `photo_search.places.*`（7 个模块）与 `photo_search.indexing.store` 已打包；4 个 migration `.sql`（含 0004）在 bundle 内 |
| `packaged_api_smoke.py --bundle dist/PhotoSearch --work-dir <unique %TEMP%>` | exit 0，`status=ok`、`problems=[]`、startup 1.8 s；中文/空格安装路径、注册来源、扫描、缩略图、静态前端与无 session 401 全部通过 |

审计的**明确发现**：`photo_search.geo.*` 与 `photo_search.scheduler.engine` 都**不在** bundle 内。
原因是本应用目前只 import `photo_search.scheduler.state`/`projection`，尚未把调度器接入进程
（P2-02 报告 §10 已记录“无常驻调度线程”）。因此冻结包中地点目录/归属 API 可用，而 GPS 执行器要等
“把 scheduler 接进应用”的那次任务才会进入 bundle，**那次任务必须重跑 C 级 smoke** 并复核导入图。
本次没有为了让模块入包而扩大任务范围。

第一次 smoke 以固定 work dir 复跑时返回 `SOURCE_OVERLAP`（上一轮 P1-05 夹具遗留的
`PhotoSearchData 数据 目录` 仍注册着同名来源），换用唯一 `--work-dir` 后 `problems=[]`；该现象是
工具夹具复用问题，不是本任务回归。

## 10. 未验证项与残留边界

1. **无常驻调度/执行线程**：`gps_executor` 目前只用确定性调用与真实 JPEG 验证；没有 worker
   进程、没有真实设备资格证据、没有跨进程并发写压力（沿用 P2-02 §10）。
2. **`GET /assets` 页面不是快照隔离读**：响应回传 `catalog_filter_revision` 供调用方判断漂移，
   但该页由多条 SELECT 组成，理论上可与并发写交错。绑定 profile/generation/revision 的搜索会话与
   `CATALOG_FILTER_REVISION_STALE` 属于 P2-06；`GET /places` 的目录游标已做 revision 校验。
3. **无地名包/解析器**：`place_resolutions`/候选/`geo_datasets` 不存在；`place_state=suggested`
   必然为空集，`/places/datasets` 明确 `unavailable`，`coverage.place_resolved=0`。
4. **GPS 覆盖范围未证实**：只用合成/生成的 JPEG 验证标签解析；真实手机的 `GPSHPositioningError`
   单位、`GPSAltitudeRef` 缺省、HEIC（P4-00C）与汉明/边界语义没有实测。未知 datum 一律
   `UNSUPPORTED_CRS`，不做 GCJ-02 转换。
5. **重命名/删除未接 undo**：operation 日志只覆盖归属操作（ASSIGN/UNASSIGN/RESTORE_AUTO 与 undo）；
   改名/别名是短事务 + revision + 投影刷新，但没有补偿入口（ADR-0005 §6.4 的通用补偿语义留给
   后续任务与 P2-03C 的“最近操作撤销”范围确认）。
6. **无自动归属**：没有任何代码路径把机器结果写进 `asset_place_assignments`；未来解析器必须以
   `SUGGESTION_ONLY` 交付，并继续保持人工锁优先。
7. **`geo/**` 未进冻结包**（见 §9），且 `places.service` 每个请求新建一个 `PlaceService`（含
   `IndexCoordinator` 构造，无磁盘 I/O）；这是可接受的当前实现，接调度时一并复核。
8. 共享的受控读取边界仍在 `api/files.py`。任务卡允许对 `ingestion/**` 做“复用或最小抽取”，本卡
   选择**复用**（`geo.extract` 调用既有实现并只做错误类型转换），因此 `api/files.py` 保持字节不变；
   把该边界下沉到 `ingestion/**` 是后续任务的整理项。

## 11. 结论与交接

P2-03A 的“0004 地点核心 + GPS 事实 + 目录 + 人工归属/撤销 + 纯筛选 API”闭环已按 D 级真实验收，
因此标记 PASS。`schema_version` 由 3 推进到 4；0001/0002/0003、冻结 ADR/JSON schema 与依赖未动。

下一任务是 **P2-03B — 离线地名包、GPS 附近城市建议、版本/缺包/升级恢复**，推荐
**DS-H / DeepSeek V4.1 Flash / 高推理**，最低 D 级（依赖或发行资源变化再追加 C）。
任务卡见 [docs/tasks/P2-03B.md](../tasks/P2-03B.md)。
