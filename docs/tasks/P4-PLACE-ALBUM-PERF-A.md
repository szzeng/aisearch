# P4-PLACE-ALBUM-PERF-A — 地点相册首次加载性能优化

日期：2026-10-11。状态：**PARTIAL / 列表优化及有界封面预生成已实现；真实冷首屏待用户复测**。
执行建议：**GPT-6 Luna / High**，同一会话完成实现、必要修复及定向验收；用户手动选择模型。个人项目，只做直接相关的简单测试。

## 1. 目标

解决打开「地点整理」后地点相册首批卡片和封面需要数秒才能出现的问题。参照已完成的 `P4-PERSON-ALBUM-PERF-A`，以**减少重复 SQL、优先显示相册、延迟无关管理请求**为主；图片优先级优化为辅。

先用少量合成请求定位后端列表、地名包冷校验、其他启动请求和封面各自耗时，再实施。不得仅因 SQL 次数下降便声称真实图库首屏已提速；真实浏览器冷/热速度未测就标记 **NOT MEASURED**。不设定必须达到的毫秒数或提速百分比。

## 2. 当前仓库依据与改动范围

实施前检查最新 HEAD、未提交修改，并按 `AGENTS.md → docs/development-status.md → 本卡` 阅读；架构与执行策略仅查地点浏览、版本一致性和个人验收相关节，不全仓重读。

- `src/photo_search/places/albums.py::albums`：`_album_rows` 查询照片与地点关系后，Python 逐行分组；城市/国家的父级和城市别名存在逐照片重复 SELECT；去重采用列表成员测试。非 CN `album_assets` 调用 `albums` 后再次执行 `_album_rows`。
- `src/photo_search/places/cn_facets.py::collection/page/members_sql`：CN 省/市/区县已有权威 SQL 聚合和分页，不要另建一套归属判断。
- `src/photo_search/places/albums.py::_index`：缓存未命中时读取/验证复合包文件；`src/photo_search/places/datasets.py::render_status_snapshot`：地名包状态读取会验证磁盘文件。二者可能造成冷启动 I/O，不可未经测量认定全部是瓶颈。
- `src/photo_search/web/places.js::boot`：首次进入即启动 capabilities、datasets、directory、operations；`places-albums.js::loadAlbums` 立即请求相册列表，生成全部卡片，图片均为 lazy。
- `src/photo_search/web/places.html`：同页容纳相册、地名包状态、目录、建议与撤销入口；脚本加载顺序与首屏请求顺序需检查。

直接相关文件优先控制在上述 6 个；测试尽量复用 `tests/integration/test_p4_place_auto_flow.py`、`tests/integration/test_p4_place_cn.py` 和 `tests/unit/test_p4_place_cn_ui.py`。不动 schema、migration、搜索语义、真实图库、模型和 GPU。

## 3. 实施顺序

### A. 优化地点列表及照片页的重复读取（必做、优先）

1. 在保持现有有效成员谓词与结果等价的前提下，**按唯一地点批量获取父级名称、别名**；绝不在每张照片循环里执行相同地点的 SQL。对同一相册成员用集合去重，避免 `asset_id in list` 的线性重复检查；首个/现有封面选择顺序保持不变。
2. 根据实际 SQL 计数和耗时，优先以集合化查询/聚合获得计数、封面与来源；若全 SQL 聚合需要大量重写或改变人工优先级，保留现有成员读取方式，只完成有证据、低风险的 N+1 消除。不得为微优化大改地点归属框架。
3. 优化**非 CN** `album_assets`：避免每次先全量重新构造 `albums` 再扫描全部成员。查询可以只围绕选中的地点计算，但必须保留旧版 offset cursor 对应的排序/总数/续页契约，及 catalog/suggestion/package revision 的变更拒绝。不可先引入全新 keyset 协议而不处理现有调用者。
4. **CN** 分组继续使用 `cn_facets.members_sql/collection/page`，不复制 GPS、人工排除、legacy 或来源兼容规则；若 CN SQL 没有实测瓶颈，不动它。
5. 不能通过不再执行成员验证、粗暴限定 1000 张照片或向缓存中永久保存照片 ID 来“提速”。

### B. 页面先显示相册，其他功能按需请求（必做）

1. 首屏优先发起相册列表请求，不让地名包校验、目录、操作日志先占用关键路径。现有导航/能力探测正常工作。
2. 地名包状态、目录、操作历史只有相应板块进入视口、被用户打开、通过锚点直达或明确操作时才首次加载；对无 `IntersectionObserver` 的环境提供可用的简单回退，不允许板块永远空白。
3. 同一个页面会话中已经成功读取的数据不因重复进入同一区块而无条件重读；显式刷新、地点修改/撤销、地名包更新及已知 revision 变化后必须失效。失败不视为成功缓存；请求并发需防止旧响应覆盖新结果。
4. 建议整理等已有显式加载入口、操作按钮、直达锚点和原有权限/错误信息不能失效。不能把延迟加载变成“隐藏数据”或静默吞错。

### C. 首屏封面与渲染（必做，小改）

1. 默认首屏前约 4 张封面使用 eager/high priority，其余保持 lazy。不要一次高优先级拉取所有封面；遵守现有 thumbnail URL/ETag/权限规则。
2. 切换城市/省份/区县/国家或输入检索词时，保持 `albumRequest`、`photoVersion` 的防旧响应机制。只有已验证数据未变化才考虑复用图片 DOM；确实变化必须刷新。
3. 不先上虚拟滚动或新的分页 API。如卡片过多实际造成明显 DOM 阻塞，才补最小分块绘制；不能让搜索只搜索已显示的局部数据。改动 JS 时同步更新 HTML 对应静态资源版本，避免浏览器使用旧 JS。

### D. 地名包冷校验（有证据才改）

1. 分别测 `_index` 缓存未命中与命中、datasets 状态请求耗时，区分大文件 SHA-256 校验和 SQL 统计。
2. 可以复用**身份及有效性已受信任**的进程内不可变索引/校验结果，或把与首屏无关的状态校验延迟到需要时；活动包/内容哈希/文件有效性条件变化，必须重新校验。
3. 不允许靠忽略 manifest、跳过必要校验或只比文件时间戳掩盖缺包、损坏、schema 不兼容。若不能小改保证正确性，只做 B 的非关键路径延迟并在报告中保留冷校验耗时。

## 4. 必须保持的业务行为

- 地点城市/省份/区县/国家分类与名称、父链、照片数、封面选择、别名筛选结果不变；按有效 asset 去重，跨来源同一照片不能重复计数。
- MANUAL_ASSIGNED 优先、MANUAL_UNASSIGNED 禁止自动归属、主动排除、生效 revision、OSM/GeoNames/CN legacy/外部来源依据均不变。
- `PRESENT` 可用照片及当前 GPS fact、唯一候选、距离限制、地名包状态约束继续有效。
- 第 48 张后正确续页，照片详情/预览和更正地点继续可用；旧游标在人工修改、GPS 解析或包切换后按既有契约拒绝，不静默显示旧成员。
- 真实用户 `PhotoSearchData` 不作访问、扫描、改写、迁移或重新建库，除非用户另行明确授权。

## 5. 个人项目最小验收

仅直接相关测试和最小合成测量，**不跑全仓 unit/integration、不跑 30 万压力、真实模型/GPU、打包和多轮审查**。

1. **列表等价**：小型合成库中多个城市、省/区县、同一地点多张照片、跨来源重复 asset、人工指定/取消/排除、无地点；比较修改前后数量/封面/顺序/别名检索，记录 SQL 次数，确认不再每照片重复父级/别名查询。
2. **分页与版本**：一个相册至少 49 张（首批 48 张及下一页），无重复、总数正确；地点归属变化后重读得到新数量，旧 cursor 正确拒绝。至少含 1 个 CN 与 1 个非 CN 路径。
3. **前端启动**：Node 简易 DOM 桩检查首次只加载必要区域、滚动或锚点直达后管理区正常加载，快速切换地点类型旧响应不会回盖，新数据刷新有效，首屏封面优先级正确。保留现有中国地点暂停/继续相关行为。
4. **少量耗时证据**：同一合成数据各测一次冷/热地点列表、datasets 请求及 SQL 数量、启动并发请求数量；允许简单重复以排除偶然异常，不做大样本性能门禁。真实浏览器/用户库未测均明确记为 NOT MEASURED。

建议最小检查（按实际改动和已存在测试选用）：

~~~powershell
.\.venv\Scripts\python.exe -m pytest -q tests/integration/test_p4_place_auto_flow.py tests/unit/test_p4_place_cn_ui.py --junitxml=test-results/P4-PLACE-ALBUM-PERF-A.xml
node --check src/photo_search/web/places.js
node --check src/photo_search/web/places-albums.js
git diff --check
~~~

修改 Python 时运行对应 Ruff；若实际改动 CN 规则附近，补跑 `tests/integration/test_p4_place_cn.py` 中直接对应的 1–2 个测试（用 `-k` 精选），将真实通过数和退出码写入 JUnit/报告。无需为本任务修绿历史全仓失败。

## 6. 完成/停止条件及报告

按 **A → B → C → D（仅证实必要时）** 实施。以数据与分页正确、明显减少 N+1/重复工作、管理请求不再抢首屏为定向完成条件；不能仅凭静态检查宣称实际用户体感 PASS。

交付 `docs/reports/P4-PLACE-ALBUM-PERF-A.md`，简述提交基线、关键代码、优化前后 SQL/请求/合成耗时、实际定向 JUnit 数量、未验证的浏览器与真实图库表现，以及剩余主瓶颈；更新 `docs/development-status.md`。若必须扩大到 migration/归属协议/持久缓存/大规模重写，则停止扩张、明确记录，并按 V3.1 的受影响面审核要求另立卡，不默认豁免高风险审核。

2026-10-11 执行记录：已完成低风险实现及 CN/非 CN 合成 50 张分页、UI 延迟加载、6 项定向 JUnit；但用户打开页面仍看到“正在整理地点相册”及地名包加载提示约 3–5 秒，不能宣称目标完成。临时 10,000 手工成员合成 catalog 的地点列表 HTTP 冷读 85 ms、热读 53 ms，状态 HTTP 4.7 ms；该证据不代表实际库/包。用户不确定测试时是否重启后端和强制刷新。继续前需确认运行的是新后端及新脚本，并分别定位相册列表 API与 `/places/datasets` 冷读；真实 PhotoSearchData 尚未访问。完整报告见 `docs/reports/P4-PLACE-ALBUM-PERF-A.md`。

后续用户反馈：重启/强刷后列表已变快，首次封面仍需数秒并先显示灰底，且与地名包刷新同时发生。缩略图 API 缓存未命中时同步生成；地名包状态显式延迟后图片速度无明显变化，已恢复进入视口时按需读取。缩略图生成已移到线程池，使用独立只读 catalog 连接并限并发 2，避免同步解码阻塞事件循环；真实图像速度待用户重启后验证。地点目录由用户认为多余，改为查询按钮或锚点直达时读取。用户新增排序要求：地点相册照片数降序，同数按最近加入图库时间降序；实现依据 `assets.created_at`。

本任务是**一次加载优化**，不做地点归属改版、不重建图库。

2026-10-11 用户复测补充：首次显示时仅一张封面立即出现，其余仍慢；这些封面显示过一次后，再刷新明显变快。冷资产首个 `/thumbnail` 请求仍需同步等待缩略图生成；线程池变化只解除 API 事件循环阻塞，不能降低单图生成耗时。报告已记录。让首访即显示需扩大到打开页面前预生成/预热；扫描/后台队列的资源代价和触发策略未确定，当前维持 PARTIAL，不宣称图片提速。

用户先选择保持现状，随后明确要求“增加预生成”。已增加有界后台预生成：应用启动时及成功扫描队列排空后，每类地点只预生成排序前 4 张封面（城市、省份、区县、国家，最多 16 张）；使用现有 content-addressed 缩略图缓存，已存在则跳过；不在扫描 worker 内解码，不改 Schema。


2026-10-11 预生成实现与验收：新增 `PlaceCoverWarmer`，启动和成功 scan queue drain 时触发，单协调 worker 合并触发并发两张缩略图并仅处理各类前 4 张；复用 `/thumbnail` 的同一内容哈希 artifact，未命中继续经受控路径校验、只读 catalog worker 与有界生成槽。单资产同类请求 single-flight，预热中前台不会重复解码。JUnit `test-results/P4-PLACE-ALBUM-PERF-A-prefill.xml` **15/0/0/0**；Ruff、py_compile、Node 语法、diff-check 通过。真实浏览器冷首屏及实际图库预热耗时未测。
