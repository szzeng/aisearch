# P4-02U — 独立 DS-H 容量证据专项审查（新会话，2026-09-27）

**性质**：只读独立审查。本轮未改任何产品文件、未改被测工具、未改夹具；新增产物只有本报告，加上
任务卡 [P4-02U](../tasks/P4-02U.md) 的状态行、`docs/development-status.md` 与下一张卡 [P4-02V](../tasks/P4-02V.md) 的交接行。
**审查方式**：V3.1 §2.4「容量证据专项审查（独立 DS-H）」——适用条件为「仅新增测量工具/夹具/报告、不改产品行为」，
本卡成立（见 §1）。
**一句话结论**：**归因方向成立，不被本审查推翻**——写锁持有者就是产品 drain 自己的 `claim`/`apply` 写事务，
WAL checkpoint、进程内未计时写路径、外部写者都被独立复算支持地排除。但**这份容量证据的交付不合格、需修订后复审**：
报告表格在「同一张表里混用运行中日志快照与最终 JSON」「6 000 档进程臂与首轮线程臂的原始证据已被同名文件覆盖」
「四臂并非同夹具状态、未交错采样却写着『同夹具、同一会话』」「写窗口定义把 COMMIT 排除在外，使『批间空隙 1.58 ms』
被读成可用空隙」「checkpoint 只有聚合值没有卡面要求的轨迹」这几处，属于可复核性缺口。裁决：
`REVIEW FAIL（需修订后复审；归因方向与 P4-02V 立项不变）`。**2026-09-27 追加：R2 修订后已由 [R2 复审报告](P4-02U-dsh-capacity-evidence-rereview.md) 记 `REVIEW PASS`，F1～F8 全部闭合；本报告 §1～§8 保留为 R1 历史结论（见 §9）。**

## 1. 审查方式与适用条件（V3.1 §2.4）

| 条件 | 本轮核对的实际证据 | 判定 |
|---|---|---|
| 仅新增测量工具/报告，产品零改动 | `git status --short` 只有 `docs/development-status.md`(M) 与 `docs/reports/P4-02U-lock-attribution.md`、`docs/tasks/P4-02U.md`、`docs/tasks/P4-02V.md`、`tools/diagnostics/p4_02u_lock_attribution.py`(均新增)；`git diff --stat -- src tests uv.lock pyproject.toml` 为空 | 成立 |
| 未改 schema/迁移/依赖/§12.2/ADR | 同上 + `uv.lock` 无 diff；`SUPPORTED_SCHEMA_VERSION` 未动（本轮未新增迁移文件） | 成立 |
| 走真实产品入口 | 进程臂 = `python -m photo_search.indexing.cli drain`；线程臂 = `maintenance.drain_semantic_outbox`；前台 = `search.service.search_assets`；夹具事件按 `bench.publish_embedding` 的真实 artifact/outbox 协议注入 | 成立（编码器为既有替身口径，报告 §5 已披露） |
| D 级注入/故障与真实进度重叠 | 各臂 JSON 有 `before` 计数、`overlap_ms`、drain `applied=30000`/`batches=150`、`write_phases` | 成立 |
| 夹具隔离、未扫真实图库 | 数据根 = `tmp/p402j-contention-close/data`（P4-02J 丢弃式副本）；参考夹具 `tmp/p402f-阶段0/scale-300000-wave/data/catalog` 的 `catalog.db` mtime = 2026-09-25 02:36、`-shm`/`-wal` = 2026-09-27 22:09，**均早于本轮 23:03–23:16 的运行窗口** | 成立 |

审查基线：`HEAD = 93a4a52`（master，工作树含 P4-02U 未提交产物，未 push）。
被审产物 SHA256（前 16 位）：`tools/diagnostics/p4_02u_lock_attribution.py` = `bb28d03fbdea615b`（25 480 B）、
`docs/reports/P4-02U-lock-attribution.md` = `eb945812c17558d9`（8 217 B）、`docs/tasks/P4-02U.md` = `98b4da25ee67ca81`。

**升级判断**：本轮发现的是证据/报告缺口，不是「测量绕开产品入口」或「结果与契约矛盾」，因此**不升级**为完整受影响面
工程主审；也不触发 V3.1 §2.3 的 Sol 阶段（无 ADR/数据安全未决项）。若修订过程引入任何 `src/**` 改动（例如在修复卡里
落退避/让步），则按 §2.3/§11 另判，不在本报告授权范围内。

## 2. 覆盖范围（我实际读到的完整测量 diff 与证据）

- `tools/diagnostics/p4_02u_lock_attribution.py` 全文（651 行：`Trace` 运行时插桩、`LockTimeline`、`CheckpointSampler`、
  四臂、`inject`）。
- `docs/tasks/P4-02U.md`、`docs/tasks/P4-02V.md`、`docs/development-status.md` 相关段、`docs/reports/P4-02U-lock-attribution.md` 全文。
- 直接调用链：`catalog/db.py::Catalog.write`（`BEGIN IMMEDIATE` + busy 超时→`CATALOG_UNREADABLE`）、
  `indexing/store.py::IndexCoordinator._timed_write/_record_write_sample/process_next`、`search/documents.py::catch_up`
  （`CATCH_UP_BATCHES=4`、`DRAIN_BATCH_SIZE=200`）、`search/service.py::search_assets`（`build_match` ⇒ `catch_up`）、
  `indexing/maintenance.drain_semantic_outbox`、`indexing/cli drain`。
- 机器可读证据 `test-results/p4-02u-{inject,arm-control,arm-no-drain,arm-drain-thread,arm-drain-process}.json`、
  运行日志 `tmp/p402u/*.log`、线程转储 `tmp/p402u/thread-dumps-{thread,process}.txt`。
- 我另写、只落在 `tmp/` 的独立复算/校准脚本：`tmp/p402u/review_analysis.py`（从 JSON 重算）、
  `tmp/p402u/review_gap_probe.py`、`tmp/p402u/review_gil_probe.py`、`tmp/p402u/review_gil_probe2.py`（机制校准，见 §5/F4）。

## 3. 独立复算表（主张 → 我从落盘 JSON 重算 → 判定）

| 报告主张 | 我的独立复算值 | 判定 |
|---|---|---|
| 持锁者 = drain 的 `claim`/`apply` 写事务 | 线程臂进程内写者普查：`write_windows` 共 310 = drain 线程 301（151 claim + 150 apply，与 `write_phases` 一致）+ 前台线程 4（成功搜索的 `catch_up` 4 批，t≈9–56 ms，各约 8–16 ms）+ 5 个被拒窗口（`rejected_ms`，无 exit）。**不存在第三个写者** | 支持 |
| 301 写窗口、持锁合计 56.2 s / 跨度 56.8 s ⇒ 占空比 99.0% | 我按 `phase_windows` 复算 raw sum = 56 272.811 ms、span = 56 777.128 ms ⇒ **99.1%**；按 `write_windows`（含前台 4 个）raw = 56 316.934 ms、span = 57 825.522 ms ⇒ 97.4% | 支持（报告口径 = phase） |
| 窗口间隙 p50 1.58 / p95 3.47 / max 18.3 ms | 用「窗口起点 = `acquire_ms`、终点 = `end_ms`」定义复算：p50 **1.579** / p95 **3.475** / max **18.279** ms（若起点用 `enter_ms` 则为 1.530/2.986/9.662） | 数值可复现，但**定义未写明**（见 F2/F4） |
| 拒绝窗口被 drain 写窗口覆盖 99%，引擎写窗口只覆盖 39% | 复算 5 个被拒搜索窗口：`write_windows` 99.0–99.1%、`phase_windows` 99.0–99.1%、`engine_windows` 38.6–39.7% | 支持 |
| 进程臂最长连续不可用 6 295.8 ms（另有 5 901 / 5 497 / 4 996） | 用「连续 blocked 采样的 wall span（首样本 `t_ms`→末样本 `t_ms`）」定义复算：**6 295.8 / 5 901.5 / 5 497.1 / 4 996.0** ms（若改用 `probe_ms` 求和则为 5 797.6 / 5 455.0 / 5 088.2 / 4 627.3） | 数值可复现，但**定义未写明** |
| checkpoint 被排除 | 线程臂 230 采样：max **62.206 ms**、p95 5.67 ms、`busy` 求和 **0**、`log_frames` ≤ **983**、WAL ≤ **4 247 752 B** | 支持 |
| 不是未计时写路径 | 独立写者普查（上）比报告所用「301 == `write_phases`」更强：报告那一步是同一计数器自证，真正证据是所有 `Catalog.write` 窗口都能归到两个线程 | 支持（理由需换） |
| 无 drain 对照 0/4 被拒 | 复算：4 次成功（5 709.3 / 4 823.0 / 5 184.2 / 4 898.3 ms），**0.0% blocked 采样（2 328 探针）** | 支持（口径需补，见 F8） |
| 线程臂 5/6 被拒、进程臂 4/6 被拒 | 线程臂：1 ok（9 620.9 ms）+ 5 拒（5 475.8–5 527.9 ms）= **5/6**；进程臂：ok 12 227.1 / 拒 5 462.8 / 拒 5 484.9 / 拒 5 474.2 / ok 17 957.6 / 拒 5 485.3 = **4/6** | 支持 |
| 被拒原文 | 5 条 `CatalogError: CATALOG_UNREADABLE: cannot start a catalog write transaction: database is locked` | 支持 |
| 正向对照可信 | 控制臂：第三方持 `BEGIN IMMEDIATE` 6 000.861 ms，1/1 被拒（5 486.1 ms），最长连续不可用 5 845.6 ms（wall span） | 支持 |
| 各臂夹具规模 | **报告未写**：控制臂/无 drain 臂 = 366 000 资产、队列 19 200；线程臂 = 396 000、队列 36 400；进程臂（30 000）= 426 000、队列 65 600 | **缺口（F3）** |
| 6 000 档进程臂 3 809 探针 / 142 blocked、最长 3 242.6 ms、单条 apply 持锁 3 150.7 ms | 日志 `tmp/p402u/arm-drain-process.log` 只留下探针计数（3 809/142）；同名 JSON `p4-02u-arm-drain-process.json` 已被 30 000 档覆盖 | **不可回溯（F1）** |
| 线程臂 457 探针 / 377 blocked；控制臂 93 探针 / 57 blocked | 最终 JSON 分别是 **713/591** 与 **99/62**；报告值是 `run_searches` 结束时打印的**运行中快照**（日志可见） | **基准不一致（F2）** |

## 4. 已成立的结论（本审查不改方向、也不改写 P4-02J）

1. 被拒的持锁者 = drain 自己的 `claim`/`apply`（产品已计时路径）；不是第三种未知写入，不是 checkpoint，不是外部进程。
2. checkpoint 排除成立：体量（WAL ≤ 4.25 MB、≤ 983 帧）与耗时（≤ 62.2 ms、`busy=0`）都不足以解释 5–6.3 s 的连续不可用。
3. 反例在 30 000 档可重复出现（线程 5/6、进程 4/6，无 drain 0/4），且被拒与 `CATALOG_UNREADABLE` 的映射由正向对照独立验证。
4. 卡片把 [P4-02J §8.3 建议 3](../reports/P4-02J-contention-closure.md) 作为唯一后续的依据准确；P4-02J §5「归因未定」中排除的
   「单条长事务」结论不被本卡推翻（补充的是短事务序列的连续性）。
5. 产品零改动、夹具隔离、未扫真实图库均成立（§1）。夹具两侧 mtime 可证参考夹具未被本轮触碰。

## 5. 发现（按严重度）

### F1（阻塞）6 000 档进程臂与首轮线程臂的原始证据已丢失，且首轮线程臂结果未披露

工具对每个模式固定写 `test-results/p4-02u-<mode>.json`，同模式的多次运行互相覆盖：`p4-02u-arm-drain-process.json`
现存的是 30 000 档（23:16），报告表格里的 6 000 档那一行（3 809/142、最长 3 242.6 ms、`write_hold_ms` p50 13.7 /
p95 387.7 / max **3 150.7 ms**）在 `test-results/` 里**已无对应文件**，日志只保留了探针计数。同一问题下，
`tmp/p402u/arm-drain-thread.log`（23:09，第一轮线程臂）是 **0/6 被拒、6 000 探针 / 0 blocked**，与最终 5/6 的结论相反，
既未在报告中披露、JSON 也被第二轮覆盖。要求：每次运行显式 `--output`（或按档位命名）留存 JSON；重跑这两臂并保留证据，
或在报告中删除/显式标注「不可回溯」的数字，并把「同配置是否存在 0/6 的形态、其参数与夹具状态为何」写清楚。

### F2（阻塞）同一张表混用「运行中日志快照」与「最终 JSON」，且复算定义未写明

线程臂与控制臂的探针计数取自运行中快照（457/377、93/57），进程臂取自最终 JSON（1 039/756）。同时 §3 表格引用的
「间隙 p50/p95/max」「最长连续不可用」都可以由最终 JSON 复现，但分别需要「窗口起点用 `acquire_ms`」与「连续 blocked 采样的
wall span」这两个未写明的定义（见 §3）。要求：统一到最终 JSON 口径、写明每个统计量的定义与去极值规则，并在报告里给出
本轮我用到的复算方法（`tmp/p402u/review_analysis.py` 等价物）或把它作为 `tools/diagnostics/` 的一部分入库。

### F3（材料）四臂不是同夹具状态、未交错采样，表头却写「同夹具、同一会话」

实际顺序（文件 mtime + 各 JSON `before`）：`inject(6000)` → 控制臂（366 000）→ 无 drain 臂（366 000）→ 进程臂 6 000
（366 000）→ `inject(30000)` → 线程臂（396 000）→ `inject(30000)` → 进程臂 30 000（426 000）；四臂是**四个独立进程**，
夹具资产数逐臂 +30 000、队列深度从 19 200 涨到 65 600，没有任何交错采样。这既偏离卡面 §3.4「同夹具、同会话、交错采样」，
也使「无 drain 对照」与「drain 全速反例臂」不在同一数据形状上（卡面把 (d) 定义为反例臂、(a)(b)(c) 为对照）。
要求：要么补一轮真正的交错对照（同状态、A/B 交替），要么在报告里如实列出各臂的资产数/队列深度/进程形态，删掉「同夹具」
这类措辞，并说明为什么这些差异不影响归因（对照的是「drain 在场/不在场」，不是「规模效应」）。

### F4（材料）写窗口定义把 COMMIT 排除在外，「批间空隙 1.58 ms」不是可用空闲窗口

工具 `traced_timed` 在 `yield` 结束时记录 `end_ms`，而 `Catalog.write` 的 COMMIT 发生在这个上下文退出之后
（产品自己的 `_record_write_sample` 反而把 COMMIT 计入 `hold_ms`）。因此报告的「窗口间隙 = 后一窗口起点 − 前一窗口终点」
≈ 前一事务的 COMMIT 时间 + 真正空闲时间。我的独立微校准（`tmp/p402u/review_gap_probe.py`，独立 SQLite WAL、与产品无关）：
1.5 ms 名义间隔档：`measured_gap` p50 **2.484 ms** = `true_free`（COMMIT 完成后→下一次 BEGIN）p50 **1.702 ms** + `commit` p50 **0.684 ms**；
混合档（8/12/40/350 ms 事务）：**2.463 = 1.628 + 0.684**——即「报出的间隙」里约 0.7 ms 根本不可竞争。
同一探针口径下，96.7% 占空比 + 1.6 ms 真空闲已能把 40 ms 超时探针饿到**连续 1.2 s 拿不到锁**。
要求：把机制表述从「空隙中位 1.58 ms ⇒ 写锁被连续不可用 6 295.8 ms」改成「有效空闲窗口 ≈ 间隙 − COMMIT ≈ 0（本夹具下
不足以被竞争者赢下）」，并把线程臂自己的最长连续不可用（wall span **32 449 ms**，报告完全未引用）写进结论——它比进程臂的
6 295.8 ms 更能支持「近乎连续不可用」。

### F5（材料）checkpoint 只有聚合值，缺卡面 §3.3 要求的轨迹与时间对齐

报告给了 230 采样、max 62.2 ms、`busy=0`、WAL ≤ 4.24 MB，但没有 `-wal`/`-shm` 大小与页数随时间的轨迹，也没有
「checkpoint 是否落在拒绝窗口内」的时间对齐；`-shm` 完全未测。要求：补一条时间序列（或明确降级为聚类统计并说明
为什么足以排除），至少给出与 5 个拒绝窗口逐一对齐的 `log_frames`/耗时。

### F6（阻塞）卡面 §5 的验收要素缺失：命令退出码、ruff/diff-check、是否跑 pytest 的说明

报告 §6 只有命令、没有退出码；未记录 `ruff check .`、`git diff --check`、驱动自检结论；也没写「产品零改动且未新增测试时
不跑全量 pytest」的理由。本轮我独立执行：`ruff check .` = `All checks passed!`（exit 0）、`git diff --check` exit 0——事实成立，
但必须由实施方按卡面写进报告。

### F7（轻微）`after` 计数是占位符，报告却说「见各 JSON 的 before/after」

工具在 `main()` 里对**所有**模式无条件执行 `report["after"] = {"counts": ""}`，覆盖了 inject 分支刚写入的真实计数，
因此四个臂的 JSON 都没有 after 状态（只有 `injection.after_publish` 有效）。报告 §6 的措辞与产物不符。
要求：改工具（一行）或在报告里改口径；臂后状态目前只能由下一臂的 `before` 间接推断。

### F8（口径，建议写清）探针二值语义：`blocked` 只对 ≥ ~85 ms 的连续不可用敏感

实测探针 `blocked` 样本的 `probe_ms` p50 = **84.74 ms**（`BEGIN IMMEDIATE` 40 ms 超时经 busy handler 超调到 ~85 ms）；
微校准中等待 47.6 ms / 87.5 ms 后成功获取的尝试都被记成 `available`。因此「0 blocked」不等于「无写」：成功搜索各自执行 4 个
约 8–16 ms 的 `catch_up` 写事务（线程臂普查），低于探针可分辨阈值。要把 no-drain 臂的「0 blocked」当作「无写锁争用」的
旁证时，应写清这条口径；反过来，这也说明线程臂 591 个 blocked 样本 + 32.4 s 连续 streak 的结论**强于**报告自己的表述。

## 6. 对 P4-02V（修复卡）的直接影响

- 修复卡候选方向 2「仅对 `CATALOG_UNREADABLE` 做 2 次 × 500 ms 有界退避」在实测下**算术不足**：被拒样本各等满
  5 s busy timeout 才失败，而连续不可用在进程臂 6.30 s、线程臂 32.45 s 量级 ⇒ 再退避 1 s 仍会落在同一个饥饿窗口内，
  达不到卡面要求的「0 拒绝」。要么把退避预算按 ≥ 饥饿时长上界重算（并给出对 P95 的代价），要么改选方向 1（跳过本次追赶、
  以既有 `pending_projection` 语义返回）或方向 3（drain 批间真让步）。
- 若选方向 3，让步必须显著大于「间隙 − COMMIT ≈ 0」的量级才有意义（F4 的校准可作为起点）；仅「稍微让一让」没有证据支持。
- 修复卡开工前仍需完成本报告的修订项（F1/F2/F3/F6），否则前后对照会再次落在不同夹具状态上。

## 7. 复现命令（本轮实际执行）

```powershell
# 1) 从落盘 JSON 独立复算（本审查新增，仅在 tmp/ 下）
& D:\SZ\project\aisearch\.venv\Scripts\python.exe tmp\p402u\review_analysis.py
# 2) 写窗口/空闲窗口微校准（独立 SQLite WAL，与产品无关）
& D:\SZ\project\aisearch\.venv\Scripts\python.exe tmp\p402u\review_gap_probe.py
& D:\SZ\project\aisearch\.venv\Scripts\python.exe tmp\p402u\review_gil_probe.py
& D:\SZ\project\aisearch\.venv\Scripts\python.exe tmp\p402u\review_gil_probe2.py
# 3) 仓库级只读检查
& D:\SZ\project\aisearch\.venv\Scripts\python.exe -m ruff check .      # All checks passed!  exit 0
git -C D:\SZ\project\aisearch diff --check                            # exit 0
git -C D:\SZ\project\aisearch status --short
git -C D:\SZ\project\aisearch diff --stat -- src tests uv.lock pyproject.toml   # 空
```

被测工具的原始命令见 [P4-02U 报告 §6](P4-02U-lock-attribution.md)；本轮**未重跑**主反例（§8）。

## 8. 未验证 / 本轮未做（如实标注）

- **未独立重跑主反例**：重跑需先 `inject --events 30000`（该夹具 publish 约 6.65 ms/事件 ≈ 200 s）再跑臂，按 V3.1 §2.4
  「无需为独立性机械重跑同一版本全部成功的昂贵实验、证据缺口或矛盾才补跑」——本轮未发现与结论矛盾之处，故以复算落盘证据
  + 独立机制校准代替；这是本报告独立性上的已知限制，若需强独立性由实施方按 F1 重跑并留存 JSON。
- 未测真实中文 CLIP 文本塔 / `POST /api/v1/search` / 浏览器首屏 / GTX 1660 / 冷 OS 缓存 / 长时 soak / 400 000 档。
- 未评估任何修复方案的效果；未放宽或修改 §12.2；本报告不授予任何性能、后台档或发行 PASS。
- 未改产品代码、schema、依赖、ADR/companion；`tmp/`、`test-results/` 产物在仓库外/被忽略，不入库。

## 9. R2 复审结论（2026-09-27 追加，不改写上文）

R2 修订（工具改为每次运行按模式/事件数/样本数/时间戳独立命名 + `analyze` 复算入口 + `arm_valid` 守卫 + 真实 `after` 计数 +
同状态交错 A/B 臂；报告统一最终 JSON 口径、写明统计量定义与探针 ≥~85 ms 口径、补 checkpoint 逐拒绝窗口对齐、
补命令/退出码/ruff/diff-check；并把线程臂自身最长连续不可用 32 449.4 ms 与「有效空闲 ≈ 间隙 − COMMIT ≈ 0」写入结论）
已由 [R2 复审报告](P4-02U-dsh-capacity-evidence-rereview.md) 独立复核：本报告 F1～F8 全部闭合，报告数字逐项可从落盘
JSON 复现；新增交错臂还通过了「锁只在 drain 写窗口内不可用」的独立证伪测试（8 段 blocked run 与 drain 写窗口重叠
98.7–100%，drain 暂停段 10.2/9.4/8.8 s 内 0 blocked）。裁决改为 **`REVIEW PASS（容量证据专项）`**；
残留 4 项非阻塞 nit（`-shm` 未记、§5「~3 s 量级」仍引用不可回溯数字、首轮交错臂未列、`analyze` 覆盖度用求和而非并集）。
§1～§8 保留为 R1 历史结论，不再代表当前状态。