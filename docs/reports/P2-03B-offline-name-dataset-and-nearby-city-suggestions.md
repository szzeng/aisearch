# P2-03B — 离线地名包、GPS 附近城市建议、版本/缺包/升级恢复报告

日期：2026-09-14。状态：**PASS（D 级：完整 unit/integration + 具名故障注入/恢复）**。

契约由 ADR-0005 与 `contracts/p2-place-contracts.v1.json` 冻结；本任务只实现，不重新定义
状态、错误码或事务语义。所有数据库、图片、地名包与夹具都在 pytest / `%TEMP%` 目录内，没有读取、
移动或修改真实图库，没有联网下载，也没有引入依赖。

## 1. 基线与交付边界

| 项 | 实际值 |
|---|---|
| 起始 HEAD | `d7229bef62570a6bf740363b6182c63ab0f57378`（P2-03A），开始时工作树 clean |
| 前置 migration | 0001/0002/0003/0004，`SUPPORTED_SCHEMA_VERSION = 4` |
| 新 migration | `catalog/migrations/0005_place_dataset_resolution.sql`；schema_version 4 → 5 |
| 新生产模块 | `geo/dataset.py`（manifest/包校验/构建/距离）、`geo/resolver.py`（解析执行器 + 持久化钩子）、`places/datasets.py`（注册表/生命周期/地点物化）、`places/suggestions.py`（可见候选与 suggestion revision） |
| 调度接入 | `Scheduler.submit` 在既有事务内把合格的 `place_resolve` 结果落成 `place_resolutions` + 候选 |
| 新工具 | `tools/places/manage_dataset.py`（build/import/validate/activate/rollback/status/cleanup）+ `tools/places/examples/**` |
| API | `/places/datasets` 由占位投影变为真实包状态；`/assets` 新增 `suggestion_revision`；`/assets/{id}/place` 返回真实建议状态与候选摘要 |
| 新增测试 | 6 个文件、**65 个用例**（见 §8） |

未修改：ADR-0001～0005 与冻结 JSON schema、0001/0002/0003/0004、五字段 job 身份、lease/attempts/迟到
提交、资产/位置/缺失判定、P2-03A 的人工锁/排除/幂等/批量/撤销语义与 `catalog_filter_revision`
触发矩阵、`pyproject.toml`、`uv.lock`、`workers/**`、打包入口、模型 manifest。没有实现地点页面
（P2-03C）、FTS（P2-04）、语义融合/search session（P2-05/06）、SearchProfile 切换、地图/边界/景点
自动识别。

## 2. 0005 数据包与解析 migration

新增 5 张表与派生索引：`geo_datasets`、`geo_dataset_files`、`active_geo_dataset`（单例，允许
`dataset_id IS NULL` = 未安装任何包）、`place_resolutions`、`place_resolution_candidates`。

关键设计点与理由：

- **包内容不可变**：`trg_geo_datasets_content_immutable` 逐列比对 24 个内容列（含
  `previous_dataset_id` 的 `IS` 比较），只有 `state`/时间戳可动；`geo_dataset_files` 是内容清单的
  从表，由清理路径显式删除。
- **生命周期状态机下沉**：`trg_geo_datasets_state_transition` 只允许
  `STAGED→VALIDATED`、`VALIDATED→{VALIDATED,ACTIVE,RETAINED}`、`ACTIVE→{ACTIVE,RETAINED}`、
  `RETAINED→{RETAINED,ACTIVE}`；任何回到 `STAGED` 的写入被拒绝。
  `trg_geo_datasets_validated_at_required` 保证 `VALIDATED` 必有 `validated_at`、`ACTIVE` 还必有
  `activated_at`。
- **逻辑输入唯一**：`uq_place_resolutions_input` =
  `(asset_id, geo_fact_id, processor_id, dataset_id, config_hash)`，因此重放是“同一个计算”的**证明**，
  不会产生第二份互相竞争的机器产物。
- **机器产物只增不改**：`place_resolutions`/`place_resolution_candidates` 的 UPDATE/DELETE 都被
  trigger 拒绝（`trg_place_resolutions_no_{update,delete}`、
  `trg_place_resolution_candidates_no_{update,delete}`）。
- **候选语义由 DDL 固定**：`kind='city'`、`match_basis='NEARBY_CITY_POINT'`、
  `confidence_basis='DISTANCE_ONLY'`、`disposition='SUGGESTION_ONLY'` 都是 CHECK，写入一个“自动命中”
  候选在数据库层就不可能。
- **相对路径**：`relative_dir`/`relative_path` 禁止 `..`；Python 校验器比冻结 schema 的“段级”规则
  更严格（拒绝任意 `..` 子串），因此永远不会接受 schema 本身会拒绝的路径。

### 迁移验收（`tests/integration/test_p2_dataset_migration.py`）

| 场景 | 实测结果 |
|---|---|
| v4→v5 正常升级 | 备份先生成（备份内 `schema_version=4` 且无 5 张新表）；单事务推进到 5；`applied=(5,)`；目标 schema 复核通过 |
| 旧行不变 | assets/places/assignments/两个 revision 单例快照逐行逐列不变；主库字节发生变化（schema 本身变了） |
| 新库初始态 | `active_geo_dataset` 单行、`dataset_id IS NULL`、`revision=0`；`geo_datasets`/`place_resolutions` 均为 0 行（不伪造 ACTIVE 包） |
| 重复迁移 | `applied=()`、无备份、`previous_version=5` |
| 注入损坏 0005 | `MIGRATION_FAILED`；版本仍 4；`geo_datasets_half`/探测键/5 张新表全部未出现；旧行快照不变 |
| 缺目标对象 | 删 `place_resolution_candidates` 后 `SCHEMA_INCONSISTENT` 且 `missing_objects` 指名该表 |
| 过新库 | 版本 6 → `SCHEMA_TOO_NEW`（`migrate_database` 与 `Catalog.open` 两条路径），字节与目录列表不变 |
| 非法写入 | 内容列 UPDATE、`STAGED→ACTIVE`、缺 `validated_at` 的 `VALIDATED`、resolution 删除/改写、`disposition='AUTO_ASSIGN'` 全部被拒绝 |

跨版本耦合测试同步更新为**版本相对**（不再硬编码 4/5）：`tests/unit/test_catalog_migrations.py`
（`CORE_TABLES` 增加 5 张表，断言改用 `SUPPORTED_SCHEMA_VERSION`）、
`tests/integration/test_p2_place_migration.py`（v3→当前版本的连续升级）、
`tests/integration/test_p2_scheduler_recovery.py`（v1→当前版本、`V5_TABLES`）、
`tests/integration/test_p2_index_repair.py`（保留中断 0003 场景，但按真实脚本集合构造目录）、
`tests/integration/test_api_sources_assets.py` 与 `tests/integration/test_catalog_scan.py`
（capabilities / `catalog init` 的版本期望改为相对值）。

## 3. 离线地名包：manifest、导入、激活、回退

包格式（ADR-0005 §8 的机器可读伴随物）：

- `manifest.json`（`photo-search-place-dataset-manifest.v1`）：package/dataset 身份、来源与许可、
  builder 与生成参数、`content_sha256`、坐标系（只允许 `WGS84`）、覆盖、兼容 catalog schema 区间、
  `files[]`（相对路径/角色/media type/size/sha256）、状态与三个生命周期时间。
- 内容文件：`places.jsonl`（`PLACES`）、`aliases.jsonl`（`ALIASES`）、`hierarchy.jsonl`
  （`HIERARCHY`）、`LICENSE.txt`（`LICENSE`）、可选 `index.json`（`INDEX`）。

`content_sha256` 是**文件清单**（按 `relative_path` 排序的 role/sha256/byte_size 的 canonical JSON）
的 SHA-256；manifest 自身不参与，因此“声明的内容”与“实际文件”必须逐项一致。builder 还会根据
`build.parameters` 重算并覆盖 `build.parameters_sha256`，validate 侧再复核，手写 spec 无法声明与
参数不一致的摘要。

导入与生命周期（`places/datasets.py`）：

1. `stage_package`：在**数据根外**读取并完整校验源包 → 校验目标 `dataset_id` 未占用 → 逐文件
   复制进 `<data_root>/places/datasets/<dataset_id>/`（每个文件 flush+fsync，最后写 manifest 并
   fsync 目录）→ 在同一事务写 `geo_datasets`(STAGED) + `geo_dataset_files`。
2. `validate_dataset`：对**已存储**的包重新校验 → `VALIDATED` + `validated_at`。
3. `activate_dataset`：重新校验 → 一个事务内把旧 ACTIVE 置 RETAINED、目标置 ACTIVE、
   更新单例指针、物化数据集地点；失败整体回滚（旧指针继续有效）。
4. `rollback_dataset`：目标 = 活动包的 `previous_dataset_id`（若仍为 VALIDATED/RETAINED），
   否则最近一次 VALIDATED/RETAINED 包；同样重新校验后激活。
5. `cleanup_dataset`：被活动指针、既有 `place_resolutions`、或仍作为其它包的
   `previous_dataset_id` 引用时返回 `PLACE_REFERENCED`；否则删 file 行 + dataset 行 + 目录
   （目录必须是 `<data_root>/places/datasets` 的**直接子目录**，越界一律拒绝）。

**地点物化**（`materialize_dataset_places`）：按 ADR-0005 §2 用 `(provider, dataset_place_id)`
外部引用映射到本地 `places`；已存在的映射**只复用** `place_id`，永不覆盖用户维护的规范名、别名或
父级；本包新引入的 country/city 才创建，并补齐层级与 DATASET 别名（同地点已存在同规范化别名时跳过）。
激活/回退**不递增** `catalog_filter_revision`（§11：数据包操作只影响建议侧）。

## 4. 附近城市建议（suggestion-only）

`geo/resolver.py` 是版本化 `non_model + CPU_PREFERRED` 执行器：`geo-nearby-city@1` /
`place_resolve` / `place-resolution.v1`，envelope 中没有 model/weights/dimension/metric/space。

- 只消费 P2-03A 的**当前 GeoFact**：`VALID`（含 `ASSUMED_WGS84`）→ 距离 ≤ `max_distance_m`
  的城市点按 `(distance, dataset_place_id)` 排序取前 `max_candidates`（默认 3，上限 10）；
  其它状态（`ABSENT`/`INVALID`/`UNSUPPORTED_CRS`）→ 成功空结果 `NO_CANDIDATE`。
- 无活动包 → `PLACE_DATASET_MISSING`；包缺件/篡改/不兼容 → `PLACE_DATASET_MISSING`/
  `PLACE_DATASET_INVALID`；无当前 GeoFact → `GEO_FACT_MISSING`（新增的诚实阻塞码，不伪装成空结果）；
  这三种都让 job `BLOCKED` 且零写入，人工目录/归属/筛选继续可用。
- 机器产物按 ADR-0005 §4 以**确定性 structured artifact**（`place-resolution.v1`）承载，
  `persist_resolution` 在 lease/input revision 资格通过后于同一事务写入
  `place_resolutions` + `place_resolution_candidates`，并解析 `local_place_id`。artifact 载荷刻意
  不含 `suggestion_revision`/`control_revision_seen`（它们是发布时读到的目录快照，不是内容），
  因此同一逻辑输入的重放产生**字节相同**的产物，不会误报 `ARTIFACT_CONFLICT`。
- 解析器**从不写** `asset_place_assignments`；晚到结果也只在资格通过后保存机器产物，人工
  `MANUAL_ASSIGNED`/`MANUAL_UNASSIGNED` 不被覆盖（有具名用例）。

### suggestion revision 与可见集合

可见集合 = 当前活动数据集的**最新** resolution 的候选，去掉该资产已激活的排除项；资产带有效
`MANUAL_ASSIGNED` 时为空。`visible_signature` 只包含可见候选本身，因此“无解析”“空结果”
“全部被锁/排除”都是空集合——发布一个空结果不会无意义地推进 revision。

触发实测：

| 动作 | suggestion revision | catalog_filter_revision |
|---|---|---|
| 首次发布有候选的解析 | +1 | 不变 |
| 重放同一逻辑输入 | 不变 | 不变 |
| 空结果（无 GPS/海域/非法/未知 CRS） | 不变 | 不变 |
| 人工 assign / unassign / undo 改变可见集合 | +1 | +1 |
| 人工操作但可见集合不变（例如解析前已加锁） | 不变 | +1 |
| 激活/回退数据包（且存在 resolution） | +1（可见集合必然换绑） | 不变 |

## 5. API

沿用 loopback Host、内存 session、Origin、double-submit CSRF、统一错误信封与 Redactor。

| 接口 | 变化 |
|---|---|
| `GET /places/datasets` | 真实投影：`state ∈ unavailable\|installed\|active\|missing\|invalid`、`reason`、`resolver_available`、`active_dataset`、`datasets[]`（含 `coverage`/`compatible_catalog_schema`/`problems`）与 `suggestion_revision`；无包时绝不伪造 ACTIVE |
| `GET /assets/{id}/place` | `suggested` 变为真实对象：`state ∈ unavailable\|no_resolution\|candidate(s)\|no_candidate`、`resolution_id`、`resolution_status`、`dataset_id/version`、`processor_id`、`control_revision_seen`、`datum_status`、`assumed_datum`、`requires_confirmation`、`visible`、`excluded_candidate_count` 与候选摘要（冻结候选字段 + `local_place_id`） |
| `GET /assets?...place_state=suggested` | 绑定 suggestion revision：请求携带过期 `suggestion_revision` → 409 `PLACE_SUGGESTION_REVISION_STALE`（`details.current_revision`）；响应回传实际使用的 revision。谓词只在**活动数据集**的 `CANDIDATES` 解析且存在未被排除候选时命中 |
| `PUT /assets/{id}/place-assignment` | 新增可选 `origin="CONFIRMED_SUGGESTION"`（仅 ASSIGN 允许，其它取值 422）；人工锁与排除语义不变 |
| `GET /places/coverage` | `resolver_available` 反映真实活动包，`place_resolved`/`places_suggested` 为真实计数（无包时仍是 false/0） |

隐私：`/geo` 仍是唯一返回精确坐标的会话保护入口；`/places/datasets`、`/assets/{id}/place`、
错误、日志、outbox、LanceDB 都不含坐标，也不含本机绝对路径（`dataset_view` 根本不投影
`relative_dir`）。无 session 全部 401；跨 Origin 403 `ORIGIN_NOT_ALLOWED`；缺 CSRF 403
`CSRF_INVALID`（三者都有用例，且被拒后零写入）。

## 6. 显式本地工具与示例

`tools/places/manage_dataset.py`（普通应用启动完全不联网；工具也不联网）：

```text
build    --spec <spec.json> --out <dir>       # 从本地 spec 确定性构建包并自校验
import   --source <dir> [--activate]          # stage + validate（可选激活）
validate --dataset-id ID
activate --dataset-id ID
rollback
status
cleanup  --dataset-id ID
```

所有子命令支持 `--data-root`，错误以 `CatalogError.as_dict()` 打到 stderr 并返回退出码 2。
示例：`tools/places/examples/mini-cities.spec.json`（极小合成 CN 城市集，许可字段明确标注
`SYNTHETIC-EXAMPLE`）与由它生成的 `tools/places/examples/mini-cities.manifest.json`；
`tests/integration/test_p2_dataset_tool.py` 断言 builder 输出与签入的 manifest **完全相等**，
因此示例不会与实现漂移。

## 7. 故障注入与恢复（D 级）

| # | 场景 | 实测 |
|---|---|---|
| 1 | 激活事务中断 | 在指针已更新后注入异常 → 整体回滚：指针仍是旧包、目标仍是 `VALIDATED`、地点不重复、`active_geo_dataset.revision` 不变 |
| 2 | 候选写入与 suggestion revision 同事务 | 在可见性计算处注入异常 → resolution/候选 0 行、revision 仍 0、job 仍是 RUNNING（未提交），可重新接管 |
| 3 | 数据包文件被删除 | 删活动包 `places.jsonl` → `/places/datasets` 报 `missing`、`resolver_available=false`、解析返回 `PLACE_DATASET_MISSING`；rollback 重新校验**目标**包仍成功 |
| 4 | 数据包文件被篡改（同长度改字节） | 报 `invalid` + `PLACE_DATASET_INVALID`；激活一个被篡改的已校验包被拒且旧指针/`revision` 不变 |
| 5 | 清理保护 | 活动包 → `PLACE_REFERENCED`；有 resolution → `PLACE_REFERENCED`；作为回退目标 → `PLACE_REFERENCED`；无引用 staged 包可删除（行与目录都消失） |
| 6 | 无 GPU | capability 只有 cpu：解析 job `SUCCEEDED`、建议可见、人工 assign 正常 |
| 7 | `artifacts=[]` | 把合格结果的 artifacts 清空后 `submit` 仍成功；无 artifact 即无机器产物，同输入可再次解析 |

## 8. 验收命令与结果

| 命令 | 结果 |
|---|---|
| `uv run --no-sync pytest -q -p no:cacheprovider tests/unit tests/integration` | exit 0；`--collect-only` 计 **546** 例，进度行 1 个 `s`，即 **545 passed / 1 skipped**（沿用既有 skip） |
| `uv run --no-sync ruff check .` | exit 0，`All checks passed!` |
| `git diff --check` | exit 0，无输出 |
| `uv run --no-sync uv lock --check` | exit 0，`Resolved 95 packages`（依赖未变） |
| 新增测试文件 | `test_p2_dataset_manifest.py` 23、`test_p2_dataset_lifecycle.py` 13、`test_p2_place_resolver.py` 15、`test_p2_place_dataset_api.py` 7、`test_p2_dataset_migration.py` 5、`test_p2_dataset_tool.py` 2（共 **65** 例；P2-03A 基线 481 → 546） |

本环境已记录的同类工具事实沿用 P2-00/P2-01A/P2-03A：`uv` 默认缓存在沙箱内 `%LOCALAPPDATA%\uv\cache`
返回 access denied（改用唯一 `%TEMP%` cache），pytest 的 basetemp 在沙箱内被 ACL 拒绝，因此
测试在沙箱外运行。

## 9. C 级判定（实际执行了额外打包验证）

任务卡的 C 触发条件是“新增依赖、`workers/**`、打包入口或**新增** package-data 目录”。本任务：
依赖未变（95 packages）、`workers/**` 未动、打包入口 `src/photo_search/__main__.py` 未动、新代码都在
既有 Python 包里（迁移落在既有的 `catalog/migrations` package-data 目录），**按卡不触发 C**。

但任务卡“已知继承项”要求：一旦把解析执行器接到应用/调度路径，就必须复核导入图并补 C 级 smoke。
本任务在 `Scheduler.submit` 里接入了 `persist_resolution`，因此仍执行了真实打包验收：

| 检查 | 结果 |
|---|---|
| `uv run --no-sync python tools/packaging/build_onedir.py` | exit 0（约 57 s），产物 `dist/PhotoSearch`（已 gitignore），21 个 NVIDIA DLL 打包 |
| `pyi-archive_viewer -r -l` 审计 | `photo_search.geo.dataset`、`photo_search.geo.exif`、`photo_search.places.{assignments,contracts,datasets,directory,filter,revision,service,suggestions}` 已在 bundle 内；`_internal/photo_search/catalog/migrations/` 含 **0001～0005** 全部 5 个 `.sql` |
| `packaged_api_smoke.py --bundle dist/PhotoSearch --work-dir %TEMP%\P2-03B 打包验收 …` | exit 0，`status=ok`、`problems=[]`、startup 1.0 s；中文/空格安装路径、注册来源、扫描、缩略图、静态前端、无 session 401 全部通过（打包进程成功迁移到 schema 5 本身即 0005 在冻结包内生效的证明） |

审计的**明确发现**（沿用并细化 P2-03A 报告 §9）：`photo_search.geo.extract`、
`photo_search.geo.resolver` 与 `photo_search.scheduler.engine` **仍不在** bundle 内——应用目前只 import
`photo_search.scheduler.state`/`projection`，没有常驻调度线程，也没有任何 API 触发解析。因此冻结包
当前可用的是地点目录/人工归属/包状态查询，**不能**执行 GPS 或地名解析；把调度器接进进程的那次任务
必须重跑 C 级并复核导入图（P2-03C 若只做页面不改导入图则不受影响，但接入后台解析的任务必须复核）。

一次需要记录的**环境事件**：打包 smoke 会把整个 bundle（约 3.4 GB）复制到 `%TEMP%`，本次 C 盘在
第二次 smoke 复核时被填满（`WinError 112`），复核命令失败；随后只清理了**本任务创建**的 3 个
`%TEMP%\P2-03B 打包*` 目录（约 3.9 GB）并确认 C 盘恢复 3.76 GB 可用。报告的 C 级证据取自第一次
成功运行（同一 bundle、同一命令），未再重复一次 C 级构建。

## 10. 未验证项与残留边界

1. **无常驻调度/执行线程**：解析闭环用真实 scheduler 对象 + 确定性执行器验证，没有常驻线程、
   没有跨进程并发写压力、没有真实端口后台解析（沿用 P2-02 §10 / P2-03A §10）。
2. **真实 GeoNames 包未使用**：本任务只用极小合成子集验证格式、生命周期与数值分支；
   多语言/别名覆盖、真实包体积与记录数、构建耗时、许可清单都没有实测（P4-05B/P4-07）。
   因此不能从本任务声称任何真实城市命中率或覆盖精度。
3. **最近点全表扫描**：`nearby_cities` 对城市点做线性扫描，只有 `(distance, id)` 排序保证确定性，
   没有 RTree/网格索引；真实包规模下的选址与吞吐未测（P2-00 路线图 §6 已列为待办）。
4. **冷缓存会重新校验整包**：`load_content` 每次冷启动都会重算所有文件的 SHA-256。执行器会预热
   进程内缓存（键为 `dataset_id:content_sha256`），但“跨进程并发解析 + 大包”的成本未测。
5. **切包不自动重算**：激活/回退只换指针与物化地点，不重新解析资产；旧包的 resolution 仍作为历史
   机器产物保留，但 `place_state=suggested` 只匹配活动数据集的解析结果。自动刷新流程属于后续任务。
6. **坏包时旧建议仍可见**：`/assets/{id}/place` 不重新校验包，因此活动包被破坏时既有候选仍可读，
   只有新的解析阻塞；`/places/datasets` 会如实报 `missing`/`invalid`。
7. **`GET /assets` 页面仍不是快照隔离读**：只绑定了 `suggestion_revision`；绑定 profile/generation/
   revision 的搜索会话属于 P2-06（P2-03A §10.2 不变）。
8. **回退链依赖 manifest 声明**：`previous_dataset_id` 只来自包自身声明；工具导入时不自动
   “previous = 当前活动包”。未声明时 rollback 退化为“最近一次 VALIDATED/RETAINED 包”。
9. **别名不参与包升级重写**：数据集别名只新增、不覆盖用户别名，也不递增地点 revision；
   成规模的数据集升级对目录 revision/outbox 的影响未测。
10. **地图/行政边界/景点**：仍不存在，`attraction` 不会被城市点包自动命中（ADR-0005 §4 不变）。
11. **精确坐标**：仍只在会话保护的 `/geo`；本任务没有新增任何坐标出口，日志/错误/诊断/outbox/
    Lance 无坐标（既有断言继续通过）。

## 11. 结论与交接

P2-03B 的“0005 数据包/解析 migration + 离线地名包 manifest/导入/激活/回退 + `non_model +
CPU_PREFERRED` 附近城市解析 + 候选/建议状态 + 显式本地工具”闭环已按 D 级真实验收，因此标记 PASS。
`schema_version` 由 4 推进到 5；0001～0004、冻结 ADR/JSON schema 与依赖未动。

下一任务是 **P2-03C — 静态地点页与人工整理交互**，推荐 **DS-L（复杂交互可用 DS-H）**，
验收 **B + 真实浏览器 smoke + C（新增 package data）**。任务卡见
[docs/tasks/P2-03C.md](../tasks/P2-03C.md)。
