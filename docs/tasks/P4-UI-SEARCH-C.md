# P4-UI-SEARCH-C — 搜索入口、条件与结果展示

> **2026-10-13 最新视觉验收入口（20万＋大图库）**：[17页图集/离线HTML](../mockups/ui2-large/README.md)；本卡参考 [12-natural-search](../mockups/ui2-large/12-natural-search.svg)、[13-import-search](../mockups/ui2-large/13-import-search.svg)、[14-unknown-search](../mockups/ui2-large/14-unknown-search.svg)。本目录 SVG 为按最新方案重制的仓库可预览矢量设计稿，并非聊天高清 PNG 原件；原 `ui2/` 参考降为历史视觉对照。视觉稿不替代业务和性能证据。


> **2026-10-13 新视觉参照：** [UI 2.0 全页面索引](../mockups/ui2/README.md)；本卡对应 [09-search](../mockups/ui2/09-search.svg)。最新 SVG 是仓库内的轻量静态效果稿；旧版 PNG 仅作导航/色彩对照，不得据旧图删掉时间轴能力。产品数据契约、布局取舍及真实功能以 [UI 2.0 规范](../plans/P4-UI-TIMELINE-V2.md) 与本卡正文为准。此处并非实施截图。


日期：2026-10-10；2026-10-13 UI 2.0 修订。状态：**PLANNED / 未实施**。规划收口 HEAD `c19749a`；依赖 [A](P4-UI-SHELL-A.md)，推荐先完成 [B](P4-UI-PHOTOS-B.md)。

推荐 GPT-6 / High；A 级前端小改，独立 DS-H 展示专项。纯文案/CSS子项可手动选 Luna Medium，不为同一闭环轮换模型。

## 视觉参考（执行前必须看图）

打开[搜索页模拟图](../mockups/P4-UI-PERSONAL-search.png)、[HTML草图搜索区域](../mockups/P4-UI-PERSONAL.html#search)与[参考规则](../mockups/README.md)。实际看图后实施一个主输入框、条件标签、结果网格和说明层级；搜索页不同时出现全局/局部两个搜索框。报告保存产品合成截图并对照；示例结果不定义新的统计、过滤或排序契约。

## 现状盘点（备份参考）

[已有页面信息盘点](../plans/P4-UI-INFORMATION-CHECKLIST.md)供查阅和设计取舍，不要求119项全部保留、逐项汇报或逐项验收。用户此前明确强调必须增加/保留的项目，按盘点第0节的需求证据落实；其余展示可按方便、清新、少改核心接口的目标精简或调整。图中未展示不自动等于必须新增。此次纠正优先于本卡对参考信息作出的泛化“全部保留”解读；业务/人工状态及接口正确性边界不变。

## 读取与允许范围

AGENTS → status → 本卡 → [总计划](../plans/P4-UI-PERSONAL.md)§3/4搜索/6/8；架构§9.1、§9.3、§9.5、§10.1～10.3；策略§2.2～2.4、§3.2～3.4。读 `web/search.html/search.css/search.js/search-place-entry.js` 与现有快照恢复、自然语言标签/UI测试。

允许搜索展示及全局搜索薄入口（五页页头可加表单，必要时一份很小的无 API 公共导航脚本）；禁止搜索解析/排序/召回/会话/字段契约/后端变化，禁止重写既有 API helper。

## UI 2.0 搜索分工

本卡继续做**一个自然语言输入框、可取消人物/地点/时间/合集条件标签、搜索结果和返回**；纯条件完整分页、相关度默认排序、语义 TopK/截断、统一意图解析及旧会话一律不改。时间轴的服务端纯条件全量/语义候选/地图范围成员整合属于后续 [SEARCH-MAP](P4-UI-TIMELINE-SEARCH-MAP-A.md)；C **不得对已加载的一页按时间排序后冒充全部搜索结果**。最新规范：[UI 2.0](../plans/P4-UI-TIMELINE-V2.md)。
## 搜索 UI 需预留的20万规模状态（不在C改解析器）

保持单搜索框与默认相关度。显示现有拍摄时间标签，并为后续 SEARCH-MAP **入库时间 / 拍摄日期未知 / 日期待就绪**三种结构化状态预留标签的同款位置与取消反馈；在后端尚不支持时不得做假按钮或把状态解释为内容词触发向量搜索。长列表沿用已有查询分页，不在前端读取整批搜索资产再排序；日期统计只在用户切换时间视图后由后续卡按需查询。C 仍零后端变更。

## 用户闭环

- 固定位置输入文字，提交到已有搜索页；结果条件与照片清楚，能取消条件并返回原结果。
- 若跨页传 `q`：编码文字、不传对象/可执行字符串；进入搜索页初始化完成后只调用一次既有提交流程；用户此次明确提交优先于旧快照。消费后用同源 history replace 清除启动参数，返回/刷新不重复发搜索。空输入仅进入并聚焦原框。初始化失败保留输入与错误。不得把跨页入口当第二套业务解析器。
- 保留查询表单 DOM ID、原序列控制、来源/自然语言时间/人物/地点解析标签及取消路径。
- 首屏精简成搜索、条件、结果；算法说明/高级筛选折叠；以图搜图为清楚的次级入口，说明其人物/时间条件不适用，不篡改 multipart 字段。
- coverage 继续按点击读取；不能把覆盖率/距离/高度匹配数量称准确率。保留上限、截断和过期提示。
- 缺模型、无匹配、网络失败、条件冲突分开；失败保留新输入，不复活旧条件。缩略图可打开，返回恢复原条件、结果页与滚动。

## 验证与复核

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
uv run --no-sync pytest -q tests/unit/test_p4_search_nl_ui.py tests/unit/test_search_place_nl_ui.py tests/unit/test_search_person_album_link_ui.py tests/unit/test_photo_view_info_ui.py --basetemp=.pytest-tmp/P4-UI-SEARCH-C --junitxml=test-results/P4-UI-SEARCH-C.xml
node --check src/photo_search/web/search.js
node --check src/photo_search/web/search-place-entry.js
git diff --check
```

跨页启动若新增行为测试，计划文件为 `tests/unit/test_p4_ui_search_entry.py`，创建后加进上述 pytest 并跑相关 Ruff；任何新增公共 JS 也必须 node check。验证一次提交、中文编码、空输入、旧快照优先级、初始化失败、返回不重复请求。

项目内隔离合成浏览器做两页纯条件结果→预览→人物相册→返回，取消一枚标签，缺编码器失败仍留输入、以图搜图限制说明可找到；不要求为 UI 加载真实模型。DOM 测试不证明视觉通过；工程/浏览器/真实语义效果分别记录。

独立 DS-H 审全部入口与展示 diff、既有请求构造/快照直接链路及证据；发现排序、scope、分页或协议变化即扩大主审并按 B/D 另卡。无新冻结问题 SOL_NOT_REQUIRED。报告 `docs/reports/P4-UI-SEARCH-C.md`，更新简短 status；完成停止，下一候选 [SEARCH-MAP](P4-UI-TIMELINE-SEARCH-MAP-A.md)（需已完成 E 的地图前置），也可转个人试用。
