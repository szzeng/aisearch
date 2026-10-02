# P4-PLACE-SEARCH-A — 普通搜索支持地点＋内容

日期：2026-10-02。状态：**PASS — 本机个人地点＋内容搜索与小样本验证**。立卡基线：`f3b04af`，立卡前工作区干净。
推荐：**DS-H / DeepSeek V4.1 Flash / 高推理**，由用户手动选择。同一会话完成短设计、实现、修复与定向验证。
前置：[地点相册已实现](../reports/P4-PLACE-AUTO-A-implementation.md)；[整体方案](../plans/P4-PLACE-AUTO.md)。

## 1. 用户目标与边界

用户明确补充：普通搜索输入“济南的日落”，直接返回济南地点范围内与日落相关的照片，不能只显示地点相册入口。
这是用户新增的一张补充功能卡，不恢复原先取消的B/C拆分，也不重做地点相册。

| 输入 | 地点条件 | 内容检索 |
|---|---|---|
| 济南的日落 | 济南城市 | 日落 |
| 在济南拍的雪景 | 济南城市 | 雪景 |
| 济南 | 济南城市 | 无内容词，走完整SQLite照片分页 |
| 山东的日落 | 山东省份（需要已有省份索引） | 日落 |
| 日落 | 无新增地点条件 | 原普通搜索 |

目标限本机个人使用，沿用用户要求的简单功能与测试。真实图库未授权；不处理真实库、下载地名包或模型。
地点沿用“人工地点优先＋GPS附近城市”的参考范围，不把附近城市解释为精确行政包含。

## 2. 开工读取与允许修改

按AGENTS.md→development-status→本卡恢复上下文，先核对实际HEAD与已有编辑。
读取主架构§4.3、§4.5.4/4.5.5、§9.2～9.5；V3.1 §3.2/3.4、§9.3；
ADR-0005人工控制/原地点筛选、ADR-0009组合过滤/会话、ADR-0011排序的相关条款。
遇到必须作跨模块核心协议或数据语义裁决时按AGENTS.md扩读，不因本卡重读无关全文。

直接相关：

- places/albums.py、filter.py、directory.py、revision.py，geo/dataset.py省份资料读取。
- search/service.py、retrieval.py、contracts.py、sessions.py、query.py、tokens.py与documents.py。
- api/routes.py、models.py、web/search.html及搜索JS/CSS、web/search-place-entry.js。
- tests/integration/test_p4_place_auto_flow.py、test_p2_search_api.py、test_p2_search_authority.py，
  tests/unit/test_p2_search_sessions.py，以及实现实际调用的搜索测试/夹具。

允许增加小型本地地名解析器、加性个人搜索API/适配器、内部地点浏览范围、页面条件显示和定向测试。
禁止改原图、人工assignment/取消/排除/撤销、资产身份、历史migration、冻结companion、旧place_ids/place_state及旧place_text口径。
不改模型/profile/向量算法/依赖/锁文件/打包配置，不重建图片向量、OCR、人脸或GPS FTS世代。

## 3. 短设计后完成一个搜索闭环

### 地名识别

使用本地ACTIVE地名/别名和已验证省份索引，不依赖云或LLM。地名目录是识别依据，不能仅识别已有照片的相册，
否则“已知城市但无照片”会被误当普通语义查询。缺省份索引不臆造省份。
第一版只支持纯地名、明确的“地点的内容”“在地点拍的内容”等上述形式；不要求任意自然语言/多个地点/否定语句解析。
按完整名称/别名与最长有效匹配识别，不把“济南路”“巴黎风格”等任意包含地名的文本自动设为城市条件。
未识别或不支持的表达保留原查询；同名歧义显示名称＋上级供选择，未选择不静默缩小到任意城市。

### 查询计划与API

在同一实现报告先写短设计：实际加性API路径/参数/返回、原查询与内容词、已解析地点、歧义选项和续页规则。
优先新增个人地点感知搜索入口（建议POST /api/v1/search/place-aware），普通搜索页面透明使用它；
复用原搜索服务和会话，不能复制一套向量/FTS/RRF实现。旧POST /api/v1/search请求/回显及旧筛选语义保持兼容。
新入口支持显式选定/取消地点；取消后使用剩余内容词全库搜索，不能又从原句自动把地点加回。
无地点请求复用原流程；纯地名复用相册SQLite分页，无需启动query encoder/GPU，不受Top5000截断。
有地点且无匹配照片时返回空结果和地点条件，不自动退回全库。

### 一个地点集合贯穿检索

复用/提取albums.py的当前有效浏览成员规则，不复制另一套GPS阈值或人工控制判断。
人工改到上海后不再匹配济南；人工取消不得用旧候选归回；无GPS/失效/缺包/排除/等距等仍按原相册规则。
不要用原place_ids过滤冒充自动地点范围，它目前主要筛人工assigned资产。新范围与来源、人物、时间、
availability及其他已有显式筛选取交集，去重/可见性保持现有规则，不因GPS条件扩大权限或候选集合。

在统一权威scope中加入加性的地点浏览条件，让向量通道、FTS通道、权威候选补查、末端复核及续页都遵守它。
内容词“日落”传给编码器和内容FTS检索；不要求照片文字同时包含“济南”和“日落”。保留已有RRF及排序。
禁止全库Top-K后再过滤；不能只改FTS一路、只改UI或只提高地名权重来代替地点范围限制。
复用现有受限精确/权威分块路径；无需新增投影、向量后端或性能研究。

缓存/会话需绑定原查询、内容词、选定地点及实际影响成员集合的filter/suggestion revision、活动包和省份索引版本；
版本变化应按现有方式要求重载，不能续页混入过期地点。不得仅沿用place_state=suggested分支的版本检查。

### 页面体验

在普通结果页显示可取消的“地点：济南”条件，GPS依据标“附近城市”；结果还是现有照片网格/预览。
同名时可直接选城市/省份/国家，地名识别无需用户填写ID。保留既有地点相册入口作为浏览选项。
主路径为输入“济南的日落”→搜索→济南范围内的相关结果，无需先进入地点页。

## 4. 小样本验收

新增建议测试tests/integration/test_p4_place_search_flow.py，约20～30张项目内合成资产＋小地名包即可。
向量结果使用受控可复算的测试encoder/index排名，FTS使用有真实命中文本的夹具；分别证明两路受限，
不能只断言解析字符串或按钮文案。合成证据不冒充真实模型“日落”准确率。

必测：

1. 济南日落、济南白天、外地日落、无地点日落构成反例；正确拆查询、两路只返回地点范围内资产，已有融合排序保留。
2. 中文/别名、纯地名、国家/有资料省份、同名选择；无照片地点空结果，未识别地名保留原查询，不误识别包含地名的普通描述。
3. GPS-only与人工地点都可检索；人工改/取消/恢复按原规则，原人工place_ids和place_text行为不变。
4. 与来源/人物/时间等现有条件取交集；小分页去重；地点修订/活动包变化使续页失效而不是返回旧地点。
5. 无地点普通搜索回归；取消地点后全库搜剩余内容词；纯地名无需query encoder、不受语义Top-K限制。

基础定向命令（新增测试落地后运行，新增路径此前不存在，不算已有通过证据）：

~~~powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
uv run --no-sync pytest -q tests/integration/test_p4_place_search_flow.py tests/integration/test_p4_place_auto_flow.py tests/integration/test_p2_search_api.py tests/integration/test_p2_search_authority.py tests/unit/test_p2_search_sessions.py --junitxml=test-results/p4-place-search.xml --basetemp=.pytest-tmp/p4-place-search
uv run --no-sync ruff check src tests tools
git diff --check
~~~

若修改共同向量scope/分块路径，加跑tests/integration/test_p4_01c_retrieval_blocks.py；
若修改其他既有检索/分页路径，补实际受影响测试，记录完整命令/退出码/JUnit，不默认全量pytest或30万性能测量。
WinError 5记环境失败，按正常权限路线重跑，不改ACL。

真实浏览器在项目临时合成图库走一次：输入“济南的日落”→普通照片结果/地点条件→预览→取消条件→重搜；
人工改一张地点后重载，确认退出原范围。记录实际fixture/URL/结果，不用静态断言冒充浏览器。
不扫描或回填真实图库，未来真实使用需要另行明确范围授权。

## 5. 收口

报告保存到docs/reports/P4-PLACE-SEARCH-A-implementation.md，含短设计、加性接口、成员规则/版本绑定、
测试命令/退出码/JUnit、浏览器记录、限制及未验证项；更新status和本卡。
定向测试、浏览器与静态检查完成后写**PASS — 本机个人地点＋内容搜索与小样本验证**。
本卡按V3.1 §9.3的用户新增个人使用补充执行，不单独拆契约/工程主审/Sol卡；人工/身份/旧协议边界不可突破。
达到上述闭环即结束，不自动再开优化、性能、发行或通用自然语言解析卡，其他待审任务状态不变。
实现已提交`c812d10`，验证及未覆盖项见[实现报告](../reports/P4-PLACE-SEARCH-A-implementation.md)。本卡独立JUnit为28/0/0/0，不沿用地点相册40项测试；本次文档提交未重复运行测试。
