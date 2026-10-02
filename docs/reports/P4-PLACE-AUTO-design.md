# P4-PLACE-AUTO — 地点搜索与自动相册任务卡核对

日期：2026-10-02。最新结论：**个人使用简化方案完成；产品未实现**。此前三阶段/多轮审查安排已被替代。
基线：`9ebcbef`；原工作区仅 `?? docs/tasks/P2-04A.md`，该文件已读取且保留。
交付：[个人使用简化方案](../plans/P4-PLACE-AUTO.md)、[唯一功能卡](../tasks/P4-PLACE-AUTO-A.md)。

## 1. 本轮授权与用户选择

用户希望 GPS 照片自动绑定地点、地名容易搜索、地点页自动整理各地照片并显示数量/照片，体验参照 Google 相册。
已确认先做“城市＋国家/省份”。本轮只核对源码/文档并立卡；没有读取个人照片、个人 catalog 或地名包运行文件，
没有启动服务、回填/重算、联网上传坐标、改变生产代码/迁移/依赖或模型配置。

## 2. 当前代码证据

| 位置/符号 | 已观察事实 | 对任务卡的影响 |
|---|---|---|
| `geo/resolver.py::DEFAULT_CONFIG` | `nearest-city-point`、max_candidates=3、max_distance_m=200000 | 只能复用为候选依据；自动选择另需验证策略 |
| `geo/resolver.py::persist_resolution` | 发布机器候选，校验输入，不写人工 assignment | 保留该边界，不能批量伪造人工确认 |
| `places/suggestions.py::visible_candidates` | 取活动包最新结果；检查人工已指定和直接地点排除 | 不能直接等同自动浏览集合，须另核对当前 GeoFact、人工未归类和祖先排除 |
| `places/filter.py::place_condition` | place_ids/assigned 只匹配 MANUAL_ASSIGNED | 只加 FTS 无法实现自动地点相册，须显式扩展新浏览范围并兼容旧口径 |
| `search/documents.py::place_values` | 只取人工有效地点、祖先/活动别名 | GPS-only 照片不会有确定性地点 FTS；这不表示语义通道绝对不会返回照片 |
| `tests/integration/test_p2_search_documents.py` | 候选地点不进入 place_text 的断言是既有行为 | 新需求涉及协议与 oracle 变化，不能当普通漏字段修复 |
| `search/service.py::_baseline_changed`、续页路径 | suggestion revision 仅在 place_state=suggested 时复核 | GPS 地点进入普通文字搜索后也需版本失效/补查，不能沿用该窄分支 |
| `places/contracts.py::PLACE_KINDS`、api models、migration0004 | kind 为 country/city/attraction/custom | 主架构描述 ADMIN，但实际冻结层未落地，省份需要兼容设计 |
| `geo/dataset.py::PLACE_RECORD_KINDS` | 包只接受 country/city | 省份不仅是 UI 改动，也影响包格式/读取/激活 |
| `tools/places/prepare_geonames.py::read_cities` | 读取城市 country_code，不保留 admin1 code | 现有城市→国家包不足以生成省级层级 |
| `places/service.py::list_places` | 返回目录记录、游标与目录revision，无每地照片计数/封面 | 需要聚合/API服务，不通过前端全量下载数数 |
| `web/places.html`、`places.js` | 页面以目录、待确认建议、手工批量操作为主 | 地点首页应改为有照片卡片→完整相册，管理功能可保留独立入口 |
| `tests/integration/test_p2_place_resolver.py::test_unassign_keeps_the_lock_and_only_leaves_the_other_candidates` | 人工未归类后仍可保留其他待确认建议 | “建议可见”与“自动相册准入”必须分别判定，不能取消后改归第二候选 |
| P4-02Y-P 报告 §6/范围 | 亚洲包只做附近建议；未做全库重算、自动确认、地点UI | 不能引用其历史PASS证明自动相册已有能力 |

## 3. 草稿需要补的重点

已有 P2-04A 将问题限定为机器建议文本投影，缺少主城市选择、自动地点筛选、相册聚合/完整分页、
省级数据兼容、历史库补齐与普通搜索 suggestion/浏览版本失效；完成后 P2-05 的路由也已过时。
仅让“上海”FTS 命中不满足用户目标；多个候选同时进入地点相册还会造成误归类/数量混乱。

最新方案以当前事实派生自动浏览集合，人工控制优先；自动结果附GPS来源，不冒充人工结论。
参考城市祖先不等于照片行政边界。数量、照片与地名入口共享同一谓词/快照。
本轮不再先冻结独立契约，也不分A/B/C实施；一张卡做短设计、最小实现与个人试用验证。

## 4. 外部资料（2026-10-02 核对）

- [Google Photos 官方地点说明](https://support.google.com/photos/answer/6153599?hl=en)：支持地点组织/搜索/浏览，
  区分相机保存、手工添加与估算；本项目只借鉴自动浏览与纠错目标，本轮不承诺视觉/相邻照片推算。
- [GeoNames 官方导出格式](https://download.geonames.org/export/dump/readme.txt)：cities500 不仅限于大城市，
  城市记录带 country/admin1 code，admin1CodesASCII 提供省级名称/ID；可用来补参考层级，不能证明行政包含。
  本轮未下载原始包，未验证本地快照对应省级数据/中文覆盖。

## 5. 验证与限制

本轮只产出说明/规划/执行提示词，没有改可执行 schema 或测试 oracle，按 V3.1 §3.2 的纯文档 A 例外
做本轮相对链接、源码路径/条款、UTF-8/空白与git diff --check核对，不运行pytest或产品测试。
原版立卡的4文档/26链接检查exit 0为历史记录，不能代替本次修订核对。
本次检查6份文档的严格UTF-8、4份任务文档空白、28条相关链接，exit 0；7个已有定向测试路径全部存在，
新增flow测试明确标为实施时创建。git diff --check通过；未执行pytest/浏览器，因为本轮没有产品改动。
不会把方案修订称为功能PASS或契约冻结。

未验证：自动地点选择质量/阈值、省级中文覆盖、新API/迁移/浏览器、30万聚合/回填性能、真实库补齐与包发行。
以往 memory 中的注册恢复只用作查找报告的线索；本轮事实以源码/报告为准，不推断真实数据根当前状态。
状态文件保留embedding独立审查为当前任务；地点需求入口已改为个人实现卡，执行时再明确转入A。

## 6. 用户要求的个人使用简化（本报告最新口径）

用户进一步要求“个人使用的相册，完成简单的功能与测试就可以，方便个人使用”。上一版按完整交付拆分过重，
现按直接要求改为一张功能卡，实施者同一会话完成短设计→前后端→定向验证→试用。具体取舍：

- 原3张功能/约5张审查卡取消；不单独契约冻结、工程主审或Sol终审，不自动生成B/C。
- 保留城市/国家/省份，但省份优先用可选INDEX附加城市资料，不新增admin kind、改旧parent或人工状态机。
  源码geo/dataset.py已支持INDEX角色文件校验，read_content目前不解释其内容，实施需补最小读取与坏文件检查。
- 相册用SQLite当前事实派生集合；地名入口/地点模式复用该集合，普通FTS/自然语言混排不变，避免扩展FTS世代。
- 自动取最近合格城市，个人默认50km、等距/过远不自动；所有结果标附近城市，不另做行政精度/阈值研究。
- 复用任务缺项补齐和现有单张纠错，保留批量入口，不新增回填状态机或大规模批量系统。
- 小样本20～30合成资产，定向pytest/JUnit、一次真实浏览器、Ruff/diff-check；不做30万/全量套件/故障全集/打包。
- 核心底线仍验证：不改原图/身份/人工协议，指定/取消优先，去重与数量/分页一致，补齐重复/重启无重复。

本轮同步主架构需求修订8 §4.5.5、策略修订10 §9.3记录限定例外，以免后续接手再次按旧重流程扩展。
此例外不推广到其他任务；若必须改变旧核心协议，先说明具体冲突并缩回加性浏览，不能以简单测试为由放宽人工保护。
