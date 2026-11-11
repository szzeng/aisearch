# P4-LOCK-CONTENTION-RECOVERY-A/B — SQLite 与模型资源锁冲突恢复

## D 最终验证与锁范围审查（2026-11-11）

状态：**IMPLEMENTED / AFFECTED VALIDATION PASS / LOCK-SCOPE REVIEW COMPLETE**。C 提交核验：本地 HEAD、`origin/master` 与 GitHub `master` 均为 `7a24a338c416067252a058d9cc3b79e60e3f2e7d`。C 的旧记录曾因当时任务卡要求而写为暂不提交；用户之后授权提交并推送，当前文档按实际 Git 状态更新。D 的代码/文档改动只在本地，不提交、不推送。

### PERF-BOUNDED

- Flow/risk：刷新人脸阶段与候选模型共用后台模型槽；取消必须能及时退出等待且保留可续做的人脸任务。审查关注锁持有范围，不新增运行时工作。
- Approach/bounds：新增一个基于隔离合成图库、线程事件和真实 `RLock` 的确定性异步取消测试；不改 100 ms 轮询、60 秒上限、推理锁或任务协议。
- Validation/stop：复用 C 的 225 项受影响集合并附加新测试；验证同 job/revision 可恢复、RLock 所有权及锁范围调用链；不运行 GPU、真实图库、全套压力测试或发行验证。

### D 改动与证据

- 新增 `test_face_stage_async_cancel_during_model_slot_wait_keeps_resumable_job`：holder 线程持有实际 `RLock`；刷新线程通过可观察代理确认已调用 `acquire()` 后才触发取消。测试断言在锁仍被 holder 持有时刷新及时退出，原 owner 的锁仍不可被第三线程取得，返回 stage 未成功且 reason/code 为 `CANCELLED`、steps=0；`detect()` 未被调用；已持久化一条 `QUEUED` job，其 `input_revision` 等于资产 SHA。释放 holder 后再次刷新，detect 收到同一 asset；数据库仍只有原 `job_id` 和 revision。
- `test-results/p4-lock-contention-recovery-d/final-affected.xml`：**226 项，203 passed / 23 skipped / 0 failed / 0 errors**。包含 C 原 225 项集合及新增异步测试。Ruff、全 `src/photo_search` 与 `tests` Python 编译、diff-check 全部通过。
- C 已提交并推送的真实状态见本节开头；C 原有 **225 项：202 passed / 23 skipped / 0 failed / 0 errors**、历史范围外 24 项失败、GPU/人脸权重/EXE 限制均保留，不将 D 的验证冒充完整测试或发行验收。

### 人脸锁持有范围审查（只读）

- `refresh/pipeline.py::_face_stage()` 在调用 `face_service.detect()` 前获取 `BACKGROUND_MODEL_SLOT`，外层持锁范围覆盖可选 `recover_cr2_format_failures()`、`detect()` 的目标查询/策略读取/任务入队与 job 查询、同步 `run_queue()` 推理，以及推理后的 face index catch-up 和人物关联/再次 catch-up。
- CR2 历史失败恢复、目标/策略读取、job 入队与选择、候选发布、人物关联均是数据库或索引工作，不执行人脸模型推理，本身不需要 GPU 模型槽；这些阶段会增加刷新等待者持有槽的时间。
- `face/service.py::run_queue()` 的 `_serialize_face_work` 保护实际调度和推理，必须继续持有槽，保证与候选 Qwen 等不兼容模型互斥。`detect()` 同步调用该装饰函数，因此现有 `threading.RLock` 的可重入是外层保护成立的前提；D 没有替换 RLock 或改变这一关系。
- 结论：外围锁正确保护当前完整 detect 闭环，但范围大于真实推理临界区，存在可避免等待。由于缩窄需要拆分 `detect()` 的准备、run_queue 推理和发布/关联阶段，并重新证明队列与发布状态边界，本卡按限制不改锁范围。若仍需降低等待，应另立小范围后续任务，不能直接移除外围锁或放开 run_queue 的互斥。

真实 GPU/人脸权重尚未验证，Windows EXE 未重建。没有修改模型身份、向量定义、数据库 schema、候选暂停恢复协议或搜索协议；完整 unit/integration 历史的 24 项范围外失败未重跑。本卡结束后停止，不提交、不推送、不制作发行包。

## C 后台模型锁与恢复预算收尾（2026-11-10）

状态：**IMPLEMENTED / AFFECTED VALIDATION PASS / INDEPENDENT LUNA H REVIEW PASS**。C 开工基准 HEAD 为 B 提交 `6ee95b3799dce2ce56948de54e2e4544d41fbdae`；A 的 `8c57f79`、`6a01ff8` 和 B 的 `6ee95b3` 当时均已在 GitHub `master`。C 执行结束时按当时任务卡要求暂不提交/推送；之后用户授权提交并推送，现核实提交 `7a24a338c416067252a058d9cc3b79e60e3f2e7d` 是本地 HEAD、`origin/master` 与 GitHub `master` 当前共同指向的版本。

### PERF-BOUNDED

- Flow/risk：刷新进入 face inference 时可能无期限等候候选任务的人脸槽；runner 当前每轮另享 30 秒，会把自动恢复额外等待放大。
- Approach/bounds：仅刷新 face stage 复用 60 秒 `BACKGROUND_MODEL_SLOT` 等待器与取消检查；原 `run_queue()` RLock 保留，因同线程同步调用而可重入。忙/取消时只把当前最多 5,000 个新 delta 优先、剩余预算补旧 backlog，幂等写入现有 face jobs。扫描 runner 的额外等待用 monotonic 累计至 30 秒，自动恢复最多 3 次；活动 lease 绝不重复领取。
- Validation/stop：临时合成图库与可控锁/时间验证超时、取消、异常释放、恢复和 cap 边界；用 fake wait 校验累计预算、最大恢复数、lease 与 runner close，不等待真实 30 秒；复用 A/B/P4-02V 受影响回归。不得扩展到 schema、模型身份或发行构建。

### C 诊断与最终证据

- 根因：`_face_stage()` 调用 `face_service.detect()` 后，其 `run_queue()` 装饰器无超时等待 `BACKGROUND_MODEL_SLOT`。B 的 runner helper 虽有 30 秒上限，但每次恢复独立重置该上限；B 任务记录中的“未提交/未推送”后来已被用户授权提交/推送覆盖，现已按 GitHub 实际 HEAD 更正。
- 新增集成测试覆盖 face slot 忙时不进入 detect、将新 delta job 入队、释放资源后下次日常刷新续接同一待处理 job；等待取消也持久化待办且及时退出；推理异常后 slot 可重新获取；RLock 嵌套可重入；5,000 项 cap 时当前扫描 delta 不被旧 backlog 挤掉。新增 runner 单测使用 fake wait 验证累计预算递减（30→18→6）、最多3次恢复、有效 lease 和缺失 job 不重复领取、过期 lease 走同一 claim 接管、Runner close 中止重试。B 的持续 SQLite BUSY、A/B 与 P4-02V 回归复用并重跑。
- 主受影响 JUnit `test-results/p4-lock-contention-recovery-c/final-affected.xml`：**225 项，202 passed / 23 skipped / 0 failed / 0 errors**。其中 23 项为原有模型/环境前置条件跳过；完整 unit/integration 历史存在 24 项范围外失败，本卡不扩大范围、不重跑全套。`uv run ruff check .`、`python -m compileall -q src/photo_search tests/unit/test_p4_lock_contention_recovery_c.py tests/integration/test_catalog_scan.py tests/integration/test_p4_02y_i_source_build.py tests/integration/test_p4_02y_library_refresh.py`、`git diff --check` 通过。
- Luna H 首审指出 cap 排序可能丢弃当前新 delta、缺失 `scan_job_id` 分支错误把 `False` 当作0秒可继续；已分别改为 fresh delta 优先及 job 缺失 fail-closed，并把两个边界纳入上述最终 JUnit。最终独立复核 **REVIEW PASS**，确认 JUnit 时间和用例覆盖最新修复、文档与 Git 状态一致，无未关闭 P1/P2。
- 真实 GPU/人脸权重未验证，Windows EXE 未重建。源码应用需重启。C 不自动启动1024D候选，不提交、不推送、不制作发行包。
- 完整 unit/integration 历史仍有 24 项范围外失败；真实 GPU、人脸权重与 Windows EXE 未验证。C 后续已按用户授权提交并推送；D 本地补测未提交/推送。

---

## B 后续补修（2026-11-10）

当前续修状态：**IMPLEMENTED / AFFECTED VALIDATION PASS / INDEPENDENT LUNA H REVIEW PASS**。基准 HEAD `6a01ff8281f65a920a067a6b5dcc58d00d915877`；工作区起始干净。本节只处理 B 卡三项遗留，不重做 A 的 BEGIN 有界重试、候选锁分类/取消及候选向量暂停恢复实现。

### PERF-BOUNDED

- Flow/risk：刷新扫描/投影不应占模型槽；语义阶段需与人脸及候选独占协调；扫描写锁争用需保留已提交位置与内容绑定；失效租约不应永久阻止来源删除。
- Approach/bounds：只在语义模型 load/encode/profile 生命周期持 `BACKGROUND_MODEL_SLOT`，等待最多 60 秒并以 100 ms 检查取消；仅 BEGIN 前 SQLITE_BUSY 进入同一 scan job 的 RETRY_WAIT，由 runner 最多再执行 3 次、每次等待不超过 30 秒；删除在同一写事务核查 job 与租约。不改 schema、索引身份、P4-02V drain 让步或确认协议。
- Validation/stop：隔离临时目录验证扫描期槽位可用、编码期互斥、超过本地缩短 busy_timeout 的持续写锁后恢复、活跃/过期租约删除边界；合并上一卡 140 项受影响集合及 P4-02V 测试，Ruff/编译/diff-check；不运行真实图库或全量回归，不提交/推送。

### B 诊断、改动和证据

- 刷新原先在 `LibraryRefresher._run()` 从 service 创建一直持有 `BACKGROUND_MODEL_SLOT`，因此扫描/投影期间也阻塞人脸任务。现在移除外围锁；刷新语义阶段才取得槽位，扫描本身无槽位。build 模式先做可恢复的 durable 分类，再进入模型槽位；获取槽位支持取消，超时明确返回失败并保留 backlog。`face.service.run_queue()`、候选任务的 `ModelResourceGate.exclusive()` 与候选确认/暂停恢复逻辑保留。
- 扫描已有 BEGIN 重试仍有限；scanner 现在只把 `phase=begin_immediate` 且 SQLite base code 为 `SQLITE_BUSY` 的 `CatalogError` 通过现有 `_requeue()` 写成 RETRY_WAIT。业务体未开始时不会重放事务；SQL/COMMIT 及 SQLITE_CORRUPT 不进入 BUSY 恢复。API runner 核对 durable job 已无运行 owner 后有界等待并继续同一 scan；若 requeue 本身仍被长时间锁阻挡，保留 RUNNING 租约，由现有租约过期接管。
- 来源删除在同一事务检查 `scan_runs` 与关联 catalog_scan job：QUEUED 或持有效 lease 的 RUNNING 仍拒绝；RETRY_WAIT、终态和 lease 已过期的 RUNNING 可安全移除登记。找不到可验证 job 时保守拒绝。删除仍不触碰原图。
- 新增模型槽位/刷新阶段并发测试、长时间 SQLite 外部 writer 锁下同 scan 恢复测试、BEGIN busy 分类测试和过期租约及 RETRY_WAIT 来源删除测试。验证命令使用 `uv run pytest -q`，JUnit：`test-results/p4-lock-contention-recovery-b/final-affected.xml`（216 项）及 `test-results/p4-lock-contention-recovery-b/retry-wait-delete.xml`（增量 1 项）。结果为**合并 217 项，194 passed / 23 skipped / 0 failed / 0 errors**（主受影响集 216 项，加后续 RETRY_WAIT 删除增量测试 1 项；包含 A 的 140 项集合）；`uv run ruff check .`、`python -m compileall -q src/photo_search tests/integration/...`、`git diff --check` 通过。SQLite 长锁用隔离连接将 busy_timeout 缩为 50 ms、持锁 750 ms，保留生产两次重试次数并验证已有位置/绑定未重复、扫描状态收敛及 job attempts=1。
- 完整 suite 历史存在 24 项范围外失败，本次未扩大或重跑全套。真实图库、真实 GPU 和冻结 Windows 发行版未验证；不声称容量/硬件验证。
- 独立 Luna H 首审指出 encoder load 异常与等待取消后的槽位释放缺少直接测试；已新增两项测试并独立重跑 2/2。增量复核确认 finding 关闭、无新增 blocker，**REVIEW PASS**。
- 应用源码重启后生效。若候选任务由进程中断，应按既有候选任务页面恢复同一任务；不自动启动候选任务。冻结 Windows 版需在后续发行验证中重建并用合成目录检查刷新、512D 搜索和候选恢复；本卡未构建发行包。

本卡下方 A 部分为已完成的基线记录，保留其历史验收与提交信息。B 执行时曾按任务卡要求暂不提交/推送；之后用户授权简单提交并推送，实际提交 `6ee95b3` 已位于 GitHub `origin/master`。C 开工时本地、远端与 `git ls-remote` 返回的 master 均为该 commit。

---

日期：2026-11-10。状态：**IMPLEMENTED / AFFECTED VALIDATION PASS / INDEPENDENT LUNA H REVIEW PASS**。用户要求修复复核缺口，并沿用先前提交与上传授权。执行报告：[P4-LOCK-CONTENTION-RECOVERY-A](../reports/P4-LOCK-CONTENTION-RECOVERY-A.md)。

## 目标、诊断与边界

分别处理 SQLite 写事务竞争与模型推理资源竞争，不把两者混为一谈。当前工作树干净，HEAD 分支为 `master`（ahead 6）。`docs/development-status.md` 仍指向上一任务 `P4-PLACE-BUNDLED-DATA-A`；本卡由用户本轮明确提出并新建。

历史归因：P4-02U/P4-02V 已确认前台搜索追赶与后台 drain 竞争时，持锁者为 `IndexCoordinator.process_batch` 的短 `claim`/`apply` 写事务序列；线程臂连续不可用最长 32.45 s、占空比 98.96%。P4-02V 已在搜索追赶侧以 100 ms 有界等待并报告 `LAGGING` 解决合法搜索被拒，不能覆盖扫描和其他直接写路径。本轮未提供原始现场日志，无法仅凭静态仓库确定某一次扫描错误的运行时持有进程/线程；合成复现将记录持锁连接、等待连接、线程、阶段及重试结果。

当前调用路径：扫描 `ScanWorker.enumerate/_process_file` → `Catalog.write()` → `BEGIN IMMEDIATE`；文件枚举、稳定性等待、SHA-256 在写事务外。扫描任务的独立 Catalog 连接不能被实例 `RLock` 协调，跨连接互斥由 SQLite/WAL 承担。`Catalog.write()` 分别包装 BEGIN 与 COMMIT 错误，配置 busy timeout 为 5 秒。维护 drain 另有拒绝后有限重试；P4-02V 的让步行为不改。

模型路径：`face.service.run_queue` 装饰器持有 `BACKGROUND_MODEL_SLOT`；候选 worker 先最多等待 5 秒获取该锁，再进入 `ModelResourceGate.exclusive(timeout=15)`，当前统一落为 `RESOURCE_BUSY`。gate 的上下文管理器对查询 reader 和 writer 使用 `try/finally`；候选 worker 对后台锁显式 acquire/release。当前没有提供原始 `RESOURCE_BUSY` 状态材料，需由定向测试分别复现两类占用。

禁止改变数据库 schema、模型身份/向量定义、搜索编码器协议、P4-02V 批次语义、候选独占设计、恢复/幂等语义或 512D 搜索；不访问真实图库/模型，不触碰用户照片，不下载模型。本次跟进仅补测试和更新记录。

## 性能设计（PERF-BOUNDED）

- 链路与风险：扫描与后台写入经不同 Catalog 连接争用 SQLite writer；候选 worker 可能等待人脸任务或前台语义查询并占用 GPU 资源。
- 做法与边界：仅对 `BEGIN IMMEDIATE` 前 SQLITE_BUSY 做有限重试，保留 5 秒单次 busy timeout；事务体/COMMIT 不重试。候选等待有总上限、短轮询和暂停/停止检查，独占模型时长与 100 项批次不变。
- 验证与停止：隔离临时 DB/合成锁验证连接竞争、短锁恢复、长期锁有界失败、原子性及两类模型锁恢复/取消/异常释放；复用 P4-02V 证据，不重跑容量基准，不新增延迟或 30 万性能承诺。覆盖本次改动的单元/集成测试、Ruff、diff-check；事务与任务恢复风险按完整受影响面复核，未复核前不标 PASS。

## 验收与报告

覆盖 `Catalog.write()` 的 BEGIN 重试及 COMMIT/回滚边界、扫描与维护调用、`BACKGROUND_MODEL_SLOT` 与 `ModelResourceGate` 获取/释放、候选暂停/停止/恢复及已确认向量幂等。定向测试均使用临时数据目录和合成模型；保留 JUnit 与原始诊断到 `test-results/`。唯一报告：`docs/reports/P4-LOCK-CONTENTION-RECOVERY-A.md`。结束时更新 `docs/development-status.md` 和本卡；不开展其他优化。

## 最终证据与停止点

- 原实现受影响集 JUnit 为 138 项，0 failures、0 errors、23 skipped。补齐两项直接缺口后，受影响集合最终重跑为 **140 项，0 failures、0 errors、23 skipped**；包括扫描与 drain 的事务重叠、候选任务暂停/恢复持久化向量幂等。完整 unit/integration 首轮为 3,664 项、3,586 passed、52 skipped、26 failed。2 项直接受新重试/接口影响的旧测试已隔离其测试目标或更新替身，并在最终受影响集通过；其余 24 项超出本任务面，完整 suite 未在修订后重跑，不能标全量 PASS。
- `ruff check .` 与 `git diff --check` 均通过。真实 `buffalo_l` 权重缺失，真实人脸推理/512D GPU 搜索未验证；不下载模型、不更改权限。
- P4-02U/V 历史 drain 持锁归因已复核，但未提供本次原始现场日志，当前事故的具体外部持有进程/线程仍未确认。
- 首次独立 Luna High 复核发现两项 P2 测试证据缺口，随后新增真实扫描/drain 锁重叠测试与候选模型真实 `semantic.build_index` 暂停/恢复向量幂等测试。Luna High 跟进只读复核 **REVIEW PASS**：确认扫描线程到达 `Catalog.write()` 边界时 drain 的 apply 事务仍持锁；候选测试核对 100 条暂停状态及恢复后 101 条 SQLite 确认项/Lance 唯一向量行。
- 新增跟进 JUnit：`test-results/P4-LOCK-CONTENTION-RECOVERY-A-final-affected-rerun.xml`；`ruff check .` 与 `git diff --check` 通过。原始运行时事故日志仍未提供；完整 unit/integration 未因本轮纯测试补充而重跑。
