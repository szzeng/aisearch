# P4-PHOTO-MEMORIES-SPEED-FIX — 回忆加载速度与缓存修复

- 日期：2026-10-23
- 状态：已实现 / 验收部分完成（真实 HTTP、首卡/封面定量和前台并发时延仍待补测）
- 基线：`403302e`（HEAD 已前进到 `f73becd`；本任务开始时回忆代码与基线一致）
- 上一任务：P4-PHOTO-MEMORIES-UX-A，SCOPED PASS
- 推荐执行模型：DS-H / 高推理；实际由用户选择，本轮不声明模型切换
- 范围：回忆后台年份调度、卡片/精选内存缓存有效性、前端进度轮询与首次显示；定向单元/API测试、30万合成回忆测量、隔离真实浏览器验证。
- 排除：磁盘缓存、schema/迁移、并行年份扫描、GPU/模型/原图、真实照片库、全仓容量认证、提交/推送、下一任务。
- 必读：V3.0 §12.2、§12.5、§14.4（智能回忆）；V3.1 §2.4、§3.2/3.4、§4.2、§9。
- 必读实现/证据：`src/photo_search/api/memories.py`、`src/photo_search/web/app.js`、`tests/unit/test_p4_photo_memories.py`、`tests/integration/test_p4_photo_memories_api.py`、现有回忆报告与忽略目录的30万合成材料。

## 目标与不变量

首次进入只触发一次有界发现；一个低优先级线程完成一年后自行推进窗口中的下一年，前台繁忙/取消/修订变化时让路或停止，且能恢复。API轮询只取状态，不调度计算。最多10年/窗口、单待办槽、每年短快照、单SQL 75ms预算、日/月候选240/62及精选24不变。卡片随年度完成渐进可见；日/月切换复用有效缓存。回忆路由重进必须服务端验证日期和相关修订，过期前端卡片可以暂留但不能混入新身份。缓存容量有界；过期或淘汰不能被表示为有效空结果或破坏分页进度。预览优先于待发现年份，且不得丢失已发布年份。保持日期、资格、viewer/详情、合集幂等与历史导航语义。

## 性能设计（PERF-SCALE 定向）

- 链路与风险：API revision/日期快照、单年日/月 SQL、后台调度、前端轮询、缩略图实际可见及前台读争用；已知 300k 热样本 SQL 约 2–9ms，疑点为约900ms轮询推动年份及TTL/LRU重算。
- 做法与边界：复用现有300k合成目录和压力材料；不改SQL，保持单worker/单待办/75ms/10年上限，比较首次卡片、10年完成、轮询/任务数、热缓存、前台并发、空/密集/无效路径及缩略图显示。另用小隔离图库验证浏览器与重启冷重算；只做重复观察，不形成硬 SLA。
- 验证与停止：先测当前树，再改动后以相同数据/机器/入口对照；记录API响应、首次卡片/封面、年SQL、让路及前台延迟。达到目标方向且正确性/前台优先成立即停止；若 SQL 快而封面慢，明确归因，不引入持久缓存。

## 验收（D，高风险状态/恢复；按 V3.1 §3.2/3.4）

1. 定向回忆 unit/API JUnit：10年自主推进、早期发布、无重复排队、预算/取消/DEFERRED恢复/STALE、日月切换、TTL/LRU恢复、前端旧响应与日期/图库/规则/收藏修订失效、viewer/保存回归。
2. Python `ruff check` 和 `py_compile`、相关 `node --check`、`git diff --check`。
3. 复用30万合成数据实测前后；性能和后台并发材料保存于 `test-results/P4-PHOTO-MEMORIES-SPEED-FIX/`，不入 Git。
4. 使用 Codex 内置浏览器、127.0.0.1、隔离合成 PhotoSearchData 真实查看首次骨架/首卡/封面、分类、返回历史及服务端重进核验。
5. 独立复核：按用户要求使用子代理 Luna High 全面核查本任务所有改动、调用链、测试与证据；DS-MAX/Sol 仅按任务策略触发，不能把未做的复核记作通过。

## 文件边界

允许：`src/photo_search/api/memories.py`、`src/photo_search/web/app.js`、直接相关回忆测试、`docs/tasks/P4-PHOTO-MEMORIES-SPEED-FIX.md`、`docs/reports/P4-PHOTO-MEMORIES-SPEED-FIX.md`、`docs/development-status.md`。已有其他用户修改和文件保持原样。无 schema、依赖或公共协议变更。

完成后仅更新本任务卡、报告、开发状态；不提交、推送或生成/启动下一任务。

## 执行结果（2026-10-23）

- 单worker自主处理当前最多10年窗口；API状态读取不推进任务；缓存移除固定TTL，按日期/相关修订/算法版本校验，保留完成窗口并修复旧身份待办阻塞。前端快速轮询首卡状态、已有卡片后降频、终态停止，并在重进与409后重新核验身份。
- 定向回忆 unit/API **25/25**：`test-results/P4-PHOTO-MEMORIES-SPEED-FIX/focused-final6.xml`；Ruff、py_compile、`node --check`、`git diff --check` 通过。Luna High 最终只读复核未发现代码 blocker，并确认 stale-pending 回归用例仅由 revision 变化触发。
- 30万隔离合成目录模拟轮询对照：首卡状态可见约 **900→251ms**，十年窗口状态完成约 **9.15s→251ms**，状态请求/轮询 **11/10→2/1**；热缓存读取两版均无新增候选计算。该结果只代表 runner 状态调用加旧900ms/新250ms轮询模拟，不代表真实 HTTP、首卡 DOM 或封面时延。
- 隔离 Codex 内置浏览器观察骨架、卡片/缩略图、分类、精选、Back/Forward、全部日期、普通合集保存及修订变化后的409→无游标刷新。真实点击到API/首卡/封面耗时、逐条SQL时延、前台搜索并发响应和真实 DEFERRED 竞争未量化；原生200%缩放与全仓容量认证未做。因此总体 **IMPLEMENTED / ACCEPTANCE PARTIAL**，相关边界见[执行报告](../reports/P4-PHOTO-MEMORIES-SPEED-FIX.md)。未访问真实照片库/原图/GPU，未提交、推送或开启下一任务。
