# P4-SEARCH-NL-UX-A — 个人相册自然语言搜索与纯筛选完整分页

日期：2026-10-09。状态：**READY / 尚未实施**。性质：个人试用功能收敛，**一张任务卡、定向简单验收**。

执行推荐：**DS-H / DeepSeek V4.1 Flash / 高推理**；GPT 备选 **GPT-6 Luna / High**。模型由用户在 Codex 手动选择，同一会话完成设计、实现、修复和验收；不默认 DS-MAX 或 Sol。

## 1. 目标与口径

搜索图片界面以一个自然语言输入框为主，自动解析已有可信的时间、人物、地点条件；有实质内容词时仍走原 FTS + Vector + RRF。

- “2023年的照片”“去年夏天的照片”→既有 EXIF 拍摄时间范围；
- “张三的照片”→已有 canonical 人物 ID；“张三和李四的合照”→`person_match=all`；
- “济南的照片”“2023年张三在济南拍的日落”→复用当前地点识别/成员范围，与时间、人物、内容取交集；
- **纯条件**（无实质语义内容）：SQLite 按需分页、完整去重总数、**能翻到最后一张**，不受现有 `AUTHORITY_PAGE_LIMIT=1000` 或语义会话 `MAX_SESSION_IDS=5000` 的总结果限制；
- **内容语义搜索**：继续固定 Top5000；“约 N 张高度匹配”只统计，不过滤、不重排，也不宣称 5000 以外全库结果可浏览。

不追求 Google Photos 全部复杂自然语言能力；优先稳定支持上述常用表达。

## 2. 开工与现状核对

先读 `AGENTS.md` → `docs/development-status.md` → 本卡，检查当前 HEAD/工作树和已有并行任务；只读实际受影响代码与相应契约，保留用户及其他任务文件。

参考：
- `docs/tasks/P4-02Y-W.md`：语义 Top5000 已完成，明确**未改**纯筛选1000上限；
- `docs/tasks/P4-PLACE-SEARCH-A.md`、`P4-SEARCH-PLACE-NL-A.md`：已有地点自然语言/纯地点完整分页；
- `docs/tasks/P4-SEARCH-RETURN-A.md`：搜索结果返回恢复。
- 主架构 §9.2/9.3/9.5、V3.1 策略 §2.2/2.4、§3 和 §9.3，ADR-0009 人物/组合过滤、ADR-0011 时间口径；只核对应有边界，不全仓重读。

主要实现位置：
`src/photo_search/api/routes.py::post_place_aware_search,_place_album_search_page`；
`src/photo_search/search/{service,retrieval,sessions}.py`；
`src/photo_search/places/natural_search.py`、`persons/**`、`taken_time/**`；
`src/photo_search/web/search.{html,js,css}`。

## 3. 实施顺序

### A. 先解决纯筛选分页

1. **优先只扩展现有个人入口 `POST /api/v1/search/place-aware`**，不改变冻结旧 `POST /api/v1/search` 协议。仅在最终无内容词且无图片向量时走 SQL 纯条件路径；旧 API 的1000条行为若保留，必须在报告中说明，不能声称全站均已解除。
2. 复用现有 `SearchScope`/`scope_condition`、canonical 人物解析、时间谓词、地点相册成员与修订规则，不能另造一套权威资格。已有明确地点继续复用纯地点分页（CN与旧地名成员不能按同名合并）。
3. 无明确地点的纯筛选使用 `asset_id` 升序 keyset 分页，`COUNT` 返回权威去重总数；单次 SQL 只取 `page_size+1` 条（`page_size` 沿用现有合法上界），不物化全部 ID、不进入固定5000条的 `SearchSessionStore`，不调用 encoder/GPU。结果仍为 `FILTER_ONLY`。
4. 游标仅保存必要位置与筛选指纹/版本绑定，沿用现有错误码/版本判定；变更人物、地点、来源/条件后不能拿旧游标拼接新结果。无关的后台新增照片不承诺跨多次 HTTP 请求快照：说明计数可能变化，不能假称全程强一致。
5. 响应保留当前前端使用的 `hits`、`total_candidates`、`next_cursor`、`has_more`、`channels` 等形状；纯条件结果总数指完整符合条件的数量，不是已缓存数量。若无法在不改冻结协议的情况下实现，记录冲突并停止扩大范围。

### B. 轻量自然语言解析

建议新增 `src/photo_search/search/natural_intent.py`，只用确定性规则和现有数据源：

- 时间：年、年月、具体日期/范围、今年/去年/上个月、春夏秋冬。春3–5、夏6–8、秋9–11、冬12月至次年2月，左闭右开；相对日期**首查解析一次并冻结解析后的条件供续页使用**。只使用既有拍摄时间口径，不将文件 mtime 充作 EXIF 拍摄时间，不凭空换 UTC。
- 人物：从活动 canonical 姓名/有效别名查找；“和……合照”=all、“或者”=any。别名可能跨人物重复，必须让用户消歧；未知/否定或无充分依据的名称不能猜人物 ID。保持现有人工锁、重定向及撤销判断。
- 地点：直接复用现有 `parse_place_query` 及用户已选定地点；不得另建地名词库或在多地点、否定情况下错误地只选一个地点。
- 产生明确的“原句 / 结构条件 / 剩余内容词 / 未识别或歧义”查询计划；有条件的语义查询把剩余实质内容交给现有搜索服务，过滤必须在召回前收窄，而不是全库 Top5000 后过滤。
- 歧义显示可选项；无法安全解析时诚实提示，不展示貌似正确的硬筛选。首查与续页/取消条件不得反复重新解析导致结果漂移。

### C. 页面简化

- 主界面突出唯一搜索框、示例提示和照片墙；识别到的日期、人物、地点做可取消 chip。
- 旧的日期框、人物多选、来源、可用性、复杂地点字段折叠到“高级筛选”，保留原功能及 DOM/事件兼容。
- 纯条件显示“符合条件共 N 张”、已显示范围、继续翻页；语义结果保留“高度匹配”与 Top5000 提示。
- **不允许前端无限积累几万张 DOM 或把全部结果塞入 sessionStorage**：纯条件默认分页/有界页面窗口；语义搜索维持现有加载逻辑。保持进入详情后返回恢复、预览切换与续页正确；不引入通用状态框架。

## 4. 最小验收（合成数据，不用真实照片）

新增建议：
- `tests/unit/test_p4_search_nl_parse.py`
- `tests/integration/test_p4_search_nl_ux.py`
- 需要时在既有前端测试增加少数实际交互用例。

只需覆盖：

1. **1005与5005**条符合条件的轻量 SQLite 资产，按小页全部翻到末尾：总数正确、跨1000/5000、无重复/遗漏；加入不符合条件资产验证交集；确认不会把全量 ID 缓存到内存会话。
2. 年、月、日期、跨年冬季、相对日期（固定时钟）；EXIF时间缺失不误入，空结果为0。
3. 已命名人物、两人 all/any、重名别名消歧；地点＋时间＋人物 AND、地点与内容词；未知地名/否定不误形成硬筛选。
4. 续页换条件/失效 revision/非法游标明确拒绝；旧 `POST /search` 与有语义文本 Top5000/高度匹配统计不回归。
5. 一次小型合成浏览器试用：输入自然语言→查看条件→取消条件→翻页→详情/预览→返回恢复；大量纯筛选结果浏览不造成 DOM/sessionStorage 随总张数无界增长。

建议定向命令（测试文件创建后再执行）：

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
$env:PYTHONPATH = 'src;tests;.'
uv run --no-sync pytest -q tests/unit/test_p4_search_nl_parse.py tests/integration/test_p4_search_nl_ux.py tests/integration/test_p2_search_endpoint.py tests/integration/test_p4_place_search_flow.py tests/integration/test_p3_person_search_filter.py tests/unit/test_p2_search_sessions.py --basetemp=.pytest-tmp/p4-search-nl-ux --junitxml=test-results/p4-search-nl-ux.xml
uv run --no-sync ruff check src/photo_search/search src/photo_search/api src/photo_search/web tests/unit/test_p4_search_nl_parse.py tests/integration/test_p4_search_nl_ux.py
node --check src/photo_search/web/search.js
git diff --check
```

若实际改动路径不同，调整定向列表并记录；遇到已知 Windows pytest 权限问题按 AGENTS.md 处理，不改 ACL。**不默认运行全量 pytest、30万性能、GPU/模型测试、重新打包或真实图库建库。** 仅文档立卡不需要运行产品测试。

## 5. 冻结边界与交付

不修改 schema/migration、历史冻结 companion、人物或地点写入规则、模型/profile、向量/FTS/RRF/Top5000、强匹配门限、依赖/锁文件及打包配置；不访问、扫描、移动、删除或改写真实图库。若需要突破冻结协议或身份/revision/撤销安全边界，先记录具体冲突并提出单独裁决，不自行放宽。

一张卡、同会话完成短设计→A分页→B解析→C界面→定向测试/浏览器→实现报告。报告写明实际测试命令/退出码、未覆盖的旧 API 行为、强一致性边界和限制；更新 `docs/development-status.md`。达到“纯条件5005可全部浏览＋上述常用表达正确＋原语义检索不回归”即收口。

按个人使用偏好只做定向验收；若无冻结协议/身份/数据安全改动，不默认独立全量主审或 Sol 终审；一旦触及 V3.1 §11 高风险条件，按既有审查门禁处理，不能以简单验收代替。
