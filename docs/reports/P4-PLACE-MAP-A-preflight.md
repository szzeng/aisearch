# P4-PLACE-MAP-A / CN — 开工核对

日期：2026-10-04。结论：**MAP 可交 LUNA 实现；CN 后做，仍需短 ADR 与边界数据核验。** 本报告是设计核对，不是实现验收或独立工程主审。

## 已核对与补齐

- 两卡分工合理：MAP 用现有 GPS + 在线 OSM；CN 用 OSM/Geofabrik 行政边界改善地点文字/成员，不阻塞地图，不把地名点当行政边界。旧 CN blocker 的来源/决策缺口已写入新版卡；未下载 PBF、创建 ADR 或激活包。
- `api/web.py` 当前没有 `/map`；现有详情是 `/#/asset/{asset_id}`，`app.js` 返回按钮已支持搜索来源。卡中明确新增地图入口和独立返回来源，保留搜索返回行为；地图恢复先核对成员版本。
- `/api/v1/assets/{asset_id}/geo` 已存在，`places/filter.py::geo_fact_view` 返回 `normalized_wgs84`；地图复用它，GeoJSON 用 `[longitude,latitude]`，零坐标合法。新增成员查询仍需过滤过期 GPS、非 PRESENT 和重复文件位置。
- `geo/extract.py::persist_geo_fact` 只递增每资产 fact_revision，现有 catalog_filter_revision 不完整覆盖 GPS 更新。补充持久地图成员版本与同事务维护、读快照一致性、游标过期；允许必要加性迁移，不改变原 GPS 内容/人工状态。
- 冻结地图点上限与准确计数：≤5000 照片点，超过时 ≤1000 聚合 cell；photos 成员用 cluster leaves，grid 成员绑定实际级别/cell/原 bbox/版本；半开边界与最外边界规则保持计数与列表一致。
- 补齐底图/WebGL 失败时仍可分页浏览、同版本本地静态资源及数字字体；集中 provider/坐标展示设置。以后高德需要官方服务接入与 GCJ-02 展示/视野适配，不是换 URL；数据库与查询仍用 WGS84。
- 用户授权在线底图、URL 坐标及不做隐私专项已明确写入卡，覆盖旧文档的本功能限制；不扩大到在线逆地理、GPS 编辑或其他服务。用户手动选择 LUNA 实现，独立审查按卡执行。

## 当前环境的 OSM 观察

通过 computer-use 浏览器只读打开 [杭州公开 OSM 地图](https://www.openstreetmap.org/#map=12/30.2741/120.1551)。截图可见道路、河流与中文地名；DOM 抽查当前已显示的前 8 个 `img.leaflet-tile`，均来自 `https://tile.openstreetmap.org/12/...png`、`complete=true`、`naturalWidth=256`。

因此本次浏览器环境能加载所选标准瓦片。未核实代理/VPN、其他大陆网络、持续可用性、地图新鲜度或本应用 MapLibre 的跨域/WebGL 渲染；这些不能由一次官网浏览推出。OSM 官网用 Leaflet，本次观察不代替产品浏览器闭环。没有读取真实照片或个人图库。

技术依据：[MapLibre 官方栅格示例](https://maplibre.org/maplibre-gl-js/docs/examples/add-a-raster-tile-source/)支持 OSM raster + GeoJSON；[本地字体示例](https://maplibre.org/maplibre-gl-js/docs/examples/style-labels-with-local-fonts/)供固定版本实现核对；[OSM 标准瓦片规则](https://operations.osmfoundation.org/policies/tiles/)要求署名、浏览器缓存/Referer，禁止批量预取且无 SLA；[高德坐标说明](https://lbs.amap.com/api/javascript-api-v2/guide/transform/convertfrom)要求外部坐标转换为高德坐标。具体 MapLibre 版本由实现时固定并记录，不能跨版本照抄示例。

## 验证与未完成项

- 已做：当前任务卡与相关架构/策略、GPS/详情直接调用链核对；OSM 公开地图截图与已渲染瓦片 DOM 检查；文档链接存在性检查与 scoped `git diff --check`。
- 文档修改不新增 pytest；未写功能代码、未运行产品接口/浏览器闭环、未建立 30 万夹具或测量性能。**没有产品 PASS 或 30 万性能通过结论。**
- LUNA 按 MAP 卡完成定向 pytest/JUnit、静态检查、约 20 个合成资产闭环及一次 30 万合成资产 API 计时/查询计划；有卡顿再补必要索引/RTree，不预先展开 GIS 专项。
- CN 保留 TODO：另执行短 ADR、四个目标地区与东京核验、完整包/回退及人工排除验证。约 1.5 GB PBF 在该卡执行时另确认下载；当前地图无需此下载。

交接顺序：`AGENTS.md` → `docs/development-status.md` → [MAP 执行卡](../tasks/P4-PLACE-MAP-A.md)。先实现 MAP；[CN 卡](../tasks/P4-PLACE-CN.md)暂不并行实施。报告和状态中的其他待审任务不追认完成，工作树其他修改保持原样。
