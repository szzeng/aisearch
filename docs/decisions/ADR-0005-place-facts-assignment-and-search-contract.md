# ADR-0005 — 地点事实、人工控制、数据包与搜索冻结契约

- 日期：2026-09-13
- 状态：**ACCEPTED / FROZEN FOR P2 PLACE IMPLEMENTATION**
- 适用范围：P2-01A 及后续 P2-02、P2-03A/B/C、P2-04～P2-08
- 机器可读伴随物：`contracts/p2-place-contracts.v1.json`
- 冻结基线：ADR-0002、ADR-0003、ADR-0004，当前 `schema_version = 1`

## 1. 决策摘要与优先级

本 ADR 把地点拆成三类互不覆盖的状态：原始 `GeoFact`、可重算的
`PlaceResolution` 和 SQLite 权威的 `PlaceAssignment`。人工指定和人工取消都是锁；自动任务只能
更新事实或建议，永远不能写、撤销或绕过人工控制。地点筛选以 SQLite 为权威，派生 FTS/向量投影
可以落后，但必须补查，不能漏掉刚归类或刚改名的资产。

ADR-0002 的资产身份、位置、job 五字段、lease、迟到提交和增量迁移规则不变。ADR-0003 的
`non_model`、`CPU_PREFERRED`、成功空结果、不可变 `SearchProfile`、space 隔离和
`CatalogFilterRevision` 外壳不变。伴随 JSON Schema 是本 ADR 的派生物；冲突时以本 ADR 为准，
并在同一次变更中修正伴随物和测试。

首版稳定外部枚举如下；大小写与拼写不得由实现自行改变：

```text
place_kind: country | city | attraction | custom
place_lifecycle: ACTIVE | RETIRED
geo_status: VALID | ABSENT | INVALID | UNSUPPORTED_CRS | SOURCE_UNAVAILABLE
datum_status: WGS84 | ASSUMED_WGS84 | UNSUPPORTED | ABSENT
resolution_status: CANDIDATES | NO_CANDIDATE
assignment_mode: AUTO_ALLOWED | MANUAL_ASSIGNED | MANUAL_UNASSIGNED
assignment_origin: NONE | MANUAL | CONFIRMED_SUGGESTION
assignment_record_state: ACTIVE | SUPERSEDED
dataset_state: STAGED | VALIDATED | ACTIVE | RETAINED
```

主架构 §4.5 的早期逻辑标签 `POI` 在本协议中收敛为 `attraction`；`ADMIN` 不属于用户确认的首版
四类，不进入 v1 枚举。未来若增加省／州等独立行政类型，必须发布新契约版本、迁移和 API capability，
不得把现有 `city` 静默改义。

除沿用现有 `/api/v1` 错误信封外，所有跨进程地点业务对象携带
`contract_version = "p2-place-contracts.v1"`，拒绝未知字段。业务稳定 ID 使用正整数；revision 从 1
开始且只递增；时间使用 UTC epoch 毫秒；SHA-256 为 64 位小写十六进制。

## 2. Place：稳定身份、层级与生命周期

`place_id` 是本地 `INTEGER PRIMARY KEY AUTOINCREMENT`，名称、坐标、父级和外部数据集 ID 均不构成
身份。一个 Place 包含 `kind`、规范名称、别名、可空父级、来源、revision 和生命周期。

- 名称不唯一；同一父级下也允许两个同名地点。选择器显示完整祖先链和稳定 ID，不自动合并。
- 每个地点最多一个父级，父级必须 ACTIVE，不能指向自己或形成环；层级允许缺级。
- `country` 不得有父级；`city` 可属于 country；`attraction` 可属于 city/country；`custom` 可无父级，
  也可挂在任一 ACTIVE 地点下。事务层按此矩阵和递归 CTE 检查跨行约束。
- 用户可创建 city、attraction 或 custom；country 通常来自已验证数据包。来源不改变本地身份。
- v1 不提供地点合并、物理删除或任意子树移动。改名和别名编辑保留 `place_id`。改父级的事务语义
  在 §7 冻结供受控维护使用，但 P2-03A 的公开 PATCH capability 必须关闭该动作。
- `RETIRED` 是软删除。存在 ACTIVE 子节点、当前归属或有效排除时拒绝退休；物理删除始终禁止，历史、
  操作记录和外键继续可读。

外部引用用 `(provider, external_id)` 唯一映射到本地地点；包升级可以新增映射或名称，但不得为同一
已映射实体重建 `place_id`，也不得覆盖用户维护的规范名称、别名或父级。

## 3. GeoFact：只读 GPS 事实与隐私

`GeoFact` 绑定稳定 `asset_id`、本次受控读取的 `location_id`、资产内容 `input_revision`、版本化
`processor_id` 和原始 GPS 字段 checksum。当前事实按资产至多一条；旧版本保留在通用 structured
artifact/history 中，不就地改写成新内容的事实。

事实分支：

- `VALID`：原始 EXIF 字段可审计，并有规范化 WGS84 纬经度。纬度 `[-90, 90]`、经度
  `[-180, 180]`，值必须有限；`(0,0)` 是合法坐标而不是缺失哨兵。
- `ABSENT`：成功空结果，原始坐标与规范坐标均为空；JobResult 仍为 `SUCCEEDED`。
- `INVALID`：非法分母、非有限值、引用方向错误或越界；坏 GPS 不阻塞缩略图或语义任务。
- `UNSUPPORTED_CRS`：保留原值和 datum 证据，但不产生内部可检索坐标。GCJ-02/BD-09 不静默转换。
- `SOURCE_UNAVAILABLE`：仅用于一次受控读取的阻塞诊断，不得替换上一条当前事实，也不能伪装成
  `ABSENT`；对应 job 为 `BLOCKED`。

明确写出 WGS84 时为 `datum_status=WGS84`；EXIF 未给 datum 但字段符合 WGS84 解释时可标
`ASSUMED_WGS84`，只能生成待确认建议。未知 datum 为 `UNSUPPORTED`。精度、海拔、海拔参考和方向
均可空；缺失值不能填造。GPS 提取是 `non_model + CPU_PREFERRED`，无模型权重、dimension、metric
或 `space_id`。

精确坐标只允许出现在 catalog/受保护 structured artifact 和
`GET /api/v1/assets/{asset_id}/geo` 的会话保护响应中。不得进入 URL/query string、普通日志、错误
message/details、搜索反馈、静态资源或向量库。诊断只记录 asset ID、状态、处理器、datum 状态和粗粒度
计数；伴随 schema 通过 closed object 与固定 privacy 标志拒绝可疑输出字段。

## 4. PlaceResolution：机器建议永不成为人工结论

PlaceResolution 是不可变机器产物，绑定 `asset_id`、GeoFact、资产 input revision、resolver
`processor_id`、config hash、地名包 `dataset_id/version` 和控制 revision 快照。候选保存 rank、距离、
显示名称、层级提示和依据。

首版唯一确定性依据是 `NEARBY_CITY_POINT`，候选固定
`disposition=SUGGESTION_ONLY`：最近点不能证明行政包含关系；边境、海上、同名、多候选或无可信点时
可以返回多个建议或空列表。景点不会由城市点包自动命中。`NO_CANDIDATE` 是成功空结果。

解析器从不写 `asset_place_assignments`。晚到结果只在 ADR-0003 lease/input revision 提交资格通过后
保存为机器建议；即使其看到旧控制 revision，也不得覆盖当前 MANUAL_ASSIGNED/MANUAL_UNASSIGNED。
候选刷新只递增独立的 `suggestion_revision`，不递增 CatalogFilterRevision。使用
`place_state=suggested` 的列表／会话必须另绑定 suggestion revision，过期后明确重查。

## 5. PlaceAssignment：一个当前控制行，人工锁优先

每个资产至多一个 ACTIVE PlaceAssignment 控制行；历史版本为 SUPERSEDED。三个模式是：

| mode | place_id | origin | 含义 |
|---|---:|---|---|
| `AUTO_ALLOWED` | NULL | `NONE` | 允许机器继续给建议；首版没有“自动确定归属”能力 |
| `MANUAL_ASSIGNED` | 非空 | `MANUAL` 或 `CONFIRMED_SUGGESTION` | 人工有效最具体地点，带锁 |
| `MANUAL_UNASSIGNED` | NULL | `MANUAL` | 人工明确取消归类，带锁 |

有效地点只来自 ACTIVE `MANUAL_ASSIGNED` 行。祖先归属查询时展开 Place 层级，不复制国家／城市多条
assignment。手动归类不依赖 GPS。目录移动、文件重命名、来源 remap 或 profile/地名包回退都不改变
asset_id 上的人工控制；内容字节变化形成新 asset_id，不自动继承旧人工结论。

排除按 `(asset_id, place_id)` 保存。assign A→B 会激活 A 的排除；显式 assign B 可以解除 B 的排除并
返回解除清单，其他排除保留。unassign 会排除原有效地点并进入 MANUAL_UNASSIGNED；原本无有效地点也
能设此锁。restore_auto 回到 AUTO_ALLOWED 且保留排除；它只允许继续产生建议，不把现有最近城市建议
升级为确定归属。

## 6. 人工操作、幂等、批量与补偿撤销

所有变更由本机会话中的 `LOCAL_USER` 发起，携带 `request_id`、规范化 payload SHA-256 和
`expected_revision`。同一 request_id + 同一 payload 返回原 operation/result，不再次递增 revision；
同一 request_id + 不同 payload 返回 `PLACE_IDEMPOTENCY_CONFLICT`。

### 6.1 指定、取消和恢复自动

- `ASSIGN` 必须给 ACTIVE target place 和当前 assignment expected revision。
- `UNASSIGN`、`RESTORE_AUTO` 同样要求 expected revision；目标可以没有当前行，此时 revision 为 0。
- revision 不符整次拒绝为 `PLACE_ASSIGNMENT_CONFLICT`，无部分写入。
- assignment 当前值、历史快照、排除、operation/items、CatalogFilterRevision event 与通用 outbox
  事件在一个 `BEGIN IMMEDIATE` 短事务提交；事务内不做文件 I/O、地名查找或投影重建。

### 6.2 批量

一批是 1～500 个去重 asset_id，每项携带自身 expected revision；服务端先验证全批，再用一个事务
全部提交。任一不存在、重复、冲突、退休地点或非法动作使整批拒绝，不返回假部分成功。超过 500
返回 `PLACE_BATCH_TOO_LARGE`；界面拆批时每批有独立 request_id/operation_id，并明确展示逐批结果。
“全选结果”必须先冻结 asset_id 快照，不能在提交时重跑动态查询。

### 6.3 地点改名、别名和改父级

改名／别名要求 place expected revision；实际可检索文本改变时短事务递增 place revision、
CatalogFilterRevision 并写投影 outbox。无变化的幂等编辑不递增。改父级还要验证目标 ACTIVE、类型
矩阵和无环；成功只改目录关系，不逐资产重写祖先。它递增一次 CatalogFilterRevision，受影响资产集
可标为全量／未知并由 outbox 分批重建。P2-03A 公开 API 暂不开放改父级，但实现不得另定语义。

### 6.4 撤销

undo 是新的补偿 operation，不删除旧日志、不降低 revision。请求携带新 request_id 和待撤销
operation 的 expected revision。只有原 operation 已 COMMITTED、未撤销，且每个目标仍等于原操作
记录的 after revision/state 时才可整批撤销；任何后续编辑使整批返回
`PLACE_OPERATION_NOT_UNDOABLE`。成功补偿恢复 before 语义、为每项生成更大的 revision，必要时再次
递增 CatalogFilterRevision。undo 本身也可被审计，但首版不提供“撤销撤销”的捷径。

## 7. 增量 DDL 设计（本任务不创建 migration）

实际 migration 编号由 P2-03A 按届时连续版本分配，不能修改 `0001_initial.sql`。SQLite 可直接表达
的 PK/NOT NULL/UNIQUE/FK/CHECK 必须下沉；环、跨表状态和幂等载荷等跨行规则由 catalog 单写事务与
测试保证，不引入复杂 trigger。

计划新增对象：

| 表／索引 | 主键与关键列 | 冻结约束 |
|---|---|---|
| `places` | place_id；parent_place_id；kind/name/origin/lifecycle/revision/timestamps | parent FK RESTRICT；kind/lifecycle CHECK；parent != self；不唯一 name；不物理删 |
| `place_external_refs` | (provider, external_id)；place_id | 外部键唯一；place FK RESTRICT；包升级不换本地 ID |
| `place_aliases` | alias_id；place_id；alias/normalized/language/source/lifecycle/revision | place FK RESTRICT；同地点规范化活动别名唯一；软退休 |
| `asset_geo_facts` | asset_id PK；geo_fact_id 唯一；location_id/input_revision/status/raw checksum/WGS84 可空 | asset/location FK RESTRICT；状态/空值组合 CHECK；当前事实每资产唯一 |
| `place_resolutions` | resolution_id；asset/geo fact/processor/dataset/input/config/status/suggestion_revision | 逻辑输入唯一；机器不可变；asset/dataset FK RESTRICT |
| `place_resolution_candidates` | (resolution_id, rank)；dataset key/local place/distance/evidence | rank 唯一；只允许 SUGGESTION_ONLY；无 assignment FK 副作用 |
| `asset_place_assignments` | asset_id PK；place_id/mode/origin/revision/operation/updated/actor | 一资产一当前行；模式组合 CHECK；FK RESTRICT |
| `asset_place_assignment_history` | (asset_id, revision)；完整快照/operation | 只追加；revision 单调；FK RESTRICT |
| `asset_place_exclusions` | (asset_id, place_id)；active/revision/operation | active 唯一行；FK RESTRICT；自动解析只读 |
| `place_operations` | operation_id；request_id 唯一；payload hash/type/status/actor/undo link/timestamps | request payload 冲突由事务检查；日志只追加 |
| `place_operation_items` | (operation_id, item_index)；asset/place/action/expected/before/after | 每 operation 的 asset 去重；before/after 可补偿；FK RESTRICT |
| `geo_datasets` | dataset_id；package/version/manifest/content checksum/state/relative dir/timestamps | 不可变内容；状态 CHECK；物理包只在数据目录 |
| `geo_dataset_files` | (dataset_id, relative_path)；checksum/size/role | 禁绝对路径与 `..`；文件完整性逐项核验 |
| `active_geo_dataset` | singleton=1；dataset_id；revision | 只指向 VALIDATED/ACTIVE 包；切换与状态同事务 |
| `place_suggestion_revision` | singleton=1；revision/committed_at/cause | 候选可见集合改变时单调递增；独立于 catalog filter |
| `catalog_filter_revision` | singleton=1；revision/committed_at/cause | 从 ADR-0003 外壳落地；只在权威可筛选事实变化时递增 |
| `catalog_filter_revision_events` | revision PK；cause/changed count/all-assets 标志 | 每次提交一条；受影响 asset IDs 用从表或 outbox 引用 |

所有 identity/history/operation 外键使用 `ON DELETE RESTRICT`。坐标列 CHECK 范围仍需 Python 在写前
拒绝 NaN/Infinity，因为 SQLite 的类型转换和跨列条件不能替代协议验证。迁移必须遵守 ADR-0002 §7：
只读预检、备份、连续版本、单事务推进 schema_version、目标 schema 完整复核和更高版本拒写。

## 8. 地名数据包 manifest、激活、恢复与回退

`PlaceDatasetManifest` 至少固定 package ID/version、dataset ID、上游来源 URL、许可证与 attribution、
生成参数、builder 版本、内容总 SHA-256、坐标基准（只接受 WGS84）、覆盖范围、语言/别名、兼容的
manifest/catalog schema、文件相对路径/角色/大小/checksum 和生命周期时间。

状态机：`STAGED → VALIDATED → ACTIVE → RETAINED`。导入先写唯一 staged 目录，校验 manifest、许可、
每个文件 checksum/size、格式、层级引用与兼容版本；完成后 fsync 并标 VALIDATED。激活在一个 SQLite
事务内改 `active_geo_dataset` 指针和包状态，磁盘目录本身不可变。崩溃前未提交则旧指针继续有效；
提交后重启按新指针复核，坏包阻塞解析并可原子回退到上一 VALIDATED/RETAINED 包。

被活动指针、PlaceResolution、在途 job/artifact、回退窗口或备份 manifest 引用的包不可清理。缺包／
坏包返回 `PLACE_DATASET_MISSING`/`PLACE_DATASET_INVALID` 并只阻塞解析任务；人工地点创建、既有名称/
层级显示、人工归属、筛选和操作历史继续可用。普通应用启动不联网下载；准备工具只下载公开地名数据，
不上传照片或坐标。

地名数据包版本和 suggestion revision 独立于 SearchProfile、embedding space、index generation 与
CatalogFilterRevision。切包只刷新候选时不重算图文向量，也不使有效地点会话过期。

## 9. `/api/v1` 地点接口与错误语义

所有接口沿用已实现的 loopback Host、内存 session、Origin、double-submit CSRF、统一
`{code,message,request_id,details?}` 错误信封和 Redactor。写接口使用现有 CSRF 边界；绝对路径、
原始 EXIF 和精确坐标不得进入普通响应。

冻结接口：

```text
GET    /api/v1/places
POST   /api/v1/places
PATCH  /api/v1/places/{place_id}
GET    /api/v1/places/coverage
GET    /api/v1/places/datasets
GET    /api/v1/assets/{asset_id}/geo
GET    /api/v1/assets/{asset_id}/place
PUT    /api/v1/assets/{asset_id}/place-assignment
POST   /api/v1/place-assignments/batch
GET    /api/v1/place-operations
POST   /api/v1/place-operations/{operation_id}/undo
GET    /api/v1/assets?...place filter...
POST   /api/v1/search
```

`GET /places` 支持 name/alias、kind、parent、ancestor、lifecycle 的 keyset 分页，游标绑定地点目录
revision。POST 创建手工 city/attraction/custom；PATCH v1 只改名／别名，不合并、不删除、不开放
reparent。`GET /assets/{id}/place` 返回有效控制、来源、锁、排除摘要、当前建议状态和 revisions；
`/geo` 才能按需返回精确坐标。

PUT body 为 action=`assign|unassign|restore_auto`、可空 target_place_id、request_id、
expected_revision。batch 携带明确 asset IDs 与逐项 revision，上限 500、全批原子。undo 也是带
request_id/revision 的写请求。coverage 分别给资产总数、GPS 提取、有效 GPS、解析完成、有效归属、
待确认和人工未归类的计数与分母；不得把 PLACE_RESOLVED 当已归类。

稳定错误码：

| code | HTTP | 语义 |
|---|---:|---|
| `PLACE_NOT_FOUND` | 404 | place/operation/asset 地点资源不存在 |
| `PLACE_ASSIGNMENT_CONFLICT` | 409 | expected assignment/place revision 已变化 |
| `PLACE_IDEMPOTENCY_CONFLICT` | 409 | request_id 已绑定不同 payload |
| `PLACE_BATCH_TOO_LARGE` | 422 | 去重前后任一资产项超过 500 |
| `PLACE_PARENT_CYCLE` | 409 | 父级造成自环／祖先环或非法类型 |
| `PLACE_REFERENCED` | 409 | 尝试退休／清理仍被引用的地点或包 |
| `PLACE_OPERATION_NOT_UNDOABLE` | 409 | 操作状态或 after revision 已被后续编辑改变 |
| `PLACE_DATASET_MISSING` | 409 | 当前解析所需包不存在；人工路径仍可用 |
| `PLACE_DATASET_INVALID` | 409 | checksum/格式/兼容性失败 |
| `CATALOG_FILTER_REVISION_STALE` | 409 | continuation 的权威筛选 revision 已过期，必须重搜 |
| `PLACE_SUGGESTION_REVISION_STALE` | 409 | suggested 列表 revision 已过期，必须重查 |
| `INVALID_PLACE_FILTER` | 422 | place_ids/state/descendant 组合非法 |
| `GEO_COORDINATE_INVALID` | 422 | 坐标非有限、越界或原始字段非法 |

409 响应可以返回安全的 expected/current revision、asset/place/operation/dataset ID 和 max batch；不得
回显坐标、路径、原始 EXIF 或 idempotency payload。

## 10. 地点筛选、组合搜索与权威补查

`PlaceFilter` 冻结：`place_ids` 是 OR，默认 `include_descendants=true`；与来源、时间、人物、标签、
可见性和语义条件是 AND。`place_state`：

- `any`：不限制地点状态；
- `assigned`：存在当前有效 MANUAL_ASSIGNED；
- `unassigned`：没有当前有效地点，包含 AUTO_ALLOWED、MANUAL_UNASSIGNED 和只有建议；
- `manual_unassigned`：只含人工取消锁；
- `suggested`：没有有效地点且当前未排除候选非空。

只要给出 place_ids，place_state 只允许 any/assigned。祖先查询递归展开后代，按 asset_id 去重；多个
file_locations 不重复计数。名称搜索匹配规范名和 ACTIVE 别名，仍返回 place_id 让用户消歧；改名和
父级变更立即以 SQLite 最新值为准。

纯地点筛选直接读 SQLite，不需要 GPU。地点 + 语义先从 SQLite 得到权威候选集，再用经过验证的
预过滤／候选 ID 检索；禁止全库 Top-K 后过滤。向量／FTS 投影可作为加速但不是权威。返回前再次读取
当前 CatalogFilterRevision 并按 SQLite 复核。若投影滞后，必须从 outbox/index_entries 识别影响集并：

1. 对有界权威候选做精确补查；或
2. 对大集合重新执行正确的分块候选检索；或
3. 返回结构化“索引更新中”而不发布不完整结果。

只过滤旧结果只能移除失效命中，不能补回刚归类、刚改名或刚改父级的资产，因此不算正确实现。
search session/cursor 同时绑定 profile_id、generation 和创建时 CatalogFilterRevision。继续翻页时不同
则返回 `CATALOG_FILTER_REVISION_STALE`，保留原查询供客户端重搜。查询计算途中 revision 变化也不
发布混合版本页。suggested 会话额外绑定 suggestion revision。

## 11. CatalogFilterRevision 触发矩阵

revision 在产生权威可筛选变化的同一 SQLite 事务中**至多递增一次**；批量和 undo 也各自一次。

| 动作 | CatalogFilterRevision | 独立 suggestion revision | embedding/profile |
|---|---|---|---|
| assign／A→B／unassign／restore_auto 的有效状态变化 | +1 `PLACE_EFFECTIVE_ASSIGNMENT_CHANGED` | 不变 | 不变 |
| assignment 幂等重放或实际 no-op | 不变 | 不变 | 不变 |
| 规范名／ACTIVE 别名实际改变 | +1 `PLACE_NAME_CHANGED` | 需要重显示时可 +1 | 不变 |
| parent 实际改变／可检索生命周期改变 | +1 `PLACE_HIERARCHY_CHANGED` | 需要重显示时可 +1 | 不变 |
| 排除或人工锁改变 suggested/未归类可见状态 | +1 `PLACE_EFFECTIVE_ASSIGNMENT_CHANGED` | +1 | 不变 |
| GeoFact 新增／刷新／失效 | 不变 | 候选集合随后变化时 +1 | 不变 |
| PlaceResolution 候选刷新、空结果、晚到可接受结果 | 不变 | +1（可见集合实际变化时） | 不变 |
| 数据包 stage/validate/activate/rollback，仅重算机器建议 | 不变 | 新建议发布时 +1 | 不变 |
| profile/space/index generation 切换 | 不变 | 不变 | 按 ADR-0003 独立切换 |
| 无冲突 undo | 按补偿造成的权威变化 +1 | 造成建议可见性变化时 +1 | 不变 |

## 12. 正常、异常与边界判定

| 情况 | 冻结结果 |
|---|---|
| 无 GPS | GeoFact=ABSENT，任务 SUCCEEDED，仍可手工归类 |
| `(0,0)`／南西半球 | 按有限范围正常保存；不当缺失 |
| 非有限／越界／非法分母 | GEO_COORDINATE_INVALID 或 GeoFact=INVALID；不产生建议 |
| 未声明／未知 datum | ASSUMED_WGS84 只给建议；未知基准 UNSUPPORTED_CRS |
| 最近城市／边界同名／海上 | SUGGESTION_ONLY，多候选或空；不写 assignment |
| 无包／坏包／切包中断／重启 | 解析 BLOCKED 或回退；人工目录、归属和筛选继续可用 |
| 手工自定义地点无坐标 | 合法；可指定、筛选和备份 |
| assign→unassign→重算 | MANUAL_UNASSIGNED 锁保留，机器不能加回 |
| restore_auto | 保留排除，仅恢复建议资格，不自动确定最近点 |
| 自动结果晚到人工操作之后 | 可留机器产物；不得写当前 assignment |
| 同资产两个 ACTIVE assignment | DB PK/事务拒绝，零部分写 |
| 500/501 批量 | 500 全批原子；501 在写前拒绝 |
| 重放相同/不同 request_id payload | 相同返回原结果；不同报幂等冲突 |
| 改名／改父级 | 稳定 place_id；短事务增 revision/outbox，禁止环；权威查询立刻见新值 |
| 引用中退休／清理 | PLACE_REFERENCED；不级联删除人工数据 |
| undo 后已有新编辑 | 整批 PLACE_OPERATION_NOT_UNDOABLE，不覆盖新状态 |
| 搜索计算／翻页时人工变化 | 复核或 STALE；重搜能补回新命中 |
| 精确坐标进入 URL/日志/反馈 | schema/应用边界拒绝并脱敏；不得成功 |

## 13. 后果、实施边界与未验证项

本任务不创建任何表、migration、API、GPS 读取器、地名包、前端或调度实现，不改变
`schema_version = 1`、`0001_initial.sql`、依赖或 SearchProfile。Schema/样例测试只证明文档与实例
契约一致，不证明数据库/API/运行时已经拒绝坏输入。

P2-02 只用确定性 non-model CPU 假任务验证调度资格，不读取 GPS。P2-03A 落地增量迁移、事实、目录、
人工事务和纯筛选；P2-03B 才用 `%TEMP%` 合成 GPS 与固定公开小包验证解析；P2-03C 接 UI；P2-05/06
验证组合搜索和旧会话；P2-08 做并发、迟到、切包和恢复故障矩阵。

尚未验证：真实 EXIF GPS 兼容范围、GeoNames 中文/景点覆盖、行政边界准确率、包大小和构建吞吐、
RTree/网格索引选择、最低配置并发性能、真实硬件断盘及备份恢复。首版没有行政边界自动归类、POI
自动识别、地图、围栏、地点合并或云端反查，不能从本 ADR 声称这些能力已经存在。
