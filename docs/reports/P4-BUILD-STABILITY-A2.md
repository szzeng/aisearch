# P4-BUILD-STABILITY-A2 — 阶段 A 审查

日期 2026-11-12。基线 `6d4d76271808c2136c7222f6160a25f94948bfb5`，开工工作区干净；A 卡的修改已按后续用户授权提交、推送，旧状态文档的“未提交”是历史文字。本卡 **IMPLEMENTATION PARTIAL / VALIDATION PENDING**，GPU 协调、诊断与关闭修复尚未完成，不代表 A 或 A2 通过。

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
