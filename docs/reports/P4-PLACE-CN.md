# P4-PLACE-CN — 实现与验证

2026-10-05。状态：**IMPLEMENTED / 定向验证与合成浏览器闭环通过 / 独立审查待做**。实现基线 `856f26e`，不推送。保留并行人物任务变更；当前 schema20 中 CN 仅拥有0018，0019/0020属人物任务。最新[兼容修复及用户测试步骤](P4-PLACE-CN-schema20.md)含51项定向证据，覆盖并行schema20的实际兼容缺口。

## 来源与产物

用户已下载 PBF 与上游 MD5，此前 TLS 下载阻塞已解除。输入 `.tmp/p4-place-cn/source/china-261003.osm.pbf`：1,603,439,115 bytes；MD5 `be04853d80940e700d2fda790c415d97` 完全匹配，SHA-256 `c3c03b41b6a72d38f70558b9ab64bbc8a0baf92e6e99d1b5f12f95e208ec71d4`。提取先按官网尺寸与冻结 SHA 验证，用户提供 MD5 后再次读取完整源文件同时核对 MD5/SHA，来源报告明确记录事后上游校验。数据截至 2026-10-03T20:20:50Z，WGS84，ODbL-1.0 / © OpenStreetMap contributors。

- 准备产物：`.tmp/p4-place-cn/prepared-261003/`，28 省级、315 地市、2,670 区县；`cn-admin.jsonl.gz` 64,555,134 bytes，SHA-256 见 source-report。拒绝：390 INCOMPLETE_RELATION、16 NO_CHINESE_NAME、230 NO_UNIQUE_CN_PROVINCE。不是全国完整覆盖，也未替换来源补造边界。
- 复合包：`.tmp/p4-place-cn/composite-261003/`，ID `geonames-asia-cities500-v1-cn-c3c03b41ffe4cbe3`，共 76,804,462 bytes。原 GeoNames 45,217 条及原内容文件字节保留，新增 3,013 行政区与独立来源/许可；274 条旧城市 ID 的精确名字＋源点包含映射。仍为单活动包，原 GeoNames 包保留回退。
- 真正组面与运行解析均通过：浙江省·杭州市·西湖区、上海市·黄浦区、广东省·深圳市·南山区、重庆市·渝中区。东京测试点仍走活动复合包中的 GeoNames，JP 的最近候选为原数据中的 Hatchōbori；不将附近点解释成行政边界确认。

## 实现范围

[ADR-0016](../decisions/ADR-0016-cn-admin-boundaries.md) 是实现方案，独立审查后才能正式冻结。新增 prepare/compose 工具；运行时显式固定 Shapely 2.1.2，places-build 固定 Osmium 4.3.1，uv.lock 已更新，未下载 AI 模型。

`0018_cn_admin_facets.sql` 新增稳定行政身份、每包目录、旧城市映射、不可变派生结果和持久更新进度，不改历史 migration/旧人工协议。严格 bbox/STRtree＋contains；缺 city、内洞/边界/重叠返回可靠上级或 unknown。结果绑定当前 GeoFact/input、包和 geo-cn-admin@1；机器结果从不写人工归属。有效旧排除无法证明新 provider 对应时保守阻止全部 CN 自动成员，人工取消/指定始终优先；只读 `/assets/{asset_id}/admin-place` 返回当前父链、机器/人工来源以及 ACTIVE_EXCLUSION_CONSERVATIVE 等原因，不写人工历史。机器区县暂不支持直接确认成旧 Place，人工更正仍走原 city/custom 入口。

省/市/区县浏览与搜索共用 CN SQL 成员规则，行政 key 为 cn:<稳定整数>；计数 SQL 聚合，照片及纯地点搜索用 keyset，内容搜索沿用权威 ID 集后再检索/排序。普通 FTS place_text 未增加机器地址。分页绑定人工/建议/包指针/GPS位置成员版本。相册冷读只核对文件与原 INDEX，不加载几何；状态页对已完整注册的包重核 manifest/所有文件 hash/registry 身份，也不重解析几何，纯文件工作后台执行后再复核 catalog 快照；中国更新的冷加载和点面计算在 CPU 线程，SQLite 在 API 线程短事务提交。

地点页新增区县与“更新中国地点/暂停”入口，每批最多100张已有有效 GPS，进度/上界/generation 持久保存；关页/暂停后继续，完成后点击新一轮；每批从高水位推进，不重读 EXIF或处理向量/OCR/人脸/缩略图。已走过资产若运行中 GPS 改变，旧结果立即不可见，下一轮更新覆盖它。新包校验失败不切活动指针；回退/激活使旧游标过期。

兼容桥明确扩至 schema18 的 max16/17，以及并行仅人脸派生增量 schema19 的 max16/17/18；schema20新增明确允许max16/17/18/19，0020只修改人脸派生；未来21不通配。包验证/激活/回退先在事务外读文件，并核对不可变 registry 的身份/版本/provider/checksum，事务内再核对状态；回退还绑定活动指针 revision，竞争切换不发布旧目标。

## 已执行证据

1. 提取工具定向 JUnit：10/0/0/0（`test-results/p4-place-cn-prepare-junit.xml`）。实际 area assembler 小 OSM/XML/PBF，洞/缺节点/MultiPolygon/重叠父级/完整包含/输入 hash guard 等。
2. 首轮 CN＋兼容＋原相册/搜索/恢复/包 API 定向 JUnit：44/0/0/0（`test-results/p4-cn-focused.xml`）；后续 generation/短事务版本 CN＋包 API＋兼容 30/0/0/0（`p4-cn-functional-final.xml`）。最终版本定向 JUnit **75/0/0/0**（`test-results/p4-cn-final.xml`，退出0），覆盖源工具、实际JS、CN、schema兼容、原相册/搜索、恢复、包API、GeoNames resolver和地图API。中间收尾子集18/0/0/0不与最终75重复相加。
3. 30 万合成与真实公开包 JUnit：2/0/0/0（`test-results/p4-cn-evidence.xml`，233.518秒）。统一实际产品 API：30万派生成员、1万第二来源副本；省/市/区县各首次＋3次重复计数/第一页/下一页，均 total=300000，第一页48，下一页48，不重复。计时 `test-results/p4-cn-300k-timings.json`：最终轻量INDEX复测省1.42–1.48秒，市1.34–1.54秒，区县0.66–0.78秒；首/次页0.57–0.74秒。是全部集中在一个行政区的合成 SQL 计时，不是30万真实边界解析、长期P95、真实图/模型/浏览器延迟。复测 JUnit **1/0/0/0**（`p4-cn-timing-final.xml`，退出0，90.068秒含夹具构建）。
4. 真正公开复合包 stage/validate/activate、四地区运行解析、东京和100张合成GPS真实几何批次全部通过（`test-results/p4-cn-real-source.json`）。批次（不含冷加载）0.0252秒，约3968张/秒；没有测全库解析，不把吞吐简单推算当作实测。真实包冷校验需完整读取/验证几何，首次更新会等待；普通相册不做点面计算。
5. 浏览器：隔离107张合成JPEG，真实页面点击更新→区县西湖区107张/浙江省·杭州市/机器标签→48张/续页96张→预览打开关闭；普通搜索“西湖区”20/107，组合“西湖区的日落”3个FakeEncoder向量命中（不代表真实模型）。最终实际JS有界桩覆盖城市/区县迟到响应、旧照片响应、暂停当前批/继续/完成后新一轮；浏览器发现的旧响应覆盖已修复，脚本v4后复验。暂停未做真实浏览器长任务演练，不夸大为全库中断验收。截图 `test-results/p4-cn-browser.jpg`，服务/浏览器仅使用合成目录。
6. Ruff/Node 与最终 scoped diff-check 通过。所有 uv 缓存 `.uv-cache`，pytest `.pytest-tmp`，JUnit `test-results`。

验证命令（直接 `.venv/Scripts/python.exe`，通过正常 Windows 权限路线，不改 ACL）：

```text
-m pytest -q tests/unit/test_p4_place_cn_prepare.py --basetemp=.pytest-tmp/p4-place-cn-prepare-1005 --junitxml=test-results/p4-place-cn-prepare-junit.xml --tb=short
-m pytest -q tests/integration/test_p4_place_cn.py tests/integration/test_place_dataset_schema17.py tests/integration/test_p4_place_auto_flow.py tests/integration/test_p4_place_search_flow.py tests/integration/test_place_package_startup_recovery.py tests/integration/test_p2_place_dataset_api.py --basetemp=.pytest-tmp/p4-cn-focused --junitxml=test-results/p4-cn-focused.xml --tb=short
-m pytest -q tests/integration/test_p4_place_cn_300k.py tests/integration/test_p4_place_cn_real_source.py --basetemp=.pytest-tmp/p4-cn-evidence-2 --junitxml=test-results/p4-cn-evidence.xml --tb=short
-m pytest -q tests/unit/test_p4_place_cn_prepare.py tests/unit/test_p4_place_cn_ui.py tests/integration/test_p4_place_cn.py tests/integration/test_place_dataset_schema17.py tests/integration/test_p4_place_auto_flow.py tests/integration/test_p4_place_search_flow.py tests/integration/test_place_package_startup_recovery.py tests/integration/test_p2_place_dataset_api.py tests/integration/test_p2_place_resolver.py tests/integration/test_p4_place_map_api.py --basetemp=.pytest-tmp/p4-cn-final-6 --junitxml=test-results/p4-cn-final.xml --tb=short
-m pytest -q tests/integration/test_p4_place_cn_300k.py --basetemp=.pytest-tmp/p4-cn-timing-final --junitxml=test-results/p4-cn-timing-final.xml --tb=short
# 真实公开包证据项设置 P4_CN_COMPOSITE=.tmp/p4-place-cn/composite-261003
# 与 P4_CN_BASE=D:/SZ/project/PhotoSearchData/places/datasets/geonames-asia-cities500-v1
```

## 收尾与下一步

并行 pipeline 编辑期间曾重复 keyword，后来恢复，最终定向执行成功。收尾回归还发现状态校验从错误层级读取 provider，已修复并重跑75项全部通过；早期失败没有当作成功。独立 [DS-H 全受影响面审查卡](../tasks/P4-PLACE-CN-review.md) 已生成，之后 Sol high 仅聚焦层级、人工保护、包切换/派生身份和未关闭发现；不标整卡PASS。未跑全仓套件、exe、真实模型或全国准确率评估。

**候选包已构建，但未导入/激活真实运行数据。** 没有打开真实 catalog、扫描照片、提交/推送；只读公开 GeoNames 参考包。审核后使用当前源码导入复合包，再在「地点整理」点击「更新中国地点」，即可按已有有效GPS浏览省/市/区县；地图底图仍使用既定OSM，PBF并非瓦片底图。示例命令仅记录，未执行：

```powershell
.venv/Scripts/python.exe tools/places/manage_dataset.py --data-root D:/SZ/project/PhotoSearchData import --source .tmp/p4-place-cn/composite-261003 --activate
# 回退：同一工具、同一 --data-root，使用 rollback；旧包保留
```

运行时只需约76.8MB复合包，不用1.6GB PBF。原始文件本轮保留，未自动清理。首次导入/完整校验与首次中国更新冷加载会读完整边界，有等待；后续普通浏览不做点面判断。30万SQL计数存在约1.5秒延迟，是本机合成测量；没有测30万真实GPS全库重解析，也不宣称全国完整覆盖。

## 2026-10-08 范围提交核对

按用户“简单提交/继续提交”，仅包含中国地点及schema20兼容规则。共享routes/models/status按片段选择；提交schema仅18和0018迁移，人物schema19/20及其迁移、队列/模型/UI均保留工作区，不能将缺少人物迁移的schema20装进地点提交。

将HEAD与这30个选定文件组装为项目临时快照，使用现有Python环境但将PYTHONPATH指向快照src，直接验证独立地点提交：JUnit **82/0/0/0**（`test-results/p4-cn-commit-snapshot.xml`，退出0）、Ruff通过。原75项范围加新增schema20边界/损坏包用例，不重跑30万/源提取/真实模型或实际图库。命令为报告最终定向范围，临时快照cwd `.tmp/p4-cn-commit/snapshot`，`--basetemp=D:/SZ/project/aisearch/.pytest-tmp/p4-cn-commit-snapshot --junitxml=D:/SZ/project/aisearch/test-results/p4-cn-commit-snapshot.xml`。原始PBF、准备/复合包、临时快照、生成JUnit均不进入源码提交；原测量、实际工作区schema20与独立审查待做边界仍按前述报告。
