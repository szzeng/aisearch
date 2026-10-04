# P4-PLACE-CN — 中国省 / 市 / 区县地点优化

日期：2026-10-04。状态：**TODO**。立卡基线：`3f1496585ac943a21c81ebf31bbaab85e1ffb695`。  
定位：个人本地相册，小范围实现、最小测试。  
推荐执行：**GPT-6 Luna / Medium**；实现、修复、定向验证尽量同一会话完成。

前置：现有地点相册与地点＋内容搜索已经完成，复用 `P4-PLACE-AUTO-A` 与 `P4-PLACE-SEARCH-A`，本卡不重做已有地点系统。

## 1. 目标

解决当前 GeoNames 在中国地点上的主要体验问题：

- 中国城市经常出现英文或附近小地名；
- GPS 明明位于某城市，却可能显示社区、小镇等不符合相册习惯的名称；
- 当前无法方便按中国常用的省 / 市 / 区县层级浏览和搜索照片。

中国照片目标显示类似：

```text
浙江省 · 杭州市 · 西湖区
广东省 · 深圳市 · 南山区
上海市 · 上海市 · 黄浦区
```

并支持：

```text
杭州
西湖区
浙江
西湖区的日落
深圳的夜景
```

中国以外继续使用现有 GeoNames。

## 2. 数据方案

中国优先使用 `frykit / frykit_data` 可用的中国行政区资料，实际开工时先核对所用版本、许可、字段与坐标基准，再适配到项目数据包。

本卡只处理：

```text
省
市
区县
行政区 Polygon / MultiPolygon
稳定行政区代码
中文标准名称
```

中国地点计算使用 WGS84；如来源数据不是 WGS84，准备阶段必须明确转换并记录，不能在运行时混用未注明坐标系的数据。

中国以外继续复用现有 GeoNames Asia。

不要删除当前：

```text
geonames-asia-cities500-v1
```

保留 rollback 能力。

## 3. 中国 GPS 归属

中国不再以“最近城市中心点”作为主要行政归属依据。

优先实现：

```text
GPS
 ↓
区县 Polygon point-in-polygon
 ↓
区县
 ↓
所属城市
 ↓
所属省份
```

例如：

```text
GPS
→ 西湖区
→ 杭州市
→ 浙江省
```

只做省 / 市 / 区县。

本卡不处理：

```text
乡镇
街道
社区
道路
门牌
POI
景点
商家
```

边界点、数据缺失或无法可靠判断时允许“不确定/缺级”，不要用最近小地名伪造成精确行政归属。

## 4. 与现有地点架构集成

尽量复用现有 dataset、地点相册和 place-aware search。

中国地点至少能得到：

```text
province_id / province_name
city_id / city_name
district_id / district_name
```

外部 ID 优先使用稳定行政区代码。

名称使用标准简体中文，可提供常用简称作为 alias，例如：

```text
浙江省 → 浙江
杭州市 → 杭州
```

### 最小改动原则

当前已有 country / city 与 province index。本卡优先用**加性中国行政区 INDEX/metadata**保存区县关系。

不要为了 district 默认重构旧 places 层级。

优先避免：

```text
数据库 schema 修改
历史 migration 修改
人工 assignment / undo / exclusion 语义修改
旧搜索核心协议修改
```

如果只是 dataset / INDEX 的小型加性扩展，属于本卡正常范围。

## 5. 地点浏览

现有地点页增加区县级浏览能力，至少支持：

```text
省
市
区县
```

例如：

```text
浙江省
  └─ 杭州市
      └─ 西湖区
```

区县照片集合来自 GPS Polygon 判断结果。

继续保持既有规则：

- 人工指定地点优先；
- 人工取消后不能被自动地点重新加回；
- GPS 自动结果仍是机器推算结果；
- 不修改原图 GPS；
- 不触发向量、人脸、OCR 重建。

## 6. 地点搜索

复用现有 `place-aware search`，不要重新实现搜索系统。

目标：

```text
西湖区
→ 西湖区全部照片

西湖区的日落
→ 西湖区照片范围
   +
   原有“日落”语义 / FTS 搜索

杭州夜景
→ 杭州范围
   +
   夜景搜索

浙江的雪景
→ 浙江范围
   +
   雪景搜索
```

省、市、区县标准名称和常用简称均可作为地点识别词。

不要通过“把完整地址文字全部塞进普通 FTS”代替地点范围过滤。

## 7. 数据包与工具

优先新增一个简单准备工具，例如：

```text
tools/places/prepare_china_places.py
```

职责：

```text
中国行政区资料
       ↓
规范化 / 坐标处理
       ↓
PhotoSearch dataset / INDEX
       ↓
build_package()
```

生成独立 dataset，例如：

```text
asia-cn-admin-v1
```

继续复用现有：

```text
build
import
validate
activate
rollback
```

如现有 INDEX 结构不足，可做最小加性扩展。

不要建立新的 GIS 服务或独立数据库。

## 8. 最小测试

本卡是个人项目，只做定向小样本测试。

### 8.1 数据包

确认：

```text
build PASS
validate PASS
activate PASS
```

### 8.2 中国坐标

选择 3～4 个明显位于行政区内部、远离边界的坐标，例如：

```text
杭州西湖区
上海黄浦区
深圳南山区
重庆渝中区
```

要求得到合理的中文：

```text
省 / 市 / 区县
```

不做全国准确率测试。

### 8.3 地点搜索

至少验证：

```text
西湖区
西湖区的日落
```

确认：

```text
纯地点
→ 区县照片集合

地点 + 内容
→ 区县范围内继续复用现有内容搜索
```

### 8.4 国外回归

只检查一个，例如：

```text
东京
```

确认原 GeoNames 路线没有被破坏。

### 8.5 真实照片

如果用户明确授权当前真实图库测试，随机检查 1～3 张带中国 GPS 的照片即可。

未授权时只做合成数据，不读取真实图库。

## 9. 明确不做

本卡不做：

- 街道 / 乡镇自动识别；
- 门牌完整地址；
- POI / 景点 / 商家；
- 高德 / 百度在线逆地理 API；
- GPS 上传第三方；
- 地图 UI；
- 新 GIS 服务；
- 全国准确率评测；
- 30 万照片压力测试；
- 全图库强制重算；
- 大规模 UI 重构；
- RTree 等性能优化，除非最小样本已经明显不可用。

## 10. 验收

满足以下即可 PASS：

```text
新的中国行政区地点包能够生成、验证并激活

中国 GPS
→ 能得到合理中文省 + 市 + 区县

杭州 / 上海 / 深圳等
→ 不再优先出现英文小地名

“西湖区”
→ 能找到对应照片

“西湖区的日落”
→ 能在西湖区照片范围内继续使用原有内容搜索

东京
→ 原 GeoNames 路线正常

如获真实图库授权：
1～3 张真实中国 GPS 照片结果基本符合人工认知
```

不要求全国行政区边界 100% 正确，也不要求街道级完整地址。

## 11. 停止条件

如果实现过程中发现必须修改：

```text
人工 assignment / exclusion 语义
资产身份
历史 migration
现有搜索核心协议
```

不要自行扩大任务，先在报告中写清具体阻塞原因。

如果只是：

```text
dataset / INDEX 小型加性扩展
中国 Polygon 读取与 point-in-polygon
albums 增加 district
place-aware search 增加 district
```

属于本卡正常范围，可直接完成。

## 12. 产物

优先控制在：

```text
tools/places/prepare_china_places.py
中国行政区 INDEX / metadata 读取
GPS Polygon 区县判断
albums 的 district 支持
place-aware search 的 district 支持
少量定向测试
```

短报告：

```text
docs/reports/P4-PLACE-CN.md
```

报告只记录：

- 实际数据源、版本、许可与坐标基准；
- dataset_id；
- 省 / 市 / 区县记录数量；
- 3～4 个中国坐标结果；
- 西湖区地点搜索结果；
- 东京回归结果；
- 如获授权，1～3 张真实照片结果；
- build / validate / activate 状态；
- 未覆盖项。

## 13. 执行模型

推荐：

```text
GPT-6 Luna
Reasoning: Medium
```

这是现有地点架构上的局部数据适配、Polygon 判定和搜索扩展，不默认升级为大型架构任务。

如果 Luna 判断必须重构 resolver 核心、数据库 schema、place contract 或 search 核心协议，则先停止并报告，不自行扩大任务。
