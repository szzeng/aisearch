# P4-02U — 合法前台搜索被拒时的写锁归属（测量/归因专项，修订 R2）

**性质**：测量与归因，**产品零改动**（`git diff --stat -- src tests uv.lock pyproject.toml` 为空）。
本版是按 [独立容量证据专项审查](../reports/P4-02U-dsh-capacity-evidence-review.md) 的 F1～F8 修订后的 R2；
审查裁决的归因方向（持锁者 = drain 的 `claim`/`apply`）未被推翻，本版不改方向，只把口径、证据归档与机制表述补齐。
本报告不设计、不实现修复；§12.2 与任何性能结论均未改变。

## 0. 本版对审查发现的闭合

| 发现 | 处置 |
|---|---|
| **F1** 同名 JSON 互相覆盖、首轮线程臂未披露 | 工具改为**每次运行按模式/事件数/样本数/时间戳命名**（`--output` 可覆盖）；6 000 档进程臂**已重跑并保留**（`…-e6000-s6-234210.json`，1/6 被拒）；首轮线程臂的 0/6 **已定位为器具缺陷并非“另一种夹具形态”**（见 §5）；线程臂新增 `arm_valid` 守卫，无效跑不可能再被当成证据 |
| **F2** 混用运行中快照与最终 JSON、复算定义未写明 | 报告全部改用**最终 JSON**；新增 `analyze` 模式（`tools/diagnostics/p4_02u_lock_attribution.py analyze --inputs …`）从落盘 JSON 复算全部统计量并写定义，产物 `test-results/p4-02u-analysis.json` |
| **F3** 四臂夹具状态不同却写“同夹具、同一会话” | 删除该措辞；逐臂列出资产数/队列深度/进程形态（§3）；并新增**同状态交错 A/B 对照臂**（§3 末行、§4） |
| **F4** 写窗口间隙含 COMMIT，不能当可用空闲 | 机制表述改为「有效空闲 ≈ 间隙 − COMMIT ≈ 0」；引用独立微校准 2.484 = 1.702 + 0.684 ms；并改用线程臂**自身最长连续不可用 32 449.4 ms**（此前未引用） |
| **F5** checkpoint 只有聚合值 | 补「逐个拒绝窗口对齐」表（§4.2）：每个拒绝窗口内 checkpoint 采样数、最大耗时、最大 WAL 帧数、`busy` |
| **F6** 缺退出码/ruff/diff-check/pytest 说明 | §7 给出逐条命令与退出码、`ruff`/`diff --check` 结论，以及**不跑全量 pytest 的理由** |
| **F7** `after` 是占位符 | 工具已改为写真实 after 计数（本次三张新 JSON 均含 `after.counts`） |
| **F8** 探针二值语义未写明 | §2 写明：`blocked` 只对**≥ ~85 ms** 的连续不可用敏感，故“0 blocked ≠ 无写” |

## 1. 结论（R2 措辞）

1. **持锁者 = 产品 drain 自己的写事务**：`IndexCoordinator.process_batch` 的 `claim`/`apply`（即 drain 自报
   `write_phases` 的 151 次 `claim` + 150 次 `apply`）。它是**产品路径内已计时**的写事务；不存在第三个写者。
2. **不是 WAL checkpoint**：线程臂 230 次 PASSIVE 采样 max **62.2 ms**、`busy=0`、`log_frames ≤ 983`（WAL ≤ 4.25 MB），
   且每个拒绝窗口内对齐到的 checkpoint 采样 max 仅 **4.9–8.0 ms**、`busy=0`（§4.2）。
3. **不是未计时写路径**：进程内所有 `Catalog.write` 窗口都能归到两个线程（drain 线程 301 条 = `claim`+`apply`，
   前台线程若干条 `catch_up` 与被拒窗口），拒绝窗口被 drain 计时窗口覆盖 **98.7–99.1%**。
4. **机制 = 写锁被 drain 的事务序列近乎占满，竞争者超时**：线程臂 301 条事务持锁合计 56.19 s / 跨度 56.77 s ⇒
   **占空比 98.96%**，单条最大仅 465.7 ms；实测的窗口间隙（p50 1.579 ms）还包含上一条事务的 **COMMIT**
   （独立微校准：所示间隙 2.484 ms = 真空闲 1.702 ms + COMMIT 0.684 ms）⇒ **有效空闲更小**；
   线程臂自身最长连续不可用达 **32 449.4 ms**。需要写锁的前台搜索因此常在 5 s busy timeout 内抢不到锁。
5. **[P4-02J](../reports/P4-02J-contention-closure.md) §5「归因未定」的收口**：其排除的是“单条长事务”，
   本卡补充的是“短事务序列的连续性/占空比”；两者不矛盾，也不改写其历史结论。
6. **产品侧缺口成立**（§12.2 后台档「不得因内部争用拒绝合法验证请求」），但修复不在本卡：见 [P4-02V](../tasks/P4-02V.md)，
   其候选方向需按本报告 §6 的实测饥饿时长重算。

## 2. 方法与仪器

- 夹具：P4-02J 的**丢弃式副本** `tmp/p402j-contention-close/data`；事件按真实 artifact/outbox 协议注入
  （`bench.publish_embedding`）。**未触碰**参考夹具 `tmp/p402f-阶段0/scale-300000-wave/data`，未扫真实图库。
- 前台：`search.service.search_assets(top_k=20)` + 既有替身编码器（与容量工具/P4-02J 同口径）；投影队列**不预热**。
- 后台：进程臂 = `python -m photo_search.indexing.cli drain`；线程臂 = `maintenance.drain_semantic_outbox`；
  交错臂 = 同一进程内 `IndexCoordinator.process_batch` 循环（与 CLI 的批循环同一产品调用）。
- 仪器（运行时插桩，仓库代码不变）：`Catalog.write` / `IndexCoordinator._timed_write` / `LanceVectorIndex.upsert_batch`
  时间戳窗口；独立连接锁探针（40 ms 超时、8 ms 节拍）；`PRAGMA wal_checkpoint(PASSIVE)` 计时 + WAL 字节；
  锁不可用 ≥1.2 s 时 `faulthandler` 全线程栈。

**统计量定义（F2/F4/F8）**

- **占空比** = Σ(`end_ms`−`acquire_ms` of `_timed_write` 窗口) / 这些窗口的跨度；窗口终点早于产品自己的 COMMIT，
  所以**窗口间隙 = 后一 `acquire_ms` − 前一 `end_ms`，其中包含前一事务的 COMMIT**，不是可竞争空闲。
- **连续不可用 run** = 锁探针连续 `blocked` 采样；`wall_span_ms` = 末样本 `t_ms` − 首样本 `t_ms`（另有 `probe_ms` 求和口径）。
- **探针口径（F8）**：`blocked` 样本的 `probe_ms` p50 = **84.4–85.0 ms**（40 ms 超时被 SQLite busy handler 拉长），
  即 `blocked` 只登记 **≥ ~85 ms** 的不可用；成功搜索自己的 4 个 `catch_up` 写事务（约 8–16 ms）**低于该阈值**，
  因此 no-drain 臂的「0 blocked」只能当旁证，不能读成「完全无写」。
- **覆盖度** = 每个被拒搜索窗口内，与 drain 窗口（或引擎窗口）重叠时间的并集 / 窗口长度。

## 3. 各臂结果（全部取自最终 JSON；夹具状态逐臂列出）

| 臂 / 证据文件 | 夹具状态（资产 / 队列） | 进程形态 | 前台结果 | 锁探针 | drain 侧 |
|---|---|---|---|---|---|
| 正向对照 `p4-02u-arm-control.json` | 366 000 / 19 200 | 同进程第三方持锁 6.0 s | **1/1 被拒**（5 486 ms） | 99 / 62 blocked，最长 run 5 845.6 ms | — |
| 无 drain `p4-02u-arm-no-drain.json` | 366 000 / 19 200 | 同进程，无后台 | **0/4 被拒**（4.8–5.7 s） | 2 328 / **0 blocked** | — |
| drain 线程臂 `p4-02u-arm-drain-thread.json` | 396 000 / 36 400 | 同进程线程 | **5/6 被拒** | 713 / 591（82.9%），最长 run **32 449.4 ms** | 301 事务；占空比 **98.96%**；单条 max 465.7 ms |
| drain 进程臂 @30 000 `p4-02u-arm-drain-process.json` | 426 000 / 65 600 | 子进程 | **4/6 被拒** | 1 039 / 756（72.8%），最长 run 6 295.8 ms | 301 事务；`write_hold_ms` p50 34.2 / p95 558.5 / max 692.6；`write_rejections=0` |
| drain 进程臂 @6 000（重跑）`…-e6000-s6-234210.json` | 432 000 / 70 000 | 子进程 | **1/6 被拒** | 4 127 / 153，最长 run 5 158.2 ms | 见 §5（首轮 0/6 已不可回溯） |
| **交错 A/B**（同状态）`…-interleaved-e12000-s4-234609.json` | 444 000 / 73 200 | 同进程线程，交替暂停 | **running 2/4 被拒、held 0/4 被拒** | 5 752 / 216 | 全程占空比 41.7%（含暂停段），单条 max 605.4 ms |

交错臂是本次为 F3 新增的**同夹具状态、同一运行内交替**对照：每个 pair 先让 drain 连续写、再暂停 drain
（`--pause-settle 1000 ms`）后各跑一次同样的搜索。结果 4 组里 running 段 2 次被拒、held 段 0 次被拒——
把「drain 的写入」与「drain 进程存在」分开，支持 §1 的占空比机制。该臂是**干预**，不是修复方案。

## 4. 归属细节

### 4.1 覆盖度（线程臂，最终 JSON）

5 个被拒窗口（5 497–5 528 ms）中：drain 计时窗口覆盖 **98.7–99.1%**；引擎写窗口（`upsert_batch`）只覆盖
**38.6–39.7%**（单条最长 ≈208 ms）。即：持锁者不是引擎写本身，而是**它所在的那条 `apply` 事务及其前后紧邻的
`claim`/`apply` 序列**把写锁连续占住。

### 4.2 checkpoint 与拒绝窗口逐一对齐（F5）

| 拒绝窗口起点 (ms) | 窗口内 checkpoint 采样 | 窗口内最大耗时 (ms) | 窗口内最大 `log_frames` | `busy` |
|---|---|---|---|---|
| 9 622.4 | 22 | 8.02 | 979 | 0 |
| 15 133.4 | 22 | 5.98 | 975 | 0 |
| 20 609.3 | 21 | 5.35 | 978 | 0 |
| 26 116.5 | 22 | 4.94 | 977 | 0 |
| 31 644.5 | 22 | 5.74 | 978 | 0 |

全程 230 采样：max 62.2 ms、p95 5.57 ms、`busy=0`、`log_frames ≤ 983`、WAL ≤ 4 247 752 B ⇒ checkpoint 的**体量与耗时**
都与 5–6 s 连续不可用不在同一量级，**排除**。

### 4.3 未被覆盖的残余

线程臂残留的 0.9–1.3% 未覆盖时间里，秒级探针采样无法逐毫秒对照 drain 的 Python/载荷构建开销；
本报告不把它写成另一种机制，只写成**采样粒度限制**。

## 5. 两个 6 000 形态与首轮线程臂（F1）

- **首轮线程臂（0/6 被拒、6 000 探针 / 0 blocked、日志 `tmp/p402u/arm-drain-thread.log`）不是另一种夹具形态，而是器具缺陷**：
  当时 `background` 的 `Catalog` 在主线程创建后交给 drain 线程使用，Python `sqlite3` 直接抛
  `ProgrammingError: SQLite objects created in a thread can only be used in that same thread`，**drain 根本没有运行**，
  因此没有写者、没有 `phase_windows`。修复方式 = 在 drain 线程内打开/关闭该连接；本版另加 `arm_valid` 守卫
  （要求 `applied > 0` 且存在 `phase_windows`），无效跑会在 JSON 里标 `arm_valid=false` + 原因，不会再被当成证据。
  该轮 JSON 已被同名文件覆盖、**不可回溯**；本报告不再引用其任何数字。
- **6 000 档是「临界档」**：首轮进程臂 0/6 被拒（该 JSON 已被覆盖，**不可回溯**，只保留在旧报告中，本版不再引用其数字）；
  本次重跑 **1/6 被拒**（4 127 探针 / 153 blocked，最长 run 5 158.2 ms）。与 30 000 档（4/6、5/6）相比，
  6 000 档 drain 事务少、占空比低、且单条长持锁形态只到 ~3 s 量级，因此**是否越线是随机的**。
  结论：被拒概率随 drain 占空比/持续时长上升，不是某个夹具尺寸专属。

## 6. 对修复卡的直接输入（不改 P4-02V 的边界）

- 实测饥饿量级：进程臂 6.30 s、线程臂 **32.45 s**。任何「有界退避」方案的预算必须按这个上界重算，
  否则仍会落在同一饥饿窗口内（审查 §6 已指出 2×500 ms 算术不足）。
- 交错臂显示：**暂停 drain 1 s（`--pause-settle 1000`）后 held 段 0/4 被拒**；说明让 drain 真正让出写锁是有效干预，
  但让出多少、对建库吞吐的代价，须由修复卡用同样口径量化。
- 本报告不选方案、不实现方案。

## 7. 命令、退出码与仓库级检查（F6）

```powershell
# 夹具与各臂（每次运行按 模式/事件数/样本数/时间戳 命名，互不覆盖）
uv run --no-sync python tools/diagnostics/p4_02u_lock_attribution.py inject --events 6000      # exit 0
uv run --no-sync python tools/diagnostics/p4_02u_lock_attribution.py arm-control                # exit 0
uv run --no-sync python tools/diagnostics/p4_02u_lock_attribution.py arm-no-drain --samples 4   # exit 0
uv run --no-sync python tools/diagnostics/p4_02u_lock_attribution.py arm-drain-thread --events 30000 --samples 6   # exit 0
uv run --no-sync python tools/diagnostics/p4_02u_lock_attribution.py arm-drain-process --events 30000 --samples 6  # exit 0
uv run --no-sync python tools/diagnostics/p4_02u_lock_attribution.py arm-drain-process --events 6000  --samples 6  # exit 0（重跑）
uv run --no-sync python tools/diagnostics/p4_02u_lock_attribution.py arm-interleaved --events 12000 --pairs 4 --pause-settle 1.0  # exit 0
# F2 复算入口（本报告所有数字均由它复算）
uv run --no-sync python tools/diagnostics/p4_02u_lock_attribution.py analyze --inputs <上述 JSON…> --output test-results/p4-02u-analysis.json   # exit 0
# 仓库级只读检查
uv run --no-sync python -m ruff check .     # All checks passed!  exit 0
git diff --check                            # exit 0
git diff --stat -- src tests uv.lock pyproject.toml   # 空 ⇒ 产品/schema/依赖零改动
```

**为什么没有跑全量 pytest**：本卡产品与测试零改动（工具在 `tools/diagnostics/`，不在 `tests/`），按 V3.1 §3.2 的
纯测量/文档分支执行「工具自检 + ruff + diff-check」。工具自检 = `analyze` 能对全部落盘 JSON 复算通过（exit 0）
+ `compileall` 语法校验通过。

## 8. 未验证 / 限制

- 替身编码器 + service 层：真实中文 CLIP 文本塔、`POST /api/v1/search`、浏览器首屏、GTX 1660 均未参与。
- 未测 40 万、冷 OS 缓存、长时 soak；夹具为累积注入的丢弃式副本，`assets` 逐臂递增（§3 表已列）。
- 未测任何修复方案的产品效果（属 P4-02V）；未放宽或修改 §12.2；不授予任何性能/后台档 PASS。
- 本报告是**测量证据 R2**，仍需独立复审；审查方如需重跑，命令与产物命名见 §7。
