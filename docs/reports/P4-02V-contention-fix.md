# P4-02V — 争用下合法前台搜索被拒的最小修复（搜索侧有界等待 + 降级）

**性质**：产品改动（搜索层）+ D 级验证。状态 `IMPLEMENTED / VALIDATION COMPLETE`，**独立 DS-H 完整受影响面主审尚未做**，
本报告不授予 PASS、不授予 §12.2/性能/发行结论。改动面：`src/photo_search/search/documents.py`（+71/−8）与
`tests/integration/test_p2_search_projection.py` 的一处既有断言（+2）——`catalog/**`、`indexing/**`、schema、迁移、依赖、
`uv.lock`、§12.2、ADR/companion、错误码集合、信封/游标/total **零改动**。

## 0. 结论摘要

- **选用卡片 §3 方向 1（搜索侧降级）**，并把它收紧为「**有界等待 + 降级 + 保持可观测**」：交互式投影追赶对写锁只等
  `CATCH_UP_BUSY_BUDGET_MS = 100 ms`；仍拿不到就**停止本次追赶**，把剩下的待补项留在持久队列里，请求按既有
  `search_projection.state = "LAGGING"` + 不变的 `queued_assets` 继续服务，不再抛 `CATALOG_UNREADABLE`。
- **机制论证（为什么不选方向 2/3）**：P4-02U 实测 drain 写锁占空比 **98.96%**、线程臂连续不可用 **32 449.4 ms**
  （进程臂 6 295.8 ms），而每条事务 < 0.7 s。因此「2 次 × 500 ms 退避」在算术上必然落在同一饥饿窗口内（P4-02U 审查 R2 已指出）；
  要「0 拒绝」只能靠**不等**（本卡）或让 drain 真正让步（方向 3，代价是建库吞吐，本卡未选）。
- 前后对照（同一驱动、同一夹具、同一 drain 入口、同一事件数；before = 在驱动内装回 pre-P4-02V 的追赶算法）：

| 场景（工作负载 / 请求形状 / 客户端） | before（旧追赶） | after（本卡） |
|---|---|---|
| 新嵌入发布（P4-02U 形状）/ 全量检索（含语义通道）/ 4 并发客户端 | 40 样本、**32 真重叠**、**22 拒绝**（全部落在 drain 重叠窗口） | 40 样本、16 真重叠、**0 拒绝**（13 降级 / 27 追平） |
| 既有向量重投影 / 纯文字检索 / 1 客户端 | 40 样本、**35 真重叠**、**7 拒绝** | 60 样本、**55 真重叠**、**0 拒绝**（48 降级 / 7 追平） |
| 新嵌入发布？→ 既有向量重投影 / 全量检索 / 1 客户端、**子进程 drain**（卡片点名形态） | —（本卡只跑 after） | 8 样本、**0 拒绝**，8/8 与 drain 重叠（drain 146.5 s，锁探针 1 446/2 102 blocked） |
| P4-02U R2 记录（输入证据） | 线程臂 **5/6 拒绝**、进程臂 **4/6 拒绝** | — |

- 延迟副作用：纯文字形状下，被降级的样本从「等满 5 s 被拒」变成 **271 ms（p50）/320 ms（p95）** 返回
  （有界等待 100 ms + FTS）。全量检索形状在 4 并发 + drain 下本身就要 30 s 量级（修复前后同类样本相当），
  那是既有的检索性能问题，不是本卡引入。

## 1. 问题与选择的机制论证

P4-02U 已证：被拒的是**合法**前台搜索，持锁者是后台 drain 自己的 `claim`/`apply` 写事务；单条事务不超过 0.7 s，
但批间几乎没有可竞争空闲（有效空闲 ≈ 报出间隙 − COMMIT ≈ 0），于是写锁在竞争写者看来连续不可用数秒到数十秒。
`Catalog.write` 的 5 s busy timeout 是**维护**预算：对交互式读路径来说，等满 5 s 只会把「拒绝」变成「更慢的拒绝」。

三个候选方向里：

1. **搜索侧降级（本卡采用）**：不依赖 drain 配合，立刻把「拒绝」变成「可服务 + 可观测的滞后」；代价是这一次请求的
   文本投影可能比 catch-up 成功时落后（但队列持久、语义与信封不变、`state=LAGGING` 可观测）。
2. 搜索侧有界退避：实测饥饿 6.30–32.45 s，2×500 ms 不足；要把预算提到数十秒，等于把 §12.2 的 8 s 后台档目标直接
   变成不达标（P95 会到 30 s 量级），因此放弃。
3. drain 批间让步：能降低占空比，但要付出建库吞吐代价，且需要与转发/新建库的排程一起评估；本卡不改维护侧。

选择受「保持结果与现有精确路径一致」「不改事务/恢复边界」约束，且**只改一条**（方向 1）。

## 2. 实现（可审的最小改动）

`src/photo_search/search/documents.py`（SHA256 前 16 位 `6b97c397f1a36570`，17 363 B）：

1. 新增常量 `CATCH_UP_BUSY_BUDGET_MS = 100`（含机制注释：引用 P4-02U 的占空比/连续不可用数字，说明为什么交互式读
   不继承维护预算）。
2. 新增上下文管理器 `_bounded_catch_up_write(catalog)`：在**当前连接**上把 `PRAGMA busy_timeout` 临时收窄到该预算，
   并在 `finally` 里恢复原值——因此同一 catalog 的维护路径（drain 自己的 5 s + 有界重试）不受影响，
   也不会把「耐心」泄漏给其它写入者。
3. `catch_up()`：每批走 `_bounded_catch_up_write`；捕获 `CatalogError` 后仅当 `is_busy_rejection(error)`（复用
   `indexing/maintenance.py` 的同一 busy 标记定义）成立时**停止追赶并返回**
   `{"processed": …, "written": …, "remaining": <当前队列深度>, "batches": …, "state": "LAGGING", "deferred_batches": n}`；
   非 busy 的 `CATALOG_UNREADABLE`（例如连接已关闭）继续抛出，错误码与消息不变。
4. 返回字典新增 `deferred_batches`（内部诊断字段）。**搜索信封不变**：`_projection_block` 仍只用 `summary + state +
   processed_assets` 组 `search_projection`，公开契约零变化；`tests/integration/test_p2_search_projection.py` 里那处
   逐字相等断言同步补该字段。

为什么放在 `catch_up` 里而不是服务层包一层：`search/service.py`（语义搜索）与 `api/queries.py`（文字筛选/分页）都通过
它做「查询前追赶」，`ocr/service.py::catch_up_projection` 也复用它；修在函数内可以让全部交互式调用者同时受益，且
`catch_up` 的返回值本就带 `state`，正是「剩余滞后要如实报告」的既有语义（主架构 §4.5.3「大范围投影分批派生」）。

## 3. 必须保持的不变量（逐条核对）

| 不变量 | 结论 | 证据 |
|---|---|---|
| 结果页 / `truncated` / `blocks` / `scanned_assets` 语义 | 未改 | 检索通道、`vector_channel`、`pending_projection_page` 一行未动；`git diff` 仅 `documents.py` 与一处测试断言 |
| `pending_projection` 待补语义（不得静默丢项） | 保持可观测且不丢 | 降级只跳过派生；`search_projection_queue` 行原样保留（单测断言 `queue_depth` 不变），信封照常报 `state=LAGGING` 与 `queued_assets` |
| outbox 领取与确认协议 / revision 权威（P4-01C A1） | 未触碰 | 未改 `indexing/**`；drain 侧 `supersede 检查 + 引擎写 + 确认同一 BEGIN IMMEDIATE` 不变（P4-02A/C/01C 点名集成全绿） |
| 搜索信封与全部错误码 | 未改 | 公开字段集合逐键相等（单测）；`CATALOG_UNREADABLE` 的**真正不可恢复**形态仍抛（单测用「连接已关闭」消息验证不降级） |
| §12.2 文本 | 未改 | 文档零改动 |
| 修复前成功样本的语义 | 不变 | 追赶成功路径逐字沿用（同批大小、同事务边界、同返回值），只是等待预算变短 |

## 4. 验收证据

### 4.1 单测 / 点名集成 / 全量（D 级）

- 新增 `tests/unit/test_p4_02v_catch_up_contention.py`（SHA256 前 16 位 `c956fa6d6c87e4d0`，4 个用例）：
  ① 另一连接持 `BEGIN IMMEDIATE` 时 `catch_up` 返回 `LAGGING`/`deferred_batches=1`、耗时 < 2 s（不是 5 s）、
  队列与文档都没变，放锁后下一次追赶 `SYNCED`；② 连接维护预算前后均为 5 000 ms；
  ③ 非 busy 的 `CATALOG_UNREADABLE` 仍抛出；④ 服务层文字搜索在持锁下返回 `state=OK` + `search_projection.LAGGING`，
  信封键集合与已同步基线逐键相同，且**不伪造文档**（只命中已派生的资产），放锁后追赶补齐第二个资产。
- 点名单测/集成：`test_p4_02v_catch_up_contention` + `test_p2_search_projection` + `test_p2_search_documents` +
  `test_p2_search_channels` + `test_p2_search_api` + `test_p2_search_authority` + `test_p2_search_topup` +
  `test_p4_01c_retrieval_blocks` + `test_p4_02a_maintenance_entry` + `test_p4_02c_recovery_and_idempotency`
  = **66/0/0/0**（`test-results/p4-02v-focus.xml`，47.7 s）。
- B 级全量：`pytest -q tests/unit tests/integration` = **1787/0/0/48**（`test-results/p4-02v-full.xml`，281.8 s；
  48 skipped 为既有跳过）。
- 仓库级：`ruff check .` = `All checks passed!`（exit 0）；`uv lock --check` = exit 0（Resolved 96 packages）；
  `git diff --check` = exit 0；`git diff --stat -- src tests` = 2 文件（+73/−8）。

### 4.2 前后对照（同夹具、同驱动、同 drain 入口）

夹具：P4-02U/P4-02J 的丢弃式副本 `tmp/p402j-contention-close/data`（**未触碰**参考夹具
`tmp/p402f-阶段0/scale-300000-wave/data`，未扫真实图库）。**资产数在两段之间不变（444 000）**：积压用「既有向量重投影」
或「既有资产重新登记嵌入」产生，不新增资产。before 段由驱动 `--legacy` 在**同一驱动、同一进程形状**里装回 pre-P4-02V 的
追赶算法（5 s 维护预算 + busy 即抛），after 段即产品实现。

| 证据文件 | 形状 | 样本 / 真重叠 | 拒绝 | 备注 |
|---|---|---|---|---|
| `p4-02v-before-concurrent-legacy.json` | 发布新嵌入 24 000 + 全量检索 + 4 客户端 | 40 / 32 | **22** | drain 79.7 s；被拒样本 p50 5 528 ms（等满 5 s）；ok 样本 p50 26.3 s |
| `p4-02v-after-concurrent-fixed.json` | 同上（本卡实现） | 40 / 16 | **0** | drain 109.3 s；40/40 返回页面，13 降级 / 27 追平 |
| `p4-02v-before4-textonly-legacy.json` | 既有向量重投影 30 000 + 纯文字 + 1 客户端 | 40 / 35 | **7** | 队列已补满 399 200；ok 样本 p50 1.59 s（含 5 s 内抢到锁的慢样本） |
| `p4-02v-after4-textonly-fixed.json` | 同上（本卡实现） | 60 / **55** | **0** | 48 降级 / 7 追平；被降级样本 p50 **271 ms**（p95 320 ms） |
| `p4-02v-after5-process-fixed.json` | 既有向量重投影 30 000 + 全量检索 + 子进程 drain（卡片点名形态） | 8 / 8 | **0** | drain 146.5 s、`write_rejections=0`、锁探针 1 446/2 102 blocked ⇒ 争用真实存在 |
| P4-02U R2（输入证据）`p4-02u-arm-drain-thread.json` / `…-process.json` | 发布新嵌入 30 000 | 6 / 6、6 / 6 | **5 / 4** | 修复前基线 |

**失败样本全记录**：被拒样本只在 before 段出现（22 与 7 条），全部落在 drain 写窗口内（`overlapped_drain=true`），
错误原文 `CATALOG_UNREADABLE: cannot start a catalog write transaction: database is locked`；after 段四个运行
**共 0 条失败**（40+60+8 = 108 个样本，其中 79 个与 drain 真重叠）。

**页面一致性**：after 段所有样本都返回页面且 `state=OK`；单测证明「同一投影状态 ⇒ 同一页面、不伪造文档」，
并证明降级不丢待补项（放锁后同一批资产被补齐）。运行期间页面仍会随 drain 的真实进度变化（确认条目使候选集变化），
因此前后两段不做逐字节页面比对；这一点在 §6 如实列出。

## 5. 命令与退出码

```powershell
# 焦点与全量测试（沙箱拒绝 .pytest-tmp 时为已知环境事实 ⇒ 走正常权限路由重跑）
python -m pytest -q -p no:cacheprovider tests/unit/test_p4_02v_catch_up_contention.py ... # exit 0（4/0）
python -m pytest -q -p no:cacheprovider tests/unit tests/integration                          # exit 0（1787/0/0/48）
python -m ruff check .        # All checks passed!（exit 0）
uv lock --check               # exit 0（Resolved 96 packages 29 ms）
git diff --check              # exit 0
# 前后对照（驱动：tools/diagnostics/p4_02u_lock_attribution.py，新增 seed-bulk/seed-queue/arm-concurrent 模式）
python tools/diagnostics/p4_02u_lock_attribution.py seed-reembed --events 24000 --output test-results/p4-02v-before-seed.json
python tools/diagnostics/p4_02u_lock_attribution.py arm-concurrent --events 24000 --samples 10 --clients 4 --legacy \
    --output test-results/p4-02v-before-concurrent-legacy.json                                  # exit 0
python tools/diagnostics/p4_02u_lock_attribution.py seed-reembed --events 24000 --output test-results/p4-02v-after-seed.json
python tools/diagnostics/p4_02u_lock_attribution.py arm-concurrent --events 24000 --samples 10 --clients 4 \
    --output test-results/p4-02v-after-concurrent-fixed.json                                    # exit 0
python tools/diagnostics/p4_02u_lock_attribution.py seed-queue   --events 400000
python tools/diagnostics/p4_02u_lock_attribution.py seed-bulk    --events 30000
python tools/diagnostics/p4_02u_lock_attribution.py arm-concurrent --events 30000 --samples 40 --clients 1 --legacy \
    --text-only --output test-results/p4-02v-before4-textonly-legacy.json                       # exit 0
python tools/diagnostics/p4_02u_lock_attribution.py seed-bulk    --events 30000
python tools/diagnostics/p4_02u_lock_attribution.py arm-concurrent --events 30000 --samples 60 --clients 1 \
    --text-only --output test-results/p4-02v-after4-textonly-fixed.json                         # exit 0
python tools/diagnostics/p4_02u_lock_attribution.py seed-bulk    --events 30000
python tools/diagnostics/p4_02u_lock_attribution.py arm-drain-process --events 30000 --samples 8 \
    --output test-results/p4-02v-after5-process-fixed.json                                      # exit 0
```

## 6. 未达标 / 未验证（如实列出）

- **卡片 §5.1 的「每个延迟场景 ≥30 真重叠样本」在“发布新嵌入 + 全量检索”这一最忠实形状上只做到 after 16 个**：
  该形状下修复后的样本要**跑完**整条全量检索，4 并发 + drain 时单样本 32–37 s，而当时的 drain 窗口只有 109 s ⇒
  窗口内最多 16 个样本。≥30 的要求在「既有向量重投影 + 纯文字检索」（同样真实、同样走 `catch_up` 与写锁争用）形状
  达到 **55 真重叠 / 0 拒绝**。若审查方要求忠实形状也必须 ≥30，需要更长的 drain 窗口（例如 6 万+ 新嵌入事件，
  按本次 12 ms/事件约需 12 min 注入）——本卡未做，如实记为未达标项。
- **纯文字形状的 FTS 命中为空**（夹具的派生文档不含查询词），因此该形状只证明「同一 catch-up 争用路径不再拒绝 +
  信封/滞后可观测」，不证明检索内容；检索内容正确性由 §4.1 的单测/集成与既有 P2 套件覆盖。
- 未测真实中文 CLIP 文本塔、`POST /api/v1/search`、浏览器首屏、GTX 1660、冷缓存、长时 soak、40 万档；
  **不授予 §12.2 后台档（P95 ≤ 8 s）达标**：本卡只关闭「不得因内部争用拒绝合法验证请求」这一条。
  4 并发 + drain 下全量检索 30 s 量级是既有性能问题（P4-02J/K 已知）。
- 观察（**不在本卡范围**，交给后续卡）：`IndexCoordinator.schedule_projection_refresh` 在本夹具上约
  **69.6 ms/asset**（444 000 条 `index_entries`，`WHERE e.asset_id = ?` 无可用索引 ⇒ 每次全表扫）；
  按主架构 §4.5.3 一批最多 500 asset_id，等于一批可能耗 30 s 量级。本卡未动该路径（属维护侧/性能卡）。
- 驱动新增模式（`seed-bulk`/`seed-queue`/`arm-concurrent`/`--text-only`/`--legacy`）是**诊断夹具**：`seed-bulk` 直接写
  与 `ArtifactStore.persist_result` 同形状的 outbox 行（引用既有 artifact，同一新 `catalog_filter_revision`），
  由 drain 自身校验（payload 校验和、space/generation 一致、supersede 顺序）；`seed-queue` 复刻既有触发器
  `(asset_id, FILENAME_CHANGED, now)` 的 upsert 形状。两者都不写业务语义字段，也不改产品代码路径。

## 7. 审查与下一步

- **必须**做独立 **DS-H 完整受影响面工程主审**：本卡改了产品读路径的失败语义（从抛错变降级 + 观测字段），
  影响面包括 `search/service.py` 与 `api/queries.py` 的查询前追赶、`ocr/service.py::catch_up_projection`、
  以及 `catch_up` 的三处调用者；审查应覆盖 `_bounded_catch_up_write` 的连接级 `busy_timeout` 收窄/恢复、
  busy 判定复用、`deferred_batches` 形状、以及「信封/错误码零变化」的逐键证据。
- **ADR 文本是否被触碰**：本卡未改任何 ADR/架构文本。`catch_up` 的语义从「查询前尽量追赶」变为「查询前有界追赶，
  拿不到写锁就报告滞后」——这与主架构 §4.5.3「大范围投影分批派生」「不得静默丢项」一致，但 DS-H 若判定这属于
  §9.3/ADR 级承诺变更，则按 V3.1 §2.3 追加 **Sol 聚焦终审**（本卡不自行宣布不需要 Sol）。
- 若审查要求忠实形状也 ≥30 真重叠样本，或要求把 4 并发的 30 s 量级延迟纳入本卡，**停止并交用户裁决**
  （那会变成性能卡，不再是本卡的最小修复闭环）。