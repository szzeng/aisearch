# P4-PLACE-MAP-A — 实现与卡内验证

日期：2026-10-04。结果：实现完成，卡内验证完成；**独立 DS-H 全受影响面审查待做，因此不标 PASS**。范围严格限于 MAP；CN 未实施，本次没有访问真实照片库。

后续启动报错修复：schema 17 明确兼容 maximum=16 的旧地名包；定向 JUnit **64/0/0/0**，现存 GeoNames 45,217 条只读完整校验通过，包字节／mtime 未变，无需重下载。见[兼容修复报告](P4-PLACE-MAP-A-schema-compat.md)。实际应用重启验收及 DS-H 审查仍待做。

后续简审 5 项均已修复并独立定向复验通过，最新 JUnit **6/0/0/0**。本次仅为实际 JS 合成桩短审，不替代真实浏览器或完整 DS-H 审查。当前结论见[简审报告](P4-PLACE-MAP-A-review.md)文末，以下历史验证记录保留。

## 设计与实现

- 新增个人地图页 `/map` 和导航入口。页面采用 MapLibre GL JS **4.7.1** UMD 固定版本，本地随包提供 JS/CSS/worker/license；试用 6.11.2 ESM 时，当前内嵌浏览器对动态模块加载失败，故换为固定版本 UMD 并完成实际浏览器验证。聚合数字使用内建 circle layer，无远程字体。OSM 标准栅格瓦片、URL、attribution、显示 CRS 配置集中在 `map-config.js`，只在视野请求瓦片。
- GPS/资产仍以 catalog 原数据为准。迁移 0017 添加持久单调 `map_member_revision`、变更触发器及 bbox 读侧索引。地图成员要求 VALID、WGS84/ASSUMED_WGS84、当前 sha revision、ACTIVE 资产及至少一个 PRESENT 文件位置；asset_id 去重，(0,0) 合法。
- `/api/v1/map/summary` 返回 count、bounds 和 member version；`/assets` 使用固定 asset_id 降序的版本绑定游标分页；`/points` 在最多 5000 张时返回完整照片点，超过后生成最多 1000 个确定性网格，网格计数覆盖完整范围。跨日期变更线、世界范围、零坐标及网格分页都有处理。详情复用既有 `/geo`，并在失效/不可见时隐藏小地图。
- 浏览器在视野变化后 debounce 请求；照片点使用 MapLibre cluster；cluster/cell 可展开或查看精确成员，网格继续分页。当前会话恢复视野和列表时核对成员版本。地图与详情之间可以往返。

## 验证

定向 JUnit：

```text
pytest -q tests/integration/test_p4_place_map_api.py tests/integration/test_p4_place_map_300k.py --junitxml=test-results/p4-place-map-junit.xml
2 passed, 0 failed, 0 errors, 0 skipped (34.753 s)
```

20 个合成资产用例覆盖有效成员过滤、重复文件位置、重复坐标、(0,0)、无效/过期/缺失 GPS、非 PRESENT、跨 ±180°、无重复分页及成员变化使旧游标失效。30 万独立合成资产与有效 GPS 结果见 `test-results/p4-place-map-300k-timings.json`，有效 GPS 300,000；全范围、城市、小范围各 4 次（首次加三次重复），所有响应 count/grid sum 与完整总数一致，首页和第二页各 48 条且无重叠。此为本机 Python ASGI API 计时，非部署环境承诺：

| 范围 | 有效成员 | points 中位数 | assets 中位数 | points 字节 | 网格 |
|---|---:|---:|---:|---:|---:|
| 全范围 | 300,000 | 565 ms | 572 ms | 131,095 | 539 格，合计 300,000 |
| 城市 | 100,000 | 250 ms | 213 ms | 1,923 | 8 格，合计 100,000 |
| 小范围 | 28,547 | 73 ms | 66 ms | 15,491 | 67 格，合计 28,547 |

bbox 的 `EXPLAIN QUERY PLAN` 使用 `idx_asset_geo_map_bbox` 覆盖索引，资产哈希使用唯一索引，文件可见性检查使用 `idx_file_locations_asset`。完整世界范围为避免无选择性的 bbox 索引退化，强制走状态索引；计时仍包含 API/ASGI transport。

完成的其他卡内检查：定向 Ruff、Python compileall、`node --check`（map.js/app.js）、`git diff --check` 通过。浏览器使用隔离合成图库实际加载本地 MapLibre 和 OSM 瓦片，确认照片聚合、点击 cluster 展开、网格列表、详情小地图/“在地图相册中查看”、返回后地图列表仍为 5 张。此浏览器结果只证明本次环境的合成流程与瓦片可达。

## 未完成项

MAP 卡 §8 要求独立 DS-H 全受影响面工程审查，重点包含迁移/成员版本事务一致性、地图 SQL 筛选与分页、点/网格语义和地图到详情调用链。尚未进行，因此状态保持“实现与卡内验证完成 / DS-H 审查待做”，不标 PASS。模型切换由用户手动进行。CN 暂不实施；无全量套件、exe 构建、真实图库扫描或真实地理数据访问。


## 审查问题修复与简单复验（2026-10-04）

按[简审报告](P4-PLACE-MAP-A-review.md)修复四项：

1. `.gitignore` 仅放行 `web/vendor/maplibre-4.7.1/dist/` 下固定版本的 JS、CSS、worker；确认三项资源及 vendor license 均在 Git 状态可见，原有通用 `dist/` 构建输出忽略规则保留。
2. MapLibre cluster 展开级别超过 `clusterMaxZoom=16` 或已无法细分时，读取 cluster leaves 并将每位成员作为可点击照片入口；同坐标桩验证两位成员分别导航到 `/#/asset/17` 与 `/#/asset/18`。
3. points 与 assets 的成员版本必须相等才更新标题/地图/列表，最多重读 3 次；地图移动即清除旧分页上下文，所有初始、cell、更多页和焦点响应核对视野请求序号/成员版本，旧游标失败会刷新当前视野。
4. MapLibre 缺失或地图构造/WebGL 初始化失败时直接请求无 bbox 的有效成员列表；该模式下同样可以继续请求后续 cursor 页，查看详情无需 map 对象。

验证命令与结果：

```text
.venv/Scripts/python.exe -m pytest -q .tmp/p4-map-review/test_review.py --basetemp=.pytest-tmp/p4-map-review --junitxml=test-results/p4-place-map-review-junit.xml
4 passed, 0 failed, 0 errors, 0 skipped
node --check src/photo_search/web/map.js  # passed
git diff --check  # passed
```

四个 JS 桩用例覆盖版本不一致拒绝发布、缺组件时首屏与续页请求、同坐标成员详情入口，以及旧视野迟到响应不覆盖新视野。桩测试不模拟完整 WebGL；本次未重跑 30 万计时、全量套件或浏览器矩阵。未访问真实图库。DS-H 全受影响面审查仍是独立待办。


## 详情地图定位回归（2026-10-04）

依据简审末尾新增的定位反例，修正 `/map?asset_id=...` 的异步时序：独立保存聚焦意图，单资产响应与当前 points/assets 成员版本比对；不依赖地图 load 前后的请求序号。焦点响应早到时待首个有效范围快照版本就绪后定位，晚到则与已发布版本核对；版本变化时有限重试。修正测试夹具的早到顺序，使它先返回单资产响应、再触发 MapLibre load。早/晚顺序定向 JUnit **2/0/0/0**；前四项回归另为 **4/0/0/0**。测试只用隔离 JS/DOM/MapLibre 桩，没有重新做真实浏览器往返或 30 万计时。
