# P4-FOREGROUND-CATALOG-WRITE-LANE-A
# 前台 Catalog 写操作统一线程隔离

状态：SCOPED PASS / SOL HIGH REVIEW PASS

优先级：P1

项目类型：个人相册项目，个人使用。

实施基线：GitHub master 产品代码 `a2059ad48f1f15a99cc89c3c3f8c4c9141fc2b17`。

设计参考：
- P4-CATALOG-CONCURRENCY-B1：人物写入 Worker，SCOPED PASS / SOL HIGH REVIEW PASS。
- P4-FOREGROUND-API-WRITE-ISOLATION-A：原大范围任务，STOPPED。
- P4-CATALOG-CONCURRENCY-STABILITY：并发专项父卡。

## 1. 目标

后台建库、索引或其他 Worker 持有 SQLite writer lock 时，普通前台写操作不能同步阻塞 FastAPI event loop。

当前人物写操作已经通过 B1 的 PersonWriteWorker 实现线程隔离。

本卡将扩展这一现有机制，让一批简单的前台 Catalog 写操作也通过同一个 Worker 执行。

目标：

- 不增加第二个写入 Worker。
- 不增加新的线程池。
- 不改变现有 SQLite 事务。
- 不改变 WAL、busy_timeout 和 BUSY 重试。
- 不修改 ApiContext.close() 的通用关闭流程。
- 不修改数据根 lease 的管理。
- 保留 B1 已通过的人物写操作。
- 不处理复杂后台任务交接。

## 2. 核心设计

### 2.1 复用现有 B1 PersonWriteWorker

直接扩展现有 `src/photo_search/api/person_writes.py`。

不另建独立 CatalogWriteWorker。

现有 PersonWriteWorker 继续作为唯一的前台写入通道。

扩展现有白名单和业务分派能力，使其能够处理多种简单 Catalog 写操作。

建议采用明确的 operation dispatcher：

- 操作名称必须来自代码内白名单。
- 不接受任意 Python callable。
- 不接受用户指定的 SQL。
- 只传递已经验证的普通参数。
- 不传递 Request、Catalog、SQLite connection、CatalogService 或其他线程对象。

Worker 在线程内部使用现有 service factory 创建 CatalogService。

同一线程内执行、关闭数据库资源。

采用懒加载业务模块，避免引入新的导入循环。

保留原有 `_person_write_worker` 生命周期入口和现有 API 兼容性，不做无关的类名或模块重命名。

### 2.2 统一队列

继续使用 B1 已验证的容量：

- 一个正在执行的任务。
- 最多四个等待任务。
- 前台人物写入和本卡新增操作共用容量。
- 超出容量时，在接受任务前返回 HTTP 503。
- 不允许无限排队。

现有人物操作保持原来的 `PERSON_WRITE_BUSY` 错误契约。

新增普通 Catalog 写操作使用明确的 `CATALOG_WRITE_BUSY`，并返回 retryable=true 和 Retry-After。

队列容量按所有前台写操作总量计算，而不是每个操作分别拥有四个等待名额。

### 2.3 ApiContext 调用

扩展 `ApiContext` 的异步提交接口，提供统一前台写入入口。

例如：

`run_foreground_write(operation, **parameters)`

现有 `run_person_write()` 保留并兼容原有调用。

不增加新的 Worker 资源字段，不改变 ApiContext.close() 的通用 Worker 关闭和数据根 lease 释放顺序。

Worker 已接受任务后，客户端断开只取消 HTTP 等待，不取消已经接受的数据库写操作。

已接受任务必须完成提交或回滚。

### 2.4 路由要求

迁移后的 API handler 只做：
- 请求格式和参数验证。
- 获取 ApiContext。
- 提交白名单写任务。
- 异步等待 Future。
- 返回原有响应。

不得在 event loop 中提前执行同步数据库读取、写入或业务预检。

如果某个路由需要数据库预读，必须确认能否将完整数据库相关逻辑安全放入 Worker；否则暂缓迁移。

不能只把最后一条 UPDATE 搬进 Worker，却把前面的同步查询留在 event loop。

## 3. 本次明确迁移范围

以最新 master 实际路由为准。

### A. 照片收藏

- PUT `/assets/{asset_id}/favorite`
- DELETE `/assets/{asset_id}/favorite`
- POST `/favorites/batch`

复用 `favorites.py`。

保持：
- 单张收藏与取消收藏。
- 批量收藏和取消收藏。
- 原有幂等行为。
- favorites_revision。
- 原有响应及错误语义。

### B. 普通照片合集

- POST `/photo-collections`
- PATCH `/photo-collections/{collection_id}`
- DELETE `/photo-collections/{collection_id}`
- POST `/photo-collections/{collection_id}/members/batch`

复用 `photo_collections.py`。

保持：
- instance_token。
- revision。
- 成员增删。
- request_id 幂等。
- 批量数量限制。
- 原有事务原子性。

不迁移 from-search 等依赖搜索 session 和跨 await 状态的特殊接口。

### C. 人物合集

- POST `/persons/collections`
- PATCH `/persons/collections/{collection_id}/{instance_token}`
- DELETE `/persons/collections/{collection_id}/{instance_token}`
- POST `/persons/collections/{collection_id}/{instance_token}/members`
- POST `/persons/collections/{collection_id}/{instance_token}/faces`

复用 `persons/collections.py`。

保持：
- 人物 canonical ID 解析。
- instance_token。
- revision。
- 成员和脸级成员校验。
- 原有事务和错误语义。

不得与 B1 的 PersonService 人物改名、合并、拆分操作混淆。

### D. 人物相册辅助操作

- PUT `/persons/{person_id}/cover`
- POST `/faces/inbox/visibility`

复用原有业务函数。

保持人物版本、人工状态和原有业务结果。

### E. 来源名称修改

- PATCH `/sources/{source_id}`

迁移 `CatalogService.rename_source()`。

本次只处理改名。

来源登记、删除和扫描登记均不纳入本卡。

## 4. 明确暂缓的操作

以下操作不得为了追求覆盖率而直接加入通用 Worker。

### 来源登记

`register_source()` 包含文件系统检查、数据库事务及提交后的 get_source/counts。

存在提交后读取与并发删除的时序问题。

本卡暂缓。

### 来源删除

删除前检查 refresher 状态，存在检查与执行之间的刷新启动竞态。

本卡暂缓。

### 开始建库

`start_scan()` 与 `ScanRunner.submit()` 具有持久任务登记和内存执行队列交接关系。

必须保持原有 HTTP 202、同来源复用及 scan/job 身份一致性。

本卡暂缓。

### 其他复杂操作

以下不纳入：

- 搜索结果保存合集。
- 智能合集规则和刷新。
- 人脸 rematch。
- 人脸详情批次登记与调度。
- Retention 清理及自动维护。
- 地点批量回填。
- 搜索 profile 激活。
- embedding provider 和模型资源管理。
- 需要文件发布、外部 Worker 或多阶段任务交接的操作。

本卡不宣称这些路径已经不存在 event loop 阻塞风险。

后续是否处理，由实际问题和独立任务决定。

## 5. 取消与关闭

直接复用 B1 已经通过的生命周期机制。

要求：

- 应用关闭时停止接受新的前台写任务。
- 已接受任务按原有顺序排空。
- HTTP 取消不取消已接受的数据库写操作。
- Worker 在线程内关闭 CatalogService。
- 保留 B1 的 5 秒正常关闭宽限和既有延迟清理机制。
- Worker 未退出时，继续保留资源引用和数据根 lease。
- 不得提前释放 SQLite 连接或数据根 lease。
- 不允许新建一个独立线程后忘记纳入现有生命周期。
- 不修改 ApiContext.close()、app.py lifespan 和通用 lease 管理。

如果实现必须改变以上通用生命周期，立即停止并报告，不擅自扩大本卡。

## 6. 简单验收

个人相册项目，使用独立合成图库和临时 SQLite 数据库。

### 测试一：核心事件循环阻塞复现

用第二条 SQLite connection 持有 BEGIN IMMEDIATE。

通过真实 ASGI API 发起一项收藏或合集写操作。

确认该操作正在等待 SQLite 写锁。

同时请求：

- GET /health
- 人物浏览。
- 照片列表。
- 轻量图库摘要。

要求这些请求在写锁释放前仍能正常响应。

释放锁后，原写操作正常返回。

这一项是本卡的核心验收。

### 测试二：线程和连接归属

确认：

- 前台请求线程不直接执行目标 Catalog.write。
- Worker 在线程内部创建和使用 CatalogService。
- Worker connection 与请求线程 Catalog 不同。
- SQLite connection 只在所属线程使用和关闭。
- 没有跨线程传递 Catalog 或 connection。

### 测试三：业务正确性

每个迁移业务组选择代表性测试，并运行其现有受影响测试：

- 收藏：单张、批量、幂等及 revision。
- 照片合集：创建、改名、成员修改及 revision。
- 人物合集：创建、成员和脸级成员修改。
- 人物封面和 inbox visibility。
- 来源改名。

保持原有 API 响应、权限、错误和版本语义。

### 测试四：队列

验证：
- 一个任务执行。
- 四个任务等待。
- 第六个任务明确返回 503。
- 不产生无界线程或无界任务。
- HTTP 等待取消后，已接受写操作仍正常完成。
- 人物操作和新增普通写操作共享同一容量。
- 两类操作各自保持原有错误码。

### 测试五：B1 关闭与回归

复用 B1 现有关闭测试。

在新旧操作同时排队时关闭应用，验证：
- 所有已接受任务安全排空。
- 关闭宽限期正常。
- 超时后仍保留资源及数据根 lease。
- 没有重复关闭、连接泄漏或 Worker 丢失。
- 原有 12 个人物写路由保持正常。

### 测试六：A0/A1 基本回归

复用：
- A0 锁序 7 个场景。
- A1 查询池的基本容量、取消和关闭回归。

不做全仓压力测试。

## 7. 静态检查与独立审查

执行：
- 本次受影响 pytest 并保存 JUnit。
- Ruff。
- Python compileall。
- git diff --check。
- Node 语法检查（涉及前端时）。

由独立 GPT-6 High / Terra High 对本次实际修改的完整受影响面做重点复核，重点检查：

- Worker dispatcher 和白名单。
- B1 原有操作兼容性。
- 新旧操作共享队列后的行为。
- Future 取消。
- 错误映射。
- API 线程与 SQLite connection 归属。
- 关闭及 lease 保留。

若发现修改触及通用 Worker 生命周期、ApiContext.close()、数据根 lease 或原有事务原子协议，停止并单独评估，不得按本卡范围直接修改。

## 8. 不做事项

- 不新增第二套写入 Worker。
- 不新增数据库线程池。
- 不修改 ScanRunner。
- 不迁移来源登记、删除和开始建库。
- 不修改 Catalog.write()。
- 不修改 SQLite busy_timeout 和 WAL。
- 不新增 Schema、表、索引。
- 不引入全局互斥锁。
- 不实施 B2 后台写入公平调度。
- 不修改 LanceDB/outbox 原子确认协议。
- 不修改人物 operation bundle、undo 和 request_id 契约。
- 不访问真实图库或真实照片。
- 不加载真实模型。
- 不构建 Windows EXE。
- 不做全仓压力测试。

## 9. 完成标准

1. 本卡指定路由不再在 API event loop 中同步等待 SQLite writer lock。
2. 其他前台轻量 API 在写锁竞争期间正常响应。
3. 已接受操作保持原有业务结果和事务语义。
4. 统一 Worker 有界运行。
5. 人物 B1 原有功能和关闭机制无回归。
6. A0/A1 基本回归通过。
7. 静态检查通过。
8. 独立重点复核通过。
9. 提供明确的测试报告和未覆盖范围。

本卡通过只表示所列操作完成前台线程隔离，不代表所有 API 写入都已隔离，也不代表 SQLite 后台写入公平性问题已经解决。

## 10. 状态

SCOPED PASS / SOL HIGH REVIEW PASS

本卡仅用于个人项目。

实施前核对最新 HEAD、工作区和 B1 实际代码，不覆盖其他并行修改。

用户已授权执行本卡；未授权提交、推送或发布。
## 性能设计与验证（实施前）

- 链路与风险：前台写入等待 SQLite writer 会阻塞事件循环，接受后取消及关闭不得丢失人工状态。
- 做法与边界：复用 B1 单线程、一个执行加最多四个等待、5 秒关闭宽限；数据库操作与事务不变，不新增容量承诺。
- 验证与停止：合成 ASGI 持锁并发、业务及队列取消/关闭、B1/A0/A1 回归，Ruff/compileall/diff-check 和独立完整受影响面复核；通用生命周期、lease 或事务协议需变更时停止。B/D 定向验证；无依赖、迁移或广泛合流触发全量回归。

## 执行结果（2026-11-27）

已接入 15 条路由，复用 B1 单线程和队列；业务、混合排队、取消及关闭回归通过。完整应用 lifespan 成功初始化后，真实 ASGI 持锁四种读取均响应；未运行 lifespan 的初次测试暴露未初始化服务时读请求等待锁的条件性限制，详见[报告](../reports/P4-FOREGROUND-CATALOG-WRITE-LANE-A.md)。不修改通用生命周期、lease 或超出本卡路径。独立 Terra High 完整受影响面复核 PASS，核验最终源码与 JUnit，未独立重跑。

## Sol High 独立复核（2026-11-27）

**CHANGES REQUIRED**：合成库真实 lifespan/API 复现启动 smart refresh 恢复延迟、持 SQLite 写锁且共享队列已满时，503 错误响应脱敏会同步重试恢复并阻塞事件循环；其他同样经过错误处理器的业务拒绝也受影响；应修复并验证这些错误响应和其他轻量 API 不被阻塞。原 Terra 复核结论保留为历史，不能覆盖这一新增发现。补跑 B1 受影响回归 181/181；原状态不得继续标 PASS。细节见[报告](../reports/P4-FOREGROUND-CATALOG-WRITE-LANE-A.md)。

### 复核修复性能与验证（编码前）

- 链路与风险：错误响应脱敏调用服务恢复，可能在事件循环等待 SQLite 写锁；须保留路径隐藏与错误契约。
- 做法与边界：仅错误处理使用已打开的请求线程 Catalog 读取已注册来源，未打开或不可读时沿用 data-root 回退，不触发服务初始化或恢复；不改通用生命周期、租约或事务。
- 验证与停止：真实 lifespan 合成库注入延迟恢复、实际持锁、业务错误与队列满及健康并发，覆盖路径脱敏及失败回退；受影响共享错误处理测试、B1 证据复用，独立复核。发现需要变更通用生命周期或无法保留脱敏时停止。

### 修复与 Sol High 复审（2026-11-27）

原 P2 已修复，真实 lifespan 延迟恢复与写锁下 ApiError、CatalogError 和满队列 503、路径脱敏及健康响应通过；修复后受影响 94/94，补充错误码及 ADR-0010 详情闭合 2/2。独立 Sol High 复审 PASS，无新增阻塞项；复核核对源码及 JUnit，未独立重跑。普通读取在启动恢复暂缓时仍可能同步等待，未覆盖的写入路径及后台公平性不在本卡结论内。详情见[报告](../reports/P4-FOREGROUND-CATALOG-WRITE-LANE-A.md)。
