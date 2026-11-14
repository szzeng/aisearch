# P4-BUILD-STABILITY-A2 — 阶段 A–B 执行报告

初始审查日期 2026-11-12，历史基线 `6d4d76271808c2136c7222f6160a25f94948bfb5`；A 卡的修改已按后续用户授权提交、推送，旧阶段中的“未提交”是历史文字。2026-11-14 基于 `5fc7b739583083e68867d2c2f9867c1342c3ba7d` 续做后的当前状态见文末“B 阶段架构收敛与必要修复”：代码面和受影响验证已收敛，但目标硬件、真实显存和独立审查仍 PENDING，A2 不是最终 ACCEPTED。

## 已核实的问题与保留成果

- A 卡的关闭取消、扫描分块心跳与旧租约拒绝、线程存活时保留数据根租约、OCR 任务间停止已在当前源码；历史测试不能替代本卡完整验收。曾有外部 SQLite 锁 30 秒后显式恢复的隔离证据，不证明自动恢复或前台延迟。
- `ocr.service.record_runtime_evidence()` 无设备参数，固定依次调用 CPU 与 GPU `cache.engine()`；`run_queue()` 即使只执行 CPU 任务也先调用它。现有 `test_cpu_probe_failure_does_not_skip_gpu_probe` 在替身运行时确认两次探测。真实 GPU 探测、执行既未取得 `BACKGROUND_MODEL_SLOT`，也不使用 `ModelResourceGate`；真实并发事故是否发生未验证。
- `PinnedOcrEngine` 在进程内通过 RapidOCR 构造 det/cls/rec ONNX sessions，`OcrEngineCache` 按签名缓存实例；`run_queue` 默认新建 cache，但调用者可以传入并持有 cache。当前源码未提供会话显存释放确认接口；cache 超出一次调用的寿命和 ORT/驱动显存何时释放尚未测得，不能把 Python 引用变化或执行锁释放当成显存释放。
- 前台语义编码通过 `GatedEncoder.query()` 使用 `ModelResourceGate` reader；候选工作先取得 `BACKGROUND_MODEL_SLOT`，再获取 gate exclusive，随后关闭当前查询 encoder，退出时逆序释放。刷新语义和人脸使用后台 RLock，人脸 `run_queue` 自身可重入。后台槽与查询 gate 的作用不同，互斥推理不等于限制已有模型驻留；不能把候选的前台拒绝窗口直接施加给 OCR 并称满足前台优先。
- `ApiContext._finish_shutdown()` 在调用 encoder.close 之前清空引用，异常时返回；`close()` 发现 `_closed` 后不重试。隔离替身验证首次 close 失败后，第二次 close 不再调用 encoder，见 `test-results/p4-build-stability-a2/phase-a-close-probe.xml`。实际 `WorkerQueryEncoder.close()` 也在 client.shutdown 前设置 `_closed`，故仅恢复外层引用不足以证明真实 worker 重试安全。现有实现遇事件循环失效或失败会保留数据根租约，但是否可安全完成重试仍待修。

## 设计结论与停止点

GPU 任务应优先在领取之前获得许可；若不能提前许可，必须验证有界等待和续租，避免等待消耗租约；CPU-only 入口不能因 GPU 探测排队，也不能把暂时忙误写为 GPU 故障并触发 auto fallback。当前 `record_runtime_evidence` 同时生成两设备共享的持久能力快照，调度器依据快照和资格记录直接将不合格 job 转为 BLOCKED；分离探测时需要证明不会污染其他设备事实、绕过原设备选择、误触发 fallback 或改变已持久化任务。不同消费者及 direct executor 调用还需纳入同一资源边界。

当前代码没有可核验的 OCR GPU session 显存释放接口或 OCR 与常驻语义 worker 的驻留预算，也没有将现有后台槽、前台 gate 与 OCR 缓存生命周期连接的机制。仅添加执行锁不能满足该卡对安全驻留及前台优先的全部要求；本机没有 GTX 1660 + HDD，无法用目标设备数据证明同时驻留。此前将“尚不能证明可安全扩展”等同于触发“架构无法安全扩展”停止条件，证据不足，现撤销该停止判断。应继续比较候选调度方案，并用隔离替身验证；真实显存和目标硬件验收保留 PENDING，不预断方案可行。

可供后续评估的最小方向：先冻结 CPU-only 与 GPU-only 能力记录及忙状态的持久语义，明确每次领取 GPU 前的有界许可和可取消等待；再通过真实运行时验证模型关闭/驻留，或由架构决策明确切换窗口与回退。方案必须验证既有 CPU fallback、前台读者、候选锁顺序及真实 VRAM；不能仅通过锁、清缓存或替身声称资源释放。

## 验证

隔离执行：`.venv\\Scripts\\python.exe -m pytest -q tests/integration/test_p4_02y_l_identity.py tests/integration/test_p4_perf_catalog_worker_lifecycle.py tests/unit/test_embedding_management_coordination.py --junitxml=test-results/p4-build-stability-a2/phase-a-baseline.xml --basetemp=.pytest-tmp/p4-build-stability-a2-phase-a`，退出 0，48 passed。额外只读行为探针 `.tmp/p4-a2-close-probe.py`（不作为产品回归测试）1 passed，证实现状缺陷而非验收修复。无真实 GPU 推理、显存、机械硬盘或用户图库访问；用户已确认目标硬件不可用。按用户要求，最终独立工程复查改用 Terra 高推理，阶段 B–E 和最终复查及目标硬件验收均 PENDING。没有产品代码性能对比、浏览器测试或全套回归。本任务不提交、不推送。

## 独立 Terra 高推理阶段 A 复查（2026-11-12）

独立只读复查确认探测耦合和 encoder 关闭问题有源码依据，现存 48/48 基线和 1/1 探针证据可读；审查者未重跑测试。指出两项 P2：缺显存/硬件数据不足以证明架构不能安全扩展，报告把领取前许可写成强制而忽略卡片允许的有界等待＋续租。已按上述内容纠正，继续设计与隔离验证。最终实现的全受影响面复查尚未进行；本次阶段 A 复查不代表实现通过。

## 2026-11-12 阶段 B 续做：OCR 提交租约时钟

隔离探针证实 `Scheduler.run_once()` 原来把领取前 `now_ms` 原样传给提交：OCR 执行超过租约后，如无其他 worker 接管，过期结果仍可成功提交；这不只是在 GPU 锁上等待的问题。`.tmp/p4-a2-lease-probe.py` 为只读基线缺陷探针，`lease-probe-r2.xml` 1 passed 表示复现旧行为，不是修复通过。首轮 `lease-probe.xml` 因 PYTHONPATH 缺少 tests 导致收集错误，已保留；补全路径后重跑通过。

现对调度器增加可选 completion clock：OCR 调用时执行结束后重新取时间，准备产物后获取写事务时再次取时间；两次均按既有 token/revision 资格检查，过期不发布 OCR 文档，任务保持原租约状态供既有过期恢复路径处理。默认调用不提供 clock 时保持原行为；未修改 task ID、处理器身份、表结构、outbox 或其他消费者时间语义。新增三个隔离测试：执行期过期、写锁等待期过期、OCR run_queue 实际路径过期，均拒绝旧结果。`tests/unit/test_p2_scheduler.py tests/integration/test_p2_scheduler_recovery.py tests/integration/test_p2_scheduler_api.py tests/integration/test_p4_02y_l_identity.py tests/integration/test_p3_ocr_lifecycle.py tests/integration/test_p4_ocr_v4_upgrade.py tests/integration/test_p4_02y_library_refresh.py` 使用 `.venv\Scripts\python.exe -m pytest -q ... --junitxml=test-results/p4-build-stability-a2/ocr-lease-affected.xml --basetemp=.pytest-tmp/p4-a2-ocr-lease-affected` 退出 0，151 passed / 11 skipped / 0 failed；11 项需要真实 OCR 资源。Ruff、compileall、diff-check 退出 0。调度器公共接口采用兼容可选参数、只有 OCR 传入，覆盖其直接消费者与通用 scheduler 测试；未改迁移或依赖，没有宣称全仓或完整 B 阶段通过。

独立 Terra 高推理进一步只读核实：现有 CPU/GPU 与其他消费者按同一 runtime package 查最新 capability snapshot；把仅 CPU 探测的 GPU 健康写成 unknown/unhealthy 可能误阻断或触发 auto fallback，复用旧 healthy 则必须证明身份与有效性。候选思路是分 processor 调度器，GPU 在事务外、领取前取得有界可取消许可，忙时保留 job 与 attempt，不调用故障 fallback；还须解决单设备快照发布、缓存驻留、所有入口、前台优先与公平性。该思路并未证明实现可行，且领前许可不解决长推理续租或显存释放。GPU 协调仍未实现；阶段 B 尚未完成，C/D/E、最终 Terra 高推理全受影响复查与目标 GTX 1660/HDD 仍 PENDING。未提交、未推送。

## 2026-11-12 单设备快照原型复查

尝试仅探测指定 OCR 设备、在单个写事务内合并另一设备的前次 provider 信息，并验证 CPU-only 不创建 GPU session、保留旧检测时间、模拟交错写入和平台变化四例；隔离 JUnit `cpu-probe-v3.xml` 4 passed。此四项不能证明跨消费者与真实并发安全。独立 Terra 高推理只读审查发现：首次 CPU-only 使 GPU 为 unknown，现有 scheduler 会将同包 GPU 任务置 BLOCKED，OCR auto 可把未探测误当成 GPU 故障而回退（P1）；驱动/GPU 设备/Session 未进入完整身份或有效期，旧健康不能证明当前有效（P1）；探测与记录时间交错可能覆盖较新事实（P2）；单一包级 failure_code 不能可靠归因 CPU/GPU（P2）。原型及其四项源码测试已撤回，保留 JUnit 作为实验记录，未将其接入实际队列，不能声称问题已修复。

撤回后重跑 scheduler、OCR、刷新受影响集合，`test-results/p4-build-stability-a2/ocr-after-review.xml`：退出 0，151 passed / 11 skipped / 0 failed。Ruff、compileall、diff-check 通过。仍须设计真实并发下的独立设备资格、共享能力事实与 fallback 归因，确保其他共享 runtime package 的消费者不被误阻断；待证明方案后才能实现 GPU 领取前许可。阶段 B 仍未完成，不进入 C/D/E；目标硬件与最终独立 Terra 高推理审查 PENDING。未提交、未推送。

## 2026-11-12 继续实现：未验收的 GPU 协调和能力快照

以下进展取代上文“GPU 协调尚未实现”的状态，均为工作中代码，不代表阶段 B 或本任务通过。OCR 的 CPU/GPU 探测和队列拆开，GPU 批次在领取前取得有界、可取消后台许可；忙和等待中取消不领取、不消耗重试，CPU 不取得 GPU 槽。初始化及推理入口也使用可重入许可。新增 Windows 命名互斥量以协调新版本进程，同一用户跨进程和 abandoned mutex 有隔离测试；非 Windows 只有进程内锁，同机其他用户及旧版本并发未验证。此前一次跨进程试验短暂使用了产品默认互斥量，后续已改用随机隔离名称。锁只协调执行，不证明 OCR 与语义模型的显存能同时驻留或被释放。

OCR 租约按需由独立连接续期，提交使用当前时钟与既有所有权检查；隔离测试覆盖慢推理、旧拥有者接管前后提交与心跳、30 秒 SQLite 外部写锁后显式恢复（`ocr-sqlite-30s-r1.xml` 1 passed）。等待资源期间不持有写事务。GPU 取消、忙、CPU 独立、局部 fallback、定长批次、cache 初始化与 provider 核验等有定向测试；`gpu-attempt-r6.xml` 为当时旧代码组合 210 passed / 11 skipped，并非当前最终回归，跳过项依赖真实 OCR 资源。Provider 列表核验不是显存预算或真实推理压力验收。

共享 ONNX 包能力快照现尝试在同一事务内合并各设备观察，并让执行调度固定本次 revision；OCR、人脸和语义写入均已接入。独立 Terra 高推理指出仍需证明真实 provider 消失与未探测的区别、同毫秒及时钟倒退、不同身份、旧版本 writer、search/profile 等读取者和故障归因；已确认其为未关闭风险，当前设计未验收。`current-shared-r1.xml` 127 项中 2 失败，分别是异常代码改名后的测试期望与测试引用移动模块，均已调整；`consumer-shared-r1.xml` 73 项中 1 失败 / 16 skipped，暴露语义搜索 profile 激活回归，修正后 `consumer-shared-r2.xml` 12 passed（包括两个上述重跑用例）；完整受影响集合尚待最终重跑。保留失败 JUnit，不将局部重跑伪称整套通过。

前台搜索当前仍可能与 OCR 重叠，是否等待不可中断推理结束、有限等待后返回可重试忙、或允许并发，已向用户询问，尚无选项答复；不能宣称保留前台优先。独立 Terra 还用合成子进程证实父进程突然退出后 AI worker 可继续存活，父进程名义互斥量释放并不能证明子进程和显存已释放；真实 GPU 后果未实测。直接 scheduler/executor 入口、worker 所有权、驻留预算与关闭时续租线程关系仍需补全。用户确认没有 GTX 1660 + 机械硬盘，目标硬件 PENDING；阶段 C/D/E、真实浏览器、性能、最终全受影响面独立复查仍未完成。未提交、未推送。

补充验证：`current-shared-r2.xml` 同一轮受影响集合 206 项、190 passed / 16 skipped / 0 failed；包括 OCR、调度器、人脸、语义建库和搜索配置，16 项为依赖人脸资源的跳过，仍非全仓或 GPU 验收。`provider-drift-r1.xml` 两个隔离测试确认 CPU 探测看到 GPU provider 消失或运行时平台身份变化时，不延用旧 GPU healthy；未覆盖所有生产者、同毫秒冲突、设备硬件变化与旧版本并发。Ruff、compileall、diff-check 通过。独立 Terra 指出候选方向是在既有 AI worker 加载前取得并持有进程级许可，结合父死亡监测或 Job Object 和启动握手；父子交接、常驻 worker 饥饿及重建开销、显存释放均未证明，该建议不是已验收方案。

## 2026-11-12 前台等待策略续做

用户选择前台等待已经开始且不可中断的 OCR 到安全边界。当前接入同进程前台等待标记，在 GPU OCR 单图之间让出，查询执行遵循模型许可 → gate → adapter 锁；首次加载在 adapter 锁之前取得许可，过时 adapter 代理在进入 gate 后拒绝使用。未知设备的工厂与 adapter 保守取得 GPU 许可，明确 CPU 的 worker 避免该许可。事件测试 `foreground-wiring-r5.xml` 26 passed，包含首次模型加载与候选建库锁顺序和过时代理；`foreground-probe-r1.xml` 2 passed，覆盖第一张 OCR 后让前台、GPU 探测中前台到达后继续 OCR。早期探针 `foreground-wiring-r2.xml`、`foreground-boundary-r2.xml`、`foreground-boundary-r3.xml` 因测试代码缺少正确导入/错误访问 job 字段失败，修正后保留原 JUnit 并重跑通过。

引入基于机器 slot 名称的前台命名互斥标记，使单个其他进程的等待对 OCR 可见，避免持模型许可的线程嵌套前台调用时发生逆序死锁；`foreground-cross-process-r1.xml` 2 passed，覆盖其他进程等待标记、进程被强杀后的 abandoned mutex 恢复及本进程嵌套。独立 Terra 审查指出多个进程排队交接时仍存在标记间隙，Windows mutex 不能证明公平；另冷启动工厂返回和首次查询执行之间会释放优先标记，后台可能再开始一张。故尚不能宣称严格跨进程或冷启动优先，也不能从互斥量恢复证明旧子进程退出或显存释放。用户选择已记录，阶段 B 和最终审查依然 PENDING。

## 2026-11-13 并发与恢复续做

前台策略已接入普通语义、地点感知和图片搜索：首次真正编码前保留前台意图；首次编码取得许可后释放，纯文本、续页和无文本过滤不登记。未知工厂/adapter 保守协调，明确 CPU 的查询不等待 GPU。定向测试 `foreground-intent-r1.xml` 40 passed、`foreground-search-affected-r1.xml` 189 passed、`foreground-search-followup-r1.xml` 71 passed、`foreground-cpu-filter-r1.xml` 2 passed；集合存在重叠，运行时各自代码版本不同，不能合称一次完整回归。独立 Terra 已检查锁顺序与早退清理，未发现新确定性 P1；多进程多个前台等待者之间的交接间隙、连续前台负载的后台公平性和未验证取消仍待处理，不能宣称严格全局优先。命名 marker 的测试先前错误终止了 Windows venv 启动器而非真正持锁子进程，`foreground-affected-r1.xml`、`foreground-leak-investigate-r1.xml` 失败均保留；改为终止测试自己创建的真实子进程后 `foreground-cross-process-r2.xml` 2 passed。`foreground-leak-one-r1` 命令曾因环境无 pytest-repeat 不支持 --count 而退出 4，无 JUnit。

OCR 心跳阻塞与应用关闭：`heartbeat-shutdown-r1.xml` 1 passed，确认合成刷新任务中真实心跳线程的写操作未结束时，应用继续保留数据根租约，释放后关闭完成；这是隔离生命周期测试，不是实机断电验收。独立双互斥原型 `active-ownership-probe-r1.xml` 1 passed，证明父进程被强杀后，存活子进程持有的独立互斥仍可阻止其他进程进入；它未接入产品，未覆盖父子授权交接、直接客户端入口、native 异步 GPU 或空闲模型驻留。独立 Terra 建议所有实际 GPU 操作全程持 active 许可，并验证绑定父进程的真实句柄；仅瞬时检查 active 不安全。缓存和 worker 可能持续占用显存，当前没有可信的 6GB 共驻预算或跨进程安全卸载机制，仍是 B 阶段核心未决项。

共享能力写入补充了倒序 provider 出现/消失、不同 runtime 身份、相同时间不同精度、recording clock 回拨以及整体健康状态的保守检查；`capability-clock-r2.xml` 4 passed、`capability-clock-r3.xml` 2 passed、`capability-clock-r4.xml` 3 passed、`capability-health-r1.xml` 5 passed、`provider-order-r2.xml` 4 passed。`capability-affected-r1.xml` 因夹具把不同 runtime 身份设为同一时刻失败，校正夹具时钟后 `capability-affected-r3.xml` 受影响集合通过；精确汇总见后续最新验证记录。同一毫秒的冲突被拒绝，时钟大幅回拨时会持续保守拒绝，自动重试/状态诊断仍未完成；旧版本 writer 和 GPU 硬件身份漂移边界未关闭。阶段 B 未验收，C/D/E 和最终复查继续 PENDING。

续做记录：provider 枚举与设备观察改按实际探测时间分别采样，较早枚举不能覆盖较新出现/消失事实；慢 CPU 探测期间 CUDA 消失的线程交错 `observation-clock-r1.xml` 3 passed。semantic worker 的缓存 provider 列表不再被当作本次完整枚举，人脸缓存结果保留原观察时间；`cross-consumer-cached-r1.xml` 2 passed。语义模型缺少可验证观察时间时拒绝资格，候选建库也使用原观察时间；`semantic-failclosed-r1.xml` 3 passed，旧 worker 对较新缺席/失败/身份事实的覆盖 `semantic-stale-r1.xml` 3 passed。Chinese-CLIP 健康检查复核两个 session 声明的 provider，Qwen 复核参数设备与浮点精度；`session-health-r1.xml` 5 passed、`session-interleave-r1.xml` 1 passed、`qwen-precision-r1.xml` 2 passed。这些是替身/状态核验，不证明实际 CUDA 推理或显存释放。此前允许旧 session 借用另一处理器更新的健康事实的实验分支已撤回，因独立复查指出这不能证明旧 session 健康；其 `semantic-compatible-r1.xml` 是已撤回方案的历史证据，不是现状通过。

扩展受影响首轮 `observation-affected-r4.xml` 338 项、35 failed / 16 skipped；失败来自旧合成适配器缺少新增观察时间。保留原 JUnit，补齐夹具的合成时间而不改变原断言后，`fixture-observation-r1.xml` 100 passed；后续 `observation-affected-r5.xml` 340 项、324 passed / 16 skipped / 0 failed，时间为 Qwen FP32 额外检查之前；其影响另由上述定向测试覆盖。完整单元与集成回归因涉及共享运行时启动，结果待记录；该范围不覆盖真实模型/设备。

附加资源边界：`_enrichment_stages()` 在建库分页时复用 face cache，但 `_ocr_stage()` 每页调用 `run_queue()` 都默认新建 OCR cache；因此即使一次 run_queue 内复用 session，跨页仍可能重复初始化。简单把 OCR cache 提升到整个建库阶段会延长其与常驻语义 worker、face cache 的同时驻留；在没有可信显存预算或安全切换方案前，不能把此改动单独当作完成生命周期管理。旧的成功 face session 若后来 provider 重检持续失败，目前不会强称清除引用已释放 VRAM；安全重建仍待设计。

## 2026-11-14 分页、取消及回归续做

OCR 缓存现限于一次 enrichment 阶段并跨分页复用；不创建模型直到实际使用。`ocr-real-pages-r2.xml` 4 passed：合成的真实分页→OCR stage→run_queue 链路中两页创建一次替身 session，同时保留每页完整权重校验（两次），未降低原有完整性检查。此前仅说明存在逐页重新创建的记录由本段更新；缓存引用寿命延长，不代表 ORT 已释放显存，也未证明与 face/Qwen 共驻安全。

多来源 GPU 忙或取消现终止整个阶段，取消在页边界、权重验签、目标读取和 CR2 恢复后均阻止后续入队，页计数只包含实际运行页；`ocr-multiscope-r2.xml` 6 passed、`ocr-cancel-boundary-r3.xml` 5 passed。CPU OCR 本次完成后检测取消，后续 CPU/GPU 不再领取；保留既有首次即取消的任务语义；`cancel-cross-device-r1.xml` 3 passed，含后续 job 排队及续做。原 `ocr-pagination-affected-r1` 因工具宿主重置而中断、无 JUnit，不推断结果；受影响集合已另启。

完整单元/集成 `full-regression-r1.xml`：3780 项、3695 passed / 52 skipped / 33 failed（退出 1）；这是历史代码运行中的快照，后续改动不能称为同一次最终全量通过。对照历史 full JUnit：23 项同名历史失败；7 项为本轮 worker 返回多余 None 字段与人脸 synthetic fixture 缺 revision，修正后 `full-fix-worker-face-r1.xml` 30 passed。其余 3 项缺模型建库断言，在载入 HEAD 原始 pipeline、使用当前其余依赖的隔离对照中仍同样失败，`default-build-baseline-r1.xml` 3 failed；原 pipeline 会先分类并入队后检查模型。不能把该混合对照夸成完整基线运行。随后 `final-incremental-r1.xml` 416 项、394 passed / 22 skipped / 0 failed；其后分页和取消代码仍有改动，相关增量回归与最终 Terra 高推理复查待完成。GTX 1660/HDD 不可用，真实显存预算、异常退出后的子进程执行权、多个进程前台公平交接与永久失效 Session 的安全卸载仍 PENDING。阶段 B–E 不得标 PASS；未提交、未推送。

## 2026-11-14 暂停交接

按用户要求暂停并提交推送；任务为 **PAUSED / IMPLEMENTATION PARTIAL / VALIDATION PENDING**，不是 A2 PASS。GPU OCR 直接 scheduler 路径新增领取前检查：无许可时先过滤 GPU 的维护和候选查询，CPU 可独立继续；手工 GPU claim 默认拒绝，受管理的 run_once 对 OCR 默认启用租约心跳和提交时钟。隔离测试 `direct-preclaim-r3.xml`、`direct-preclaim-r4.xml`、`direct-heartbeat-r1.xml`、`direct-clock-r1.xml` 均为各 3 项 0 failed，含 270 条 GPU 积压、无许可时 GPU 重试及过期租约不变、直接 CPU/GPU 租约与连续任务时钟。公开低层 jobs API 和显式内部私有标记仍属信任边界，不能声称对恶意或旧版本调用实现强隔离。

`preclaim-affected-r3.xml` 290 项、0 failures / 0 errors / 11 skipped；`ocr-pagination-affected-r3.xml` 312 项、0 failures / 0 errors / 11 skipped。两次工具会话重置，未取得终端进程退出码；上述只报告 JUnit 内容，不标记完整进程通过；两次集合、前述全量和增量均处于不同代码时点。`ruff check .`、`git diff --check` 在暂停时退出 0；此前全量 3780 项的 33 失败、52 跳过及其对照边界见上文。

独立 Terra 高推理全受影响面复查仍有未关闭项：父进程异常退出后旧 AI 子进程可能继续使用 GPU（P1）；当前命名等待标记不能保证多个进程前台交接公平；无法证明 OCR 与常驻模型 6 GB 显存共驻或永久失效 session 的安全卸载；显式 `now_ms` 若远超 catalog 时钟，OCR 领取与心跳时钟可能不一致并缩短租约，须统一时钟域或补防护。真实 GTX 1660 + HDD 不可用，阶段 C 进度诊断、D 关闭重试、E 最终回归与独立最终复查均未完成；不要把本次提交当成发布或验收。

## 2026-11-14 B 阶段架构收敛与必要修复

**状态：B 代码面已实现并通过自动化和 Windows 进程级验证；任务不是最终 ACCEPTED。** 当前环境没有 Qwen、Chinese-CLIP、OCR 或人脸权重，无法执行真实模型推理和显存预算；目标 GTX 1660 6 GB + 机械硬盘仍不可用，独立 DS-H 全受影响面审查也尚未执行。因此不得把本段写成正式发布或目标硬件 PASS。

### 根因分类

- **原代码缺陷：** `ApiContext` 在 encoder 关闭前清空引用；`WorkerQueryEncoder` 在 `client.shutdown()` 前设置 closed；关闭异常后第二次调用直接返回，三者共同造成失败后不可重试。独立 `WorkerQueryEncoder`/模型选择 CLI 调用没有自行获取 GPU 许可，可绕过 API `GatedEncoder`。子进程退出后父端只靠队列长超时发现，且 spawn 子进程未绑定父进程生命周期。OCR 领取后的提交若由任意调用方传入与 Catalog 不同的 `now_ms`，会使用不同时间域。
- **架构设计缺口：** 原实现的父进程命名 Mutex 只协调执行，没有表达“父进程拥有子进程”的生命周期；Qwen 子进程驻留与 OCR/人脸父进程 Session 驻留也未被区分。仅用同一把执行锁不能证明获取锁时另一模型已经归还 VRAM。
- **原本有效的机制：** OCR GPU 已在领取前取得后台槽，CPU OCR 不进入 GPU 槽；GPU OCR 按张检查前台；Scheduler 直接 `run_once()` 在无槽时不领取 GPU；任务 token/revision 提交检查、30 秒 SQLite 等待恢复和 LanceDB outbox 语义保留。

### 最终资源所有权

| 执行路径 | 实际 GPU 执行者 | 许可持有者 | 领取/初始化规则 | 退出规则 |
|---|---|---|---|---|
| Qwen / Chinese-CLIP Worker | spawn 子进程 | API 父进程持有 `BACKGROUND_MODEL_SLOT`；`WorkerQueryEncoder` 的直接入口会自行取得该槽，已持有时可重入 | 父进程取得槽后启动/查询子进程；子进程在 Job Object 绑定和启动门放行前不加载模型 | 正常关闭先停子进程再释放槽；父进程被强杀时 Job Object 关闭会终止子进程，OS 再释放许可 |
| OCR GPU / CPU | OCR 线程 | GPU 使用后台槽；CPU 从不取得 GPU 槽 | GPU 在探测、Session 创建和单图推理前取得槽；任务在槽内才领取 | 每个推理 `finally` 释放；任务间重新检查前台和取消 |
| 人脸 | Face 执行线程 | 同一后台槽，保留 RLock 可重入 | 人脸阶段在入队/探测前取得槽；每张之间检查前台 | 当前推理完成后让出，缓存引用在模型族边界释放 |
| 候选建库 | candidate 线程及其单 Worker | 后台槽 + `ModelResourceGate` exclusive | 保持既有规则：先停止旧查询 Worker，再建立唯一候选 Worker；不在锁内等待 SQLite | 候选结束或暂停时关闭候选 Worker，逆序释放 gate 与后台槽 |
| 后台刷新语义 | refresh 线程及查询 Worker | 后台槽；模型族结束后回收 query encoder | 先用现有工厂取得固定 space 的 Worker；前台等待时在安全边界停止后续图片 | 在释放后台槽之前调用 encoder 回收，再进入人脸/OCR |

**显存策略：** 不宣称多模型安全共驻，也不把执行锁释放当作显存已经释放。刷新在语义阶段结束时终止在位 Qwen Worker；人脸阶段结束先清空其 Session 所有权，再启动 OCR GPU。OCR 不因该切换策略改走 CPU；只有原有显式 CPU 路径或既有故障策略才会使用 CPU。ONNX Runtime/驱动是否在引用清空后立即归还全部 VRAM 仍需要目标硬件测量。

### 锁顺序与前台

资源顺序固定为：前台意图/等待标记 → `BACKGROUND_MODEL_SLOT` → `ModelResourceGate` → adapter/Worker 请求串行锁。候选建库保留后台槽 → exclusive gate。等待资源时不持有 SQLite 写事务；提交时重新验证 lease token、输入 revision 和当前时钟。释放顺序相反，所有许可均由上下文/finally 释放。

OCR 已开始的单张推理允许完成；观察到前台等待后不领取下一张。人脸和后台语义同样在单图/单任务安全边界检查前台，不在一次不可中断推理中途强制回滚。语义 Worker 的 adapter 串行锁在 GPU 许可之后获取，避免直接 CLI 入口形成反向锁序。单应用进程内多个串行前台请求结束后，OCR 能在请求间隙继续；测试使用事件控制，未依赖 sleep 判定互斥通过。

### 统一任务时钟

`Catalog.lease_now_ms()` 以持久化协议所需的墙上毫秒为值，但在单个进程内不倒退；系统时钟回拨时冻结到已观察的高水位，恢复后继续前进。生产 Scheduler 全部使用该 clock 形式；OCR 的领取、心跳、执行结束重验和写事务提交都使用同一来源。GPU 等待、worker 退出等待等本地超时继续使用 `time.monotonic()`。调用方误传未来 `now_ms` 不再能人为缩短 OCR 租约。

### Worker 与关闭

- AI Worker 在 `Process.start()` 后立即加入带 `KILL_ON_JOB_CLOSE` 的 Windows Job Object；子进程在启动门打开前只检查父进程存活，不加载模型。父进程正常退出、异常退出或被强杀时，子进程均不能留在父进程许可之外继续推理。
- Worker 回复等待按短间隔检查进程存活；子进程异常退出立即形成结构化 `WorkerExited`，不再等待完整 900 秒冷启动超时。
- `AiWorkerClient.shutdown()` 只有在 child 已退出、请求/回复队列、Job 句柄、进程句柄和诊断临时文件均处理成功后才标记完成；失败保留对应引用并抛 `WorkerShutdownError`，下一次调用重试。
- `WorkerQueryEncoder.close()` 失败后保持 `_closing`，阻止半关闭 Worker 接收新任务，但保留 client 供再次关闭。
- `ApiContext` 在 encoder 真正关闭前不清引用；失败后 `close()` 可重试，已成功资源不重复释放。并发关闭由 close/shutdown 串行锁保护，工作线程未结束时继续保留数据根租约。

### 修改文件

| 文件 | 修改原因 |
|---|---|
| `src/photo_search/runtime/process_ownership.py` | 新增 Windows Job Object、父进程存活检查 |
| `src/photo_search/workers/client.py` | 启动门、Job Object、异常退出探测、可重试完整清理 |
| `src/photo_search/workers/ai_worker.py` | 父进程绑定完成后才加载模型 |
| `src/photo_search/workers/query_encoder.py` | 直接调用许可下限、关闭重试与半关闭拒绝 |
| `src/photo_search/api/context.py` | encoder 引用保留、关闭重试、后台回收回调与并发生命周期 |
| `src/photo_search/api/refresher.py`, `api/face_detail_runner.py` | 在模型族切换前回收语义 Worker |
| `src/photo_search/refresh/pipeline.py` | 后台语义逐任务让出、模型族边界与语义 Worker 回收 |
| `src/photo_search/face/service.py`, `face/engine.py` | 人脸逐任务让出、统一租约时钟、Session 所有权释放 |
| `src/photo_search/catalog/db.py`, `scheduler/engine.py`, `ocr/lease_heartbeat.py`, `ocr/service.py` | 非倒退租约时钟和统一 claim/heartbeat/submit clock |
| `src/photo_search/indexing/semantic.py` | 后台建库可选的逐任务前台让出 |
| `src/photo_search/models/embedding/cli.py`, `taken_time/service.py` | 所有生产 Scheduler 使用统一 lease clock |

新增/完善了真实 spawn 子进程测试、Job Object 强制终止测试、子进程异常退出、串行前台后 OCR 恢复、时钟回拨/未来时间、CPU OCR 独立性、直接 Scheduler 许可、关闭失败重试和模型族 Session 所有权测试。没有迁移、依赖、模型身份、向量维度、OCR Processor ID 或确认协议变化。

### 本轮自动验证

- HEAD 基线定向集合：`test-results/p4-build-stability-a2/b-head-baseline.xml`，202 项，196 passed / 6 skipped / 0 failed / 0 errors，终端退出码 0。
- 最终受影响面：`test-results/p4-build-stability-a2/affected-final-r1.xml`，382 项，376 passed / 6 skipped / 0 failed / 0 errors，终端退出码 0。
- 专项关闭/进程集合：`final-focused-r1.xml`，终端退出码 0；真实 Windows 父进程强杀后子进程退出和 GPU Mutex 释放、子进程异常退出、正常关闭均通过。
- 全量首轮：`full-after-b-r1.xml`，3826 项，3747 passed / 27 failed / 52 skipped / 0 errors，终端退出码 1。历史 `full-regression-r1.xml` 33 个失败中，26 个同名失败仍存在；5 个 Worker channel 失败和 2 个 person-detail 失败本轮不再出现，另新增 1 个 `test_p4_02c...two_maintainers...` 并发交错断言。该新增项单独复跑通过（`contended-r1.xml`），判定为时间敏感的时间窗假设，不是产品行为失败。
- 终轮全量：`full-final-b-r2.xml`，3827 项，3749 passed / 26 failed / 52 skipped / 0 errors，终端退出码 1。26 个失败与历史 JUnit 全部同名，最终没有新增失败；历史 7 个失败已不再出现。终轮新增的 1 项是本轮新增的正常关闭测试，不是重试集合。
- 52 个跳过均保留原有环境条件：5 项 Chinese-CLIP/P0-03 权重、23 项 buffalo_l 人脸权重、19 项 PP-OCRv4 权重、2 项 Windows symlink 权限、1 项 GPU opt-in、1 项未配置的公开中文地名资源包、1 项 collection 级跳过。没有为本轮通过而新增 skip、xfail 或削弱断言。
- `ruff check .`、`compileall`、`git diff --check` 均退出 0。

### 未关闭项与结论

- 目标 GTX 1660 6 GB + HDD 不存在；当前只观察到 RTX 4060 Laptop 8188 MiB。模型目录也没有可执行真实 Qwen/CLIP/OCR/face 权重。真实 VRAM 峰值、模型切换后的实际归还时间、OOM 余量和 HDD 长时恢复仍为 **PENDING**，不能由合成测试替代。
- 独立 DS-H 全受影响面工程审查尚未执行；按仓库策略不能由实现者自检冒充独立审查。阶段 C 建库进度诊断、D 的其余非必要清理、E 最终发布验收均未开始。
- 因此 **B 的代码面已收敛，但不是最终完整 ACCEPTED**：自动化受影响面和 Windows 进程所有权通过，目标硬件/真实显存/独立审查未完成。下一阶段 C、D、E 不应在用户接受本结果并决定真实硬件验收前宣称已完成或正式开始。

## 2026-11-14 独立审查阻塞项修复

上一轮独立工程审查把 B 判为 FAIL，指出三项 P1：semantic 无待办时未回收驻留 Qwen、Job Object 分配失败泄漏句柄、候选关闭失败丢失 Worker 所有权。另指出 Face Session 释放 fail-open、`demo-search` 直接 GPU 入口和真实显存证据等风险。本轮只读审查后按以下方式修复；独立 DS-H 子代理复核随后给出 **REVIEW PASS，无未关闭 P1**。

### 模型族切换

- `refresh_library()` 在准备进入 enrichment 前无条件调用幂等的 `encoder_release`，覆盖 semantic 无待办、space 不匹配、manifest 缺失等提前返回路径。
- 释放失败时 semantic 状态改为 `FAILED / MODEL_RESOURCE_RELEASE_FAILED`，停止 face/OCR，不启动第二模型族。
- `FaceEngineCache.release_sessions()` 不再先清空全部缓存：只有可选 close hook 成功后才移除对应 engine；失败项保留供重试。
- 后台 enrichment 的 face Session 释放失败会停止后续 OCR；`FaceDetailRunner` 保留失败 cache，并在后续 close 中重试。
- 新增“已驻留语义 Worker + 无语义待办 + 进入 enrichment”“semantic 释放失败不进入 face/OCR”“face 释放失败不进入 OCR”的隔离测试。

### Windows 子进程所有权

- `WindowsJobObject.assign()` 在 `AssignProcessToJobObject` 失败时关闭 Job；Job close 只有在 `CloseHandle` 成功后才清空内部句柄，失败可重试。
- `AiWorkerClient.start()` 将 `Process.start()` 后的一切失败纳入统一清理：terminate/kill child、关闭 Process、Job、请求/响应队列和临时 stderr 文件。
- 启动清理失败时保持启动门关闭，避免未归属 child 继续加载模型；已关闭的 Process 不会再被 `is_alive()` 二次访问。
- 新增 AssignProcessToJobObject 失败、首次 CloseHandle 失败后 shutdown 重试、正常关闭和父进程真实强杀测试。

### 候选与寿命

- `CandidateBuildManager` 在 encoder close 失败时保存 `_unreleased_encoder`，拒绝启动新 build；`close()` 和后续 `_launch()` 会重试释放。
- `ApiContext` 在 candidate close 返回 false 时保留 manager；再次 `close()` 会先重试 candidate，再继续 catalog/encoder 收尾。延迟 worker 清理也把 `close() is False` 视为失败并继续保留数据根租约。
- `demo-search` 的 raw `AiWorkerClient` 入口现在先取得共享后台 GPU 槽。

### 验证结果

- 定向修复集 `review-fixes-r6.xml`：139 passed / 0 skipped / 0 failed / 0 errors，终端退出码 0；DS-H 指出的 face 逐阶段状态和 semantic 原失败原因保留两个 P2 收口后，`review-fixes-r8.xml` 再跑 53 passed。
- 最终受影响面 `review-affected-r4.xml`：398 项，392 passed / 6 skipped / 0 failed / 0 errors，终端退出码 0。
- 全量 `review-full-r1.xml`：3837 项，3759 passed / 26 failed / 52 skipped / 0 errors，终端退出码 1。26 项与历史 `full-regression-r1.xml` 同名且首行错误一致，无新增失败；历史 7 项本轮已不再出现。
- `ruff check .`、`compileall`、`git diff --check` 退出 0。

独立 DS-H 复核记录：`test-results/p4-build-stability-a2/ds-h-recheck-review-20261114.md`。复核确认上述三项 P1、Face Session fail-closed 和 `demo-search` GPU 槽均正确；其提出的 face 逐阶段状态未回写、semantic 原失败原因可被覆盖两项 P2 已在本轮修复并由最终受影响面覆盖。剩余 P2 为 D 阶段其他 worker 关闭重试、时钟跨重启高水位及真实 VRAM/目标硬件。

### 仍未关闭

- GTX 1660 6GB + 机械硬盘、真实 Qwen/OCR/face 权重、实际峰值 VRAM、驱动归还时间和长时恢复未验证。
- ONNX/PyTorch Session 引用清空仍不等于可观测的显存归还；6GB 安全预算只能由目标硬件证明。
- 系统时钟大幅前跳后回拨及进程重启组合仍可能保留未来租约；单实例高水位不跨重启保存。
- 更一般的 refresher/face/runner 等 worker close 引用保留仍属于 D 阶段收尾；本轮只闭合与 B 直接相关的 encoder/candidate/face-session 路径。
