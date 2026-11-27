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

## 2026-11-27 设计复核：范围、候选方案与待验证边界

本轮 HEAD `82366c6bb38d2fce505b1ab9f11c2b4243fefa6a`，开始时工作区干净。仅静态分析；本节是设计候选，不是实施或独立审查通过。`routes.py`、`map_api.py` 与 `web.py` 为 app 挂载的路由；以下盘点按实际 handler 和调用链分组，路径前缀均为 `/api/v1`。类 1 是业务事务可在线程自有 catalog 执行的候选，仍需做失败、重放和线程归属验证；不等于已迁移。类 3 是确定有写入/调度或其他阻塞风险、不能简单搬迁的保留项；类 4 并不声称文件系统、读查询或模型永不阻塞。GET 端点可能有派生写入，不能仅按 HTTP 方法判断。

### 完整路由盘点（按实际执行路径分组）

| 分类 | 实际路由 / handler | 业务方法、事务与决定 |
|---|---|---|
| 1 | POST `/sources`；PATCH `/sources/{source_id}` | `register_source` / `rename_source` → `CatalogService.catalog.write()`；登记返回 `get_source` 和 counts，整个结果在 worker 自有连接完成。 |
| 1 | PUT、DELETE `/assets/{asset_id}/favorite`；POST `/favorites/batch` | `set_favorite` / `set_favorites_batch` → `favorites.py` 的 `catalog.write()`；保留幂等及 revision。 |
| 1 | POST `/photo-collections`；PATCH、DELETE `/photo-collections/{collection_id}`；POST `/photo-collections/{collection_id}/members/batch` | `photo_collections.create_collection` / `rename_collection` / `delete_collection` / `edit_members` 各自 `catalog.write()`，保留 token、revision、成员、request_id。 |
| 1 | PATCH、DELETE `/photo-smart-collections/{smart_id}`；PUT、DELETE `/photo-smart-collections/{smart_id}/exclusions/{asset_id}` | `photo_smart_collections.rename_collection` / `delete_collection` / `set_exclusion` → `catalog.write()`；保留 token、revision、幂等。 |
| 1 | POST `/persons/collections`；PATCH、DELETE `/persons/collections/{collection_id}/{instance_token}`；POST 同路径 `/members`、`/faces` | `person_collections.create_collection` / `rename_collection` / `delete_collection` / `edit_members` / `edit_face_members` → `catalog.write()`；这五条没有经过 B1。 |
| 1 | PUT `/persons/{person_id}/cover`；POST `/faces/inbox/visibility` | `person_albums.set_cover` / `set_inbox_visibility` → `catalog.write()`；保留 revision 和人工状态。 |
| 1 | POST `/places`；PATCH `/places/{place_id}`；PUT `/assets/{asset_id}/place-assignment`；POST `/place-assignments/batch`、`/place-operations/{operation_id}/undo` | `PlaceService.create_place` / `patch_place` / `write_assignment` / `write_batch` / `undo` → `self.catalog.write()`；线程内创建 PlaceService，保留 request_id、revision、原子事务及错误适配。 |
| 1 | POST `/scheduler/pause`、`/scheduler/resume` | 路由内 `catalog.write()` → `scheduler_state.set_paused`；仅迁该事务和结果，不更改活动 lease。 |
| 1 | POST `/storage/library-retention/anomalies/{source_id}/{scan_id}/resolve` | 路由内 `catalog.write()` → `resolve_source_anomaly`；不触碰清理/删除协议。 |
| 1 | POST `/search/feedback` | `submit_feedback` → `catalog.write()`；传普通已验证字段在线程内重建请求，保留 request_id、200 replay / 201 响应。 |
| 2 | POST `/sources/{source_id}/scan` | `CatalogService.start_scan` 的 `catalog.write()` + `ScanRunner.submit`；两个步骤必须由同一 Runner 内协调，见下文。 |
| 3 | DELETE `/sources/{source_id}` | `refresher.is_running()` 后 `CatalogService.delete_source` 的 `catalog.write()`；目前直接迁移会引入检查后刷新启动窗口，暂缓。 |
| 3 | POST `/photo-collections/from-search`、`/photo-collections/{collection_id}/members/from-search`、`/photo-smart-collections/{smart_id}/save-static` | 搜索 session、授权和快照校验后调用 `photo_collections.create_collection` / `edit_members` 的 `catalog.write()`；涉及跨 await 的快照、replay 和事务内 revision 重验。未证明只搬最终调用足以保持全部竞态与取消语义，单列设计后再迁。 |
| 3 | POST `/photo-smart-collections/from-search`、`/photo-smart-collections/{smart_id}/replace-rule` | `create_collection` / `replace_rule` 后可再 `publish_candidates`，各有 `catalog.write()`；首个事务已提交后的缓存发布失败被特别处理，存在跨事务与 session 时序，暂缓。 |
| 3 | POST `/photo-smart-collections/{smart_id}/refresh` | `SmartRefreshRunner.submit` 在调用线程内 `request_refresh` → `catalog.write()`，其队列预留、失败补偿和 executor 提交必须协调；虽有 Runner，当前登记仍阻塞。暂缓。 |
| 3 | POST `/faces/rematch` | `face.service.rematch_unassigned` → `catalog.write()`，还有有界匹配/派生工作与人脸序列化，非普通轻量事务；暂缓。 |
| 3 | POST `/faces/source-detail-batches`、`/{batch_id}/pause`、`/{batch_id}/resume`、`/{batch_id}/retry`（完整前缀 `/faces/source-detail-batches`）；POST `/assets/{asset_id}/face-detail` | `source_detail_batches.create_batch` / `pause` / `resume`，或 `face.service.enqueue_source_detail` → `catalog.write()`；部分登记后 `FaceDetailRunner.submit[_batch]`，涉及已提交任务和关停恢复，暂缓；pause 也需复核与活动任务的状态同步。 |
| 3 | POST `/storage/library-retention/cleanup`、`/storage/library-retention/automatic` | `create_library_retention_run` / `set_library_retention_automatic` → `catalog.write()`，部分后续调用 `RetentionCleanupRunner.start`；涉及跨事务授权和恢复，暂缓。 |
| 3 | POST `/places/albums/backfill` | `place_backfill.run` → GPS/地点 pipeline，可能执行多个事务和文件/处理工作；暂缓单独评估。 |
| 3 | POST `/search-profile/activate` | `activate_profile` → `catalog.write()`，涉及 profile/generation 原子协议及 readiness；暂缓，不能按普通写入处理。 |
| 3 | POST `/embedding-management/providers/{provider_id}/candidate-build`、`/tasks/{task_id}/pause`、`/tasks/{task_id}/resume`、`/providers/{provider_id}/check`、`/providers/{provider_id}/isolate-orphan`、`/providers/{provider_id}/switch`；DELETE `/embedding-management/offline-transition` | candidate manager、模型检查和离线切换的文件/状态/worker 协调，不属于单一 Catalog.write；不能以“已有 worker”推断整个 handler 无阻塞，暂缓独立处理。 |
| 3 | PUT `/map/provider-settings`；DELETE `/map/provider-settings/key`；POST `/assets/{asset_id}/local-action`；POST `/storage/maintenance/request`；DELETE `/storage/maintenance/request` | 文件写入、系统动作或维护状态；不属于 SQLite 写锁问题，也不能断言无文件系统等待。 |
| 4（已隔离） | 12 条 `run_person_write`：POST `/person-operations/retention-cleanup`、`/face-mappings/decisions`、`/persons/visibility`、`/persons/merge`、`/persons/{source_person_id}/split`、`/person-operations/{operation_id}/undo`、`/faces/{face_id}/create-person`、`/face-assignments/batch`、`/persons/{person_id}/regroup`；PUT `/persons/{person_id}/label`、`/persons/{person_id}/name`、`/faces/{face_id}/assignment` | 已由 `PersonWriteWorker` 执行 `PersonService` 写事务；保留 B1，不纳入新 worker。 |
| 4（后台执行） | POST `/places/admin/update` | `run_catalog_projection` 在现有 worker 上调用 `cn_facets.update_auto` 的写事务；不是 event loop 直接等待该写事务，不因此迁移。 |
| 4（轻量提交） | POST `/library/refresh`；DELETE `/memories/jobs` | `LibraryRefresher.start` 提交内存队列/后台线程；`MemoriesRunner.cancel` 是内存取消。刷新本身与删除来源有协调关系，不能据此忽略该竞态。 |
| 4（非写入） | POST `/photo-random-sample`、`/photo-random-summaries`、`/search`、`/search/place-aware`、`/search/image`、`/search/by-asset`、`/search/by-asset/validate` | 只读/检索或内存会话；搜索计算本身可走现有 worker，不按方法名将其当用户写事务。 |
| 4（其他只读与派生） | 全部其余 GET/HEAD：health、session、capabilities、stats、storage 的只读视图、sources/jobs、favorites/memories、普通/智能合集详情和资产、assets/timeline/原图、places、persons/faces/operations、search/profile、embedding status、map 查询，以及 web 页面；GET/HEAD 缩略图、预览、人脸裁剪 | 按各自 read/query 或媒体派生路径运行；媒体缺失时 `asyncio.to_thread(_generate_one_in_worker)`，涉及文件/派生工作且不等于 event loop 直接等待 Catalog.write。存在同步读取及其他等待可能，不在本卡声称已解决。POST `/storage/inspect`、PUT/POST `/storage/backup` 的重工作走 `asyncio.to_thread`，并非简单 Catalog.write 候选。 |

边界：此清单静态覆盖挂载路由及写入调用族；动态插件、每个深层依赖的所有副作用未做运行时证明。任何上述“类 4”仍须在实施前复核路径，无证据不得将未知写锁等待称为无风险。`service()` 的惰性恢复和普通只读请求可能触发额外初始化，不能把普通页面响应测试误认为全路由证明。

### 扫描：Runner 内登记与执行候选

现状：Runner 只有一个 daemon 线程，`submit` 去重后排来源 ID，`close` 将 `_closed/_stop` 设真并清空等待队列；线程在 `_run_one` 中执行扫描。`start_scan` 在事务内查找活动 scan，复用 scan_id/job_id 或新建并排 job。现有 `_wait_for_scan_retry` 对特定 BUSY 做有界重试；源码注释明确下一次请求可恢复中断 scan，未发现启动自动扫回所有 scan 的链路。

推荐待审核流程：在 Runner 自身引入有界“登记请求”队列和每来源登记状态；HTTP 将纯 ID 提交并异步等待结果，不把 Catalog 传出。Runner 自己的线程在开始扫描前使用自身 service 调 `start_scan`，提交成功后先在 Runner 内将同来源执行意图置入自身待执行状态，再完成 HTTP future；不得依赖取消后的 HTTP 协程再次 submit。相同来源登记期间共享一个 future 或串行复核；成功返回既有 `source_id/scan_id/job_id/state/reused`，重复调用语义必须与实际 `start_scan` 的第一次和重放分别一致，不能简单向所有请求返回相同 `reused`。活动扫描期间新请求也必须核对当时持久状态，不能仅凭 `_active` 伪造成功。单扫描线程正忙时登记会排队，必须显式限制队列并在未接受前 503；可能等待一个完整扫描，属于用户体验风险，需测试与设计复核，不能承诺快速返回。

关闭：在 Runner 锁内把排队但尚未开始登记的请求原子摘除，并对仍在等待的 future 返回明确可重试 503；这类预留不称已提交。已经开始的登记，即使 HTTP 取消也必须执行到 commit 或 rollback；注册成功但关闭已开始时，不报告成功后假称已执行，保留持久 scan/job 并返回明确可恢复状态，或按现有 API 的成功契约交付但必须能恢复。为满足取消与进程退出安全，需把 Runner 的拥有者线程改为非 daemon，且在线程退出前完成正在登记的事务、在所属线程关闭 Catalog；其 `close` 超时返回 false 继续由现有 `ApiContext.close` 观察 `_thread` 并保留 lease。此改变仅属于 ScanRunner 自身生命周期，不改 PersonWriteWorker、通用关闭或 lease 代码，但需单独审查其对现有扫描中断的影响。

提交到 DB 与排入进程内队列之间的进程崩溃窗口无法通过内存队列消除；持久 scan/job 是恢复依据而不是假称窗口不存在。最小恢复路径：重新发起同一来源 scan，`start_scan` 原子复用未终结 scan/job；对于活跃 RUNNING lease，遵循现有 lease 到期及 `run_scan` claim/refusal，不抢占有效 owner。QUEUED、RETRY_WAIT、过期 RUNNING 各自的可恢复条件和重启后完整行为必须合成故障注入验证；当前没有证据保证自动重启恢复。若产品要求无用户重试的自动恢复，需明确增加有界启动扫描及旧 lease 处理，单独评估，不能声称已经有此行为。数据库已持久但响应丢失时允许同源重试；不存在“没落库却报成功”。

### 不改 `ApiContext.close` 的写 worker 生命周期候选

独立 `CatalogWriteWorker` 必须有自己单线程、单独 service、1 执行 + 4 等待、白名单、接受后 Future 不取消、满载 503/Retry-After。直接在 context 新增字段却不纳入 `close` 是不安全的；把它挂在 PersonWriteWorker、只登记 daemon 线程或只等待 HTTP future 也不可取。

可研究一个**仅由 ScanRunner 拥有的组合资源**：现有 `context._runner` 仍是 `close` 唯一可见对象；Runner 在首次前台写请求前创建独立 CatalogWriteWorker，Runner 自己的非 daemon `_thread` 在退出前等待子写线程排空且在线程内关闭其 service。Runner.close 同时停止两个入口，并在有限宽限内 join 所有权线程；超时返回 false，让现有延迟清理和 lease 跟随仍存活的 `_thread`。Runner 必须保证子 worker 无论异常、关闭失败或新任务竞争都不能早于 `_thread` 被遗弃：子关闭失败保留引用并可重试，任何子线程存活期间所有权线程必须存活；初始化与 context 关闭之间的受理必须受已有 lifecycle lock / Runner condition 安全线性化，不持锁等待 SQLite。重复 `close` 可安全重试，不能改变现有 B1 自己的生命周期。这样**无需改 `ApiContext.close()` 的通用机制和 lease 实现**，但改变 ScanRunner 自身的生命周期且增加耦合，尚未验证在所有异常和事件循环退出路径均安全；不能把它描述为已证明的现成方案。另一种直接把新 worker 加入 `ApiContext.close` 列表的常规接入会修改该函数，故不满足本轮明确目标。

### 删除来源的局部协调

当前 `is_running` 检查在 event loop 上，而刷新 `start` 也从 event loop 提交；将删除排队后，检查通过到真正删除之间可以开始刷新。单次重新检查仍有 TOCTOU。理论上可在同一进程用 refresher 自己的短状态锁为删除登记“删除预留”：刷新启动在预留期间明确拒绝或异步等待；删除获得预留后线程内检查并事务删除，finally 释放，不能在持有锁时同步等待 SQLite。必须同时覆盖所有刷新启动入口、等待任务取消、启动/关闭和错误、已运行与排队 build；还须确定对原 API 行为的影响。该协议尚未证明覆盖所有入口，且属于新的局部协调，故本轮建议**暂缓迁移删除**，保留旧实现与已知 event loop 阻塞，不将它计入完成标准。已有 scan-in-flight 的事务内拒绝仍须保留。

### 实施文件与最小验证计划

候选涉及新增 `src/photo_search/api/catalog_writes.py`、`api/runner.py`、`api/context.py` 的提交入口/懒创建（不改 `ApiContext.close()` 本体）、`api/routes.py`，以及现有 API/runner/关闭测试和文档；如组合资源不可证明安全则不实现。应使用白名单 adapter 在线程内重建 PlaceService / FeedbackRequest 等，不传 request catalog、connection、业务 service 或不受控 callable。

最小合成测试：真实 ASGI + 第二连接 `BEGIN IMMEDIATE`，等待注册/收藏或普通合集写时确认 `/health`、人物浏览和轻量图库读先完成，解锁后验证提交；逐类业务及错误/revision/token/request_id，登记 counts；1 执行 + 4 等待、满队列拒绝、客户端取消后提交/回滚；扫描同源并发与不同源容量、活跃扫描时登记、提交前后取消与关停、关闭时未开始请求可重试拒绝、提交后崩溃/重启手动重试和有效/过期 lease；联合子 worker 关闭超时、关闭失败重试、线程所有权和 lease 不提前释放（含子进程退出）；保留来源删除/刷新竞态回归；B1 人物写、A0 七场景及 A1 少量读取/关闭回归；pytest JUnit、Ruff、compileall、diff-check。使用合成数据，不跑全仓压力；高风险完整受影响面独立 DS-H 审查仍是实施 PASS 门槛。

### 停止条件及结论

现已提出不修改 `ApiContext.close()`、lease 实现或其他 worker 生命周期的**候选**，但组合资源的超时/异常所有权和扫描恢复尚未通过设计独立复核或故障验证；不能证明其已经满足全部安全要求，也不能宣称设计阻塞已解决。它改变 ScanRunner 自身生命周期，是否符合原停止条件 4 的边界需审查；不改其他通用 worker。删除、智能刷新及其他需要跨 worker 的入口继续阻塞全卡目标。维持 **STOPPED / DESIGN REVIEW PENDING**；先审查更小的 [扫描登记与组合资源设计卡](../tasks/P4-SCAN-RUNNER-REGISTRATION-DESIGN.md)，未授权实施前不写产品代码，不启动 B2。最终执行范围若排除类 3，不能标记原卡全量 PASS。

## 独立 Sol 复核（2026-11-27）

**REVIEW CHANGES REQUIRED / STOPPED**。独立 Sol High 只读审查上述方案和实际调用链；未运行产品或访问真实图库。以下是静态识别的风险及需复现的时序，不把现有代码中的潜在竞态称为本次引入或已动态复现。候选尚未获实施许可，也未修改产品代码。

1. **[P1] 单扫描线程方案不能保持既有 202 和同源复用契约。** `runner.py` 的扫描线程在 `_run_one` 中可能执行完整扫描，若登记也排在该线程，重复请求要等扫描完成，可能登记为新 scan；`docs/tasks/P1-04.md:118–124` 明确要求重复请求复用同一非终态 scan/job，202 不等扫描结束。上一节“共享或串行复核”未解决这个反例，须撤回单线程登记推荐，另设计有界独立登记通道和准确受理边界，不额外改变扫描执行的单线程约束。
2. **[P1] 仅按 source ID 去重不能保证已登记身份被执行。** `runner.py:95–105,190–204` 按 source 判断 active/queued；`scanner.py:586–609` 可先提交旧 scan 成功，而 Runner 尚未清 `_active`；此时新登记可创建新 scan/job，`submit` 却因相同 source 视为重复，路由仍返回新任务的 202，新的持久任务无内存执行意图。刷新 `refresh/pipeline.py:441` 也可能在登记与执行间竞争同源工作；当前 scanner 执行会重新查找/登记，不保证执行的身份就是响应中的任务。需在合成闸门下验证两种交接时序；设计必须将任务身份、去重、扫描/刷新冲突与恢复协议说清，若需改变既有事务或扫描身份协议则按原停止条件另行处理。
3. **[P1] 所有权组合不能仅凭 Runner 自身线程退出证明 lease 安全。** 成功回调 `runner.py:203` → `context.py:347–376` 可在检查 `_closed` 后进行库操作，再懒创建并启动 retention runner；同时 `context.py:619–653` 可能已经快照 workers，后到子 worker 不在关停清单。静态时序显示潜在提前释放 lease 的路径，需要合成闸门覆盖新旧 retention runner、关闭和创建交错；如必须修改通用生命周期或其他 worker，触发停止条件 4。本发现不声称已在运行中复现。
4. **[P2] 来源登记移线程后，响应读取会与删除并发。** `ingestion/service.py:223–232` 提交插入后才 `get_source`；原删除路由仍同步执行时，可能恰在提交和读取之间删除，令登记已提交但响应变成不存在。须设计保持原响应/并发语义的协调或延后迁移登记，并用提交后闸门复现验证；不能未经验证将其视为简单直迁。
5. **[P2] 恢复与取消边界尚不充分。** `jobs/queue.py:546` 的未到期 RETRY_WAIT 会以状态冲突拒绝，不等同 `runner.py:217` 的 BEGIN BUSY 自动重试；活动 lease 及 `store.py:151` 的 BLOCKED 也须列出可恢复条件。相同请求的多个 waiter 不能因一个取消而取消共享 future；停收时“队列预留”与“已接受”线性化点及已提交但丢响应的行为必须唯一明确。非 daemon Runner 若扫描永久卡住会使进程保持存活；事件循环退出后延迟清理可能无法在原进程完成 lease 释放，需分别验证正常关闭、超时、进程退出和同进程重新绑定，不应承诺自动恢复。
6. **[P2] 路由表的分类表述需修正。** AST 静态核对得到 `routes.py` 168、`map_api.py` 7、`web.py` 6 个显式 router 装饰器；另有 FastAPI 自动 OpenAPI 路由。web 路径没有统一 `/api/v1` 前缀。`/search`、`/search/place-aware` 的搜索 worker 可能执行 FTS catch-up 写入（`catalog_reads.py:347–355`、`search/documents.py:329`），应归为已有 worker 的派生写而非无写入；`GET /assets?text=` 和智能合集资产的条件查询也可能进入 projection/search worker，`GET /session` 会消费一次性凭据并建立内存会话。以上不意味着 event loop 直接等待对应写事务；`context.service()` 懒恢复等例外仍需单独核验。

后续建议先形成更小的扫描登记设计：独立有界登记通道、每请求不可互相取消的等待、保持即时响应与同源持久身份，并将跨 worker 所有权/lease 和既有竞态单独审查。该建议仍非已验证实现；原卡保留 STOPPED，类 1 的登记直迁和 Runner 组合资源候选均不得按 PASS 实施。
