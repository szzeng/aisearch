# 手机相册式地点整理

2026-10-01 用户明确要求补齐行政层级、GPS 自动归属、跨区域景区与层级浏览；取代上一卡“仅附近城市建议”的范围限制。本卡覆盖 geo/places、迁移、搜索投影、API、Vue 与对应测试；不扫描私人图库，不重复模型实验。

已读取当前状态、上张卡、架构/策略及地点相关实现。本次新增数据语义见配套 ADR；原 GPS、人工控制、操作历史、稳定 ID 及搜索权威边界必须保留。

交付：离线多边形数据安装/版本化、Shapely 空间索引、国家/省/市/区自动归属、行政层级与景区叠加、照片数/封面/逐级浏览、来源与覆盖说明、人工纠正优先。无 GPS 不猜地点，重叠边界不随意选一个。

验证：迁移保真/回滚、边界/空洞/重叠/跨区、人工锁/撤销/换包、查询与 FTS、前端构建/组件及隔离浏览器。按用户授权和策略 §5 运行完整受影响面，不重跑无关大模型/规模测试。JUnit 与实际命令汇总本卡。独立 DS-H 完整受影响面工程审查及 Sol 高风险聚焦终审待后续手动进行，不将实现者自检标正式 PASS。

## 交付和证据

实现见 [ADR-0014](../decisions/ADR-0014-offline-place-boundaries.md)。已安装到本机公开 benchmark 库，schema 16→17 自动备份迁移，原 2,000 张资产保留。运行端口仍为 8765；未扫描私人图库。两份边界 JSON 在应用数据目录 `geo/`，合成 GPS 界面证据为 `tmp/place-geography-preview.jpg`，测试服务器已关闭。

- 数据准备：`uv run --group geo-data python tools/places/prepare_boundaries.py --output <应用数据目录>/geo/china-boundaries.json`；西湖通过 `--osm-json <公开 Nominatim 导出 JSON> --output <应用数据目录>/geo/westlake-scenic.json` 转换。固定 mapchina 源 checksum、3,269 节点/2,901 区县、1 处有效性修复；OSM relation 13164471，1 个景区 MultiPolygon。源许可/年份/署名在包及 UI 中可查。
- 后端：`.venv/bin/pytest -q tests/unit/test_place_boundaries.py tests/integration/test_place_boundary_api.py tests/unit/test_p2_place_contracts.py tests/integration/test_p2_place_migration.py tests/unit/test_catalog_migrations.py tests/unit/test_p2_place_directory.py tests/integration/test_p2_place_api.py tests/integration/test_p2_place_page.py tests/integration/test_p2_place_resolver.py tests/integration/test_p2_search_authority.py tests/integration/test_p2_acceptance_search.py tests/integration/test_p3_person_place_interleave.py tests/integration/test_p4_02y_library_refresh.py tests/integration/test_p4_02y_i_source_build.py --basetemp=.pytest-tmp/geo-complete --junitxml=test-results/geo-complete.xml`：**140 passed，23.14 秒，0 skipped/error/failure**。首次检查发现迁移重建遗漏禁止删除触发器，已补回并通过回滚/守卫反例；目录响应明确升级 v2，相关契约断言已更新。
- 前端：`npm --prefix frontend test`：**15 passed**，JUnit `test-results/frontend.xml`；`npm --prefix frontend run build`：类型检查及生产构建通过。
- 静态：`.venv/bin/ruff check` 本卡所有新增/受影响 Python 模块、`git diff --check`、`UV_CACHE_DIR=.uv-cache UV_PYTHON_INSTALL_DIR=.python uv lock --check --offline` 均通过。
- 打包：`UV_CACHE_DIR=.uv-cache UV_PYTHON_INSTALL_DIR=.python uv build --wheel --out-dir tmp/geo-wheel` 成功；ZipFile 逐字节验证前端 dist 与 migration 0017，metadata 包含 Shapely。
- 浏览器：6 张隔离合成图片、真实边界包。中国 5 张→浙江 3 张→杭州 3 张→西湖区 2 张，独立西湖景区 2 张；南昌同名区不混入，无 GPS 样本不自动归类。照片详情正确显示行政链、景区及 GPS 边界依据。实际预览验证已安装两包、历史区划提示、许可和署名；公开 benchmark 无可归类 GPS，不伪造地点照片。

当前限制：mapchina 为年份未注明的历史区划（如杭州仍含下城区），不能称为最新全国完整区划；西湖景区只是已接入的一个公开范围，全国/全球 POI 库未提供。跨行政区景区机制由合成双市反例验证；未验证十万图地理查询性能、GPS 精度圆、Windows 冻结 GEOS 资源和独立工程验收。测试通过仅代表上述受影响面自检，不标正式 PASS。

下一卡：[前端与地点独立审查](frontend-review.md)。按仓库路由由用户手动切换 DS-H 做完整受影响面审查，随后 Sol high 仅复核迁移/人工锁/lineage/投影失效及未决高风险项；不要求重复模型实验。
