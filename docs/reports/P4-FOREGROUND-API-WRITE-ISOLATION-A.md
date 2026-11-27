# P4-FOREGROUND-API-WRITE-ISOLATION-A 执行报告

2026-11-27：**STOPPED / DESIGN AND VALIDATION PENDING**。本地 HEAD 为 `a2059ad48f1f15a99cc89c3c3f8c4c9141fc2b17`，开始时工作区干净；本次仅文档记录，没有修改产品代码或访问真实图库。

## 已核查的调用链和停止原因

`request_scan` 同步执行 `CatalogService.start_scan`，事务提交后调用 `ScanRunner.submit`（`src/photo_search/api/routes.py`，`src/photo_search/ingestion/service.py`）。新写 worker 一旦接受请求，客户端取消不能阻止写入完成，因此不能仅在等待 worker 的 HTTP coroutine 恢复后提交 Runner。

应用关闭先给现有人物写 worker 五秒，再调用 `ApiContext.close`；`close` 即使一个 worker 没有退出，也继续关闭列表中的其他 worker，包括 ScanRunner，并把仍活动线程交给延迟清理（`src/photo_search/app.py`、`src/photo_search/api/context.py`）。`ScanRunner.close` 标记其关闭，之后 `submit` 抛出异常（`src/photo_search/api/runner.py`）。若新增扫描写线程在五秒后才完成登记，此接入方式可能在 Runner 已关闭后提交，留下已登记但未正常交给 Runner 的任务。因此需要先解决新写 worker 与 Runner 的关闭依赖，并验证数据根 lease 和失败重试。这涉及任务停止条件 4；未擅自修改既有通用关闭及 lease 机制。是否存在不改通用机制的局部安全方案尚待设计和验证，不断言其不可能。

另外，来源删除当前在 API 中检查刷新状态后同步执行删除；异步排队会让刷新可在检查与事务之间启动，需要在设计中保留该拒绝语义。此处是待解决的时序风险，尚未做动态复现。

## 初步盘点，尚不完整

- A：来源登记/改名/删除/扫描登记分别进入 `CatalogService` 同名方法及 `catalog.write()`；单张及批量收藏、普通照片合集写操作直接调用对应模块的同步写函数；人物合集的创建、改名、删除和成员修改同样在路由同步调用并进入 `catalog.write()`。这些候选需要逐条核对及处理，不能视为已隔离。
- C：12 个人物操作使用 `run_person_write` 和现有 `PersonWriteWorker`，不能据此推断人物合集也已隔离。
- B/D 及智能合集、地点、调度、维护等所有剩余路由：尚未完成逐条判定；本清单不是完整盘点。

## 验证与后续

本次只有静态阅读和 `git status` / `git rev-parse HEAD`；未运行 pytest、Ruff、compileall、并发复现或独立审查，所有实施验收均为 PENDING。恢复时须先限定安全的关闭顺序与取消行为，完成完整盘点，再按任务请求执行合成 ASGI 持锁、业务、队列、关闭、B1/A0/A1 定向测试并保存 JUnit；需要 DS-H 全影响面独立审查。未启动 B2，未提交或推送。
