# P4-PLACE-CN — 中国省 / 市 / 区县地点优化

日期：2026-10-04。状态：**TODO — Phase 0 决策门禁待完成**。立卡基线：`3f1496585ac943a21c81ebf31bbaab85e1ffb695`。  
定位：个人本地相册；小范围实现、最小测试，不扩成 GIS 系统。

现有地点相册与地点＋内容搜索已完成，复用 `P4-PLACE-AUTO-A` 与 `P4-PLACE-SEARCH-A`。

## 1. 目标

解决 GeoNames 在中国地点上的主要体验问题：

- 中国照片可能显示英文或附近小地名；
- 最近城市点不等同于行政归属；
- 需要按省 / 市 / 区县浏览和搜索照片。

目标结果：

```text
浙江省 · 杭州市 · 西湖区
广东省 · 深圳市 · 南山区
上海市 · 上海市 · 黄浦区
```

目标搜索：

```text
杭州
西湖区
浙江
西湖区的日落
深圳的夜景
```

中国以外继续使用现有 GeoNames。

---

# Phase 0 — 最小决策门禁

负责人：**DS-H，高推理**。  
只冻结本卡必需决策，不做全仓架构审查，不写功能代码。

产物：

```text
docs/decisions/ADR-0015-cn-admin-boundaries.md
```

ADR 只需明确以下内容。

## 0.1 数据源

正式首选：

```text
OpenStreetMap
+
Geofabrik China extract
```

允许一次性使用中国 `.osm.pbf` 作为构建输入，但：

- 原始 PBF 不进入 PhotoSearchData；
- 不进入安装包；
- 不成为运行时依赖；
- 生成精简行政区包后可删除。

只提取行政区所需对象和字段：

```text
boundary=administrative
admin_level=4 / 5 / 6
name / name:zh
ref:admin:CN（如有）
parent hierarchy
Polygon / MultiPolygon
```

不导入道路、建筑、POI、商家等普通 OSM 数据。

## 0.2 项目行政层级

项目内部统一解释为：

```text
admin_level=4 → province
admin_level=5 → city
admin_level=6 → district
```

若实际 OSM 个别区域不满足该层级，必须在准备阶段保守处理并记录，不猜测缺失父级。

## 0.3 坐标与事实边界

项目权威 GPS 继续使用：

```text
asset_geo_facts.latitude
asset_geo_facts.longitude
```

只接受当前已有的 WGS84 / ASSUMED_WGS84 有效事实。

省 / 市 / 区县是可重新计算的派生结果，不修改原图 GPS。

## 0.4 自动归属

中国自动归属优先：

```text
GPS
 ↓
district Polygon point-in-polygon
 ↓
city
 ↓
province
```

无法可靠命中时允许缺级或 unknown，不退化成错误的“精确行政归属”。

## 0.5 人工优先

必须保持现有语义：

```text
MANUAL_ASSIGNED   → 人工地点优先
MANUAL_UNASSIGNED → 阻止自动地点
AUTO_ALLOWED      → 可使用机器派生地点
```

机器 Polygon 结果不能伪装为人工确认。

## 0.6 数据包升级

激活新版本中国行政区包后，只允许对已有有效 GPS 重新做地点解析。

禁止因此：

```text
重新读取原图 EXIF
重新建向量
重新 OCR
重新做人脸
重新生成缩略图
```

ADR 完成并通过窄核对后，进入 Phase 1。

---

# Phase 1 — 实现

执行：**GPT-6 Luna / Medium**。  
实现、修复、定向验证尽量同一会话完成。

## 2. 数据包

新增或等价实现：

```text
tools/places/prepare_china_places.py
```

输入：

```text
Geofabrik China OSM PBF
```

输出独立 dataset，例如：

```text
asia-cn-admin-v1
```

准备阶段负责：

- 过滤行政边界；
- 中文标准名称与常用简称；
- parent hierarchy；
- Polygon / MultiPolygon 规范化；
- CRS 明确为 WGS84；
- manifest / checksum；
- PhotoSearch 可读取的行政区 INDEX / sidecar。

保留：

```text
geonames-asia-cities500-v1
```

用于非中国地点和 rollback。

## 3. 最小架构改动

当前旧地点模型不直接支持 district。

优先采用**加性行政区 sidecar / INDEX**，例如：

```text
index
├─ city_to_admin1
└─ cn_admin
   ├─ provinces
   ├─ cities
   └─ districts
```

district 至少包含：

```text
id
name
aliases
parent_city_id
polygon_ref
```

不要为了本卡默认修改：

```text
places.kind
历史 migration
人工 assignment / exclusion / undo
旧 place_text 语义
旧搜索核心协议
```

机器自动行政区也不要简单塞入旧 manual-only `place_text`。

## 4. 中国 GPS 归属

对于中国有效 GPS：

```text
latitude / longitude
→ district Polygon
→ parent city
→ parent province
```

中国以外继续现有 GeoNames 最近城市逻辑。

边界点、Polygon 异常、层级缺失时保守返回缺级/unknown。

## 5. 地点浏览

现有地点相册增加：

```text
province
city
district
```

例如：

```text
浙江省 · 3200 张
杭州市 · 1800 张
西湖区 · 680 张
```

继续保持人工地点优先和人工取消阻断自动归类。

## 6. 地点搜索

复用现有 `place-aware search`：

```text
西湖区
→ 西湖区照片

西湖区的日落
→ 西湖区成员范围 + 原有“日落”内容搜索

杭州夜景
→ 杭州成员范围 + 夜景搜索
```

标准名称和常用简称可参与地点识别。

不要通过“把完整地址塞进普通 FTS”替代结构化地点范围过滤。

## 7. 重新解析

提供一个简单受控入口，使新中国行政区包激活后可以：

```text
已有有效 GPS
→ 重新计算省 / 市 / 区县
```

不重新扫描照片，不触发其他 AI 建库。

## 8. 最小测试

个人项目只做小样本定向测试。

### 数据包

确认：

```text
build / validate / activate PASS
```

### 中国坐标

选 3～4 个明显位于行政区内部的位置：

```text
杭州西湖区
上海黄浦区
深圳南山区
重庆渝中区
```

检查中文省 / 市 / 区县基本合理。

### 地点搜索

只需重点验证：

```text
西湖区
西湖区的日落
```

### 国外回归

只检查一个：

```text
东京
```

确认原 GeoNames 路线正常。

### 真实图库

只有用户明确授权时，随机检查 1～3 张中国 GPS 照片；否则只用合成数据。

不做全国准确率和 30 万性能压测。

## 9. 明确不做

本卡不做：

- 乡镇 / 街道 / 社区；
- 门牌完整地址；
- POI / 景点 / 商家；
- 在线逆地理；
- 地图 UI；
- 地图瓦片；
- 新 GIS 服务；
- PostGIS / RTree 专项；
- 全国准确率评测；
- 全图库重新读 EXIF；
- 大规模 UI 重构。

## 10. 验收

满足以下即可：

```text
中国行政区包可生成、验证、激活
中国有效 GPS 可得到合理中文省 / 市 / 区县
杭州 / 上海 / 深圳不再优先显示英文小地名
“西湖区”可找到对应照片
“西湖区的日落”可做地点范围 + 内容搜索
东京仍正常
人工 assignment / unassign 语义不变
未重建向量 / OCR / 人脸
```

## 11. 停止条件

如果实现发现必须修改以下核心契约：

```text
人工 assignment / exclusion 语义
资产身份
历史 migration
旧搜索核心协议
```

停止扩大任务，记录阻塞。

以下属于本卡正常范围：

```text
dataset / INDEX 小型加性扩展
Polygon 读取与 point-in-polygon
albums 增加 district
place-aware search 增加 district
已有 GPS 的重新地点解析
```

---

# Phase 2 — 窄受影响面复核

负责人：**DS-H**。

只检查：

1. 原始 GPS 事实未被改写；
2. 人工 assignment / unassign / exclusion 仍优先；
3. 中国 Polygon 结果仅为机器派生；
4. 数据包切换与重新解析边界清楚；
5. district scope 未破坏旧地点搜索；
6. 非中国 GeoNames 回归正常。

不要求全仓工程主审，不默认追加 Sol。

## 12. 报告

输出：

```text
docs/reports/P4-PLACE-CN.md
```

只记录：

- ADR 结论；
- 实际数据源、版本、许可、CRS；
- dataset_id；
- 省 / 市 / 区县记录数；
- 3～4 个中国坐标结果；
- 两个地点搜索结果；
- 东京回归；
- 如获授权，1～3 张真实照片结果；
- build / validate / activate 状态；
- 未覆盖项。
