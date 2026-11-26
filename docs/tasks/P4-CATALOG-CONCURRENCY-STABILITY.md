# P4-CATALOG-CONCURRENCY-STABILITY — 数据库并发与前台响应稳定性专项

建卡日期：2026-11-26。状态 **A0 SCOPED PASS / A1 IMPLEMENTED · VALIDATION PENDING / B1 SCOPED PASS / B2 CONDITIONAL**。当前规划基线：GitHub `master`，`b4f826e579b0e731bab064f7be0ee50dd0768b1c`。

本卡是专项父任务，只负责统一问题边界、诊断方案、执行顺序和联合验收。产品改动分别由工作包 A、B 承担。初始规划提交仅建立和关联任务文档。A0 的运行证据记录于下。

## A0 · 查询池锁序复现与修复（2026-11-26）

**A0 SCOPED PASS / TERRA HIGH REVIEW PASS**。合成子进程复现 `_lock` 与 `_activity` 双向互等，修改为锁外注册 Future 完成回调。复现前 1 个目标失败、4 个阴性对照通过，修复后 7 个新增场景及最终受影响 JUnit 328/328 通过。取消/关闭、Memories 和数据根 lease 回归通过。 结果见[诊断报告的 A0 后续验证](../reports/P4-CATALOG-CONCURRENCY-DIAGNOSTIC.md#a0-后续验证--查询池锁序死锁)。

A0 是用户单独授权的 P0。用户指定 Terra High 替代 A0 默认 DS-H，执行完整受影响面独立审查。 独立重跑 7/7，无 P0/P1/P2 发现。A0 单独验收。

## A1 · 图库明细读取（2026-11-27）

**A1 IMPLEMENTED / VALIDATION PENDING**。图库明细按来源单飞并保留有效缓存，入口有界等待并在实际 Future 结束后释放容量。空范围复用只读快照和位置计数，读 SQL 13→8（无模型合成臂）。最终受影响 JUnit 400/400，含 A0 7/7 和真实 HTTP 断开/关闭回归；前端定向 3/3。已观察合成图库真实页面、窄窗口和排队，用户停止 Computer Use 后，页面失败/重试由自动测试覆盖，未现场复验。独立 Terra High 初审发现跨来源共享资产的旧明细。续修覆盖终态后 B 的缓存失效、摘要请求失败和旧响应，独立最终复核 `REVIEW PASS`，新旧定向组合 9/9。真实浏览器失败与重试仍待验。 [A1 报告](../reports/P4-CATALOG-CONCURRENCY-A1.md)

## 0. 任务结构与关系

本专项包含两个相关但根因不同的工作包：

| 工作包 | 任务卡 | 负责问题 | 状态 |
|---|---|---|---|
| A | [P4-SOURCE-DETAIL-STABILITY](P4-SOURCE-DETAIL-STABILITY.md) | 查询池锁序、图库明细读取卡顿、重复请求和 Worker 排队 | A0 SCOPED PASS。A1 IMPLEMENTED / VALIDATION PENDING |
| B | [P4-FOREGROUND-WRITE-FAIRNESS-A](P4-FOREGROUND-WRITE-FAIRNESS-A.md) | 前台人物写入与后台建库并发治理 | B1 SCOPED PASS / TERRA HIGH REVIEW PASS，受影响 JUnit 225/225。B2 CONDITIONAL（仅在 B1 后仍实测写锁饥饿时实施）。[B1 报告](../reports/P4-CATALOG-CONCURRENCY-B1.md) |

两个问题共享“后台工作不能拖垮前台响应”的产品目标，但不能混成一把数据库锁：

- A 主要涉及只读 Worker 池、SQL 读取成本、前端请求生命周期和文件/数据库 I/O。
- B 主要涉及 SQLite 单写者竞争、后台写事务的连续占用、前台写入意图和同步代码阻塞事件循环。
- WAL 允许读写并行，但 SQLite 仍只有一个写事务能同时持有 writer lock。只读 Worker 饱和不等于 SQLite 写锁死锁；前台写锁等待也不等于只读 Worker 队列满。
- 不新增覆盖全系统的全局互斥锁，不通过提高 Worker 数量或延长 timeout 掩盖问题。

## 1. 用户问题与证据边界

### 1.1 图库明细读取问题（工作包 A）

已报告的复现形态：

1. 登记两个尚未建库的图库。
2. 分别或同时展开“任务与统计明细（按需读取）”。
3. 同时打开两个图库时，页面和后台可能长时间没有可见进展。
4. 登记新来源后更容易出现卡顿。

已有代码审查发现：只读 Worker 数量为 2；来源列表刷新会清空全部明细缓存但保留展开状态；同一 source_id 没有完整的 in-flight 去重；未建库/空 scope 仍可能执行多组统计查询；来源级统计还会计算全库 assets_total。这些是已确认的代码形态，具体哪一项构成本次卡顿主因仍须由 A 的合成测试和性能测量确认。

### 1.2 后台建库与前台人物修改问题（工作包 B）

已报告的复现形态：

- 后台正在建库，前台同时给人物修改名称。
- 应用一度无法恢复，用户观察到页面与后台长时间停滞。
- 用户确认后台扫描、语义索引、人脸识别、OCR 等阶段都可能发生，不能把问题预先限定为语义阶段。

用户提供的事故日志中，人物标签 PUT 和随后的人物 browse GET 最终均返回 200 OK，缩略图请求也有 200/304。它证明这些请求最终获得了成功响应，但没有给出等待时长、事务持有者或线程状态；不能单凭这段日志认定发生了 SQLite deadlock，也不能排除请求曾长时间等待。

当前代码有两条明确风险线索：

- 人物标签路由是 async handler，但直接调用同步 PersonService.rename；该方法进入 Catalog.write，等待 SQLite 时可能阻塞 API 事件循环。
- 人物改名在同一个原子事务中读取/更新人物操作 bundle，并发布照片搜索投影和人脸修复队列。人物关联较多时事务可能比单字段 UPDATE 更长。该事务的原子性不能为了缩短锁时间而随意拆开。

### 1.3 历史并发证据

- P4-02U 合成压力测试曾测得后台 drain 写锁占用率 98.96%，线程臂最长连续不可用约 32.45 秒；这是历史合成测试证据，不是本次事故的现场测量。
- P4-02V 采用搜索追赶侧 100ms 有界等待，不能覆盖人物改名等直接写操作，也没有让后台 drain 主动让出 writer lock。
- P4-LOCK-CONTENTION-RECOVERY-A/B/C/D 已处理 BEGIN BUSY 有界重试、扫描恢复、模型资源锁等待/取消及相关生命周期问题；这些机制应复用，不重新实现。
- P4-BUILD-STABILITY-A2 已补充后台 Worker 生命周期、线程自有 Catalog/SQLite 连接关闭、lease 与恢复机制；其目标硬件/真实模型验证边界仍按原报告保留。

历史结果不能替代本专项的当前代码基线测试。

## 2. 统一技术目标

后台建库持续运行时，前台至少应能：

- 正常打开图库来源、明细和普通照片页面；
- 正常浏览人物相册和照片预览；
- 修改人物名称、执行已支持的人物维护操作；
- 及时获得明确的成功、冲突、忙或可重试结果，而不是无期限假死。

后台在前台操作结束后必须继续推进，不能丢失、重复领取、重复确认或错误完成任务。

## 3. 实施原则

1. **先测量，再优化。** 区分 SQLite BEGIN 等待、事务持锁、COMMIT、只读 Worker 排队、连接打开/关闭、SQL 执行和 API 事件循环阻塞。
2. **A、B 分开实施。** A 只读请求生命周期和 SQL 快速路径，与 B 的写入优先机制分别验收。
3. **统一诊断，不统一锁。** 可共享诊断字段、合成数据夹具和最终压力场景；不能让只读 Worker 和 SQLite writer 共用一把全局互斥锁。
4. **后台只在安全边界让步。** 不强行中断活动事务；先结束当前事务，再阻止开始下一批后台写入。
5. **保留数据一致性。** 不拆分人物操作原子事务；不拆分 LanceDB 写入与 SQLite outbox 确认之间的既有协议。
6. **前台优先也必须有后台进展。** 连续前台流量不能让后台永久饥饿；公平策略和最大延迟边界须由基线测量与测试确定，不先臆造性能数字。
7. **连接线程归属明确。** SQLite connection/Catalog 只在创建它的线程中使用和关闭；不得把 API 事件循环线程的 Catalog 直接交给 worker thread。

## 4. 共享诊断设计

### 4.1 SQLite 写事务

复用 Catalog.write 当前的 BEGIN BUSY 失败详情，补齐成功和失败路径的分段耗时：

- operation / stage：受控的操作类别，不记录用户内容；
- role：foreground / background / unclassified；
- process_id、thread_id、thread_name；
- begin_wait_ms；
- transaction_body_ms（从 BEGIN 成功到 COMMIT/ROLLBACK 前）；
- commit_ms；
- retry_count；
- outcome：committed / rolled_back / begin_failed / commit_failed；
- SQLite error code/name。

在当前进程内维护短生命周期的活动事务登记，帮助识别本进程持锁线程和阶段。SQLite 无法让等待连接直接查询其他进程的持锁者；跨进程 holder 必须允许显示 unknown，不能猜测身份。

诊断采用 opt-in 或慢操作采样；不记录 SQL 展开文本、照片路径、人物名称、原图内容或其他隐私。不得新增持久诊断表。

### 4.2 只读 Worker 与明细

复用现有 catalog_reads 性能指标，包括 queue/open/close 时间和 SQL 语句计数；为来源明细拆分统计阶段耗时。A 需要区分请求重复、Worker 排队、SQL 慢查询和文件/磁盘竞争。

### 4.3 关联方式

尽可能沿用现有 request_id、operation_id 和后台 stage/job 标识，形成一次前台操作与后台批次的时间线。日志字段必须低基数、脱敏，避免将个人数据写入诊断日志。

## 5. 工作包 A：图库明细读取稳定性

任务卡：[P4-SOURCE-DETAIL-STABILITY](P4-SOURCE-DETAIL-STABILITY.md)。

保留原卡的目标和不变量，按以下顺序执行：

1. 最小诊断：单个未建库明细、两个明细并发、登记第三个来源后刷新、明细与后台建库并行。
2. 前端请求生命周期：按 source_id 单飞；保留仍存在来源的缓存；定向失效；处理列表重建、折叠/重开、旧响应和取消。
3. 空 scope 快速路径：在权威 scan/built/PRESENT 事实确认后，跳过无效统计 SQL，同时保持 UNBUILT/NO_IMAGES/UNAVAILABLE 等语义完全等价。
4. SQL 查询计划：重点审查 semantic_breakdown、enrichment_counts、ocr_enrichment_counts 和 source-specific catalog_counts；只有 EXPLAIN 与结果 oracle 支持时才优化。
5. 只读 Worker：默认保留 CATALOG_WORKERS=2；先做明细有界并发与前台余量，不盲目增加线程。

A 不负责修改 SQLite 写入公平性，也不应通过全局锁限制后台写事务。

## 6. 工作包 B：前台写入优先与后台安全让步

任务卡：[P4-FOREGROUND-WRITE-FAIRNESS-A](P4-FOREGROUND-WRITE-FAIRNESS-A.md)。

B 的初始目标是人物写操作和建库后台写事务，实施前先做完整调用链审查：

1. 人物写入口盘点：rename、merge、undo、人工关联/修正、split 等实际存在的用户触发写操作；只纳入当前 API 真实入口，不臆造接口。
2. API 线程隔离：用户触发的人物写操作不在 async event loop 中同步等待 SQLite；使用独立、受生命周期管理的写入 Worker。Worker 必须在自身线程内打开连接、构造 Catalog/PersonService、执行并关闭；不得跨线程复用请求线程的连接。
3. 前台写意图：在请求进入 worker queue 前登记 foreground intent，使后台能在安全批次边界看到前台等待者。
4. 后台让步：按明确 stage/role 识别应用管理的后台写入；在 BEGIN/批次安全边界等待前台意图。协调器只维护短生命周期状态/Condition，不在数据库事务或 LanceDB 操作期间持有 Python 全局锁。
5. 后台恢复：前台事务完成后唤醒后台继续；取消、异常、关闭均释放登记；已经开始的原子事务不强制中断。
6. 防止后台饥饿：测试连续前台写入下后台仍能在有界时间内取得批次机会；公平上限依据基线和产品可接受响应确定。
7. BEGIN BUSY：现有 5 秒 timeout、有限重试和 scan/drain 恢复语义须逐项核对。若为让步引入角色化等待预算，必须证明不会把普通业务体或 COMMIT 错误误判为可重试 BUSY。

建议优先复用 catalog_reads 现有活动跟踪/Condition 的设计经验，但其当前状态只跟踪该 owner Catalog 的只读 Worker 请求，不足以直接代表全进程前台写入意图。实现前必须核对 Catalog owner、独立后台连接和同一数据根的关联方式。

## 7. B 必须审查的后台阶段

以下是调用链审查清单，不代表每个阶段都要改代码。只在确实持有/频繁申请 SQLite writer lock 的安全边界接入让步：

- 文件扫描与 file location 状态更新；
- 内容 hash 后的 asset/location 绑定；
- 语义分类、任务入队和索引 outbox drain；
- 搜索投影 catch-up；
- 拍摄时间、GPS、地点等 enrichment；
- 人脸检测结果提交、人脸索引发布和自动详情任务；
- OCR 任务入队、结果提交及其他控制性写入。

模型推理本身应在 SQLite 事务外进行。BACKGROUND_MODEL_SLOT 与 ModelResourceGate 是模型资源协调，不是 SQLite 写锁；不得改变既有锁顺序或在等待数据库期间无意持有 GPU 模型槽。

对 IndexCoordinator.process_batch，LanceDB upsert/delete 与 SQLite outbox 状态确认的现有原子恢复边界必须保留。只允许在完整 batch/transaction 之间让步，不能在 LanceDB 已写而 SQLite 尚未确认的中间阶段拆开或暂停。

Lease heartbeat、任务状态恢复和关闭控制等维持任务正确性的写入不能被普通后台让步策略无限期饿死。

## 8. 联合验收场景

A、B 分别通过后，使用同一个独立合成 data root 进行联合验收：

1. 登记两个含合成照片、尚未扫描/建库的来源。
2. 同时展开两个来源的任务与统计明细。
3. 保持两张明细展开，再登记第三个来源。
4. 后台开始扫描和内容绑定。
5. 后台依次覆盖语义索引/outbox、搜索投影、模拟人脸、OCR、拍摄时间/GPS/地点等阶段；真实 GPU/模型不是本轮必需条件。
6. 前台修改合成人物名称，并打开人物相册和照片预览。
7. 同时请求健康状态、来源摘要和普通图库读取。
8. 前台完成后，后台继续处理直至合成任务队列收敛。
9. 注入前台请求取消、后台停止/恢复、短时 SQLite 外部 writer 和应用关闭边界。

验收必须能从诊断中区分读 Worker 排队、SQLite 写锁等待、事务过长和 API 事件循环阻塞，不能只凭“页面最后恢复”判定通过。

## 9. 验收标准

### A 单项

- 同一来源明细请求无重复并发；
- 刷新来源摘要不清空仍有效的明细缓存；
- 两个未建库来源均能及时返回，第二个有明确等待状态；
- 空 scope 快速路径与原统计结果逐字段等价；
- 失败/取消/重开不遗留 Worker，不显示旧响应；
- 不增加 Worker 数量，不新增 schema/index。

### B 单项

- 人物写请求不阻塞 API event loop；等待期间健康/轻量读取接口仍能响应；
- 人物写 Worker 使用线程自有连接，关闭时安全 drain；
- 前台写意图到达后，后台不再开始新的普通写批次；已运行事务安全结束后前台可以继续；
- 前台结束后后台自动恢复，任务状态、lease、job_id、revision 保持正确；
- 连续前台请求不会造成后台永久饥饿；
- 事务原子性、LanceDB/SQLite 确认协议、人物操作 undo/idempotency 不变。

### 联合验收

- 两张明细、第三来源登记、后台各阶段和前台人物操作并行时，无长时间无响应；
- 前台人物操作可成功或返回明确、可重试的业务冲突，不假成功；
- 后台在前台操作后继续推进；
- 无任务丢失、重复领取/确认、重复向量、人物关联丢失或人工事实覆盖；
- 无遗留线程、Future、SQLite 连接或数据根 lease 泄漏。

绝对延迟阈值应在阶段 0 记录基线后确定；不得把合成小样本外推为 30 万图库容量承诺。

## 10. 测试与审查范围

优先复用并按实际 diff 选择受影响集合：

- A：原 P4-SOURCE-DETAIL-STABILITY 列出的 frontend、sources API、progress、balanced metadata、catalog worker lifecycle 测试。
- B：Catalog.write BEGIN/COMMIT/rollback、人物生命周期/undo/idempotency、扫描与 library refresh、index outbox/Lance recovery、OCR lease/heartbeat、worker shutdown 与 API responsiveness。
- 联合：新增一个合成图库端到端并发测试；每个并发步骤使用 Event/Barrier 等确定性同步，不依赖长时间随机 sleep。
- 保留独立 JUnit、真实命令退出码、基线和 after 测量；不把不同代码时点的 JUnit 简单相加。

若修改共享 Catalog.write、跨线程 worker 生命周期、协调器或锁顺序，必须执行完整受影响面独立 DS-H 审查；涉及既有 ADR/事务恢复承诺时按项目规则追加聚焦审查。不得把自检称为独立审查。

## 11. 不做事项与安全边界

- 不访问、扫描、读取、修改或删除用户真实图库和真实照片。
- 不增加数据库表、索引、Schema 迁移或持久化调度状态。
- 不改变 SQLite journal mode、WAL checkpoint 策略或全局 busy timeout 来掩盖问题。
- 不增加全局互斥锁，不跨线程共享 SQLite connection。
- 不强制中断已开始的 SQLite 事务。
- 不把 LanceDB 写入移出既有 outbox 原子确认/恢复协议。
- 不改变人物 bundle、人工关联、undo、request_id 幂等和 revision 语义。
- 不下载/加载真实模型，不要求 GPU、GTX 1660/HDD 或 Windows EXE 验收。
- 不做全站性能重构，不新增常驻高频日志。
- 不在本父任务中自动提交产品代码；后续实现提交需按当时授权执行。

## 12. 执行顺序与停止条件

1. A1 只保留必要的简单收口，不追加大型审查或全量压力测试。
2. 实施 B1：人物写操作移出 API event loop，使用线程自有 Catalog/SQLite 连接；以少量合成并发测试验证。
3. B1 通过后，观察人物写入等待情况。只有仍存在明显后台写锁饥饿时，才建立并实施 B2；否则 B2 不做。
4. 如实施 B2，再进行一次小型合成联合回归。

按个人项目标准控制范围：先修复真实问题，不为调度抽象而抽象。不得使用全局锁替代 SQLite writer 机制；若需要 schema、跨进程互斥或改变人物/outbox 原子事务协议，先停下并取得单独确认。

最终报告：docs/reports/P4-CATALOG-CONCURRENCY-STABILITY.md。报告完成后更新本父卡和 docs/development-status.md。任一必需验收未完成，保留 IMPLEMENTED / VALIDATION PENDING 或 BLOCKED，不得标记整体 PASS。
