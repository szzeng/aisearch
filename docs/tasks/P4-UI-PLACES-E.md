# P4-UI-PLACES-E — 地点照片、地图与整理工具

> **2026-10-13 新视觉参照：** [UI 2.0 全页面索引](../mockups/ui2/README.md)；本卡对应 [06-places](../mockups/ui2/06-places.svg)、[07-place-album](../mockups/ui2/07-place-album.svg)、[08-map](../mockups/ui2/08-map.svg)。最新 SVG 是仓库内的轻量静态效果稿；旧版 PNG 仅作导航/色彩对照，不得据旧图删掉时间轴能力。产品数据契约、布局取舍及真实功能以 [UI 2.0 规范](../plans/P4-UI-TIMELINE-V2.md) 与本卡正文为准。此处并非实施截图。


日期：2026-10-10；2026-10-13 UI 2.0 修订。状态：**PLANNED / 未实施**。规划收口 HEAD `c19749a`；依赖 [A](P4-UI-SHELL-A.md)、[CORE](P4-UI-TIMELINE-CORE-A.md)、[B](P4-UI-PHOTOS-B.md)，现有地点工程结果只复用未改变部分。

推荐 GPT-6 / High；视觉 A 级，涉及新增地点日期只读查询按 B 类受影响面复核，独立 DS-H 覆盖完整受影响展示/入口与原写请求链。

## 视觉参考（执行前必须看图）

打开[地点页模拟图](../mockups/P4-UI-PERSONAL-places.png)与[地图页模拟图](../mockups/P4-UI-PERSONAL-map.png)，再查看[HTML草图](../mockups/P4-UI-PERSONAL.html#places)的地点/地图区域及[参考规则](../mockups/README.md)。实际看图后实施相册/地图局部切换、地点卡、照片区及折叠工具。示意地图和数字不定义新的聚合/统计，不改真实地图数据来源；报告保存对应产品合成截图对照。

## 现状盘点（备份参考）

[已有页面信息盘点](../plans/P4-UI-INFORMATION-CHECKLIST.md)供查阅和设计取舍，不要求119项全部保留、逐项汇报或逐项验收。用户此前明确强调必须增加/保留的项目，按盘点第0节的需求证据落实；其余展示可按方便、清新、少改核心接口的目标精简或调整。图中未展示不自动等于必须新增。此次纠正优先于本卡对参考信息作出的泛化“全部保留”解读；业务/人工状态及接口正确性边界不变。

## 读取与范围

AGENTS → status → 本卡 → [总计划](../plans/P4-UI-PERSONAL.md)§4地点地图/6/8；架构§4.5.4～4.5.5、§9.3、§10.1～10.3；策略§2.2～2.4、§3.2～3.4。允许原前端展示/局部导航；CORE 未能覆盖地点相册的日期成员页时可最小修改 `places/albums.py` 等**只读 adapter**，不得改地名包/地点成员真值/人工建议归属恢复的写协议。

## UI 2.0 地点相册时间轴

地点**列表卡**保持原数量/最近加入排序；进入**地点相册照片页**才使用统一时间轴：可信拍摄/入库、日/月/年、年月筛选和直接定位、自动续页、打开/纠错照片后返回原日期位置。服务端必须先计算真实地点成员（含 CN facet、人工归属优先、OSM/GeoNames 身份和图片资格），再时间排序与稳定分页；禁止只对旧 48 张前端重排冒充整相册。若原 offset 遍历无法满足，按查询证据做最小只读 adapter 并绑定成员版本、游标及修订。地图点聚合/瓦片/范围仍原样，**地图范围照片时间浏览留给 SEARCH-MAP**；冷缩略图首屏真实性能仍是另外的待验事项。
## 20万～30万地点查询改造与性能闸门（E 卡重点）

- **当前已知瓶颈形态**：非 CN `places/albums.py::album_assets()` 调用 `_album_rows()` 和 `_group_rows()`，先遍历可能接近全库的行集合，再按 offset 切片。20万以上不能只是将此旧返回的48张按日期前端重排，也不能每加载下一页重新生成全库成员列表。
- 在保留非 CN 的原**人工指定优先、排除、有效 GPS 解析/活动包、同名父链、视频及在线资格**下，形成选中地点的**SQL 权威成员集**，在该集内以可信拍摄日期或入库日期进行 **keyset + LIMIT（约48～60）**；CN facet 优先复用其成员 SQL，绝不绕开版本和强制重建地点包。未知/待就绪桶按 `created_at DESC,asset_id DESC`，成员筛选之前不准随意少算图。
- 原自动/人工/取消/恢复归属与 CN/OSM/GeoNames 身份真值不变。对小 fixture 逐项比较**旧地点有效资产 ID 集合**与新 SQL 集合（包括人工排除、无GPS、已缺失、跨来源重复、视频、CN区县）确保完全相等且照片去重；语义不等价则不得上线优化路径。
- **20万元数据基准**：至少选择成员偏多的城市、稀疏城市、CN区县三种代表 scope；量取第一页、翻5页、跳5年前某月、当前地点年统计，≥20次暖机、记录各段 SQL `EXPLAIN QUERY PLAN` 与 P50/P95/max，**照片页 P95≤1000ms 为目标**；30万重点复查高成员地点，不重跑模型/地图 PBF。年统计缓存按地点成员/包版本失效，每页不重扫全量。达不到性能目标则将瓶颈与 `PERF_PENDING/FAIL` 明示，不能以UI通过覆盖。
- 若 SQL 优化确需索引/Schema，不默默修改迁移。先报告最小索引、兼容、V3.1必要门槛再执行；若必须重写地点成员真值/写事务，停止并另立高风险卡。此卡不是地图地图瓦片性能、GPS重建或普遍地理重构。

## 闭环与边界

- 地点相册首屏，城市默认，省/区县/国家切换保留；地点/地图作为局部切换，五页共同外壳一致。
- 地名包、目录、新建/改名、建议批量整理、操作历史整体放「地点工具」；保留全部旧 DOM ID、请求绑定和锚点。旧锚点需展开后定位。
- 常用地点仍按现有名称选择真实来源；同名区别和来源依据可见，不按文字相同归并。
- 地图保留范围/成员版本/聚合/降级照片列表；接入共同外观时核对地图尺寸/resize。无GPS/缺包/无范围结果/瓦片失败分开提示。
- 只允许必要的地点**只读日期分页**适配；不顺手重写 `places.js::boot()` 按需加载、任务调度或整个地点体系，这不是通用性能卡。清楚说明后台读取与用户点击更新的区别。
- 不增加自动地点确认；不改人工指定/取消/恢复自动、不改变批量原子/游标/日志撤销规则。无新后端统计，数量仍用已有可信字段。

## 验证

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
uv run --no-sync pytest -q tests/unit/test_p4_place_unified_ui.py tests/unit/test_p4_place_cn_ui.py tests/unit/test_p4_place_map_review_ui.py tests/unit/test_photo_view_info_ui.py --basetemp=.pytest-tmp/P4-UI-PLACES-E --junitxml=test-results/P4-UI-PLACES-E.xml
node --check src/photo_search/web/places.js
node --check src/photo_search/web/places-albums.js
node --check src/photo_search/web/map.js
git diff --check
```

必要新增测试覆盖地点权威成员按日期分页、人工优先/视频资格、年月直接跳转、游标失效、两页不漏重及展开锚点与工具可访问，Python新增测试跑Ruff。隔离合成浏览器地点卡→照片→返回、地点/地图切换、地图范围照片→详情→恢复范围、同名候选与旧批量/日志可访问；瓦片不可用时显示准确降级，不为测试发起新下载或上传位置。**20万代表地点元数据规模为专项必测、30万仅关键读查询复查**；不跑真实PBF/包导入/模型/用户服务，也不造20万原始图片。

独立 DS-H 审新旧地点成员资格等价、日期/游标/查询计划及性能证据、展示/动作映射、原请求与权限、地图状态恢复；出现新写动作/权限/数据语义变化即扩大主审并按 B/D 另卡，无高风险未决 SOL_NOT_REQUIRED。

报告 `docs/reports/P4-UI-PLACES-E.md`，不把既有正式CN包/真实照片PENDING改成PASS。完成停止，下一候选 [C](P4-UI-SEARCH-C.md) 与 [SEARCH-MAP](P4-UI-TIMELINE-SEARCH-MAP-A.md)，再 [F](P4-UI-VIEWER-F.md)。
