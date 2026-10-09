# P4-UI-SEARCH-C — 搜索入口、条件与结果展示

日期：2026-10-10。状态：**PLANNED / 未实施**。规划收口 HEAD `c19749a`；依赖 [A](P4-UI-SHELL-A.md)，推荐先完成 [B](P4-UI-PHOTOS-B.md)。

推荐 GPT-6.1 Sol / High；A 级前端小改，独立 DS-H 展示专项。纯文案/CSS子项可手动选 Luna Medium，不为同一闭环轮换模型。

## 视觉参考（执行前必须看图）

打开[搜索页模拟图](../mockups/P4-UI-PERSONAL-search.png)、[HTML草图搜索区域](../mockups/P4-UI-PERSONAL.html#search)与[参考规则](../mockups/README.md)。实际看图后实施一个主输入框、条件标签、结果网格和说明层级；搜索页不同时出现全局/局部两个搜索框。报告保存产品合成截图并对照；示例结果不定义新的统计、过滤或排序契约。

## 现状盘点（备份参考）

[已有页面信息盘点](../plans/P4-UI-INFORMATION-CHECKLIST.md)供查阅和设计取舍，不要求119项全部保留、逐项汇报或逐项验收。用户此前明确强调必须增加/保留的项目，按盘点第0节的需求证据落实；其余展示可按方便、清新、少改核心接口的目标精简或调整。图中未展示不自动等于必须新增。此次纠正优先于本卡对参考信息作出的泛化“全部保留”解读；业务/人工状态及接口正确性边界不变。

## 读取与允许范围

AGENTS → status → 本卡 → [总计划](../plans/P4-UI-PERSONAL.md)§3/4搜索/6/8；架构§9.1、§9.3、§9.5、§10.1～10.3；策略§2.2～2.4、§3.2～3.4。读 `web/search.html/search.css/search.js/search-place-entry.js` 与现有快照恢复、自然语言标签/UI测试。

允许搜索展示及全局搜索薄入口（五页页头可加表单，必要时一份很小的无 API 公共导航脚本）；禁止搜索解析/排序/召回/会话/字段契约/后端变化，禁止重写既有 API helper。

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

独立 DS-H 审全部入口与展示 diff、既有请求构造/快照直接链路及证据；发现排序、scope、分页或协议变化即扩大主审并按 B/D 另卡。无新冻结问题 SOL_NOT_REQUIRED。报告 `docs/reports/P4-UI-SEARCH-C.md`，更新简短 status；完成停止，下一候选 [D](P4-UI-PEOPLE-D.md)，也可先转个人试用。
