# P4-CATALOG-CONCURRENCY-B1 — 人物写操作线程隔离

状态：**PLANNED / NOT STARTED**。个人项目简化任务。
实施基线：`b4f826e579b0e731bab064f7be0ee50dd0768b1c`（2026-11-27）。

父任务：[P4-CATALOG-CONCURRENCY-STABILITY](P4-CATALOG-CONCURRENCY-STABILITY.md)。
工作包 B：[P4-FOREGROUND-WRITE-FAIRNESS-A](P4-FOREGROUND-WRITE-FAIRNESS-A.md)。

## 目标

后台建库占用 SQLite 写锁时，人物修改可以等待数据库，但不能阻塞 FastAPI event loop。等待期间健康检查和其他轻量 API 仍须正常响应；数据库锁释放后，人物操作按原有业务规则完成。

**本卡只解决“同步人物写操作阻塞 API”问题，不实现后台写入优先调度。**

## 当前代码依据

当前 `api/routes.py` 的人物/人脸写入 async 路由直接调用同步 `PersonService` 方法；这些方法内部调用 `Catalog.write()`。SQLite BEGIN 等待会同步阻塞调用线程，因此在 event loop 直接执行时会拖住同一事件循环的其他请求。

现有 `ApiContext._service_factory`、`ScanRunner` 等后台 Worker 已提供线程自有 `CatalogService` / SQLite connection 的模式。本卡优先复用，不跨线程传递 request Catalog。

## 实施范围

### 1. 迁移用户触发的人物写入口

先以当前 API 路由为准，统一盘点并迁移实际存在的 `PersonService` 写操作：

- 人物改名（label/name）、合并、拆分、撤销；
- 人物可见性/忽略状态；
- 单个及批量人脸关联修正；
- 从人脸创建人物；
- 人脸 regroup；
- 人脸映射决策。

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

完成后更新本卡、工作包 B、专项父卡及 `docs/development-status.md`，附简短测试结果。未执行的验证不得标记 PASS。

**本卡仅为实施任务定义，不代表已经修改产品代码。**
