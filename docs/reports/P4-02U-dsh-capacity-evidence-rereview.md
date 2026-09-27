# P4-02U — 独立 DS-H 容量证据专项审查 · R2 复审（新会话，2026-09-27）

**性质**：只读独立复审。本轮未改产品文件、未改被测工具、未改夹具；新增产物只有本报告，加上
[R1 审查报告](P4-02U-dsh-capacity-evidence-review.md) 的追加闭合段、`docs/development-status.md`
与 [P4-02U](../tasks/P4-02U.md)/[P4-02V](../tasks/P4-02V.md) 的状态交接行。
**审查方式**：V3.1 §2.4「容量证据专项审查（独立 DS-H）」第二轮（R2 修订后复审）。
**裁决**：**`REVIEW PASS（容量证据专项复审通过）`** —— R1 的 F1～F8 逐条闭合，且 R2 报告里每一个关键数字我都
从落盘 JSON 独立复算一致；新增的同状态交错 A/B 臂我另做了独立证伪测试（见 §4），未发现与结论矛盾之处。
另有 4 项**非阻塞**残留（§5：`-shm` 未记、§5「~3 s 量级」仍引用不可回溯数字、首轮交错臂未列入、`analyze` 覆盖度用求和
而非并集），以及 1 项状态交接同步（已由审查方在 §6 处理）。本复审**不授予任何 §12.2/性能/发行 PASS**。

## 1. 复审基线与范围

- 基线：`HEAD = 93a4a52`（master，工作树含 P4-02U 未提交产物，未 push）；`git diff --stat -- src tests uv.lock pyproject.toml` 为空。
- R2 产物 SHA256（前 16 位）与大小：
  - `tools/diagnostics/p4_02u_lock_attribution.py` = `3255ef7886560f7b`（37 368 B，R1 时为 25 480 B）
  - `docs/reports/P4-02U-lock-attribution.md` = `8fe9efbfbfe72639`（13 540 B，R1 时为 8 217 B）
  - `test-results/p4-02u-analysis.json` = `3471d020834ed2d3`（13 069 B）
  - 新增臂：`…-arm-drain-process-e6000-s6-234210.json` = `92ab19cbc046e95e`（518 381 B）、
    `…-arm-interleaved-e12000-s4-234609.json` = `40d4d112cb9ab5d6`（738 413 B）
- 复核范围：工具全文（919 行）中的 `Trace` / `LockTimeline` / `CheckpointSampler` / 六个臂 / `analyze` / `main`；
  R2 报告全文；6 个纳入 `analyze` 的臂 JSON + 未纳入的首轮交错臂 JSON；产品调用链
  `indexing/store.py:707-721`（`process_all`）、`indexing/maintenance.py:119-215`、`catalog/db.py::Catalog.write`。
- 机器可读证据全部在 `test-results/`（被忽略）；本轮我另写的复算脚本只在 `tmp/p402u/review_analysis_r2.py`。

## 2. R1 发现逐条闭合（我独立核对的证据）

| R1 发现 | R2 处置 | 我的独立验证 | 判定 |
|---|---|---|---|
| **F1** 同名 JSON 覆盖、首轮线程臂未披露 | 每次运行按 `模式-e事件数-s样本数-时间戳` 命名（`--output` 可覆盖）；6 000 档进程臂重跑并保留；新增 `arm_valid` 守卫；首轮线程臂在 §5 说明为器具缺陷 | 新命名文件按时间逐个存在（23:41–23:46）；`arm_valid=bool` 出现在新线程/交错臂（旧 4 臂为 `None`，属修订前产物，报告已不引用旧 0/6 数字）；**首轮线程臂「drain 未运行」有独立旁证**：两轮线程臂的 `before` 都显示 `entries_pending_repair=30000`/`outbox.PENDING=30000`（drain 一个事件都没确认），而队列恰减少 41 200→36 400 = 4 800 = 6 次成功搜索 × `CATCH_UP_BATCHES(4)` × `DRAIN_BATCH_SIZE(200)` ⇒ 该轮只有前台 `catch_up` 在写、drain 确实没跑，与报告解释一致 | 闭合 |
| **F2** 混用运行中快照/最终 JSON，定义未写 | 全部改用最终 JSON；新增 `analyze` 复算入口（`p4-02u-analysis.json`）并写明定义 | 复算结果与报告逐项一致（§3 表）；线程臂 713/591、控制臂 99/62 即最终 JSON 口径；缺口是「探针墙钟 run」定义已写入 JSON 的 `definition` 字段 | 闭合 |
| **F3** 四臂夹具状态不同却写「同夹具、同一会话」 | 删除该措辞，逐臂列出资产/队列/进程形态；新增同状态交错 A/B 臂 | §3 表逐臂状态与 JSON `before` 完全一致（366 000/396 000/426 000/432 000/444 000）；交错臂在同一运行内交替（`pairs` 记录 running/held 两段）；报告不再出现「同夹具、同一会话」的旧措辞（仅在 §0 引用 R1 原文） | 闭合 |
| **F4** 窗口间隙含 COMMIT 却当可用空闲 | 机制表述改为「有效空闲 ≈ 间隙 − COMMIT ≈ 0」，引用微校准，并改用线程臂自身 32 449.4 ms | 复算证实线程臂 `sum(hold)/span = 56 186.285/56 774.508 = 98.96%`、`max_hold = 465.675 ms`、`gap p50 1.579/p95 3.475/max 18.279`；探针最长 run wall span = **32 449.379 ms**（R1 未被引用） | 闭合 |
| **F5** checkpoint 只有聚合值 | 补逐拒绝窗口对齐表 | 我按同一 JSON 重算 5 个拒绝窗口内的 checkpoint 采样：22/22/21/22/22，max ms 8.02/5.98/5.35/4.94/5.74，max frames 979/975/978/977/978，busy 全 0 —— 与 §4.2 表逐格一致；全程 230 采样 max 62.206 ms、p95 5.571、`busy=0`、frames ≤ 983 | 闭合（残留：`-shm` 未记，见 §5 R1） |
| **F6** 缺退出码/ruff/diff-check/pytest 说明 | §7 给出命令 + 退出码 + ruff/diff-check + 不跑全量 pytest 的理由 | 我独立复跑：`ruff check .` = `All checks passed!`（exit 0）、`git diff --check` exit 0、`compileall` 该驱动 exit 0；`git diff --stat -- src …` 空。拒绝跑全量 pytest 的理由（产品/测试零改动，按 V3.1 §3.2 走工具自检 + ruff + diff-check）与卡面 §5 一致 | 闭合 |
| **F7** `after` 是占位符 | 工具改为写真实 after 计数 | 3 个新 JSON 均有真实 `after.counts`（如 6 000 档 `entries_pending_repair 6000→0`、交错臂 `8200→0`）；旧 4 个 JSON 仍是 `{"counts": ""}`，属修订前产物，报告已限定为「本次三张新 JSON」 | 闭合 |
| **F8** 探针二值语义未写明 | §2 写明 `blocked` 只登记 ≥ ~85 ms | 复算 `blocked_probe_ms` p50 = 84.4–85.0 ms（5 个含探针的臂一致） | 闭合 |

## 3. 独立复算与 R2 报告数字对照（全部一致）

| R2 报告数字 | 我的独立复算 | 一致 |
|---|---|---|
| 控制臂 99/62、最长 run 5 845.6 ms、1/1 被拒 | 99/62、5845.609 ms、1/1 | ✔ |
| 无 drain 2 328/**0** blocked、0/4 被拒 | 2328/0、0/4 | ✔ |
| 线程臂 713/591（82.9%）、最长 run 32 449.4 ms、5/6 被拒 | 713/591、32449.379 ms、5/6 | ✔ |
| 线程臂 301 事务（151 claim+150 apply）、占空比 98.96%、单条 max 465.7 ms | 301、（98.96%、465.675 ms） | ✔ |
| 拒绝窗口覆盖 98.7–99.1%（drain）/38.6–39.7%（engine，单条 max ≈208 ms） | 98.7/99.1/99.1/99.0/99.1；38.6/38.6/38.8/38.9/39.7；engine max 207.9 ms | ✔ |
| 进程臂 @30 000：1 039/756、最长 run 6 295.8 ms、4/6 被拒 | 1039/756、6295.778 ms、4/6 | ✔ |
| 进程臂 @6 000（重跑）：4 127/153、最长 run 5 158.2 ms、1/6 被拒、夹具 432 000/70 000 | 4127/153、5158.15 ms、1/6、432000/70000 | ✔（报告多进一位） |
| 交错臂：running 2/4 被拒、held 0/4、5 752/216、占空比 41.7%、单条 max 605.4 ms | 完全一致（41.74%、605.427 ms） | ✔ |
| checkpoint 全程 230 采样、max 62.2 ms、busy=0、frames ≤ 983 | 230、62.206、0、983 | ✔ |
| 被拒原文、无 drain 0/4、线程 5/6、进程 4/6 | 逐字一致 | ✔ |

## 4. 我对新增交错臂做的独立证伪测试（R1 没有的新证据）

1. **锁只在 drain 写的时候不可用**：交错臂（12 000）8 段 blocked run 的墙钟时间，与 drain `phase_windows` 并集的重叠度为
   **98.7%–100%**（最长 5 648 ms 的 run 覆盖 98.9%）。没有任何一段 blocked 落在 drain 的写窗口之外。
2. **drain 暂停段锁确实空闲**：该臂 drain 的 3 个长空闲段为 **10 193 / 9 403 / 8 775 ms**，正是 held 段（settle + 搜索）；
   这些区间内探针 **0 blocked**。这把「drain 进程存在」与「drain 正在写」分开，直接支持占空比机制，而不是「drain 在场就会拒」。
3. **保真度比对**：交错臂的 drain 循环 = `ensure_scalar_indexes()` + `process_batch(now_ms, max_events=BATCH_SIZE)` 直到返回 0，
   与产品 `store.py:707-721` 的 `process_all` 逐行同构（同一产品方法、同一批大小、同一停止条件）；差值只是没有产品侧的 busy-retry 包装，
   而该包装在本臂（主动暂停）下不参与。因此「暂停 drain ⇒ 锁空闲」可以外推到产品 drain 形态。
4. 未纳入 `analyze` 的首轮交错臂（`…-interleaved-e6000-s4-234433.json`，23:44:33）我另行读过：running 0/3、held 0/3 被拒，
   drain 只 applied 3 800（19 批，低频占空比），探针 4 951/100、最长 run 485.8 ms。它与「占空比越低越不易被拒」一致，
   不构成矛盾；但报告未列出它（§5 R3）。

## 5. 残留（全部非阻塞，建议后续顺手修）

- **R5（材料；审查方本轮已更正）修复卡输入曾引用已撤回的数字**：`docs/tasks/P4-02V.md` §1 原文写「另一形态（6 000 档单条
  `apply` 持锁 3 150.7 ms）说明『单条事务也可能持续数秒』，修复不得依赖『单条一定短』」——该数字只存在于被覆盖的首轮
  6 000 档 JSON，R2 报告 §5 已声明不再引用。我已把 §1 改成可回溯口径：单条事务最长 **692.6 ms**（30 万进程臂）/
  465.7 ms（线程臂）/ 538.0 ms（6 000 重跑）/ 608.3 ms（交错臂）⇒ 修复**可以**依赖「单条事务 < 1 s」，但**不能**依赖
  「批间一定有可用空隙」。同段「最长连续不可用 6 295.8 ms / 占空比 99.0%」也已改为线程臂 **32 449.4 ms** / **98.96%**。

- **R1（`-shm` 未记）**：卡面 §3.3 点名 `-wal`/`-shm` 大小与页数轨迹；R2 记录了 `wal_bytes`（8 ms 节拍的采样）与
  `log_frames`，但没有 `-shm`。本夹具里 `catalog.db-shm` 是固定 32 768 B 的 WAL 索引，量级上不可能解释 5–6 s，故不阻塞；
  若要严格对齐卡面，采一次 `-shm` 大小即可闭合。
- **R2（§5 仍引用不可回溯数字）**：§5 开头声明「首轮 6 000 档……本版不再引用其数字」，但同节末句仍写
  「单条长持锁形态只到 **~3 s 量级**」——该 `3 150.7 ms` 只能来自被覆盖的首轮 JSON。本次重跑的 `write_hold_ms` max 是
  **538.0 ms**（`p50 17.7 / p95 528.0`），建议改写为该数字或删除该从句。
- **R3（首轮交错臂未列 + 证据不自述版本）**：`…-interleaved-e6000-s4-234433.json` 已保留但未列入报告/`analyze` 输入；
  另外各 JSON 没有记录工具版本/命令行，读者无法判断某文件由哪个修订产生（我本轮是靠「有无 `phase_windows`」与 mtime 反推的：
  4 个旧臂与 6 000 进程臂重跑无 `phase_windows`，首轮交错臂连 `phase_windows`/`checkpoint_samples` 都没有）。
  建议 JSON 内写入驱动 SHA256 + `sys.argv`，并在 §3 表里补一行首轮交错臂（或注明为何不列）。
- **R4（`analyze` 覆盖度用求和而非并集）**：`analyze` 对同一 label 的窗口是直接累加重叠时长；本夹具的 `phase_windows` 天然
  串行、`engine_windows` 单独统计，所以结果与并集一致（我复算相同）。若将来出现嵌套/重叠窗口，覆盖率会 >100%，
  建议改成区间并集（或加断言）。

## 6. 状态交接同步（审查方本轮处理）

R2 修订只追加了交接文字，`docs/development-status.md` 与卡状态行里仍留着 R1 已撤回或已被 R2 取代的数字，会误导下一个会话：

- 状态文件 P4-02U 段仍写「批间空隙 p50 1.58 ms ⇒ 写锁占空比 99.0%、连续不可用长达 6 295.8 ms」与
  「6 000 档另观测单条 `apply` 持锁 **3 150.7 ms**」——后者是 R2 明确不再引用的不可回溯数字，前者已被 R2 的
  98.96%（`acquire..end` 口径）与线程臂 **32 449.4 ms** 取代。本轮已改为 R2 口径。
- 卡状态行同时保留了「F1～F8 已闭合，待独立复审」与 R1 遗留句「但报告与证据需按 F1～F6 修订后复审」。
  本轮已改写为 `REVIEW PASS` 并指向本报告。
- P4-02V 的「实现前置 = P4-02U 专项审查」现已满足（复审通过），并把「开工前先完成报告修订」更新为「修订已完成并复审通过」；
  候选方向 2 的退避预算提醒继续保留。另按 §5 R5 更正了 §1 的两处已撤回数字（单条持锁与占空比/连续不可用口径）。

## 7. 结论、升级判断与后续

- 归因结论（持锁者 = drain `claim`/`apply`；排除 checkpoint、未计时写路径、外部写者；机制 = 写锁被事务序列近乎占满使竞争者超时）
  在 R2 证据下**成立**，且被我的独立复算与独立证伪测试支持。
- **不升级**为完整受影响面工程主审：本卡仍是纯测量（`src/**`、`tests/**`、schema、依赖、§12.2 零改动）。
  **不需要** Sol 聚焦终审：无 ADR/数据安全未决项；若 P4-02V 落地任何 `src/**` 改动，按 V3.1 §2.2/§2.3/§11 另判。
- 后续：[P4-02V](../tasks/P4-02V.md) 可直接开工（前置已满足）。开工时须按实测饥饿量级（进程臂 6.30 s、线程臂 32.45 s）
  重算退避预算或改选方向 1/3；目前没有任何证据支持「2 次 × 500 ms」够用。

## 8. 复现命令（本轮实际执行）

```powershell
# R2 报告数字的独立复算（本轮新增，仅在 tmp/ 下）
& D:\SZ\project\aisearch\.venv\Scripts\python.exe tmp\p402u\review_analysis_r2.py
# 交错臂「锁只在 drain 写时不可用」证伪测试（同一脚本内的区间重叠计算 + 单独一次性检查）
# 仓库级只读检查
& D:\SZ\project\aisearch\.venv\Scripts\python.exe -m ruff check .            # All checks passed!  exit 0
& D:\SZ\project\aisearch\.venv\Scripts\python.exe -m compileall -q tools\diagnostics\p4_02u_lock_attribution.py  # exit 0
git -C D:\SZ\project\aisearch diff --check                                  # exit 0
git -C D:\SZ\project\aisearch diff --stat -- src tests uv.lock pyproject.toml  # 空
```

## 9. 未验证 / 本轮未做

- 未重跑任何被测臂（R2 证据自洽、可复算，V3.1 §2.4 允许不机械重跑）；重跑命令已由 R2 报告 §7 给出。
- 真实中文 CLIP 文本塔、`POST /api/v1/search`、浏览器首屏、GTX 1660、冷 OS 缓存、长时 soak、400 000 档均未验证。
- 未评估任何修复方案；未放宽或修改 §12.2；本报告不授予任何性能、后台档或发行 PASS。