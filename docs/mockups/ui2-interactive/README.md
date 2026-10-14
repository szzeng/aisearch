# UI 2.0 · 整体观感与点击演示

日期：2026-10-13。**用户已确认保留此最后版本，作为任务书唯一现行视觉基线**。按用户要求做轻量整体观感演示，使用180条合成记录及本地SVG插画；不连接真实图库、应用API或模型。

- [单文件演示](demo.html)：双击即可用普通浏览器离线打开，无需安装依赖或启动照片应用。
- [开发预览入口](index.html)：相同页面，样式与脚本分文件保存。
- 当前会话预览：`http://127.0.0.1:18772/demo.html`。预览服务退出后仍可使用离线单文件。

主要体验：照片／人物／地点页面切换，进入人物或合集／地点相册，在页头唯一搜索框输入“海边”后直接点击“搜索此…”执行当前范围查询；空词点击范围按钮会展示范围内全部演示照片。页头 Enter 始终全库搜索；结果页固定范围可取消，取消后须再次显式提交才显示全库结果。点照片预览并关闭返回。地图、整理与图库管理仅提供示意交互，关注布局与整体观感。

简单检查：JS语法检查通过；浏览器实际观察首页、人物目录→张三相册→范围内“海边”15条演示结果→预览→关闭返回，搜索词及范围保留。检查范围仅为原型基本点击，不代表产品功能、模型检索、20万性能或完整浏览器验收。SVG插画和文字标签检索都是模拟，数量按演示记录计算。

参考：[当前任务规范](../../plans/P4-UI-TIMELINE-V2.md)。产品八卡仍PLANNED；本演示不替代20万有效照片元数据的必要性能验证。


## 任务书默认参考与页面映射

素材基线提交：`cf49a8f`。最后版 `demo.html` 保持原样，分文件 `index.html` 是同版维护入口；不新建另一套演示。旧SVG/PNG仅留档，不作为开工必读。

| 卡片 | 页面与主要操作 |
|---|---|
| SHELL-A | [照片](demo.html#photos)、[人物](demo.html#people)、[地点](demo.html#places)、[管理](demo.html#management) |
| SEARCH-C | [搜索](demo.html#search)、[张三相册→搜索此相册](demo.html#person/zhang) |
| TIMELINE-CORE-A | [照片→拍摄/入库、分组、筛选及跳转](demo.html#photos) |
| PHOTOS-B | [照片](demo.html#photos)、[单来源](demo.html#source/demo)、[管理](demo.html#management) |
| PEOPLE-D | [人物](demo.html#people)、[张三](demo.html#person/zhang)、[家庭合集](demo.html#collection/family)、[整理](demo.html#organize) |
| PLACES-E | [地点](demo.html#places)、[深圳相册](demo.html#place/shenzhen)、[地图示意](demo.html#map) |
| TIMELINE-SEARCH-MAP-A | [人物→搜索此相册](demo.html#person/zhang)、[合集→搜索此合集](demo.html#collection/family)、[地点→搜索此地点](demo.html#place/shenzhen)、[搜索](demo.html#search)、[地图示意](demo.html#map) |
| VIEWER-F | [照片→点击照片预览](demo.html#photos)、[相册→搜索/预览/返回](demo.html#person/zhang) |

搜索范围演示使用页头唯一关键词：先输入关键词再点相邻的“搜索此…”，按钮会立即执行；空词则运行范围内全部照片。页头 Enter 执行全库查询。搜索结果的取消范围不会自动放宽并显示全库结果。预览需点任一照片，没有独立viewer路由。窄屏直接调整窗口宽度。实施时只对本卡画面及必要基本操作保存合成产品截图，不要求像素复制、插画或示例计数一致。模拟逻辑不替代真实成员/日期/游标/权限/人工操作及20万性能门槛，缺失能力按规范/卡片补齐。八卡状态以各任务卡为准。

`demo.html` 为单文件离线版，由 `index.html`、`styles.css`、`model.js` 和 `app.js` 同步生成；搜索演示交互以分文件源码为准。
