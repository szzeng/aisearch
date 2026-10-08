# ADR-0016 — 中国行政边界与现有地名包共存

日期：2026-10-05。状态：**IMPLEMENTATION PROPOSAL / 独立 DS-H 与 Sol 聚焦终审待做；尚未正式冻结**。

适用 P4-PLACE-CN，沿用 ADR-0005／ADR-0015。真实源与四地区门禁已通过，以下是本次实现使用的最小契约。

1. **来源与构建**：Geofabrik `china-261003.osm.pbf`，1,603,439,115 bytes，数据截至 2026-10-03T20:20:50Z；上游 MD5 `be04853d80940e700d2fda790c415d97` 与本地完整文件一致，SHA-256 `c3c03b41b6a72d38f70558b9ab64bbc8a0baf92e6e99d1b5f12f95e208ec71d4`。ODbL 1.0 / © OpenStreetMap contributors / WGS84。PyOsmium 4.3.1 组面、Shapely 2.1.2 验证；原始 PBF 仅在项目临时目录用于构建，运行包只含行政边界。28 省级、315 地市、2,670 区县，压缩边界 64,555,134 bytes；缺 relation/中文名/可靠父级均记录拒绝，不补造来源数据。四个公开内点的名称及父链全部通过，不表示全国完整覆盖。
2. **包共存**：一个不可变复合包，原 GeoNames 内容文件逐字节保留，主 v1 manifest/source/INDEX 继续只描述原城市点内容。新增 `cn-admin/manifest.v1.json` 冻结独立 `photo-search-cn-admin-addon.v1` / `cn-admin-facets.v1` / WGS84，明确引用 data、source-report、ODbL 文件；数据及报告用现有允许的 INDEX role 声明，许可用 LICENSE role，各文件均纳入顶层 checksum。新加载器明确分开原 INDEX 与该版本化 addon，不能靠文件顺序取代 GeoNames INDEX。顶层 record_count 仍是 GeoNames 的记录数，行政数量单列。复合包要求 schema≥18，单活动指针原子切换，旧包保留回退；国外继续活动复合包内的 GeoNames。
3. **派生契约**：增量 `0018_cn_admin_facets.sql`，不改历史 migration/Place 枚举/人工状态机。独立稳定正整数 `admin_id` 对应 `(osm-admin,relation/<id>)`，每包保存名字/别名及省、市父级，允许真实缺 city。不可变结果按 asset/dataset/geo_fact/input_revision/`geo-cn-admin@1` 唯一，三个可空 facet ID + MATCHED/UPPER_ONLY/UNKNOWN/BOUNDARY_OR_OVERLAP；严格 contains、bbox/STRtree 预筛，不容差猜边界。省/市/区县相册沿用 kinds，行政 key 为 `cn:<admin_id>`，每行政投影携带独立 contract；`/places/admin/capabilities` 显式声明能力与不支持人工区县确认。旧城市 key/Place API 保持原义。
   - MANUAL_ASSIGNED 不使用资产 GPS 补充行政区；只在 GeoNames **原始地点点位唯一包含且源名字/别名精确匹配**时，把旧稳定 city ID 映射至行政市/省显示（无 district），否则继续旧人工地点视图。不改人工名称/父级/ID。MANUAL_UNASSIGNED 永不自动加入。任何未证明对应的有效旧排除均保守阻止 CN 自动成员，只读 `/assets/{asset_id}/admin-place` 解释原因为 ACTIVE_EXCLUSION_CONSERVATIVE（动态查询，不篡改不可变几何结果）；排除一直从权威表读取，升级/回退不清空它。
   - 中国城市点不能代替缺失行政边界：复合包的原 CN 城市自动项不加入省/市行政浏览，未知留空，可靠上级独立显示。港澳等仍沿原 HK/MO 路线。国家浏览保留原 GeoNames/人工路径，不能称作边界确认。
4. **切换与执行**：验证完整包及不可变 registry 内容后才激活，物化只读 facets 与原活动指针同事务。完整验证/激活/回退的文件验证在写事务外，回退发布再次核对指针revision/目标状态/checksum；状态页只核对已完整注册包的manifest及全部文件字节/registry，不重解析几何，后台文件验证后复核catalog快照。明确兼容桥：schema17允许max16；schema18允许max16/17；并行 schema19仅增加人脸队列/批次，允许max16/17/18；schema20仅增加人脸坐标帧/自动授权与历史批次索引，允许max16/17/18/19；未来21不通配。包本身不重写。相册/内容检索共用 CN SQL 成员谓词，普通 FTS 的 manual-only place_text 不变；计数用 SQL 聚合，照片/纯地点搜索用 asset_id keyset。游标绑定人工/建议/活动指针及 GPS/位置成员版本。
   - “更新中国地点”每请求最多100张已有当前有效 WGS84/ASSUMED_WGS84 GPS；几何计算在 CPU 后台线程，SQLite 在原 API 线程短事务发布。持久高水位/上界/generation 避免每批从头扫描，重复输入复用结果；暂停/关闭页面后再次点击继续，完成后点击才新开一轮以覆盖新增/变化 GPS。运行中更改已走过的 GPS 需下一轮重查，当前旧结果立即从成员查询排除。包切换、竞争重启或迟到输入拒绝旧提交；不读 EXIF，不建图文/OCR/人脸/缩略图。

数据准备是工具，复合包是候选产物；本轮仅在隔离合成 catalog 验证，不修改真实图库/catalog 或自动安装运行数据。实际证据和未覆盖项见 [任务报告](../reports/P4-PLACE-CN.md)。独立审查完成后才能正式冻结与标整卡 PASS。

来源：[Geofabrik China](https://download.geofabrik.de/asia/china.html)、[中国层级](https://wiki.openstreetmap.org/wiki/China/Boundaries)、[OSM 许可](https://www.openstreetmap.org/copyright)。
