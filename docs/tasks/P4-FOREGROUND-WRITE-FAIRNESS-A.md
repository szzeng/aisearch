# P4-FOREGROUND-WRITE-FAIRNESS-A — 前台写入优先与后台建库安全让步

建卡日期：2026-11-26。状态：**PLANNED / NOT STARTED**。实施基线：GitHub `master`，`b334bd75a020108bf826e2790d2850290bf4210a`。

父任务：[P4-CATALOG-CONCURRENCY-STABILITY](P4-CATALOG-CONCURRENCY-STABILITY.md)。本卡是工作包 B；与工作包 A [P4-SOURCE-DETAIL-STABILITY](P4-SOURCE-DETAIL-STABILITY.md) 同属一个专项，但处理不同资源瓶颈。本卡只建立设计，不代表已经修改产品代码。

## 1. 用户问题与目标

用户报告：后台建库期间，前台修改人物名称，应用曾出现长时间无法响应且无法自行恢复的情况。后台阶段不确定，扫描、语义索引、人脸识别、OCR 都可能发生，不能预先只检查 semantic 阶段。

提供的日志中，人物标签 PUT 和随后的 persons/browse GET 最终返回 200 OK，缩略图请求有 200/304。日志没有请求耗时、SQLite holder、线程状态和事务时间，因此它不是“已证明死锁”的证据。

目标：在任何受支持的后台建库阶段运行时，前台人物修改能够及时取得数据库写入机会；等待数据库不能阻塞 API event loop；前台完成后后台能够安全继续，且不丢失或重复任务。

## 2. 当前代码证据与待验证点

已核对当前 master：

- api/routes.py 的人物标签路由是 async handler，但直接调用 persons_of(request).rename。
- persons_of(request) 使用当前 request Catalog 构造 PersonService；PersonService.rename 是同步函数，内部直接进入 Catalog.write。
- Catalog.write 使用每个 Catalog 实例自己的 RLock；不同 Catalog/线程的连接不共享这个 Python 锁，跨连接 writer 竞争最终由 SQLite WAL 的单写者规则处理。
- Catalog.write 已对 BEGIN IMMEDIATE 前 SQLITE_BUSY 做有限重试并在失败详情中记录等待方 PID/TID/thread name；现有 busy timeout 为 5 秒。成功路径尚无完整 BEGIN wait / transaction hold / COMMIT 分段诊断。不得把 SQL 体或 COMMIT 错误混入 BEGIN BUSY 重试。
- 人物改名通过 person operation bundle、operation items 和 publish_derived 更新派生队列；这些写入属于既有原子事务边界。
- IndexCoordinator.process_batch 的 apply 路径会在 SQLite 写事务持有期间完成 LanceDB upsert/delete 并更新 outbox 确认状态。不能将 Lance 写入和 SQLite 确认随意拆开。
- 已有 P4-02U/V、P4-LOCK-CONTENTION-RECOVERY-A/B/C/D 与 P4-BUILD-STABILITY-A2 机制必须复用，不重复实现。

待验证：人物关联规模对事务持锁时长的影响；后台每个阶段的事务长度/批次间隙；哪些后台写入路径能安全地在事务前或完整批次间让步；同一进程外的其他 writer 对等待的影响。

## 3. 工作范围

### 3.1 阶段 B0：调用链盘点与基线诊断

先建立受控的操作分类和耗时证据：

- Catalog.write 的 BEGIN 等待、事务体持锁、COMMIT、重试次数和 outcome。
- 识别同一进程内活动 writer 的 PID/TID/thread name、role、stage/operation；跨进程 holder 不能确认时标记 unknown。
- 对人物改名、合并、撤销、人工关联/修正等真实入口测量事务持锁时间。
- 对后台扫描、绑定、语义/outbox、搜索投影、拍摄时间/GPS/地点、人脸、人脸详情和 OCR 写入路径建立实际调用清单。
- 复用现有 search/catalog perf diagnostics；不新增持久化诊断表，不记录人物名称、照片路径或 SQL 展开文本。

只有证明某个路径存在竞争或不合理持锁时，才改该路径。

### 3.2 阶段 B1：人物写操作离开 API event loop

把用户触发的人物写操作放到独立、受生命周期管理的写入 Worker 中执行。

设计要求：

1. API handler 只做参数/身份验证、登记前台写意图、提交工作和 await 结果；不得在 event loop 直接调用同步 PersonService 写操作。
2. 使用专用有界 worker（首选单写线程，因 SQLite 本身只有一个 writer），不要与只读 CATALOG_WORKERS 池共用，也不要使用无界的任意线程扩散。
3. Worker 必须在线程内部打开 SQLite RW connection、构造 Catalog 和 PersonService、执行操作并在同一线程关闭。可参考 catalog_reads worker 的线程自有连接模式；不能把 API event loop 的 Catalog/connection 传入另一个线程。
4. 连接打开必须使用现有 catalog 文件，禁止触发不必要迁移或新建空库；核对数据根 lease 和应用 shutdown 顺序。
5. worker queue 必须有界。队列饱和时返回明确的可重试忙状态，不可无限排队或假装成功；容量和错误映射由现有 API 契约与基线测试确定。
6. 请求在真正开始前取消，可安全取消排队项；操作已经开始后不得强行中断原子事务。断开连接/客户端取消时，服务端必须等待或安全收尾；客户端重试沿用 request_id/幂等语义，不能重复执行人物修改。
7. 应用关闭时先停止接收新任务、取消未启动任务、等待已启动任务安全结束、关闭 worker-owned Catalog，再释放数据根 lease。关闭失败必须保留资源引用并允许重试，复用 A2 的生命周期原则。

先盘点 PersonService 所有实际写入口和对应 API 路由，再将用户触发的人物变更作为一个完整切片迁移；至少核查 rename、merge、undo、人工人脸关联/修正、split 等当前存在的写入口。只迁移真实存在且属于用户触发的路径，不扩大到所有 API。

人物读取是否也需要移出 event loop，先用耗时和 API 响应测试证明，不在本卡默认扩展。

### 3.3 阶段 B2：前台写意图与后台安全让步

设计一个进程内、按同一 catalog/data-root 身份共享的轻量协调器。它不是 SQLite 替代品，也不是覆盖整个程序的全局互斥锁。

目标状态：

- 前台人物写请求在进入 Worker 队列前登记 foreground intent。
- 后台建库写入在安全事务/批次边界申请 background admission。
- 有前台写等待时，后台不得开始新的普通写批次。
- 已经开始的后台事务安全完成；不强制中断、不回滚已提交数据。
- 前台事务完成后通过 Condition/Event 唤醒后台，后台自动继续。
- 等待支持取消、停止和应用关闭；所有 token/counter 在成功、异常、取消时成对释放。
- 协调器只在更新计数/状态时短暂持有 Condition lock；不得在 SQLite transaction、LanceDB 操作、模型推理或文件 I/O 期间持有 Python lock。
- 不同数据根相互独立；同一数据根的 API、扫描和刷新连接能看到相同的进程内协调状态。
- SQLite 仍是最终 writer lock authority；其他进程/旧版本/外部工具不受进程内协调器控制，继续由现有 BUSY/lease 机制保护。诊断不得声称跨进程公平性。

实现前必须解决“前台意图到达与后台 BEGIN 之间”的竞态：后台必须先获得可追踪的批次 admission，再进入事务；前台到达后阻止后续批次。若 BEGIN 正在等待外部 writer，必须证明等待预算/取消/恢复不会使前台无限等待。

前台优先不能导致后台永久饥饿。连续前台请求场景下，需要有可测的后台最大延期/公平策略；策略数值从阶段 B0 基线和既有产品响应预算推导，不在没有证据时随意设置常数。

### 3.4 阶段 B3：后台写入安全边界审查

按实际调用图逐项判断是否需要让步，不把“建库阶段”当成足够精确的写入边界：

| 阶段 | 需审查的边界 |
|---|---|
| 文件扫描 | 每个位置/批次的短事务边界；文件枚举、stat、hash 应在事务外 |
| 内容绑定 | 每个绑定批次提交后 |
| 语义分类 | durable 分类页/任务入队事务边界 |
| 语义索引 | 完整 outbox apply 批次之后 |
| 搜索投影 | 每轮 catch-up/drain 的完整事务边界 |
| 拍摄时间/GPS/地点 | 每个 job/result 批次提交后 |
| 人脸 | 检测/推理在事务外；结果提交和索引发布的原子边界 |
| 人脸详情 | 每个任务/批次提交后 |
| OCR | 推理在事务外；结果提交、job/lease 控制边界 |

要求：

- 优先使用已有阶段/批次边界，不把单条业务事务拆成多个部分。
- 不能在 LanceDB 已写入但 SQLite outbox 尚未确认的中间状态让步。
- 不能让模型推理等待数据库时继续持有不必要的 BACKGROUND_MODEL_SLOT 或 ModelResourceGate。
- 保留 P4-LOCK B/C 的扫描 RETRY_WAIT、取消、恢复和 lease 规则。
- 保留 P4-02V 的搜索追赶 100ms 有界等待和 LAGGING 语义；本卡不改变它的维护预算。
- lease heartbeat、任务恢复、关闭清理等控制写入不可被普通后台让步无限延迟。
- 如果某个阶段没有安全边界或修改会改变事务/恢复协议，先停止并单独提出设计，不强行接入。

## 4. 协调器设计验收

用 Event/Barrier 构造确定性时序：

1. 后台进入一个真实/合成的短写事务并持有 batch admission。
2. 前台人物写请求登记 intent。
3. 后台当前事务完成。
4. 断言后台不开始下一批普通写事务，前台 worker 获得执行机会。
5. 前台提交/回滚并释放 intent。
6. 断言后台自动恢复，持久任务被继续处理且无重复。
7. 注入前台取消、worker 异常、SQLite BUSY、后台停止和应用关闭，确认状态计数/连接/lease 正确。
8. 连续前台写入时，验证后台在测量确定的上限内仍能取得批次机会。

不得依赖随机 sleep 证明锁顺序；必要等待必须由 Event/Barrier 观察真实状态。

## 5. 重点回归与联合场景

### B 单项

- 人物 rename/merge/undo/人工关联等实际写入口不阻塞 event loop。
- 写入 Worker 使用线程自有 connection，且不与读 Worker 混用。
- 现有 expected_revision、request_id replay、operation bundle、undo 和 publish_derived 结果逐项不变。
- 活动事务不被中断；BEGIN BUSY 与 COMMIT/业务异常分类不变。
- 前台 intent 可见；后台安全让步；前台完成后后台继续。
- 后台在连续前台写入下仍有界进展。
- 应用 shutdown/restart 不遗留线程、Future、Catalog 或数据根 lease。

### 必须联合 P4-SOURCE-DETAIL-STABILITY

使用独立合成图库同时执行：

1. 两个未建库来源明细并发读取；
2. 明细保持展开时登记第三个来源；
3. 后台执行扫描、语义/outbox、模拟人脸和 OCR、时间/GPS/地点任务；
4. 前台修改人物名称并打开人物相册/照片预览；
5. 同时请求健康状态、来源摘要和普通读取；
6. 前台结束后后台继续推进至队列收敛。

必须证明 A 的只读 Worker 排队不会被 B 的写入协调器误判为 SQLite 写锁；B 的前台写意图也不能占用 A 的只读 Worker。

## 6. 不变量与禁止事项

- 不新增 Schema、表、索引、持久化调度状态或迁移。
- 不改变 WAL、全局 busy_timeout 或 P4-02V 预算。
- 不增加全局互斥锁；协调器只做进程内有界批次 admission。
- 不跨线程复用 SQLite connection/Catalog。
- 不强制中断已开始的事务。
- 不拆分人物 authority/operation bundle/derived publication 的原子事务。
- 不拆分 LanceDB upsert/delete 与 SQLite outbox 确认的原有协议。
- 不改变 job_id、lease、input_revision、retry/attempt、人物关联和人工修改语义。
- 不在持有模型资源锁或 SQLite writer transaction 时等待另一个资源。
- 不扫描或访问用户真实照片库；只使用隔离合成文件和临时 catalog。
- 不下载或加载真实模型；GPU 只使用 deterministic fake/stub。
- 不重建 Windows EXE，不做 30 万容量承诺，不扩大成全站重构。

## 7. 性能设计与停止条件（PERF-BOUNDED）

- 修复前先测量人物操作等待、事务持锁、后台批次时长、明细 Worker 排队和 API event loop 响应。
- 修复后使用相同合成数据和相同并发时序比较。
- 前台请求到达后，等待时间应主要受当前已开始的单个安全后台批次及外部 SQLite writer 影响；后台不能连续追加普通批次。
- 人物修改不得因等待 SQLite 而阻塞健康/轻量 API 响应。
- 后台在前台流量持续存在时仍须有界进展。
- 数值阈值从基线和既有项目预算确定；不以增加 timeout、降低统计准确性或减少工作量换取表面通过。
- 任一方案需要改变 schema、outbox 原子确认、人物操作协议或跨进程 ownership 时，停止并请求单独确认。

## 8. 验证范围与报告

优先复用：
- Catalog.write BUSY/BEGIN/COMMIT/rollback 相关测试；
- P4-LOCK-CONTENTION-RECOVERY-A/B/C/D 受影响测试；
- P4-BUILD-STABILITY-A2 Worker shutdown/lease 生命周期测试；
- 人物生命周期、undo、revision、idempotency 和人工关联测试；
- 扫描、refresh、semantic outbox/Lance recovery、OCR lease/heartbeat 测试；
- API responsiveness 与 catalog worker lifecycle 测试；
- P4-02V catch-up contention 回归。

仅按实际 diff 扩大受影响集合，不因任务名称自动跑全仓。共享 Catalog.write、worker lifecycle、协调器或锁顺序有变化时，执行完整受影响面独立 DS-H 审查；涉及 ADR 原子性承诺时增加聚焦审查。

最终报告归入父任务报告：docs/reports/P4-CATALOG-CONCURRENCY-STABILITY.md。父任务和本卡状态按真实测试更新。任何关键场景未验收，保持 IMPLEMENTED / VALIDATION PENDING 或 BLOCKED，不得标记 PASS。
