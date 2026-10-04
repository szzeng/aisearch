# P4-PLACE-MAP-A — 个人照片地图相册

日期：2026-10-04。状态：**READY FOR IMPLEMENTATION — 开工核对完成，功能与性能待验证**。核对：[短报告](../reports/P4-PLACE-MAP-A-preflight.md)。

定位：个人使用的小型 Google 相册式地图浏览。**本卡先做，直接使用已有 GPS，不依赖 P4-PLACE-CN。** 一张卡完成地图相册与详情小地图。

## 1. 最小体验

```text
地图：相近照片聚合成带数量的圆点，点击放大
  ↓ 拖动 / 缩放结束
当前范围 · N 张照片
照片网格：分页浏览 → 点击进入现有详情 → 返回保留地图现场
```

详情有有效 GPS 时显示小地图与“在地图相册中查看”；无有效 GPS 时隐藏地图区。中国行政区任务完成后，仅复用其地点文字，不改变地图坐标或成员规则。

## 2. 地图与底图

- 使用 MapLibre GL JS、GeoJSON source 与内建 clustering；脚本/CSS/所需 worker 固定同一版本并作为项目静态资源提供。聚合数字使用该版本支持的本地字体或随包字形，不另依赖远程字体服务。
- V1 首选 OpenStreetMap 标准在线栅格底图：`https://tile.openstreetmap.org/{z}/{x}/{y}.png`，MapLibre 使用 raster source 叠加照片点。保留 tile URL 配置与 attribution，遵守浏览器缓存/Referer 要求；只请求当前视野，不批量下载或预取。先在本机验证可达性，失败可换同坐标体系的 provider，V1 不做多底图。
- 底图失败、断网或 WebGL 不可用时提示“地图暂不可用”，保留当前 bbox 的网格；尚无视野时按全部有效 GPS 成员分页，不能误报“没有照片”。
- 无 GPS 照片时显示默认视野与“当前图库没有带位置的照片”。

**个人使用例外（用户已明确）**：允许在线瓦片、地图位置/bbox URL 和新增地图坐标响应，覆盖 V3.0 §4.5.2 的在线瓦片限制及 ADR-0005 对本卡的坐标限制；不增加在线底图确认、隐私开关或隐私专项验收。API 复用已有会话机制；不接在线逆地理。

底图设置集中保存 `provider/tile_url/attribution/display_crs`，地图展示与照片成员查询分开。V1 仅实现 OSM/WGS84；未来高德须另接官方服务/Key、WGS84↔GCJ-02 展示与视野适配，**不能只换瓦片 URL**。数据库/API/分页始终保留 WGS84，不预做高德转换代码。

## 3. GPS 与成员规则

只读 `asset_geo_facts`，统一用于详情、地图点、网格与计数：

```text
status = VALID
datum_status in (WGS84, ASSUMED_WGS84)
latitude / longitude 为有限有效值
input_revision 匹配当前资产 sha256
至少一个 PRESENT file_location，并满足现有可见性规则
```

按 asset_id 去重，多个文件位置只算一张；`(0,0)` 合法。使用 `/geo` 的 `normalized_wgs84`（数据库 latitude/longitude），GeoJSON 顺序为 `[longitude, latitude]`，不使用 raw_gps 或地点中心。投影限幅只影响展示，不写回 GPS。人工地点改名/取消不移动地图点；地点文字仍遵守人工优先。不重新读 EXIF、不改原图 GPS、不触发向量/OCR/人脸或批量缩略图重建；缩略图/预览按现有入口按需读取。

## 4. 小接口

优先复用现有 `GET /api/v1/assets/{asset_id}/geo` 读取详情坐标，地点文字复用现有 place 摘要，不重复新增 /geo 或改变其旧响应。

新增或等价实现：

| 接口 | 用途与最小返回 |
|---|---|
| `GET /api/v1/map/summary` | 有效成员 total、初始 bounds、member_version；没有 GPS 时 bounds 为空 |
| `GET /api/v1/map/assets` | bbox 网格分页：assets（asset_id、已有 capture_time、现有缩略图/详情入口）、total、next_cursor、成员版本 |
| `GET /api/v1/map/points` | bbox/zoom 的 GeoJSON、mode（photos/grid）、完整 total、成员版本；照片点含 asset_id，聚合点含 cell_id/asset_count/cell_bounds |

bbox 参数为 `west/south/east/north`；网格 limit 默认 48、上限 100，固定按 asset_id 降序分页。校验有限数值、纬经度范围与 south ≤ north；west > east 表示跨 ±180°，全世界范围显式使用 [-180,180]，处理地图 world-wrap 后的范围。

网格与点查询共用同一成员谓词；cursor 绑定 bbox/排序/成员选择与 `member_version`。实现持久单调地图成员版本，GPS 事实、资产内容及文件位置归属/可用性变更与版本同事务提交；可用加性迁移/触发器，不改原事实含义。现有 catalog_filter_revision 单独不足，不能以行数或最大时间戳代替。所有接口在同一读快照计算各自 count/数据/版本；客户端批次版本不一致或游标过期即重载，重启不续用旧会话游标。

## 5. 点数与联动

- **地图点与网格分页分开**：不能拿第一页 48 张作全部地图点。
- 范围内 ≤5000 张时返回完整照片点并使用 MapLibre cluster；超过时由后端按 zoom 做简单网格聚合，自适应粗化至最多 1000 个 cell。每张照片只计入一个 cell，所有 asset_count 之和等于 bbox 完整 total，不抽样、不因超限让地图变空。聚合点单独显示数量，不再送入普通照片 cluster。
- 点击照片 cluster 或聚合 cell 放大；同坐标照片无法继续分开时打开成员分页列表。photos 模式用当前 source 的 cluster leaves 精确 asset_id，不能用包围盒代替成员；grid 模式由 `/map/assets` 接收 cell_id、实际聚合级别、原 bbox 与成员版本，按同一半开归属规则分页（世界最外边界闭合），列表 total 必须等于该 cell 的 asset_count。不把聚合展示位置当作拍摄位置；单照片点打开现有详情。
- 仅 `moveend` 后约 250ms debounce 刷新；取消或忽略旧请求，地图点/总数/网格属于同一视野。视野变化清空旧分页；加载更多只更新网格。
- 从详情进入用 `/map?asset_id=...` 读取 /geo 并聚焦该照片；地图内部允许 lat/lng/zoom 定位参数。初始/聚焦 zoom 合理限幅。
- 普通打开先恢复同一浏览器会话的视野；首次使用 summary bounds 初始化。bounds 跨日期变更线用短经度区间，单坐标设置合理 zoom。
- 新增 `/map` 页面与现有导航入口；详情复用 `/#/asset/{asset_id}`，增加独立 map 返回来源，保留现有搜索/普通浏览返回行为。恢复视野、已加载网格与滚动位置前核对成员版本，过期则保留视野重载；首次全国/全球视野也能看到聚合数量并继续放大。

## 6. 小样本验证与验收

本卡按个人使用收口：定向 pytest（JUnit XML）、Ruff、JS 语法/diff-check、一次浏览器闭环及下述 30 万合成数据简单计时；不追加全量套件、多浏览器矩阵、大规模压测矩阵或 exe 构建。

约 20 个合成资产覆盖 A/B 区域、范围外、无/无效/过期 GPS、非 PRESENT、重复文件位置及同坐标照片：

1. bbox、去重计数与分页无重复/遗漏；零坐标、经纬度顺序、±180°、非法参数及 GPS/文件位置变更后的旧游标正确处理。
2. 小夹具降低切换阈值，验证 grid/photos 两种模式、聚合计数总和/完整 total、同坐标列表和网格分页；旧请求不覆盖新视野。
3. 有 GPS 的详情地图出现、无 GPS 隐藏；断网/底图失败有提示、照片仍可浏览。
4. 浏览器：地图 cluster → 拖动/缩放改变网格 → 单照片/同坐标列表 → 详情小地图 → 回地图恢复现场。

默认只用合成资产，真实图库须另获用户授权。所列功能、验证和审查完成后，只标 **PASS — 本机个人地图相册与小样本验证**；浏览器闭环未完成不能标 PASS。

## 7. 30 万照片的基本性能要求

- 成员过滤、count、分页与聚合在 SQLite/后端完成，不把 30 万条坐标发送浏览器，不先读全库到 Python 再截取 48 张。网格使用 keyset 分页，地图点载荷受上述上限约束。
- bbox 查询检查 EXPLAIN QUERY PLAN，按结果补必要索引；普通索引仍明显慢时，允许最小 SQLite RTree 加性投影，不另建 GIS 服务。GPS 原表仍为权威，投影变更/修复不遗漏新成员。
- summary 的 bounds/总数及可复用查询结果按成员版本缓存；成员变化失效。分离 assets/points 接口不应重复无必要的全库计数。
- 一次建立项目临时目录内的 30 万合成资产/GPS 夹具，覆盖分散、密集、同坐标和跨日期变更线；记录实际有效 GPS 数。通过产品 API 对全范围、城市、小范围各做首次及 3 次重复查询，核对完整 total/聚合数量/分页，记录后端耗时与响应大小。可复用 CN 夹具，不扫真实照片、不做长时间压测。
- 只承诺实测结果；查询明显卡顿须修复或记录限制，20 张功能测试不能替代 30 万计时证据。

底图依据：[OSM 瓦片使用说明](https://operations.osmfoundation.org/policies/tiles/)。该服务可用性为 best-effort，当前网络可达性须在浏览器闭环实际验证。

## 8. 执行、阅读与报告

实现推荐 GPT-6 Luna / Medium，同一会话完成实现、调试与定向验证。开工做一份短设计记录写入报告，确认底图、坐标展示、bbox/分页/点数规则；独立 DS-H 核对本卡全部改动与直接调用链，Sol high 仅聚焦坐标/成员契约，不增加隐私专项。模型均由用户手动切换。

只读 V3.0 §4.5.4、§10、§12.4 地点相关项；V3.1 §2.4、§3.2、§10/11；ADR-0005 的相关条款（上述个人使用例外优先），以及现有 geo/资产 API、详情/网格和测试。不读全仓。

短报告 `docs/reports/P4-PLACE-MAP-A.md` 记录底图/版本配置、接口与点数上限、验证命令及 JUnit 汇总、浏览器闭环、30 万简单计时/查询计划、审查结论与未覆盖项。

不做 Timeline、路线/轨迹、地图搜索/POI、离线底图、无 GPS 位置推测、GPS 编辑、GIS 服务或独立空间索引专项。允许本卡必需的加性成员版本/读侧索引；若需改资产身份、GPS 内容或人工地点状态机，停止扩大实现并记录具体冲突。
