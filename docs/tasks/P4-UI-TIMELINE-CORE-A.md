# P4-UI-TIMELINE-CORE-A — 共享照片时间轴核心与只读日期查询

> **2026-10-13 新视觉参照：** [UI 2.0 全页面索引](../mockups/ui2/README.md)；本卡对应 [01-photos](../mockups/ui2/01-photos.svg)、[03-person-album](../mockups/ui2/03-person-album.svg)、[07-place-album](../mockups/ui2/07-place-album.svg)。最新 SVG 是仓库内的轻量静态效果稿；旧版 PNG 仅作导航/色彩对照，不得据旧图删掉时间轴能力。产品数据契约、布局取舍及真实功能以 [UI 2.0 规范](../plans/P4-UI-TIMELINE-V2.md) 与本卡正文为准。此处并非实施截图。


日期：2026-10-13。状态：**PLANNED / 未实施**。上游 [A](P4-UI-SHELL-A.md)；下游 [B](P4-UI-PHOTOS-B.md)、[D](P4-UI-PEOPLE-D.md)、[E](P4-UI-PLACES-E.md)及后续 SEARCH-MAP。本卡是一张可独立验收的公共能力卡，不接管普通页面的最终布局。

推荐 **GPT-6 / High**；新增有界只读查询与前端状态管理，按 V3.1 B 类受影响面及独立 DS-H 复核；若必须触及 schema/人工真值/写事务，先单独升级风险与验证，不扩大暗改。

## 前置与权威约束

先读 `AGENTS.md` → `docs/development-status.md` → 本卡 → [UI 2.0 规范](../plans/P4-UI-TIMELINE-V2.md) §1～3/6 → [旧 UI 总计划](../plans/P4-UI-PERSONAL.md)相关节 → ADR-0011 与 V3.1 §2/§3（必要时对应 §9）。核对 master HEAD、工作树和其他任务变更；用户照片/PhotoSearchData 不可读取、移动或重建。检查 `api/queries.py`、`taken_time/{read,contract}.py`、`api/routes.py`、`persons/{albums,collections}.py`、`places/albums.py`、`web/{app,persons-albums,places-albums}.js` 现有行为；只读确实相关的文件与测试，避免全库重构。

## 唯一交付

**服务端权威时间分页 + 日期分布/跳转 + 可复用前端时间轴组件**，通过一张隔离小型测试页面/调用证据证实；不要顺手改 A/B/D/E/C/F 整页样式。

### A. 明确时间语义（不能仅给旧排序加日期标题）

- `拍摄时间` 的正常年月/日桶限当前规则版本可信、`EXIF_ORIGINAL/EXIF_DIGITIZED`，使用 civil `taken_at_sort`；`EXIF_ROOT/FILE_MTIME` 或无可信时间单列“无可靠拍摄时间/待补齐”组，按稳定 asset_id 等回退排序但不得冒充日期。
- `加入图库时间` 用 `assets.created_at` 的已定义本地日历语义；不能把目录复制/迁移时间推断成拍摄时间；时间依据 UI 切换只控制查询，不改旧字段/旧 API 的 `time_desc/capture_desc`。
- 日/月/年只是展示分组；按年月筛选是真过滤；“跳转到某年某月”定位窗口并保留全相册范围，不偷偷变为过滤。旧 `capture_from/capture_to_exclusive` 的上下界和兼容语义不能被新 endpoint 默改。

### B. 只读查询/API

- 优先在旧 `asset_page` 附近抽取可复用的权威资格/时间表达式，不复制一套数据真值。新增/扩展有限且明确的只读查询：时间照片页（建议 `GET /api/v1/timeline/assets`）与当前范围 period 年月摘要（建议 `GET /api/v1/timeline/periods`）；实际路由名按现有习惯决定。
- scope 至少 `all/source/person/collection`；地点先定义 adapter/协议接口，但如果 `places/albums.py` 现有成员分页不适合直接正确实现，交由 E 卡提供其权威后端 adapter，**不在本卡强行改遍地点代码**。person canonical 合并、collection 有效成员、照片资产按 asset 去重，视频/缺失/来源资格前置；无权限、无效 token/ID 不应猜测成空结果。
- 校验 `time_basis`、`group_by`、`year/month`、成员条件；页长约 48～60，有界稳定 keyset（日期排序键、asset_id，以及无日期桶显式排序），有版本/筛选签名绑定的不可复用游标。成员变化/旧游标产生明确冲突或可重启信号；旧请求不能静默混进当前视图。
- 每页同一响应给 `asset_id`、`thumbnail_url`、展示时间/来源与可信标签、日期分组键、`next_cursor`、必要版本。禁止前端对每张图多发 GET /assets/{id} 获取日期；不要为本卡发原图。
- Period endpoint 回答**当前成员集合**里存在的年与指定年的月（和有意义的真实计数）；可信拍摄时间与入库时间依据不同，范围选择不同就必须分别统计。不可从首页前 50 张外推。跳转指定年月从数据库按区间定位，绝不循环跳过几百页。
- 优先利用已有索引；若要 migration 只为明确证据支持的最小索引，核对兼容和必要 B/C/D 门槛、授权后独立做，不以猜测“30万会慢”为由全库加索引。

### C. 轻量共享前端组件（仅一套，保留多页结构）

- 新增小型 `web/photo-timeline.js` + 局部 CSS（实际名称允许依现有命名收口），提供 `mount/render/append/reset/dispose` 等明确接口及 adapter hook；不创建第二套页面路由或状态总线。
- 支持日/月/年标题跨页正确合并、IntersectionObserver 临近底部预取、失败时“加载更多/重试”、异步请求 generation/cursor 隔离、照片网格占位/缩略图懒加载、跳到年月及保留/恢复滚动锚点。DOM 回收作为按性能证据触发的受控优化；首版不强制复杂虚拟化。
- 通过 scope adapter 允许首页/人物/合集/地点沿用其查看照片及更正动作；本卡不劫持合照人脸、地点卡和写入控制。统一呈现的时间轴只是照片列表组件。

## 与其他任务的文件归属

本卡主要负责只读服务/API/时间投影与新共享组件；B 负责 `app.js/index.html` 接入，D 负责 `persons-albums.js` 接入，E 负责 `places-albums.js` 与必要地点只读 adapter。**先核对并顺序施工，禁止两卡并行修改同一文件。**

## 最小验收

新增小型 `tests/unit/test_p4_ui_timeline_core.py`（或拆为一个 API integration + 一个前端 DOM 小测试）；只测本卡实际新接口/组件，不为设计常量写脆弱的 DOM 镜像断言。至少覆盖：
1. 可信 EXIF / 文件时间 / 未抽取 / rules_version mismatch 的不同桶与过滤，拍摄 civil 跨午夜、跨年和闰日；
2. 入库时间与拍摄时间切换，日/月/年汇总，年/月真实 counts，空集及无时间桶；
3. 2～3 页：同日跨页、相同时间戳 tie-break、唯一资产去重、年月直接跳转；游标旧版本/成员变化与筛选切换废弃旧响应；
4. person/collection/source 有效成员、相册更新、视频资格前置；日期随分页返回、无逐图 N+1；
5. observer 自动下一页、失败重试、dispose/返回位置、旧响应不回填新范围。

`uv` 使用 `.uv-cache`，pytest `--basetemp=.pytest-tmp/P4-UI-TIMELINE-CORE-A --junitxml=test-results/P4-UI-TIMELINE-CORE-A.xml`，针对实际测试文件 `pytest -q`，新增/修改 JS 做 `node --check`，Python `ruff check` / `py_compile`，`git diff --check`。可用项目内 `.tmp/P4-UI-TIMELINE-CORE-A` 做 20～40 张合成图库小浏览器复测。**不跑全仓、AI 模型、GPU、真图库或打包**；若触及 schema/写语义则依策略提高门槛。

独立 DS-H 完整覆盖只读资格与成员/版本/游标/日期语义、接口/前端事件和证据。无法正确处理地点分支就明确标 E 依赖，不假称本卡已覆盖地点。报告 `docs/reports/P4-UI-TIMELINE-CORE-A.md`，更新 status；本卡通过后停，不自动执行 B。
