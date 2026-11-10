# P4-LOCK-CONTENTION-RECOVERY-A — SQLite 与模型资源锁冲突恢复

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
