# P4-PLACE-CN-AUTO — 中国行政区地点自动整理

状态：**PLANNED / NOT IMPLEMENTED**。本卡只定义实施范围；未修改业务代码、未运行测试、未构建发布包。

## 1. 背景与目标

Windows 发布包实测：建库完成后「地点相册」为空；用户点击「整理地点 → 高级维护 → 更新中国地点」后，中国地点才出现，而「地图相册」一直正常。

这说明当前照片 GPS 和中国行政区数据可以通过手动流程完成匹配；问题重点是**中国行政区派生结果没有随建库/刷新自动生成**。不应通过重新扫描照片、重新提取 GPS 或重建 GPS 数据库解决。

目标：
- 新图库首次建库后，中国地点自动可用。
- 已有图库升级后，自动补齐历史照片。
- 日常新增照片或 GPS 变化后，自动补充/更新地点。
- 处理中断或程序重启不丢工作。
- 正常使用不再要求用户手动点击「更新中国地点」。

个人项目原则：优先复用现有表、索引、revision 和派生结果；保持实现简单，不建设通用任务平台。

## 2. 当前代码事实（实施前重新核对 HEAD）

### 2.1 已有数据与状态

- `asset_geo_facts`：每个资产一条当前 GPS 事实，`asset_id` 是主键；GPS 更新时会更新 `geo_fact_id`、`input_revision`、`fact_revision`。
- `cn_admin_resolutions`：保存中国行政区派生结果，复合主键为 `dataset_id + asset_id + geo_fact_id + input_revision + processor_id`。因此可直接判断当前 GPS 是否已经有当前数据包/处理器版本的结果。
- `cn_update_runs`：现有按 dataset 保存的全量高水位与续跑进度；`start()` 会重置游标，`inputs()` 每批最多读取 100 条，`publish()` 持久推进进度。
- `metadata`：已有 key/value 表，可保存轻量的处理版本标记，不需要为此新增表。
- `map_member_revision`：已有单调 revision。migration 0017 的触发器会在 `asset_geo_facts` 插入/更新/删除，以及资产内容/状态和照片位置可用性变化时递增；CN 结果写入本身不会递增它。
- `cn_admin_resolutions` 的复合主键可用于按 dataset、asset、GeoFact、input revision 和 processor 查询现有结果。

### 2.2 当前刷新链路

- `refresh/pipeline.py::refresh_library()` 在扫描、搜索投影和语义阶段后调用 `_enrichment_stages()`。
- 当前 enrichment 已有 `gps` 和附近城市 `place_resolver`，但尚未接入中国行政区匹配。
- 日常 refresh 有内存 `content_delta`（上限 50,000）和单阶段 `ENRICH_LIMIT=5,000`；build 模式使用有界来源分页。
- 当前 `POST /places/admin/update` 手动接口会加载活动数据包并调用 CN 分批处理；地点页按钮是目前主要触发入口。

## 3. 核心方案：不新增专用数据表

**默认禁止新增 CN pending/queue 表、通用任务表或数据库 migration。** 优先只使用以下现有结构：

1. `cn_update_runs`：只负责首次全量补齐和未完成全量任务的断点续跑。
2. `cn_admin_resolutions`：既是结果表，也是“当前 GPS 已处理”的事实依据。
3. `map_member_revision` + `metadata`：用一个轻量 revision 标记判断地图/GPS 事实自上次 CN 检查后是否发生变化。
4. SQL `NOT EXISTS`：只挑出当前有效 GPS 尚无当前 CN 结果的照片。

### 3.1 首次全量 / 旧图库升级

- 新 dataset 没有 `cn_update_runs` 时，自动调用现有 `start()` 建立全量游标。
- run 未完成时，自动续接现有 `inputs/calculate/publish`，保留每批 100 条与持久化进度。
- 新图库 build 可在一次 build 的 CN 阶段中持续处理至完成、取消或达到现有安全预算；普通 refresh 对历史补齐使用有界预算，剩余工作留待后续 refresh。
- 已有图库升级时，即使照片 GPS 早已存在、没有新的 `content_delta`，也必须因为缺少当前 dataset 的全量完成状态而自动启动补齐。
- 全量完成后保留 `cn_update_runs.complete=1`。普通 refresh 绝不能再次调用 `start()` 重置全量游标。

### 3.2 日常增量：缺失结果反查

全量完成后，不再使用全量游标，也不依赖内存 `content_delta` 作为正确性保障。

- 为活动 dataset/processor 在现有 `metadata` 中保存最近一次完成 CN 检查时的 `map_member_revision`。key 至少绑定 dataset ID、数据包内容身份和 processor ID，避免换包或升级解析器后误用旧标记。
- 普通 refresh 先比较当前 `map_member_revision` 与 metadata 标记。相同则 CN 阶段直接跳过，不执行照片级查询；不同则进行一次增量 catch-up。
- 增量查询从当前有效的 `asset_geo_facts` 出发，要求资产内容 revision 仍匹配、GPS 状态有效且 datum 为 WGS84/ASSUMED_WGS84，并通过 `NOT EXISTS` 检查是否缺少当前 dataset、当前 `geo_fact_id`、`input_revision` 和 processor 的 `cn_admin_resolutions`。
- 每批最多 100 条，按 `asset_id` keyset 顺序读取；单次普通 refresh 总处理量有界，优先沿用 `ENRICH_LIMIT=5,000`。同一次 refresh 内使用局部游标，避免每个小批次都从头查询。
- 每批写入后，已完成照片自然不再满足 `NOT EXISTS`；未完成照片没有结果，下一次 refresh 仍能被查到。程序在 GPS 事实发布后、CN 阶段开始前退出也不会漏项。
- 超过单次预算时不更新 metadata 完成标记；下一次 refresh 再从 SQL 缺失结果中继续。无需额外 pending 表或持久化逐照片队列。
- 所有 CN 结果仍绑定当前 dataset、GeoFact、input revision 和 processor；写入前复核活动数据包与当前 GPS 身份，拒绝迟到结果。

### 3.3 revision 标记的安全提交

- 开始 catch-up 时读取 `map_member_revision` 快照。
- 只有当缺失结果查询已确认没有待处理项时，才在一个短写事务内重新读取当前 revision。
- 仅当当前 revision 仍等于开始快照时，才将该值写入现有 `metadata`；如果处理中又有 GPS、资产内容或位置可用性变化，则不推进标记，下次 refresh 再检查。
- metadata key 按 dataset/content identity/processor 区分。新数据包或 processor 版本没有旧标记，会自动触发一次检查。
- `map_member_revision` 对文件位置变化也可能产生额外触发，这是允许的保守误报；关键是 GPS 事实变化不会漏报。CN 结果写入不触发该 revision，避免自我循环。

### 3.4 SQL 性能边界

- 优先使用现有 `asset_geo_facts` 主键、`cn_admin_resolutions` 复合主键和现有索引；先看 `EXPLAIN QUERY PLAN` 和合成图库实测，不先加索引。
- 必须如实区分：该方案在 revision 未变化时完全跳过 CN 查询；revision 变化时可能遍历 catalog 中的 GPS 事实行来确认缺项，但**不扫描原始照片、不读取 EXIF、不重新提取 GPS、不做已完成照片的几何匹配**。
- 如果查询计划/耗时确实不合格，先优化 SQL；仍不够时才提出对现有表增加一个窄索引。不得以性能为由直接增加 pending 表。
- 不允许把内存 `content_delta` 当唯一增量来源，因为它有上限，且不能覆盖 GPS 写入后、CN 阶段前崩溃的情况。

## 4. 刷新流水线接入

- 在 `refresh/pipeline.py::_enrichment_stages()` 增加独立 `cn_admin` 阶段，必须在 `gps` 之后；不依赖附近城市 `place_resolver` 的结果。
- 仅在现有 `enrich=True` 路径执行，遵循 refresh 取消、阶段状态、错误隔离和资源生命周期。
- CN 阶段一次加载并复用活动数据包几何内容；不能每处理 100 条就重新冷加载/校验整份 package。
- build 模式的来源分页与 CN 自己的 100 条批次不能意外嵌套重置；CN 全量 run 在一个阶段内推进一次，遵循现有 build 安全预算。
- 抽取共享的内部批处理函数，供自动 refresh 与手动 API 共用；不要通过 HTTP 调用自身 API，也不要维护两套解析实现。
- 无活动数据包、无 CN addon 或包校验失败时，CN 阶段明确报告 SKIPPED/失败原因；不能阻断扫描、搜索索引或地图相册。
- CN 是可选派生阶段。失败时保留可恢复状态并报告 `PARTIAL_FAILED`，不升级为核心图库失败。
- 阶段状态区分 full backfill 与 incremental，报告 scanned、newly resolved、remaining/pending、complete；不能把“请求已提交”当作处理完成。

## 5. 数据与人工操作保护

- 机器结果只写现有 `cn_admin_resolutions`；不得写入/覆盖 `asset_place_assignments`、人工 `places` 目录、`asset_place_exclusions` 或操作历史。
- 不改变 `cn_facets.members_sql()` 对活动 dataset、当前资产/GPS revision、PRESENT 位置、有效 datum、人工控制和排除条件的判断。
- MANUAL_ASSIGNED、MANUAL_UNASSIGNED、有效排除和撤销行为必须与现状一致。
- UNKNOWN、UPPER_ONLY、边界/重叠结果遵循现有几何解析契约；不得猜测或补造区县。OSM 覆盖有限，不承诺每张中国 GPS 照片都能解析到区县。
- 不改地图相册、GPS 聚合、底图、照片索引或搜索语义。
- 不重新扫描照片、不读取 EXIF、不运行向量、人脸、OCR、缩略图或模型推理。

## 6. 手动入口与 UI

- 保留「更新中国地点」作为手动补齐/故障恢复入口，与自动 refresh 共用内部批处理函数。
- 普通点击默认继续未完成全量或缺失结果 catch-up；全量已完成且 revision 未变化时应显示已完成，不得无提示地重置全量游标。
- 若确实需要强制全量重查，保留为明确的维护动作，不作为正常日常更新语义。
- 地点页最小调整状态文案：自动整理中、仍有待处理、已完成、不可用/失败可重试；不重做管理页面。
- 优先沿用现有 capabilities/status API；若新增状态字段，保持 API 加性兼容。

## 7. 验证计划

全部使用隔离合成图库，不访问真实照片、真实 PhotoSearchData 或实际发布包数据。

1. **新图库首次 build**：至少 5,101 张有效中国 GPS 照片，跨越 100 条 CN 批次与 5,000 条预算边界；build 后不调用手动 API，地点相册能显示结果。
2. **已有图库升级**：预置历史有效 GPS、无当前 dataset 的完整 run；第一次 refresh 自动启动全量补齐。
3. **全量中断续跑**：完成若干 100 条批次后取消/模拟重启；下一次 refresh 续跑，不重复做已完成几何计算。
4. **GPS→CN 崩溃窗口**：GeoFact 已提交但 CN 阶段尚未执行时模拟退出；下次 refresh 由 revision 变化触发缺失结果反查并补齐。
5. **日常新增**：全量完成后新增有 GPS 照片，map revision 改变，普通 refresh 自动解析；再次 refresh 在 revision 不变时跳过 CN 查询。
6. **GPS 变化**：已有资产的 GeoFact/input revision 改变后，旧结果不再匹配，新 revision 被反查并生成新结果。
7. **预算与续做**：超过 5,000 条缺失结果时单次 refresh 有界停止；已处理结果不重算，下一次 refresh 继续，最终无遗漏。
8. **revision 竞争**：CN catch-up 期间插入/更新 GeoFact；最终 revision fence 不推进旧标记，下一次 refresh 能补齐。
9. **数据包/处理器变化**：切换 dataset 或 processor ID 后旧 metadata 标记不得复用；新身份触发补齐。
10. **人工保护**：MANUAL_ASSIGNED、MANUAL_UNASSIGNED、有效排除和撤销；机器处理不改变人工状态，成员 SQL 结果符合原合同。
11. **降级**：无活动包、无 CN addon、损坏包、解析异常与取消；其他核心功能继续可用，失败可重试。
12. **查询计划与性能**：记录 anti-join 的 `EXPLAIN QUERY PLAN`；验证使用现有结果主键做存在性检查，revision 不变时完全跳过；在合成大图库中记录 revision 变化时的 catalog 查询耗时。只有实测需要才提议现有表索引。
13. **回归**：GPS 提取、附近城市 resolver、CN 省/市/区县浏览分页/搜索、人工归属与撤销、refresh 取消/恢复、地图 API 和状态展示。

复用已有 CN 几何、数据包和 30 万合成查询证据；不重下 PBF、不重建地名包、不重复执行完整 30 万性能套件，除非本次 SQL/写入路径确实改变并有明确理由。新增测试只覆盖本卡真实受影响路径。

## 8. Windows 手动验收

使用实现后的新源码/新发布包和隔离图库：

1. 新建图库并完成建库，直接打开地点相册，不点击「更新中国地点」；确认可匹配 GPS 的中国照片已出现。
2. 新增一张中国 GPS 照片，执行普通 refresh，确认自动出现。
3. 关闭并重启程序，确认地点结果保留；再次 refresh 且 map revision 未变化时，不再执行 CN 反查。
4. 使用已有图库升级，确认首次 refresh 自动补齐历史照片；中断后再次 refresh 可续做。
5. 手动入口只作为补齐/修复路径验证，不作为自动化通过前提。

## 9. 交付与停止条件

- 最小代码实现、受影响定向测试和少量 UI 状态调整。
- **默认不新增表、不新增 pending queue、不新增 migration。** 若 EXPLAIN 与实测证明现有索引不足，只允许先提出针对现有表的窄索引方案并说明收益；不得自行扩大为新表/任务系统。
- 新增 `docs/reports/P4-PLACE-CN-AUTO.md`：记录全量/增量分流、metadata revision 标记、SQL 查询、预算、恢复行为、EXPLAIN、测试结果和未验事项。
- 同步 `docs/development-status.md`，准确标注实施与验证状态。
- 不自动生成/发布正式 Windows EXE/ZIP；发布包重建与用户现场验收另行执行。
- 不操作真实照片、真实 catalog、用户数据根或用户地名包；不下载 PBF/模型，不改人工地点数据。
- 若现有 `map_member_revision` / `metadata` 无法提供可靠变更检测，先给出具体漏报证据与最小替代方案；未获确认前不新增表或 migration。

**完成标准：新图库首次建库自动出现可匹配的中国地点；已有图库升级自动补齐；日常新增/变化自动更新；中断后不丢工作；无变化时跳过 CN 查询；不新增专用数据表；用户无需再手动点击「更新中国地点」。**
