# P4-UI-TIMELINE-SEARCH-MAP-A — 搜索结果与地图照片时间模式接入

日期：2026-10-13。状态：**PLANNED / 未实施**。前置 [CORE](P4-UI-TIMELINE-CORE-A.md)、[B](P4-UI-PHOTOS-B.md)、[C](P4-UI-SEARCH-C.md)；地图接入另依赖 [E](P4-UI-PLACES-E.md)。本卡仅是最后的适配，不重构检索或地图算法。

推荐 **GPT-6 / High**；搜索候选与成员/游标契约受影响，按 V3.1 B 类必要定向验证、独立 DS-H 全受影响面核查；若要改变排名/TopK 或地图身份与写协议，停止并升级单独设计。

## 前置核对

`AGENTS.md` → `docs/development-status.md` → 本卡 → [UI 2.0 统一规范](../plans/P4-UI-TIMELINE-V2.md) → [搜索 C](P4-UI-SEARCH-C.md)/[地点 E](P4-UI-PLACES-E.md) 已完成报告 → 直接相关 `web/search.js`、`web/map.js`、`web/search.html`、`api/queries.py`、当前 `/search/place-aware`/纯条件查询/地图范围与 member_version 实现及必要契约。特别核对用户最近的统一自然语言解析和纯条件不受 1000 上限、语义候选仍有截断的事实；别混淆为统一“全库时间排序”。

## 唯一闭环

### 搜索结果
- 默认**相关度**排序与当前候选语义原样保留。仅在已有搜索结果 UI 上增加「按时间查看」模式；清楚显示“按时间整理**当前检索候选**”还是“纯条件完整结果按时间浏览”，避免用户误认为两个模式命中集相同。
- 对**纯权威过滤**（人物/合集/地点/时间/图库等无需语义评分）尽量沿用全量数据库成员过滤 + 时间轴服务端时间页；与原完整分页一致，不受浏览端/TopK 截断。纯地点分页与修订语义照旧。
- 对**FTS/向量/融合语义结果**：不得仅按当前前端已加载页排序后叫“整个搜索结果按时间”；先证明能用冻结且有界的 session 候选快照/游标在服务端正确分页，且清楚标记候选截断/上限。若缺该保证，**不要开放这个入口**，只维持原相关度结果并在报告记录此子项后置，不改原搜索算法补洞。
- 搜索页仅一个主输入框、可取消 intent chips、失败保留输入；范围/时间/人物取消后旧 time cursor 必须失效；搜索→照片详情→人物相册→返回恢复原模式、过滤、页面与滚动，不重复触发搜索。

### 地图范围照片
- 原地图聚合/瓦片/点位、bbox/zoom/member_version/点击点的相片、GPS 与当前范围统计完全不变。仅“当前范围照片列表”可适配时间轴组件；范围变动时必须按当前 map 的权威 bbox/member_version 获取成员并重置游标。
- 不能把地图当前显示的一页图片用前端时间排序当成整个 viewport 的时间轴；无法提供全范围权威日期页则保留现有分页，不强行落地。地图缺瓦片、照片离线时有原降级行为；来源与版权继续显示。
- 与原 `map.js` scope/事件状态机保持一致，不在地图页实现第二套导航或全新地图请求层。

## 权限和隔离

只可调整必要只读查询/前端 adapter 与其测试，保留旧搜索 API、统一意图解析、FTS/vector/RRF、语义 TopK、snapshot/revision、防过期与地图协议。绝不根据前端几张图片推断总数/完整年月分布；模型缺失的搜索不可伪造结果。禁止真实 PhotoSearchData、模型或地图大数据重建。

## 最小验证与停止

合成 fixture：一个完整纯条件搜索跨两页按时间浏览；一个有上限的语义检索仍默认相关度、有提示且不能错误冒充全量；一组 intent 标签取消/再查询; 一个地图 bbox 缩放后照片集合变化；一组 409/过期 cursor；一个详情关闭/跨页面返回。若部分后端语义无法保证，则记录为**DEFERRED**，不要为赶进度降低正确性。

针对受影响测试文件执行 `pytest -q ... --basetemp=.pytest-tmp/P4-UI-TIMELINE-SEARCH-MAP-A --junitxml=test-results/P4-UI-TIMELINE-SEARCH-MAP-A.xml`，改动的 JS 全部 `node --check`、相关 Python Ruff/编译、`git diff --check`；小型隔离浏览器验证，杜绝无真实运行冒称现场 PASS。不默认全仓/GPU/大模型/用户照片库。

独立 DS-H 复核查询资格、候选冻结、排名未改、分页以及地图 bbox/member_version 与 UI 返回。报告 `docs/reports/P4-UI-TIMELINE-SEARCH-MAP-A.md` 与 status 只标实际完成范围；完成即停，下一建议 F。
