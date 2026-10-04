# P4-PLACE-CN — 开工前置核对

日期：2026-10-04。状态：**BLOCKED — 数据来源与架构决策待明确**。

## 已核对

- 当前执行入口 `docs/development-status.md` 未列出 P4-PLACE-CN；任务卡存在，基线为 `3f1496585ac943a21c81ebf31bbaab85e1ffb695`。当前 HEAD 为 `0c1ff872d992016562e0b7ac5eef0dfa2625f191`，且工作树有其他任务的未提交改动，本次未修改这些文件。
- 地名数据包现有 INDEX 只接受 `{schema_version, provider, source_snapshot, city_to_admin1}`，既有解析器只建 country/city；GPS resolver 按最近城市点生成机器建议。区县 Polygon、层级浏览与区县搜索必须新增契约和一致的成员解析路径，不能只塞入显示字段。
- `frykit_data` 仓库 README 将省市县来源指向高德和天地图；仓库许可证是 MIT，但 README 没有单独说明底层行政边界数据的再分发许可或坐标参考系。README 说明其同时含高德、天地图来源且两者区县覆盖有差异。
- 高德官方行政区查询文档要求 Web API Key；当前无法从已核对页面确认其边界数据可作为可离线重分发包。天地图行政区数据页面无法读取其具体数据/许可细则（HTTP 418）。因此未下载、生成或激活中国地名数据包，也未使用在线逆地理服务。
- V3.0 §4.5.5 与 V3.1 P5 将经审核行政边界自动归类列为后置能力，要求先做决策记录；V3.1 §11 把地点层级、坐标基准、机器/人工状态、包激活列为高风险受影响面，执行路由要求独立 DS-H 全面审查。当前 P4-PLACE-CN 卡未包含该决策/审查步骤，与现行架构和策略不一致。
- 额外候选：OpenStreetMap / Geofabrik 中国 extract。OSM 官方许可页确认数据为 ODbL，Geofabrik 的中国页面列出数据截至时间并提供中国全量 `.osm.pbf`，页面显示约 1.5 GB；Geofabrik GIS 格式说明称坐标为 WGS84/EPSG:4326。OSM 中国边界规则将 admin_level=6 说明为县级行政区，并指出 `ref:admin:CN:*` 代码可能存在与维护边界有关的差异。此方案可附署名/ODbL，但仍要筛选和核验行政关系、中文名称、父级及代码；且全量下载较大，尚未下载。
- 另核对了两个较小的公开候选：china-village-boundaries 自述 CC0/WGS84，但同时明确原始出处和原始授权状态不明，故 CC0 声明不足以确认可复用原始数据；AreaCity 数据包约 13 MB、解压 130 MB 以上，混合民政/地名库与高德/腾讯来源，暂未找到边界数据独立许可证和稳定源 CRS 说明；两者目前都不满足来源可追溯门槛。

## 尚未执行

未改代码、未下载数据、未访问真实图库或 PhotoSearchData、未构建/验证/激活 `asia-cn-admin-v1`、未运行测试。不能据此将 P4-PLACE-CN 标为实现完成或 PASS。

## 解除阻塞所需

1. 确认是否接受 OpenStreetMap/Geofabrik 中国全量来源和约 1.5 GB 下载量，或提供另一个有许可/归属说明的可本地离线数据源；源坐标参考系需明确，非 WGS84 时需记录可审计转换。
2. 按 P4-PLACE-CN 推进此架构后置项时，补齐其要求的决策记录和 DS-H 全受影响面审查门禁；完成后方可验证定向数据包、浏览/搜索和东京回归。

## 来源核对

- frykit_data README: https://github.com/ZhaJiMan/frykit_data/blob/main/README.md
- frykit_data LICENSE: https://github.com/ZhaJiMan/frykit_data/blob/main/LICENSE
- frykit README 数据说明: https://github.com/ZhaJiMan/frykit/blob/main/README.md
- 高德行政区域查询文档: https://lbs.amap.com/api/webservice/guide/api/district/
- 天地图行政区划入口: https://cloudcenter.tianditu.gov.cn/administrativeDivision/
- OpenStreetMap copyright/license: https://www.openstreetmap.org/copyright
- OpenStreetMap China boundary tagging: https://wiki.openstreetmap.org/wiki/China/Boundaries
- Geofabrik China extract: https://download.geofabrik.de/asia/china.html
- Geofabrik GIS formats and coordinate reference: https://download.geofabrik.de/osm-data-in-gis-formats-free.pdf
- china-village-boundaries source/license/CRS declaration: https://github.com/thedavidweng/china-village-boundaries
- AreaCity source description: https://github.com/xiangyuecn/AreaCity-JsSpider-StatsGov
