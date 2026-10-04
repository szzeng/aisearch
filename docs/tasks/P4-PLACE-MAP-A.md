# P4-PLACE-MAP-A — 个人照片地图

日期：2026-10-04。状态：**TODO**。  
定位：个人本地相册；参考 Google Photos 的核心地图浏览体验，做最小可用版本。  
推荐执行：**GPT-6 Luna / Medium**。

本卡与 `P4-PLACE-CN` 可以独立实现：地图直接使用已有 GPS；中国行政区任务完成后，只额外显示更好的中文省 / 市 / 区县名称。

## 1. 目标

一张卡完成两个相关功能。

### A. 照片详情小地图

打开照片详情时：

- 有有效 GPS：显示拍摄位置小地图；
- 无 GPS：不显示地图；
- 提供“在地图相册中查看”。

### B. 独立地图相册

新增独立地图页，例如：

```text
/map
```

核心体验：

```text
地图当前可视范围
        ↓
查询 bbox 内照片
        ↓
地图点 / cluster 更新
        ↓
下方照片网格同步更新
```

用户拖动、缩放地图即可浏览当前区域内的照片。

## 2. 技术方案

前端地图优先：

```text
MapLibre GL JS
```

底图使用在线 provider。

第一版：

- 不建设地图服务器；
- 不下载离线底图；
- provider / style URL 可配置，不深度写死某家服务；
- 显示底图要求的 attribution。

地图服务只负责底图，不调用在线逆地理、POI、路线等接口。

## 3. GPS 数据边界

地图只读取现有：

```text
asset_geo_facts
```

资格：

```text
status = VALID
datum_status in (WGS84, ASSUMED_WGS84)
latitude / longitude 非空
asset 至少有一个 PRESENT file_location
```

不要：

- 重新读取照片 EXIF；
- 修改原图 GPS；
- 因地图触发向量 / OCR / 人脸 / 缩略图重建。

## 4. 功能 A — 照片详情小地图

优先对现有 asset detail API 做最小只读扩展；若不合适，再增加：

```text
GET /api/v1/assets/{asset_id}/geo
```

最小返回：

```text
asset_id
latitude
longitude
datum_status
place_label（有则返回）
```

不要返回原始文件路径或完整 EXIF。

详情页：

```text
照片详情

拍摄时间
地点文字（有则显示）

┌──────────────────────┐
│          ●           │
│       拍摄位置        │
└──────────────────────┘

[在地图相册中查看]
```

地图：

```text
center = [longitude, latitude]
固定一个合理 zoom
单个 marker
```

按钮跳转：

```text
/map?lat=...&lng=...&zoom=...
```

无有效 GPS 时整个地图区隐藏。

## 5. 功能 B — 地图相册页面

布局保持简单：

```text
┌───────────────────────────────┐
│            地图               │
│       ●23          ●158       │
│                 ●8            │
├───────────────────────────────┤
│ 当前范围 · 189 张照片          │
│ [图][图][图][图][图]          │
│ [图][图][图][图][图]          │
└───────────────────────────────┘
```

要求：

- 地图可拖动；
- 地图可缩放；
- 当前范围照片总数可见；
- 下方照片网格只显示当前地图框照片；
- 点击照片继续复用现有照片详情。

## 6. bbox API

新增或等价实现：

```text
GET /api/v1/map/assets
```

参数：

```text
west
south
east
north
limit
cursor
```

查询基于已有 GPS：

```text
south <= latitude <= north
west/east 按 bbox 规则过滤 longitude
```

同时满足有效 GPS、PRESENT asset 和现有可见性规则。

需用一个明确的小函数正确处理跨 ±180° 经线，不引入 GIS 框架。

## 7. 返回与分页

照片网格第一页默认：

```text
48 张
```

返回至少：

```text
asset_id
latitude
longitude
capture_time（已有则返回）
total
next_cursor
```

缩略图继续复用现有：

```text
/api/v1/assets/{asset_id}/thumbnail
```

不要新建第二套缩略图系统。

## 8. 地图点与 cluster

MapLibre 使用 GeoJSON source + 内建 clustering。

低缩放：

```text
● 23
● 158
● 8
```

点击 cluster：

```text
继续 zoom
```

高缩放出现单照片点时：

- 可显示简单缩略图卡；
- 点击打开现有照片详情。

第一版不要自研复杂空间聚合算法。

## 9. 地图与照片网格联动

只在地图交互结束后刷新。

推荐：

```text
moveend / zoomend
→ 200～300ms debounce
→ bbox 查询
→ 更新地图点和照片网格
```

不要拖动过程中持续请求。

过期请求可以取消或忽略旧响应。

## 10. 初始地图

### 从照片详情进入

```text
使用 URL lat / lng / zoom
→ 定位到该照片
```

### 普通打开地图相册

用所有有效 GPS 的简单 bounds 初始化即可，例如：

```text
MIN/MAX latitude
MIN/MAX longitude
```

不要求为此建立空间索引。

### 没有 GPS 照片

显示普通默认地图和提示：

```text
当前图库没有带位置的照片
```

## 11. 性能策略

个人项目第一版不要提前做复杂 GIS 性能优化。

先采用：

```text
bbox 后端过滤
+
下方照片分页
+
地图点数量合理上限
+
MapLibre cluster
```

若真实个人图库使用后确认 bbox 查询明显慢，再单独建性能卡。

本卡不做：

```text
PostGIS
RTree 专项
地图 vector tile server
空间缓存服务
30 万地图性能专项
```

## 12. 隐私边界

照片 GPS：

```text
本机 PhotoSearch API
→ 当前浏览器
```

不要主动将图库 GPS 列表上传给地图 provider。

在线地图只获取当前地图视野所需底图资源。

不要调用：

- 在线逆地理；
- POI；
- 地理编码；
- 路线；
- 用户位置历史。

## 13. 最小测试

只使用小型合成数据，除非用户明确授权真实图库。

### 后端

准备约 10～20 个 asset：

```text
区域 A
区域 B
范围外
无 GPS
非 PRESENT
```

只验证：

1. bbox A 只返回 A；
2. bbox B 只返回 B；
3. 无 GPS 不返回；
4. 非 PRESENT 不返回；
5. 分页无明显重复/遗漏；
6. ±180° bbox 小测试一次。

### 详情页

验证：

```text
有 GPS → 小地图出现
无 GPS → 小地图隐藏
```

“在地图相册中查看”跳转带 lat / lng / zoom。

### 浏览器闭环

只做一次：

```text
打开地图相册
→ 看到 cluster
→ 拖动或缩放
→ 当前范围照片变化
→ 点击照片进入详情
→ 看到小地图
→ 点击“在地图相册中查看”
→ 回到对应地图位置
```

完成即可。

不做多浏览器矩阵、全国地图测试和大量性能压测。

## 14. 验收

满足以下即可 PASS：

```text
1. 带 GPS 的照片详情显示拍摄位置小地图
2. 无 GPS 不显示错误地图
3. 可从详情跳到对应地图位置
4. 独立地图页可拖动和缩放
5. 当前地图框改变时，下方照片网格同步改变
6. 相近照片能 cluster
7. 点击照片复用现有详情
8. 不重新读取原图 GPS
9. 不改变人工地点语义
10. 不触发向量 / OCR / 人脸重建
```

## 15. 明确不做

V1 不做：

- Timeline；
- 当天路线 / 旅行轨迹；
- 卫星 / 地形多底图；
- 地图搜索框；
- 导航；
- POI；
- 地址反查；
- 街景；
- 离线地图；
- 无 GPS 照片位置推测；
- 地标识别；
- 地图内编辑 GPS；
- 批量修改 GPS。

## 16. 执行与报告

推荐：

```text
GPT-6 Luna
Reasoning: Medium
```

输出短报告：

```text
docs/reports/P4-PLACE-MAP-A.md
```

只记录：

- 使用的 MapLibre / 在线底图配置；
- API 变更；
- 小型合成测试结果；
- 一次浏览器闭环；
- 未覆盖项。

若实现过程中发现必须修改 GPS 事实契约、asset identity 或人工地点状态机，则停止并报告，不自行扩大任务。
