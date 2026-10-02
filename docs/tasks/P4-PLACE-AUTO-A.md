# P4-PLACE-AUTO-A — 一张卡完成个人GPS地点相册与地名查找

日期：2026-10-02，个人使用简化版。状态：**READY FOR PERSONAL IMPLEMENTATION / NOT STARTED**。
推荐：**DS-H / DeepSeek V4.1 Flash / 高推理**，用户手动选择；实现/修复/测试尽量同一会话。
本卡取代此前同ID的独立契约冻结卡，不再生成B/C或默认独立审查卡。
上层：[简化方案](../plans/P4-PLACE-AUTO.md)；依据：[核对记录](../reports/P4-PLACE-AUTO-design.md)。

## 1. 目标与授权

完成一个本机可日常使用的闭环：**GPS自动按附近城市整理→地点封面/数量→照片网格→地名查找→人工纠错**。
保留城市＋国家/省份。用户已要求个人相册、简单功能与测试即可，按V3.1 §9.3限定例外执行。
本轮立卡不是代码已实现；用户要求执行本卡后再实现。真实个人库读取/补齐须另有明确范围授权。

开工按AGENTS.md→development-status→本卡→方案/报告读取，记录实际HEAD/已有编辑。
立卡HEAD=9ebcbef；原有未提交docs/tasks/P2-04A.md保留，不沿用它返回P2-05的旧路由。
embedding等待审事项不纳入此任务或追认PASS。执行本卡时明确更新状态入口。

## 2. 读取与允许修改范围

本次已经做过设计核对，实现不重复全仓或全文架构审计。重点读取：

- 主架构§4.3/4.5（含新§4.5.5）、§9.3/9.5、§10.1；V3.1 §3.2/3.4、§4.1、§9.3；
  ADR-0005的人工控制/排除/旧筛选，ADR-0011排序。
- places/{suggestions,filter,directory,assignments,service,revision}.py，geo/{extract,resolver,dataset}.py。
- api/{models,queries,routes}.py，search/{service,retrieval,sessions}.py；
  refresh/pipeline.py中GPS/解析缺项调度，tools/places/{prepare_geonames,manage_dataset}.py。
- web/places与web/search的HTML/JS/CSS、现有缩略图/详情组件、直接相关测试。

允许上述目录增加最小浏览服务、只读省份索引、加性相册API/UI、小包准备与定向测试，更新主报告/status。
禁止改原照片/真实data root、人工assignment/undo/排除语义、旧place_ids/place_state/place_text、
历史migration、既有冻结companion、模型/profile/向量算法、依赖/锁文件或打包配置。
优先不加数据库迁移/独立持久投影，使用SQLite当前事实派生集合；不搭新后台系统/通用插件框架。

## 3. 同一会话内的实现步骤

### 第一步：短设计，然后直接实现

把实际API路径、浏览集合谓词、省份索引结构、翻页版本写入同一主报告，无需先开契约冻结卡。
保持既有人工事实/建议含义；新增的是有来源的自动浏览能力，不能称为确定行政归属。

| 当前人工状态 | 用于相册的地点 |
|---|---|
| MANUAL_ASSIGNED | 人工地点；省份可由其城市祖先的附加资料提供 |
| MANUAL_UNASSIGNED | 无地点，阻止自动分组 |
| 无控制行/AUTO_ALLOWED | 当前GPS结果的最近城市，50km以内，标“GPS附近城市” |
| 无GPS/过期输入或结果/未知基准/缺包/等距/太远 | 无自动地点，人工入口仍可用 |

校验当前sha/GeoFact、活动包、候选local place ACTIVE及当前人工排除。
ASSUMED_WGS84允许作带说明的推算；不取全部候选，被排除的首选不自动顺延到更远城市。
50km是个人整理默认上限，做边界测试/小样本观察，不做阈值研究或准确率承诺。
人工编辑后重新读取最新控制，晚到结果不得影响人工控制。

省份优先加入包的可选INDEX文件：城市外部ID→省级ID/名称/别名，使用同来源快照和包文件checksum；
旧目录kind/parent保持，不新增admin kind。省份按外部ID/代码分组，不能只按名称合并。
老包无INDEX仍能浏览城市/国家；新准备的小包必须演示省份。缺省份资料显示缺级，不伪称真实包已补齐。
优先用现有公开原始数据＋小型省份映射文件，不下载全球大包/模型。新文件放入新准备包，不原地改活动包。
自定义地点无城市依据时不强行补省份。

### 第二步：前后端一起打通

- 一个SQLite浏览集合用于聚合、封面、名称入口与照片列表；复用现有可见性/来源/可用性规则，
  asset_id去重，人工与GPS数量不重复，国家/省份按资产集合求并集。
- 新增加性相册查询，旧目录/人工筛选API兼容。读API不需要GPU，不触发来源扫描或全库处理。
- 首页默认有照片的城市卡片，可切国家/省份、搜名称/别名；卡片有缩略图、上级文字与完整总数。
- 点击卡片看完整分页照片，复用缩略图/详情/排序；不走语义Top5000截断。
- 主搜索框输入地名，显示地点入口和少量照片预览，选定后看全部；同名显示国家/省份消歧。
  可提供明确的地点模式直接查照片；普通语义/FTS混排保持，不静默剥离自然语言词成硬筛选。
- 翻页/缓存绑定影响集合的现有revision、活动包/省份索引版本，变化时重载，计数/列表同快照。
- 复用人工指定/取消/恢复自动/撤销，按名称选地点。首先保证详情单张纠错，原批量入口继续可用，
  不为本卡新增“全选几万张”的批量系统。

已有照片：有效GPS/候选直接可查；缺GPS/解析的按asset_id小批复用现有任务，去重并允许重启继续。
最小界面一个“补齐照片地点”入口＋已处理/剩余/失败摘要，不开发独立回填状态机/复杂管理页。
打开地点页不重新扫来源或重建向量/人脸/OCR；真实库执行仍需范围授权。

### 第三步：小样本验证，达到可用即结束

约20～30张项目内合成照片/资产＋小包，复用现有harness；不读取真实照片。
新增测试验证用户行为/真实状态，不给按钮文案和内部函数逐项写镜像测试。

必测：GPS-only自动分组及中文/别名入口；国家/省份汇总；人工改/取消/恢复；去重与小分页；
50km内/边界/外、等距、无/坏GPS、未知基准/缺包；人工编辑后重载/重启；
补齐重复不重复，原图与其他能力不变。沿用现有任务/事务资格检查，不扩展长时间故障全集。

新增建议测试tests/integration/test_p4_place_auto_flow.py，实际创建后以最终路径为准，收敛后执行：

~~~powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.uv-cache'
uv run --no-sync pytest -q tests/integration/test_p4_place_auto_flow.py tests/integration/test_p2_place_api.py tests/integration/test_p2_place_resolver.py tests/integration/test_p2_search_api.py --junitxml=test-results/p4-place-auto.xml --basetemp=.pytest-tmp/p4-place-auto
uv run --no-sync ruff check src tests tools
git diff --check
~~~

若修改包准备/读取，再补tests/unit/test_p4_02y_p_geonames_prepare.py和tests/integration/test_p2_place_dataset_api.py；
新增索引测试坏hash/缺索引/旧包兼容。
若修改普通搜索service/sessions/权威候选路径，再补tests/integration/test_p2_search_authority.py、
tests/unit/test_p2_search_sessions.py。只扩实际受影响集合，不默认全量pytest/30万测量。
补测也需JUnit和.pytest-tmp，精确命令/退出码/计数写报告；collect-only或无输出不能算通过。

真实浏览器一次：地名输入→城市/省份入口→照片网格→预览→改/取消→重载复核。
记录URL、夹具和实际结果，静态断言不冒充浏览器。无需多机型/视口矩阵、性能分位数研究或exe。
WinError5记录环境问题，走正常权限重跑，不改ACL；失败只修最小边界及直接回归。

## 4. 完成与报告

同一主报告：docs/reports/P4-PLACE-AUTO-A-implementation.md，含短设计、改动/API、GPS规则/省份来源、
JUnit/命令/浏览器、失败与未验证项。无需独立契约/审查报告或反复切模型。

必测和浏览器完成后写**PASS — 本机个人地点相册与小样本验证**，更新status，转个人试用反馈；
不自动生成B/C、审查/优化/发行卡。未验证完写IMPLEMENTED / VALIDATION PENDING，不提前PASS。
不声称30万性能、全量地名准确率、精确行政区、真实省份包已安装或现有exe支持。

若发现必须修改旧人工控制、身份、不可变协议或既有migration，记录具体冲突并缩回加性浏览方案。
不因简化测试突破人工安全底线；有实际新需求或无法局部修复的问题才说明，不默认延伸研究。
