# P4-CATALOG-CONCURRENCY-B1 — 人物写操作线程隔离

状态：**SCOPED PASS / TERRA HIGH REVIEW PASS**。个人项目简化任务。
实施基线：`ec6a8d1`（2026-11-27）。[执行报告](../reports/P4-CATALOG-CONCURRENCY-B1.md)。

父任务：[P4-CATALOG-CONCURRENCY-STABILITY](P4-CATALOG-CONCURRENCY-STABILITY.md)。
工作包 B：[P4-FOREGROUND-WRITE-FAIRNESS-A](P4-FOREGROUND-WRITE-FAIRNESS-A.md)。

## 目标

后台建库占用 SQLite 写锁时，人物修改可以等待数据库，但不能阻塞 FastAPI event loop。等待期间健康检查和其他轻量 API 仍须正常响应；数据库锁释放后，人物操作按原有业务规则完成。

**本卡只解决“同步人物写操作阻塞 API”问题，不实现后台写入优先调度。**

## 当前代码依据

当前 `api/routes.py` 的人物/人脸写入 async 路由直接调用同步 `PersonService` 方法；这些方法内部调用 `Catalog.write()`。SQLite BEGIN 等待会同步阻塞调用线程，因此在 event loop 直接执行时会拖住同一事件循环的其他请求。

现有 `ApiContext._service_factory`、`ScanRunner` 等后台 Worker 已提供线程自有 `CatalogService` / SQLite connection 的模式。本卡优先复用，不跨线程传递 request Catalog。

参照主架构 V3.0 §8.3.1–8.3.4、§12.2，执行策略 V3.1 §2.4、§3.2、§3.4。

## 性能设计与验收

- 链路与风险（PERF-BOUNDED）：现有 SQLite writer 等待阻塞人物写请求所在的 API event loop，健康检查也无法推进。
- 做法与边界：一个专用线程，最多一个执行、四个等候，满队列在接受前拒绝。SQL、业务批次上限和事务保持原样，沿用本卡的争用时独立响应要求。
- 验证与停止：受控 writer 持锁时，用真实 ASGI 路由验证健康请求先完成、人物请求仍等待；锁释放后核对提交、幂等和清理。定向 B/D 回归收口，不新增容量或发行承诺。

## 实施范围

### 1. 迁移用户触发的人物写入口

先以当前 API 路由为准，统一盘点并迁移实际存在的 `PersonService` 写操作：

- 人物改名（label/name）、合并、拆分、撤销；
- 人物可见性/忽略状态；
- 单个及批量人脸关联修正；
- 从人脸创建人物；
- 人脸 regroup；
- 人脸映射决策、用户手动触发的历史人物操作快照清理。

只读详情、浏览、映射查询不迁移。内部 retention cleanup 等后台维护操作不纳入本卡。

### 2. 一个简单的专用写入 Worker

- 使用一个专用单写线程和小型有界队列；不与两个只读 `CATALOG_WORKERS` 共用。
- 复用 `ApiContext._service_factory` 和现有 Worker 生命周期模式。
- Worker 在线程内部创建并持有自己的 `CatalogService` / Catalog / SQLite connection；在同一线程中使用、关闭。
- 每个任务只传入白名单操作名和已验证的普通参数；Worker 在线程内构造 `PersonService` 并执行。不得传入 request 的 Catalog、connection 或 PersonService。
- API handler 只负责验证、提交任务并异步等待结果，不直接调用同步写方法。
- 队列满时在任务被接受前返回明确的可重试 503；复用项目错误格式，避免无界排队。
- 不新增通用任务框架，不增加多 Worker，不引入全局锁。

### 3. 取消与关闭

- 任务一旦被 Worker 接受，即视为已提交的业务请求。客户端断开只结束等待响应，不强行取消已经接受的写操作；正在执行的 SQLite 事务必须正常提交或回滚。
- 对带 `request_id` 的操作保留原有幂等重放；无 request_id 的状态设置操作保持原有幂等语义。
- 应用关闭时停止接收新任务，排空已经接受的有限队列，等待活动操作结束，再由 Worker 线程关闭自己的 Catalog/connection。
- Worker 未完全退出时，按现有 ApiContext 延迟清理方式保留资源和 data-root lease；不得提前释放。
- 不改变原 API 成功响应结构、业务错误和 revision 检查。

## 简单验收

只用独立合成图库和临时数据库，完成以下关键测试即可：

1. **核心复现：** 用第二个 SQLite connection 持有 `BEGIN IMMEDIATE`；通过真实人物改名 API 发起请求并等待。等待期间并发请求健康接口/轻量 API，必须及时响应。释放测试锁后，改名请求成功。
2. **连接归属：** 断言人物写操作在专用线程执行，使用的 Catalog/connection 不同于 request Catalog，并在所属线程关闭。
3. **业务正确性：** 改名成功、相同 request_id 重放不重复执行；再选合并或单个人脸修正做一个代表性 smoke test。
4. **关闭与队列：** 有活动任务时关闭应用，确认已接受任务安全完成、Worker 退出、连接关闭、data-root lease 最终释放；若队列有界，顺便验证队列满时明确拒绝。

使用 Event/Barrier 控制关键时序，不做随机长时间压力测试。

## 回归范围

运行本次改动涉及的人物 API/业务测试、一个 Worker 生命周期测试、A0 锁序 7 场景及 A1 读取池的少量基本回归；执行 Ruff、compileall 和 `git diff --check`。不跑全仓压力测试，不要求完整 DS-H。实现者做一次简短自查；只有触及共享 Catalog 锁顺序、通用生命周期或 ADR 原子事务时才扩大审查。

## 不做事项

- 不实现前台写意图协调器或后台批次让步（后续 B2，且需先证明仍有必要）。
- 不修改 `Catalog.write()`、busy_timeout、WAL、BEGIN BUSY 重试。
- 不改变人物 operation bundle、undo、revision、request_id 幂等和派生队列原子性。
- 不拆分 LanceDB 写入与 SQLite outbox 确认事务。
- 不增加数据库表、索引、Schema 迁移或全局互斥锁。
- 不访问真实图库/照片，不下载模型，不构建 Windows EXE。

## 实施结果（2026-11-27）

- 迁移 12 个用户写路由，覆盖 11 种白名单操作。人物 Worker 使用独立 Catalog，执行、回滚及关闭均在所属线程。队列上限为 1 个执行和 4 个等候，超限返回可重试的 `503 PERSON_WRITE_BUSY`。
- 旧同步路由对照中，健康响应晚于持锁线程的 6 秒保护释放。改后同一合成卡点在锁释放前返回健康和人物详情，锁释放后改名成功且重放只留下一个业务操作。关停时已接受写入排空，等待方取消后写入仍提交。连接首次关闭失败后在原线程重试，成功关闭且 Worker 退出后可重新取得数据根租约。
- 独立 Terra High 首轮发现一项 P2。修复为人物写关闭宽限 5 秒，超时后进入既有延迟清理。非守护 Worker 在事件循环退出后继续完成已接受的写入并保有租约，永久故障会使进程保持存活。实测覆盖宽限内、超时接管及独立子进程退出。独立复审通过，无新增 P0/P1/P2，复跑 34 项通过。
- 定向 B/D JUnit 225 项通过，零失败、零跳过，两组退出码均为 0。包含 A0 7 场景和三项 A1 读取回归。Ruff、compileall、diff-check 通过。共享 Catalog 锁序、通用延迟清理及 ADR 事务实现保持原有行为。
- B2 仍为条件任务。此次验证使用受控的单个写锁，尚无持续后台写锁饥饿的实测证据。数据均为隔离合成数据。
