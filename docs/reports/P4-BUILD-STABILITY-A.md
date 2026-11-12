# P4-BUILD-STABILITY-A 执行记录

2026-11-12；基线 `f9d3e40`，工作区起始干净。状态：**IMPLEMENTATION PARTIAL / VALIDATION PENDING**，不得视为整卡通过。未提交、未推送，未访问真实图库。

## 已核实与局部修改

- `LibraryRefresher.close()` 原本未将停止请求传给流水线；新增事件并传递 `cancel_check`。流水线在扫描来源之间、扫描结束、投影和语义阶段后、enrichment 后检查取消，以既有 `FAILED` 状态和 `CANCELLED` 错误记录中止，避免宣称 COMPLETE。底层仍有未接入取消的阶段，不能声称及时退出。
- 扫描哈希原先分块读取期间无取消或心跳。现于分块边界检查取消，租约剩余不足半程时通过短事务续租；读取本身不持写事务。文件绑定与最终确认取得写锁后重新取时间，拒绝等待期间过期的租约。单次系统调用阻塞超过租约仍会失去资格，由旧 worker 停止提交并由后续扫描恢复；不能强行中断系统 I/O。
- 候选管理器原先无界 join；现有界请求停止，存活时保留 owner 和任务状态。应用自有与启动器转交租约均在工作线程和清理完成后释放，关闭失败或事件循环不可用时保守保留租约。
- OCR `run_queue()` 的能力探测会创建 GPU 模型会话；执行器按设备执行 OCR。已支持在任务之间安全停止，未领取任务保持可恢复；GPU 会话加载和推理尚未接入共享槽，不能宣称 GPU 协调完成。此前人脸与语义槽证据不能代替 OCR 并发测试。
- 上述是代码观察，未证实现场死锁；资源竞争、锁饥饿和慢 I/O 实际发生情况均需注入或现场证据。尚未完成其他指定文件、事务和诊断面的完整审查。

## 验证与待办

使用隔离测试环境，执行 `.venv\Scripts\python.exe -m pytest -q tests/unit/test_api_refresher_queue.py tests/integration/test_p4_02y_library_refresh.py --junitxml=test-results/p4-build-stability-a/refresher-final.xml --basetemp=.pytest-tmp/p4-build-stability-refresher-final`：退出 0，45 passed。新增测试验证关闭信号传递和扫描前取消不冒充完成；这些不证明真实运行中的全部阶段或租约安全。`python -m ruff check .`、`python -m compileall -q src/photo_search tests` 退出 0；`git diff --check` 首次因追加文本换行格式失败，统一换行后退出 0。

**仍必需：** GPU OCR 协调与 CPU 独立性；候选和刷新线程关闭及租约生命周期；真实慢盘与跨进程慢读取接管；完整阶段诊断；30 秒 SQLite 持锁、锁顺序、取消、扫描恢复、LanceDB 故障及全部任务卡定向测试；完整受影响 B/D 验证；独立 DS-H 全受影响面审查。无依赖或迁移变化；目前尚不能界定完整最终影响及是否触发全量回归。任务卡要求 GTX 1660 + 机械硬盘实机；本机显卡查询显示 RTX 4060 Laptop，未完成目标设备实测，不能以合成测试替代。未测 30 万容量、前台争用或发行包。

## 2026-11-12 续做

- 哈希增加可选检查点并保留 SHA 计算；各既有测试替身转发回调。扫描取消后保留 RUNNING 及租约；期满后复用同一任务恢复。刷新在 enrichment 阶段之间也检查取消。
- 隔离测试注入慢块：验证心跳限频（六个以上检查点、三次续租）、读取时不持事务、块间取消后恢复、单次阻塞越过租期拒绝绑定并恢复，以及写事务获取期间过期拒绝旧 owner。最后一项模拟事务获得期间时钟前进，并非真实跨进程 I/O 停顿。
- 最终扫描相关四个文件：`python -m pytest -q tests/integration/test_catalog_scan.py tests/integration/test_catalog_scan_streaming.py tests/integration/test_catalog_identity_lifecycle.py tests/integration/test_p4_02y_fast_scan.py --junitxml=test-results/p4-build-stability-a/hash-affected-final-v2.xml --basetemp=.pytest-tmp/p4-build-hash-final-v2`，退出 0，74 passed；刷新相关两文件 `--junitxml=test-results/p4-build-stability-a/refresh-final-v2.xml --basetemp=.pytest-tmp/p4-build-refresh-final-v2`，退出 0，46 passed。两次为分开运行，不称一次全套。Ruff、compileall 和 diff-check 退出 0。
- 任务仍为 IMPLEMENTATION PARTIAL / VALIDATION PENDING：GPU OCR、关闭与数据目录租约保护、阶段诊断、30 秒外部持锁及其余矩阵、独立 DS-H 审查、GTX 1660 + 机械硬盘必测均未完成。此次变更尚未做完整受影响面独立审查；未提交、未推送。

## 2026-11-12 关闭、OCR 停止与 30 秒持锁续做

- 候选线程 `close` 限时并保留活线程状态；context 等待刷新、候选、扫描、人物、清理及回忆线程和智能合集 future，再于所属事件循环完成共享资源清理，应用自有/启动器转交租约最后释放。事件循环不可用或清理失败时保守保持租约。扫描、人物 worker 在连接创建线程关闭各自 SQLite 连接。智能合集与清理 runner 的 close 改为有界等待；智能合集 future 包含心跳线程退出，避免心跳仍写入时释放租约。扫描 runner 将停止信号传至扫描器，扫描中断按 CANCELLED 报告。
- OCR 刷新在一个任务完成后、下个任务领取前检查停止请求；未领取任务保持 QUEUED，复跑完成，避免把未完成工作置为不可恢复的 CANCELLED。尚不能在模型加载或单次推理内部即时取消。
- 临时 SQLite 外部连接真实持有写锁 30 秒，释放后原扫描 job 可显式重试、成功且不重复；`test-results/p4-build-stability-a/sqlite-30s-v2.xml` 1 passed，30.374 秒。不能由此宣称自动恢复或前台时延达标。写锁后的租约时间核验扩至枚举、重排、阻断和失败事务，过期重排定向拒绝通过。
- OCR 停止改动前的生命周期/扫描集合 `test-results/p4-build-stability-a/lifecycle-r4.xml` 200 passed（后续改动仅 OCR service、刷新流水线与其测试；刷新受影响面由下述 OCR/刷新集合复验）；其他人物、清理、智能合集消费者 `lifecycle-consumers.xml` 76 passed；OCR 与刷新 `ocr-affected.xml` 101 passed、11 skipped（缺少测试所需资源）；各自退出 0，覆盖有重叠，不累计成无重复的总数。之前 `lifecycle-r1.xml` 147 项中 1 项失败：智能合集关闭未等待即时发布；已恢复最长 5 秒等待，`lifecycle-fix.xml` 与 r4 对应节点通过。`shutdown-first.xml` 的失败为测试缺少 `time` 导入，`shutdown-second.xml`、`shutdown-transfer.xml` 与后续相关集合已通过。Ruff、compileall 和 diff-check 退出 0。
- **仍未完成，不能 PASS：** GPU OCR 的探测与执行共享能力快照，简单在已领取 GPU 任务后等待锁会使租约失效；CPU 单独执行、GPU 任务不被误标 BLOCKED/消耗重试、会话加载/显存释放和锁顺序尚需完整设计与验证。轻量阶段/卡顿诊断尚缺；全部指定故障矩阵及前台争用证据尚缺。Python ThreadPoolExecutor 的进程退出行为及原事件循环已经停止时的最终清理仍需验证；现实现以保留租约而非冒充清理完成处理后者。独立 DS-H 完整受影响面审查及 GTX 1660 + 机械硬盘实机均未完成；用户确认当前无目标实机，不能以 RTX 4060 或模拟测试替代。未提交、未推送。
