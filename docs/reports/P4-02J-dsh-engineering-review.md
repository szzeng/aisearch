# P4-02J 独立 DS-H 完整受影响面主审报告

被审对象：**P4-02J `asset_id` 标量索引覆盖维护（P4-02D F4 收口）**，提交 `aa33f65`（实现）、
`757fb46`（10 万/30 万复测）、`3a3e542`（独立仪器争用复测）；基线 `ad37a26`（P4-02H 契约冻结后）。
本报告是 [P4-02J 任务卡](../tasks/P4-02J.md) 停止点要求的**独立 DS-H / DeepSeek V4.1 Flash / 高推理
完整受影响面工程主审**。

主审原则：不引用被审报告的任何结论作为证据。下表每一行都来自本会话真实执行的命令、只读探针与产物文件；
被审报告的数字只用于比对是否一致。本会话未改任何产品代码、未改被审报告，未改 ACL。

## 1. 主审结论

**产品代码：`REVIEW PASS`（0 处必须修正）；本卡整体：`不予 PASS`——报告面 3 处必须闭合（全部为
陈述/交付级，产品代码不需要任何改动）。**

| 编号 | 级别 | 结论 |
|---|---|---|
| 覆盖自愈机制 | 产品 | 通过：仅维护侧可选索引，`refreshed` 语义正确、幂等、页不变；`no_table`/`created`/`present`/`unavailable` 语义与 P4-01C B2 全部保留 |
| **F-A** | 报告陈述 | **必须修正**：报告 §1 与提交 `757fb46` 把「中位数比」写成「**P95 改善** 5.9×/36×」 |
| **F-B** | 报告陈述 | **必须修正**：§6 把**空闲**测量当作「争用复测」结论（8 个前台样本与后台 drain **零重叠**） |
| **F-C** | 交付缺项 | **必须补**：必须交付 3 要求的「索引字节、表 version 变化」未进 §3 表 |

对下一卡的问题「Sol 是否必选」，本主审判定 **`SOL_NOT_REQUIRED`**（理由见 §8）；F-A/F-B/F-C 闭合后
只需一次**只审修订面**的短审，不需要重跑产品面复测。

## 2. 被审面与基线（受影响面完整性）

- **产品面改动只有一处**：`src/photo_search/indexing/store.py::LanceVectorIndex.ensure_scalar_index`
  （`git diff --stat ad37a26..HEAD` 中唯一的 `src/**` 文件，+20/−3）。其余 5 个文件是 P4-02J 报告、
  P4-03 准备包、`docs/development-status.md`、任务卡与新增测试
  `tests/integration/test_p4_02j_scalar_index_coverage.py`。
- **未触碰**：检索语义（`search`/`search_candidates`/`_CONFIRMED_BLOCK_SQL`/候选块与全序）、API 信封/
  `query_hash`/游标/错误码、RRF/BM25/距离、ADR-0001～0012 与 companion、`0001`～`0016`、
  `PhotoSearch.spec`/`tools/packaging/**`、`pyproject.toml`、`uv.lock`。`SUPPORTED_SCHEMA_VERSION = 16`、
  migrations 最大 `0016_jobs_claim_order.sql` 均未变。
- **调用面（读全量后确认）**：`ensure_scalar_index` 只被 `IndexCoordinator.ensure_scalar_indexes` 调用，
  后者只在 `IndexCoordinator.process_all` 的**维护侧**被调用；该报告只进入 `indexing/maintenance.py` 的
  运维 JSON 载荷（`scalar_index`），`src/photo_search/api/**` 与搜索读路径都不读它。
  `rg` 确认没有任何 ADR/contract 文件枚举该状态值 ⇒ 新增 `refreshed` **不是**契约变更。
- 工作树：`git status --short --untracked-files=all` 为空；被忽略的 `.pytest-*`、`test-results/`、`tmp/`
  之外无残留改动。

## 3. 独立复现的验收

| 项目 | 命令 | 独立结果 |
|---|---|---|
| 聚焦套件 | `uv run --no-sync python -m pytest -q -p no:cacheprovider tests/integration/test_p4_02j_scalar_index_coverage.py tests/integration/test_p4_01c_retrieval_blocks.py --basetemp=.pytest-basetemp-p402j-review/focused2 --junitxml=test-results/p4-02j-review-focused2.xml` | **11 / 0 / 0 / 0**，exit 0，8.029 s |
| B 级全量 | `uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration --basetemp=.pytest-basetemp-p402j-review/full2 --junitxml=test-results/p4-02j-review-full2.xml` | **1715 / 0 / 0 / 48**，exit 0，295.687 s |
| Lint / 空白 / 锁 | `uv run --no-sync ruff check .`；`git diff --check`；`uv lock --check` | 全部 exit 0（`All checks passed!` / `Resolved 96 packages`） |
| 覆盖自愈探针 | `uv run --no-sync python tmp/p402j-review/probe_scalar_index.py` | 见 §4（`test-results/p4-02j-review-probes.json`） |
| 争用仪器时序探针 | `uv run --no-sync python tmp/p402j-review/probe_concurrency_overlap.py` | 见 §6（同一证据文件） |
| 页 sha 复算 | 由 `test-results/p4-02j-stage0-*.json` 的 `page` 重算 `sha256(json.dumps(page, ensure_ascii=True))` | 10 万 `25ac38b4aecc`、30 万 `4eece2f113d3`，与 P4-02F as-built 页**完全相同** |

**全量用例数差异（已定位，不是缺陷）**：被审的 `p4-02j-full.xml` 记 **1729 / 0 / 0 / 48**，本主审为
**1715 / 0 / 0 / 48**；两侧节点差恰为 14 条，全部来自
`tests/integration/test_p4_02i_a3_isolation.py`。该文件是另一会话遗留的**未跟踪** P4-02I 原型模块，
现已不在工作树中（只剩 gitignore 的 `__pycache__/*.pyc`）。主审侧多出的用例集合为 **0**
⇒ 两侧的被跟踪用例集合完全一致，差异只来自那份原型文件。旁证见 §9 O-6。

**环境事实（非产品缺陷）**：受限令牌下 `--basetemp` 建根目录后无法枚举（`PermissionError [WinError 5]`），
首次沙箱内复跑 10 例因此 setup 报错；改走正常 Windows 权限路由后全部通过。**未改任何 ACL。**

## 4. 覆盖自愈机制（独立只读探针，不引用被审测试）

主审直接驱动产品适配器（`LanceVectorIndex`），用真实 LanceDB 0.38.0 观察引擎目录项
（`lancedb.index.IndexConfig` 暴露 `name`/`index_type`/`num_indexed_rows`/`num_unindexed_rows`/`size_bytes`）：

| 步骤 | 引擎事实 | 产品上报 |
|---|---|---|
| 第一批 400 行 + 首次维护 | `BTree`、`num_indexed_rows=400`、`num_unindexed_rows=0`、`size_bytes=4 200` | `created` |
| 紧接着再调用一次 | 目录已完整 | `present`（**不白花重建预算**） |
| 追加 400 行（模拟下一波）后维护 | 追加后 `400/400`；重建后 `800/0`（14.687 ms） | `refreshed` + `unindexed_rows_before=400` |
| 再追加 400 行后维护 | 追加后 `800/400`；重建后 `1 200/0`（13.848 ms） | `refreshed` + `400` |
| 第三次追加后维护 | 追加后 `1 200/400`；重建后 `1 600/0`（13.322 ms） | `refreshed` + `400` |

- **逐项相同的页**：每一波的 `search_candidates` 页（`(asset_id, distance)`，`limit=10`）在重建前后
  **完全相同**；对整表再做一次显式 `replace=True`（14.869 ms）后仍相同。
- **状态兼容**：`list_indices()` 的目录读取仍在同一个 `try` 内（P4-01C B2 的「目录不可读不得拖垮 drain」
  保持）；`_table` 仍在 `try` 之外（缺表/维度不符仍是结构化 catalog 失败）。重建抛异常 ⇒ `unavailable`
  （失败即降级为扫描，绝不改变答案）。独立复跑
  `test_ensure_scalar_index_reports_every_state` 与 `test_a_refused_index_catalogue_never_blocks_the_outbox_drain`
  均通过。
- **机制结论**：`create_index` 只覆盖建立当刻的行、后续写入成为未索引行，这是 F4 的真实机制；本卡把
  「不完整 ⇒ 就地 `replace=True` 重建、完整 ⇒ `present`」写成维护侧自愈，与证据一致。
- **口径提醒（不是缺陷）**：自愈是**惰性**的——重建发生在**下一次维护调用开头**，所以「建库结束即 100%」
  依赖建库流程末尾那次额外维护调用。波次 JSON 里每波状态为 `no_table`×1 / `created`×1 /
  `refreshed`×18（10 万）、×58（30 万），最后一波写入的 5 000 行正是由容量工具
  `tools/diagnostics/capacity_validation.py:746` 的 `drain --limit 0` 这一次补跑覆盖的
  （`scalar_index` 记 `refreshed`、`unindexed_rows_before=5000`）。这不影响正确性（索引只是访问路径），
  但报告应把它写明，避免被读成「每批写完整即满覆盖」。

## 5. P4-01 caveat 的反证是否成立（点名项）

- P4-01 的原记录（[P4-01 §3.3/§8](P4-01-ann-scale-benchmark.md)）是：在同一张 10 万行表上，
  **连续第三次 `create_index(replace=True)` 进程几乎不消耗 CPU、>9 min 未完成**；该现象出现在
  **ANN 变体（IVF_PQ 参数）**的重复重建回路里，修法是改用全新表 + `drop_index` 后重建。
- 本卡的反证：30 万档 60 波里完成 59 次 `replace=True` 重建（单次上限 213 ms，`drain` 15.06 s），
  未复现。主审独立探针在同一张表上连续 4 次 `replace=True` + 1 次显式全量重建，全部 <15 ms。
- **判定**：反证**成立**，且被审报告把 P4-01 的场合定性为「ANN 变体」与 P4-01 自身记录一致。
  被审报告给出的较保守备选（`drop_index` + `create_index`）**不必在本卡启用**；已记录在案，若未来
  真的出现同形态停顿再按它切换。**残余风险（已知、不阻断）**：`create_index` 没有超时，
  一次病理性停顿会阻塞 drain —— 这与 `created` 分支原本就有的风险同类，本卡没有引入新的失败模式。

## 6. 维护预算与争用口径复核（F-A / F-B）

### 6.1 预算与末波 drain（追溯通过）

| 项 | 10 万 | 30 万 | 证据 |
|---|---|---|---|
| 重建次数 | `created`×1 + `refreshed`×18 + 末次补跑×1（共 20） | `created`×1 + `refreshed`×58 + 末次补跑×1（共 60） | `p4-02j-wave-*.json` 的每波 `scalar_index` + `scalar_index_pass` |
| 单次重建上限 | 51 ms（P4-02F 诊断） | 213 ms（P4-02F 诊断） | `P4-02F §7` |
| **末波** drain（含重建、含前台重叠） | 11 058.8 ms | 15 064.0 ms | `p4-02j-wave-*.json` `waves[-1].drain_ms` |
| 对照：修复前**末波** drain | 10 673.5 ms | 129 731.3 ms | `p4-02f-wave-*.json` `waves[-1].drain_ms` |
| 全部波次 drain 合计 | 128 791.8 ms | 554 019.4 ms | `drain_total.ms` |
| 对照：修复前合计 | 146 928.7 ms | 677 868.0 ms | `p4-02f-wave-*.json` |
| 索引字节 / Lance version | 707 126 B / version 521 / 500 fragment | 2 226 551 B / version 1 561 / 1 500 fragment | `p4-02j-stage0-*.json` `layout_*` |
| 对照 as-built 覆盖与字节 | 5 000/95 000（5%） | 5 000/295 000（1.67%）、33 513 B、version 1 525、1 523 fragment | `P4-02F §11/§12.1` |

结论：§3 的「11.06 s / 15.06 s」与「对照 10.67 s / 129.7 s」都是**末波** drain，口径一致
（P4-02D/F 用的也是同一个口径），比对成立；而且**合计 drain 也没有退化**（反而更快）。
**唯一需要改的是文字**：§3 的「实测 drain（含重建）」应写明是「**末波** drain（含前台重叠）」，
否则会被读成整次建库的 drain。

### 6.2 F-A：把中位比写成 P95 改善

| 档 | as-built 中位 / P95 | 修复后中位 / P95 | 中位比 | **P95 比** |
|---|---|---|---|---|
| 10 万 | 8 572.6 / 9 197.1 ms | 1 437.3 / 1 459.24 ms | **5.96×** | **6.30×** |
| 30 万 | 165 448.3 / 201 346.8 ms | 4 577.1 / 4 787.24 ms | **36.15×** | **42.06×** |

被审报告 §1 与提交 `757fb46` 写「**service P95 改善 5.9×（10 万）与 36×（30 万）**」；这两个比值实际是
**中位**比（8572.6/1437.3、165448.3/4577.1）。（`docs/development-status.md` 的 P4-02J 小节只列了原始
P95 数值 9 197.1 → 1 459.2 / 201 346.8 → 4 787.2 ms，没有比值 ⇒ 只需在报告与提交说明里改。）修法二选一：
①把数字改成 P95 比 **6.3× / 42.1×**；②保留 5.9×/36× 但把词改成「中位」。不得让「P95」与中位比同现。

### 6.3 F-B：§6 的「争用复测」不是争用测量

§6 的结论是：「独立仪器 30 万档前台 **P50 4 849.8 / P95 4 908.0 ms**，
**与同档空闲 P95 4 787.2 ms 几乎相同 ⇒ 覆盖完整时后台维护对前台的影响几乎消失**」。
主审核对 `tools/diagnostics/ann_scale_benchmark.py::concurrency_report` 与 `_foreground_searches`：
后台 drain 子进程启动后，前台先做 **1 次预热搜索**再做 `time.sleep(warm_seconds = 2.0)`，之后才开始
8 次采样；而被审 JSON 里后台 worker 自身的 `elapsed_ms = 1 469.9`。

**带时间戳的独立复跑**（同一函数、`scale=20000`，只给 `Popen`/`search_assets`/`_foreground_searches`
加时间戳，不改行为）：

| 事件（相对 worker 启动） | 时刻 |
|---|---|
| 前台进入 / 预热搜索开始 | 2.7 ms / 22.1 ms |
| **后台 worker 退出** | **1 670.5 ms** |
| **第一个被测前台样本开始** | **2 808.3 ms** |
| 最后被测样本 / 前台退出 | 6 487.4 ms / 6 930.5 ms |

⇒ `samples_started_before_worker_exit = 0`：**8 个被测样本没有一个与后台 drain 重叠**。
30 万档同构（`worker.elapsed_ms` 1 469.9 仍在 2.0 s 预热内结束，且该档预热搜索本身约 4.8 s，间隙更大），
所以 §6 的 4.91 s 是**该仪器自身夹具上的空闲前台**，不是争用结果。

**该保留的正确结论**：

- 真正的争用证据在 §4（容量工具 `foreground_during_drain`，`overlap_ms` 覆盖整个末波 drain）：
  30 万 **7 721.2 ms**（2 样本）对比**同一 root** 的空闲 P95 4 787.2 ms ⇒ **残余争用 ≈1.61×**；
  10 万 2 468.9 ms 对比空闲 1 459.2 ms ⇒ ≈1.69×。修复前的**同一口径**争用值是 129 705.6 ms（30 万，
  `p4-02f-wave-300k.json`）/10 663.2 ms（10 万，`p4-02f-wave-100k.json`）⇒ 争用前台本身改善
  **16.8× / 4.3×**。注意不要把修复前的争用值与修复后的空闲值相除：那是两个自变量
  （覆盖修复 + 争用/空闲），得不到可解释的系数。该 root 修复前的空闲 P95（201 346.8 ms）本身高于它的
  争用值，说明两轮测量的缓存/会话状态不可互换，本卡只能做同口径的前后对比。
- §6 的两仪器空闲值互相印证（4.91 s vs 4.79 s，不同夹具同量级），可以说「**前台成本由查询路径本身主导**」，
  但**不能**用它证明「维护争用几乎消失」。
- 修法：§6 的结论句与 `docs/development-status.md` 末尾「覆盖完整时后台维护对前台的影响几乎消失」
  改成上述 1.6×（或直接删除该结论，只保留「该仪器采样窗内无后台负载」这一事实）。

## 7. F-C：必须交付 3 的缺项

任务卡「必须交付 3」要求记录「**索引字节**、**表 version 变化**」，§3 表只有重建次数/单次上限/
累计预算/末波 drain/对照，缺这两项。数据已在手（见 §6.1 末两行），补两行/两列即可
（建议同时给 as-built 对照：33 513 B → 2 226 551 B、version 1 525 → 1 561）。

## 8. 受影响面判定与 Sol 归属

- 本改动落在**产品维护/索引生命周期**，属 AGENTS.md 的「DS-H 完整受影响面工程主审」范围，主审已完成
  （改动面、调用面、状态兼容、页不变、预算、caveat 反证、争用口径全部点名核对）。
- **不命中 V3.1 §2.2 六条 DS-MAX 条件（逐条）**：①无跨模块异常且根因未证（根因已用单变量 + 引擎目录项证到
  `create_index` 只覆盖建立当刻的行使后续行未索引）；②无 transaction/revision/undo 状态改动；③无「测试通过
  但代码与 ADR 对不上」（检索侧 ADR-0001～0012 与其伴随契约零 diff，`SUPPORTED_SCHEMA_VERSION` 仍 16，
  migrations 最大仍 `0016`）；④无一次修复引发的连锁回归（全量 0 失败 0 错误，合计 drain 反而更快）；
  ⑤diff 只有一处函数（+20/−3），不存在多状态机耦合的长推演；⑥Sol 阶段尚未启动 ⇒ 无 Sol 提出的反查需求。
  故**不建议升 DS-MAX**。
- **`SOL_NOT_REQUIRED`（按 V3.1 §2.3/§2.4）**：本卡不在 §2.3 的 Sol 必选清单（非 P4-04/P4-05/P4-08 发布边界，
  不冻结 §11 高风险协议，DS-H 未留 ADR/数据安全未决项 —— F-A/F-B/F-C 是报告陈述与交付缺项，不涉及
  契约或数据安全）。改动只改访问路径，页在**同进程前后**（本卡用例 + 主审探针）与**跨运行同夹具**
  （as-built 页 sha 复算一致、夹具摘要相同）两个层面逐项相同 ⇒ 满足 §2.3「DS-H 证明没有高风险语义变化」
  且任务卡「若证明只改访问路径且页逐项相同，可能记 `SOL_NOT_REQUIRED`」。**条件**：F-A/F-B/F-C 必须以
  文档修正闭合，并由一次只审修订面的短审确认；若闭合过程中改动扩到产品面，本判定作废、按 §2.4 重新扩大审查。
- 闭合 F-A/F-B/F-C 只需**只审修订面**的短审（点名校对三处文字与两行表格 + 重跑 `ruff`/`git diff --check`），
  不必重跑产品面复测；模型切换由用户在 Codex 中手动完成。

## 9. 非阻断观察（记录备查）

- **O-1**：`tests/integration/test_p4_02_capacity_validation.py:135` 断言
  `scalar["state"] in {"created", "present"}`。该用例的夹具是**单波**（`SCALE=150`、默认
  `--publish-wave 5000`），所以现在仍是 `created` 而通过；一旦该用例改成波次形状就会报 `refreshed`。
  建议顺手把 `refreshed` 纳入闭集（不是本次必须）。
- **O-2**：`ensure_scalar_index` 迭代目录两次（先取名字集合、再找同名项）；语义正确，只是可合并。
- **O-3**：`num_unindexed_rows` 属性缺失时 `getattr(..., 0)` 会被当作「完整」⇒ 保守方向（不重建），可接受。
- **O-4**：并发两个维护进程同时 `replace=True` 时，落败方走异常 ⇒ `unavailable`（安全降级）。
  本卡没有并发写与重建交互的实测，已在「未验证」项里，属如实记录。
- **O-5**：§2 表的「每次查询读盘」在**10 万修复后**一栏是「—」（其余三栏 3.67 GB / 28.75 GB / 0.69 GB），
  建议补上以便同表可比。
- **O-6（工作树事实，非本卡缺陷）**：`docs/development-status.md` 仍写着两份 P4-02I 未跟踪文件
  「不修改、不提交、不删除」，但 `tests/integration/test_p4_02i_a3_isolation.py` 与
  `tools/diagnostics/p4_02i_a3_isolation.py` **已不在工作树**（只剩 `__pycache__/*.pyc`）。
  这解释了 §3 的 14 例用例差；恢复 P4-02I 前需先确认这两份文件的去留（main 不含它们）。
- **O-7**：`test-results/` 与 `tmp/` 都是 gitignore 的本地产物，被审证据（`p4-02j-*.json`/`*.xml`）
  不被提交；本主审的探针与证据同样只留在本地（`tmp/p402j-review/**`、
  `test-results/p4-02j-review-*.{xml,json}`）。

## 10. 停止点与下一步

1. **本卡不予 PASS**：请 DS-H 实现侧完成 F-A（统计口径）、F-B（§6 结论与状态文件同句）、F-C（补索引字节与
   version）三处**文档面**修正，**不要改产品代码**（主审未发现产品缺陷，改代码反而会引入未审面）。
2. 修正提交后，只需**只审修订面**的短审：三处文字 + §3 两行表格 + `ruff check .`/`git diff --check`；
   若同时改了 `docs/development-status.md`，一并核对，无产品面改动即无需重跑 B 级全量。
3. 通过后 P4-02J 才可收口；再按用户路线进入「后台争用复测收口」（目标 ≤1.5 s，本卡后 30 万仍 7.72 s）
   与 **P4-03A 契约阶段**。**F2 不因 P4-02J 关闭**；**A3 仍未实现/未启用**；**§12.2 未改**；
   **P4-02F 仍非性能 PASS**。

## 11. 主审自跑的产物

- `test-results/p4-02j-review-focused2.xml`（11 / 0 / 0 / 0）、`test-results/p4-02j-review-full2.xml`
  （1715 / 0 / 0 / 48）
- `test-results/p4-02j-review-probes.json`（覆盖自愈探针、页 sha 复算、争用时序探针、预算与统计复算、
  工作树事实）
- `tmp/p402j-review/probe_scalar_index.py`、`tmp/p402j-review/probe_concurrency_overlap.py`
- 只读复核命令：`git diff --stat ad37a26..HEAD`、`git status --short --untracked-files=all`、
  `rg` 遍历 `src/**` 的 `scalar_index` 消费者、`uv run --no-sync ruff check .`、`git diff --check`、
  `uv lock --check`
