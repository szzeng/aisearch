# P4-CATALOG-CONCURRENCY-B1 — 人物写操作线程隔离与 API 响应保护

建卡日期：2026-11-27。状态：**PLANNED / NOT STARTED**。实施基线：GitHub `master`，`bfdba39f7c524a4454b33cceff25d027a830986b`。

父任务：[P4-CATALOG-CONCURRENCY-STABILITY](P4-CATALOG-CONCURRENCY-STABILITY.md)。工作包 B：[P4-FOREGROUND-WRITE-FAIRNESS-A](P4-FOREGROUND-WRITE-FAIRNESS-A.md)。

## 1. 目标

修复 async API handler 直接执行同步 `PersonService` 写操作的问题。

后台建库持有 SQLite writer lock 时，人物修改可以等待数据库，但不能因此阻塞 FastAPI event loop；健康检查和其他轻量 API 必须仍能响应。数据库锁释放后，人物操作按原有规则成功或返回原有结构化错误。

本卡只做**人物写操作线程隔离**，不做后台写入公平调度。

## 2. 当前代码依据

已核对：

- `api/routes.py` 的人物/人脸写入路由是 async handler，但直接调用 `persons_of(request)` 的同步方法。
- `persons_of(request)` 使用 API request 所属的 `Catalog`；`PersonService.rename/merge/split/undo/set_face_assignment/edit_face_assignments/regroup_faces/create_person_from_face/set_visibility/decide_face_mappings` 等写方法内部同步进入 `Catalog.write()`。
- `Catalog.write()` 的 SQLite BEGIN 等待是同步操作；现有 busy timeout 为 5 秒并有既有 BEGIN BUSY 重试。等待发生在 event loop 线程时，会拖住同一事件循环的其他请求。
- `ApiContext` 已有 `_service_factory` 和后台 Worker 生命周期模式；扫描等后台工作已使用线程自有 Catalog/SQLite 连接。应复用已有模式，不跨线程传递 request Catalog。
- 人物操作的 authority、operation bundle、undo、revision、request_id 幂等和派生队列发布属于现有业务事务边界，必须原样保留。

## 3. 实施范围

### 3.1 盘点并迁移真实的人物写入口

检查当前 API 中实际暴露的 PersonService 写操作，至少覆盖当前存在的：

- 人物改名（`/label`、`/name`）；
- 人物合并、拆分和撤销；
- 人物可见性/忽略状态修改；
- 单个人脸关联修改、批量人脸修正；
- 从人脸创建人物；
- 人脸 regroup；
- 人脸映射决策。

以当前代码实际路由为准；只迁移用户触发的写入口。只读的人物详情、人物浏览、映射查询和人脸状态查询不迁移。后台 retention cleanup 等内部维护任务不纳入本卡。

### 3.2 专用写入 Worker

采用一个简单、专用、受生命周期管理的单写 Worker：

- 与只读 `CATALOG_WORKERS` 池分离，不阻塞图库明细读取。
- 使用小型有界等待队列；容量按现有使用场景选保守值。队列满时返回明确、可重试的结构化忙状态，不无限排队。
- API handler 只负责验证请求、提交工作并 await 结果；不得在 event loop 同步等待 `Catalog.write()`。
- Worker 在自己的线程中创建、使用和关闭 Catalog/SQLite connection 及 PersonService。禁止将 `service_of(request).catalog`、其 connection 或由它构造的 PersonService 传给 Worker。
- 优先复用 `ApiContext._service_factory` 和现有后台 Worker 连接/关闭模式。可按任务打开关闭连接，或采用线程内长期连接；选择最简单且能保证线程归属和安全关闭的实现。
- Worker 异常应按原有 API 异常类型和 response model 传播，不改变成功响应格式。

### 3.3 取消与应用关闭

- 工作尚在队列中且未开始时，请求取消可以移除该任务。
- 操作已经开始后，不强行中断 SQLite 事务；允许其正常提交或回滚。客户端断开只影响响应等待，不得制造半个业务操作。
- 保留现有 request_id、幂等重放和 revision 检查。客户端重试时不得重复应用已提交的人物操作。
- 应用关闭时停止接收新任务，取消尚未开始的排队任务，等待正在执行的原子操作结束，并在 Worker 所属线程关闭连接。
- Worker 和连接完成清理后才能释放 ApiContext 持有的数据根 lease。复用 A2 已有生命周期原则，不另建复杂关闭框架。

## 4. 简单验收测试

只使用独立合成图库和临时 SQLite 数据库，不访问真实照片库。

必须完成以下测试：

1. **核心复现：** 测试连接持有 `BEGIN IMMEDIATE`，前台通过真实人物改名 API 发起写入并等待；同时请求健康检查/轻量 API，必须正常返回。释放测试 writer 后，人物修改正常完成。
2. **线程归属：** 证明人物写操作运行在线程 Worker，使用的 Catalog/connection 与 API request Catalog 不同；连接在所属线程内关闭。
3. **业务语义：** 至少验证改名成功、request_id 重放不重复执行；再选一个人脸修正或合并入口做代表性 smoke test。已有业务测试继续保护 undo、revision、人工关联语义。
4. **关闭清理：** 有一个活动或排队写操作时关闭应用，确认活动事务安全结束、未启动任务被取消、无遗留线程/连接，数据根 lease 最终释放。
5. **容量保护：** 如果实现有界队列，验证队列满时返回明确可重试错误，释放容量后可以继续提交。

测试使用 Event/Barrier 控制时序，不靠长时间随机 sleep。

## 5. 最小回归范围

- 新增一个人物写 Worker/API 并发集成测试文件或扩展现有相关测试。
- 运行受影响的人物 API/操作测试（rename、merge、split、undo、face correction、visibility）。
- 运行相关 ApiContext/Worker 关闭生命周期测试。
- 保留 A0 锁序 7 场景和 A1 读取池基本回归。
- 执行 Ruff、compileall、Node 语法（仅当改动前端时）及 `git diff --check`。

不运行全仓压力测试，不要求完整 DS-H；实现者做一次针对线程归属、取消、异常和关闭顺序的简短代码自查即可。若实际修改共享 Catalog 生命周期、锁顺序或既有 ADR 原子协议，再扩大审查并说明原因。

## 6. 不做事项

- 不实现前台写意图协调器。
- 不让扫描、语义、人脸、OCR 后台阶段主动让步；这些属于后续 B2。
- 不修改 `Catalog.write()`、SQLite busy timeout、WAL 或 BEGIN BUSY 重试。
- 不增加全局互斥锁、数据库表、索引或 Schema 迁移。
- 不改变人物 operation bundle、undo、revision、request_id 幂等或派生队列原子性。
- 不把 LanceDB 写入移出 SQLite outbox 确认事务。
- 不迁移只读人物 API，不扩大到所有业务 API。
- 不访问真实图库、不读取或修改真实照片、不下载模型、不构建 Windows EXE。

## 7. 完成标准

- 后台持有 SQLite writer lock 时，人物写请求可以等待，但健康检查和其他轻量 API 不被阻塞。
- 锁释放后人物操作按原有业务语义完成。
- 人物 Worker 使用独立线程和线程自有连接。
- 取消、队列满和应用关闭均有明确行为。
- 不改变已有人物操作结果、幂等、undo 和人工关联语义。
- 简单受影响测试全部通过；未执行的验证不得标记 PASS。

完成后更新本卡、工作包 B 父卡、专项父卡和 `docs/development-status.md`。执行报告简短记录实现、测试结果和未解决问题。

**本卡只建立任务规划，不代表已修改产品代码或已执行测试。**
