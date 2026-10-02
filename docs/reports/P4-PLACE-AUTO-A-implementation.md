# P4-PLACE-AUTO-A — 实现与验证报告

日期：2026-10-02
基线：`696a06e`。既有 P4-EMBEDDING-ARCH 独立审查继续待做，本报告不改变其状态。真实图库未访问。

## 结果

**PASS — 本机个人地点相册与小样本验证。** 完成只读派生的城市/省份/国家相册、相册照片分页、地名搜索入口、单张人工更正及有界缺项补齐。人工地点与人工取消仍由既有 assignment API 控制；GPS 结果只是“GPS附近城市”浏览提示。

## 设计与改动

- `GET /api/v1/places/albums?kind=city|province|country&name=...` 返回有照片地点卡片、数量、封面和来源；`GET /api/v1/places/albums/{kind}/{key}/assets?limit=...&cursor=...` 按稳定 asset_id 分页，不受语义 Top-K 截断影响。`POST /api/v1/places/albums/backfill` 每次最多分别处理100个 GPS 缺项与100个当前活动包解析缺项。
- 浏览集合仅含 PRESENT 文件位置且以 asset_id 去重。MANUAL_ASSIGNED 取人工地点；MANUAL_UNASSIGNED 阻止自动分组；只有当前 sha256 的有效 GPS fact、WGS84/ASSUMED_WGS84、活动包下有效解析、ACTIVE 城市候选、距离≤50km、rank 1 不等距且未被人工排除时才进入附近城市。被排除的首选不会顺延。无 GPS、无效/未知 datum、过期事实、缺包、并列或超距都不自动展示。
- 包的可选 `index` 为 `{schema_version, provider, source_snapshot, city_to_admin1}`；城市外部 ID 映射到 `{id,name,aliases}`。校验来源快照与省级键；包准备器在 GeoNames `admin1CodesASCII.txt` 存在时生成映射并计入 checksum，旧包无索引仍可看城市/国家。该映射是来源附加资料，不宣称精确行政边界。
- 游标绑定 catalog filter revision、suggestion revision、活动包/内容 checksum、相册 kind 和 key；读取期间 revision/package 变化返回 409，要求重载。卡片混有人工与 GPS 资产时标注混合来源。
- 地点页新增相册筛选、国家/省份/城市卡片、预览、照片内联更正和缺项处理入口；主搜索的查询文本仅产生独立地点入口，不改变普通语义/FTS 查询。回填复用现有 GPS/resolver pipeline stages，不扫描来源或重建向量/人脸/OCR。

## 验收证据

- 合成集成测试构造24张 JPEG 资产（含重复文件路径）及边界/坏GPS/等距/人工控制事实，验证地点别名搜索、国家/省份聚合、唯一分页、50km边界、手动归类/取消/恢复，以及回填重复调用不会重复生成结果。
- 定向测试命令（按任务卡扩展到受影响的地名包读取/准备端点）：

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
uv run --no-sync pytest -q tests/integration/test_p4_place_auto_flow.py tests/integration/test_p2_place_api.py tests/integration/test_p2_place_resolver.py tests/integration/test_p2_search_api.py tests/unit/test_p4_02y_p_geonames_prepare.py tests/integration/test_p2_place_dataset_api.py --junitxml=test-results/p4-place-auto.xml --basetemp=.pytest-tmp/p4-place-auto
```

结果：退出码 0；**40 tests / 0 failures / 0 errors / 0 skipped**，JUnit：`test-results/p4-place-auto.xml`。另外 `uv run --no-sync ruff check src tests tools` 通过，`git diff --check` 通过。
- 浏览器运行在本机 loopback 的临时合成图库：24张照片；`/search` 查询 `Hangzhou` 出现“杭州 · 中国 · 24 张”地点入口，进入 `/places?album_kind=city&album_key=2` 展示相册和网格、打开预览；将一张照片地点改为上海后，卡片变为上海1张、杭州23张，页面重载后结果保持。将首张照片的地点输入留空并保存，触发人工未归类；随后刷新页面，相册仍为23张且首张可见照片为照片 #2，确认该照片没有被 GPS 自动加回。操作只写入临时合成图库。
- 沙箱首次创建 pytest basetemp 报 `WinError 5`；未更改 ACL，按仓库规则走正常 Windows 权限重跑并成功。没有读取、修改或回填真实照片库；真实包省份覆盖、规模性能和安装包不在本卡验证范围内。
