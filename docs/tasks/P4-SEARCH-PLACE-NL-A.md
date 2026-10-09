# P4-SEARCH-PLACE-NL-A — 搜索图片的自然语言地点查询

> **2026-10-09个人待审范围更新（当前优先）：** 按[个人审查约定](../plans/P4-PERSONAL-REVIEW.md)与[本组唯一入口](P4-PERSONAL-REVIEW-PLACE-A.md)收口。下文保留历史实现要求；冲突的重复全量/30万/模型/打包/固定多轮审查及浏览器组合要求，以本次个人基础范围为准。先复用可核对证据，缺口才补最小测试；首次审查覆盖完整未审新增面，身份、人工状态、撤销、迁移/恢复保护仍必留。此更新未执行审查或新增PASS；已有现场未验事项照实保留。

日期：2026-10-08。状态：**映射根因已修 / 定向合成回归通过 / 现场复测待用户完成**。立卡基线：`5d7bc139b2b73de5f30c77fbee9e6a1cc648f108`。
推荐：**DS-H / DeepSeek V4.1 Flash / 高推理**，用户手动选择；同会话完成短设计、实现、调试、定向测试及修复。
依据：[用户反馈与源码分析](../reports/P4-SEARCH-PLACE-NL-A-planning.md)。前置：已实现P4-PLACE-SEARCH-A；CN功能可选复用，现有待审状态不变，不要求安装新包。

## 1. 唯一闭环与停止点

用户在“搜索图片”输入“在济南拍的照片”，直接看到当前济南地点范围内全部可分页照片；输入“在济南拍的日落照片”，看到济南范围内日落相关照片。无需先点击地点相册入口。个人使用，简单测试。

一张卡内完成解析、页面、验证三个工作包；不另开契约/后端/UI子卡。达到下面小样本闭环后停止，进入个人试用反馈，不自动扩展聊天、性能、发行或审查任务。

## 2. 开工读取

`AGENTS.md → docs/development-status.md → 本卡 → 规划报告 → 实际Git状态`。本轮立卡时只有`.tmp/`和既有tmp清理报告未跟踪；保留并行/用户文件，不清理、不自动提交。

仅读V3.0 §4.3、§4.5.4/4.5.5、§9.1～9.5；V3.1 §2.1/2.2/2.4、§3.2/3.4、§9.3、§11；ADR-0005人工控制/地点筛选、ADR-0009组合过滤/会话、ADR-0011拍摄时间/排序相关条款；复用CN时读ADR-0016的成员/父链/key约定及尚未冻结说明。不默认全文重读。

直接代码：

- `src/photo_search/api/routes.py`的`_place_aware_parse`、`post_place_aware_search`、`_place_album_search_page`；`api/models.py::PlaceAwareSearchRequestModel`。
- `places/albums.py`、`cn_facets.py`的choices/成员谓词、`directory.py`、`revision.py`。
- `web/search.html`、`search.js`、`search-place-entry.js`、`search.css`；主搜索/取消/歧义/续页/返回快照与查看器状态。
- `search/service.py`、`retrieval.py`、`sessions.py`仅读实际调用和scope/版本绑定相关路径。
- `tests/integration/test_p4_place_search_flow.py`、`test_p4_place_cn.py`中的查询相关用例；原地点相册、API、authority、session回归及相关前端测试夹具。

## 3. 允许范围及不变量

可修改上述API加性适配/地点解析、搜索页面、直接相关测试及本卡/报告/status。必要时新增小型纯解析模块；不建立另一套地点词库、检索融合或相册成员规则。

保持旧`POST /search`冻结请求/回显、旧人工`place_ids/place_state/place_text`语义；不改migration/schema/稳定ID/人工归属与取消/排除/撤销/包激活/父级数据。CN `cn:<admin_id>`与旧city key保持原义；只消费已安装且当前有效的目录/成员。

地点条件继续与来源、人物、拍摄时间、可见性等取交集。向量/FTS必须先限定同一权威范围，禁止全库Top-K后过滤；保留RRF、排序及既有会话过期语义。

纯地点走现有SQLite完整分页，无文本编码器/GPU，无语义Top5000截断；已知地点无照片返回带地点chip的空集合。未知地点/缺包不能伪装成已形成地点过滤。

不下载数据/模型，不改依赖/embedding profile/门限，不重建向量/OCR/人脸或解析真实GPS，不访问真实照片/catalog；只用项目内合成夹具，pytest临时目录`.pytest-tmp`、uv缓存`.uv-cache`。

## 4. 工作包一：解析与查询计划

实现前在实现报告写短设计：保留原句、明确地点key、剩余内容、歧义状态/选项、取消状态；优先复用现有加性API字段，不新增冻结协议。说明归一字符串与原字符串位置映射，避免空白/英文别名/标点导致截取错位。

必支持的正向例句及预期见规划报告表格；至少覆盖：

- 纯地点、`济南的照片`、`在济南拍的照片`、`找一下在济南拍摄的所有照片`→济南＋空内容。
- `济南的日落`、`在济南拍的日落照片`、`帮我找济南拍的夜景`、`济南 夜景`→地点＋实质内容。
- `日落照片，在济南拍的`→明确后置地点＋日落。
- 目录已有省/市/区县的全称/别名；`山东省济南市拍的照片`、`在杭州西湖区拍的日落`依据真实父链消歧。没有该目录则保留查询并清楚说明，不能制造层级。

先识别可靠地点和句式，再只移除请求框架内的通用词；不得全局删除“照片”“我”“在”等字。`老照片`、`照片里的文字`、`济南路上的夜景`、`巴黎风格的房间`保持原内容，不误设拍摄城市。

否定或多个地点尚不做布尔解析；如`不是在济南拍的照片`、`济南或青岛的照片`，不能抽取济南作为单一肯定硬条件。保留原句并显示简短未解析提示，允许用户改写或显式筛选；不要宣称这些语句已精确满足。

真正同名必须选地点，显示中文类型/上级。选择前后都保留内容词，修复现有ambiguous返回空content导致“日落”丢失的问题；可用每候选剩余内容或已证明共同内容，不能任取错误片段。

CN与旧GeoNames同名不是自动等价。仅在当前有效只读映射已证明对应时，才按既有CN浏览规则选择展示范围；不得按名称字符串合并、改ID或扩大人工归属。无法证明就保留可区分的来源/父级选项，不把两套成员悄悄union。实际映射策略写入报告。

纯地点返回也需保留原句，修复当前`_place_album_search_page`从已清空的request.text回显original_query=None的问题；续页重用同一计划，不因重新解析漂移。不得更改revision增长/成员版本协议来修解析。

## 5. 工作包二：直接展示照片的搜索页面

提交/回车→同页照片网格＋“地点：济南”可移除条件；含内容时可简短显示剩余检索词。地点相册只保留现有导航/辅助浏览，不作为搜索结果替代。

移除搜索页对旧`search-place-entry.js`并行入口链的加载/绑定，以及误导文案和每次输入的四路相册查找。优先不增加新的自动补全服务；如果保留任何异步建议，必须校验最新输入/请求序号，旧响应不能覆盖新状态。

主输入采用日常例句提示，地点状态/place_id/包含后代放进默认折叠的高级条件，保持原DOM ID/显式筛选行为；不要顺带重做全部表单或人物/时间系统。

取消地点后保留实质内容词并同步可见输入：日落＋济南→全范围日落；纯地点→空输入纯筛选。不重新从旧原句偷偷加回地点。新搜索、歧义选择、取消条件、续页、返回恢复与查看器状态一致。

空集合解释当前地点范围无匹配照片，提供明确取消/放宽入口；不自动扩大范围。地点条件依据来源诚实显示，GPS附近城市与CN行政结果不混称。资源版本需更新，记录合成浏览器实际加载版本；真实用户服务是否重启/刷新另记未验证。

## 6. 工作包三：个人小样本验收

本次用户明确“个人相册，简单测试”，沿用V3.1 §9.3已限定的组合搜索个人验收边界。只运行直接定向pytest/JUnit、小图库真实浏览器与静态检查，不默认全量B/D、30万计时、真实模型或打包。

用约20～30张合成资产、小地点目录/小CN包及确定性encoder/index：济南日落/济南白天/外地日落/无地点构成反例。可在原测试文件补例句参数化，并新增实际JS最小DOM行为测试`tests/unit/test_search_place_nl_ui.py`；不是只测字符串和HTML存在。

必验：

1. 上述常用句式正确分流；纯地点返回范围内所有成员，设小page_size验证跨页计数/去重，替换encoder为会报错的桩证明未调用。
2. 地点＋日落同时证明FTS和受控向量走正确预过滤；地点选择后不丢日落，取消后日落可返回外地命中。
3. 真同名＋内容/父链消歧、空白/标点/别名偏移、旧/CN候选共存、已知无照片、未知/缺目录、否定/多地点/道路/风格负例。
4. 复用人工改到外地/取消的边界与CN旧成员规则；来源/人物/时间交集各选最小反例；一次地点成员版本变化拒绝旧续页，不改协议。
5. 实际JS新搜索/歧义选择/取消/续页/返回快照及旧提示移除；原ordinary search无地点查询回归。

参考命令（新增UI文件存在后执行；其他新增测试路径落地后补到本卡，不把不存在路径当成已验证）：

~~~powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
uv run --no-sync pytest -q tests/integration/test_p4_place_search_flow.py tests/integration/test_p4_place_auto_flow.py tests/integration/test_p4_place_cn.py tests/integration/test_p2_search_api.py tests/integration/test_p2_search_authority.py tests/unit/test_p2_search_sessions.py tests/unit/test_search_place_nl_ui.py --junitxml=test-results/p4-search-place-nl-a.xml --basetemp=.pytest-tmp/p4-search-place-nl-a
uv run --no-sync ruff check src/photo_search/api/routes.py src/photo_search/api/models.py tests/integration/test_p4_place_search_flow.py tests/unit/test_search_place_nl_ui.py
node --check src/photo_search/web/search.js
git diff --check
~~~

新增解析模块纳入Ruff；仍修改/使用旧入口脚本则也做Node检查。若确实修改共同scope/检索分块，加跑`tests/integration/test_p4_01c_retrieval_blocks.py`，JUnit单独保存。不动该路径不机械加跑。WinError5记环境失败，按正常权限路线复验，不改ACL。

真实浏览器一次隔离合成闭环：`在济南拍的照片`→全范围结果及续页→照片预览并关闭；`帮我找在济南拍的日落照片`→相关照片→取消地点后外地日落；同名地点＋日落→选择仍搜日落；照片详情的页内返回与浏览器Back分别确认恢复现场。无照片地点保持空状态。记录fixture、资源版本、动作/结果；JS桩不能代替此浏览器验收。

本卡不测真实库准确率；内容模型结果沿用当前语义能力，不因合成编码器通过就声称“日落”真实识别提升。

## 7. 收口、模型与审查边界

实现报告：`docs/reports/P4-SEARCH-PLACE-NL-A-implementation.md`，记录短设计/实际改动/解析正负例/API与页面证据、命令退出码/JUnit/浏览器记录、未验证项。更新本卡和status；必需验证未完成写IMPLEMENTED / VALIDATION PENDING，全部完成才写**PASS — 本机个人自然语言地点搜索与小样本验证**。

DS-H高推理完成全卡，不默认切换max或Sol。个人例外不改变其他待审任务，`SOL_NOT_REQUIRED`仅适用于本卡未改高风险协议且无未决项的实际结果；如果必须触碰§11边界，停止扩大并记录冲突，另按§2.4独立DS-H全受影响面工程审查→必要的Sol high聚焦终审。只有§2.2命名条件有证据时才建议max。

本卡实现通过定向JUnit、静态检查和24张合成图片浏览器闭环；反馈后的合成CN旧地点映射回归也已通过。用户截图确认地点页济南相册是“济南市·山东省·GPS行政区”，30张。根因已由合成夹具复现：解析候选原先选择旧GeoNames本地key，而相册成员实际属于CN facet key，导致`FILTER_ONLY`为0。现仅消费当前ACTIVE包中由旧城市坐标落入行政边界且名称/别名匹配所生成的`cn_legacy_links`，将该旧名称/别名解析到同一CN facet；未映射候选保持独立，不按名称猜测或合并成员。完整定向集合JUnit **58/0/0/0**、Ruff/Node/diff-check通过，详见实现报告末尾。未访问真实照片/catalog，也未重启用户服务；需用户刷新/重启服务后复测“济南的照片”，故状态为**修复实现与合成回归通过 / 现场复测待完成**，不标本机PASS。没有切换模型、提交或推送，不自动生成审查/性能/打包任务。其他待审任务状态保持原样。
