# P4-02V — 独立 DS-H 完整受影响面工程主审（新会话，2026-09-28）

**性质**：只读独立审查。本轮未改任何产品/测试文件、未改被审驱动与夹具；新增产物只有本报告，加上任务卡
[P4-02V](../tasks/P4-02V.md) 的状态行与 `docs/development-status.md` 的交接行。
**审查方式**：V3.1 §2.4「完整受影响面工程主审」——逐项覆盖全部改动文件、直接调用链、契约/ADR 差异与必需测试证据。
**结论**：**产品面 `ENGINEERING REVIEW PASS`**（实现正确、无回归、无不变式破坏，修复效果由本主审独立复现）。
另有 **1 项验收口径问题（F1）需用户裁决**（忠实形状的“真重叠样本数 16 < 卡片 §5.1 的 ≥30”），以及 2 条非阻断建议（F2/F3）。
**ADR/协议未决项：无** ⇒ `SOL_NOT_REQUIRED`（理由见 §6）。基线 `HEAD = 93a4a52`，工作树含 P4-02V 未提交改动。

## 1. 受影响面（逐项）

| 文件 | 变更 | 本主审判定 |
|---|---|---|
| `src/photo_search/search/documents.py` | `+71/−8`：新增 `CATCH_UP_BUSY_BUDGET_MS=100`；新增 `_bounded_catch_up_write`（当前连接临时收窄 `PRAGMA busy_timeout`，`finally` 恢复）；`catch_up()` 每批改走它，仅当 `is_busy_rejection` 成立时停止追赶并返回 `state=LAGGING`；返回字典新增 `deferred_batches` | 语义正确；见 §3/§4/§5 |
| `tests/integration/test_p2_search_projection.py` | 既有逐字相等断言补 `"deferred_batches": 0`（`+2`） | 属真实新增字段的同步，不是放宽断言 |
| `tests/unit/test_p4_02v_catch_up_contention.py`（新增） | 4 个用例：持锁下 `LAGGING`+`deferred_batches=1`+<2 s、维护预算恢复、非 busy 的 `CATALOG_UNREADABLE` 仍抛、服务层 `state=OK`+`search_projection.LAGGING` 且信封键逐键相同 | 覆盖本卡核心；见 §4 |
| `tools/diagnostics/p4_02u_lock_attribution.py`（诊断，非产品） | 新增 `seed-bulk`/`seed-queue`/`seed-reembed`/`arm-concurrent`/`--legacy`/`--text-only` 与按运行命名 | `--legacy` 与 diff 中删除的 pre-fix 算法**逐行一致**（本主审比对），before 证据可信 |

`catalog/**`、`indexing/**`、schema/迁移、依赖、`uv.lock`、§12.2、ADR/companion、错误码集合、信封/游标/total 均无 diff（`git diff --stat -- src tests uv.lock pyproject.toml` 仅 2 文件）。

## 2. 直接调用链与契约核对

- 三个调用者全部受益且**信封未变**：`search/service.py::_projection_block` 只取 `summary` + `state` + `processed_assets`；
  `search/query.py::search_summary` 逐键构造；`ocr/service.py::catch_up_projection` 原样透传（内部 API）。
  ⇒ 新字段 `deferred_batches` **不进入任何公开响应**（本主审逐处读码核对，非仅信报告）。
- 响应模型本就允许该状态：`api/models.py:290` `state: Literal["SYNCED", "LAGGING"]`，且注释写明 LAGGING 表示仍有排队资产待派生
  ⇒ 本次“拒绝 → 带滞后可服务”的改动**复用了已冻结的状态取值**，未新增字段/错误码。
- 契约文本核对：`p2-contracts.v1.json`、ADR-0003、ADR-0005 **均未规定** 搜索在投影追赶拿不到写锁时必须报
  `CATALOG_UNREADABLE`；主架构 §4.5.3 反而要求「目录 revision 与持久待投影标记在短事务提交、**大范围投影分批派生**、
  查询期间从 SQLite 读最新名称、不在长事务逐图重写全库」⇒ 本卡是把实现改回该要求，而不是改承诺。
- §12.2 后台档「不得因内部争用拒绝合法验证请求」未改，本卡正是为满足它。

## 3. 独立验证（本主审自己跑）

| 项 | 本主审结果 | 与实现者对照 |
|---|---|---|
| 点名单测/集成（自选文件：`p4_02v` 单测 + `p2_search_projection` + `p2_search_documents` + `p2_search_upload`） | **62 / 0 / 0**（`test-results/p4-02v-review-focus.xml`） | 实现者 66/0/0（不同文件集） |
| B 级全量 `pytest -q tests/unit tests/integration` | **1787 / 0 / 0 / 48，240.97 s**（`test-results/p4-02v-review-full.xml`） | 实现者 1787/0/0/48，281.8 s |
| `ruff check .` / `uv lock --check` / `git diff --check` | 全绿（exit 0） | 一致 |
| **反例：before（`--legacy`）** | 既有向量重投影 30 000 + 纯文字 + 1 客户端：**20 样本 / 13 真重叠 / 5 拒绝**（全部落在 drain 窗口内，`test-results/p4-02v-review-before-legacy.json`） | 实现者同形状 40/35/7 |
| **反例：after（本卡实现）** | 同夹具同形状：**20 样本 / 14 真重叠 / 0 拒绝**，全部返回页面且耗时 237–298 ms（`test-results/p4-02v-review-after-fixed.json`） | 实现者 60/55/0 |
| 驱动证据复核 | 实读 `test-results/p4-02v-*.json`：before 22/32、after 0/16（并发发布形状）；before 7/35、after 0/55（纯文字形状）；after5 子进程 8/8 重叠 0 拒绝 | 与报告表格一致 |

## 4. 前后行为等价与“不丢项”

- **同一投影状态 ⇒ 同一页面**：`catch_up` 成功路径（相同批大小、相同事务边界、相同返回值语义）逐字沿用；
  失败路径只是“不再把拒绝抛给调用者”。单测第四条同时断言：降级时**只命中已派生的资产**（不伪造、不半写），
  并断言信封键集合与已同步基线**逐键相同**。
- **不静默丢项**：`search_projection_queue` 行在降级时原样保留（单测断言 `queue_depth` 不变），放锁后同一批资产被补齐
  （`SYNCED`、`processed=1`、documents 1→2）。本主审独立 after 跑中 14 个重叠样本全部 `LAGGING` 且页面正常返回。
- **真正不可恢复的错误仍抛出**：`is_busy_rejection` 只匹配 busy 三类文本；单测用「connection is closed」验证
  非 busy 的 `CATALOG_UNREADABLE` 继续抛出，错误码不变。

## 5. 发现

### F1（验收口径，需用户裁决；非产品缺陷）
卡片 §5.1 要求“修复后 0 拒绝，且每个延迟场景 ≥30 个有效样本”。实现者的**最忠实形状**（发布新嵌入 + 全量检索 + 4 并发）
after 段只有 **16 个真重叠样本**（drain 窗口 109 s、单样本 32–37 s 的客观限制）；满足 ≥30 的是**纯文字形状**
（55 真重叠 / 0 拒绝）。本主审独立复跑把纯文字形状又加了 20 样本 / 14 重叠 / 0 拒绝，
但**没有**为忠实形状补到 30（需 6 万+ 事件、约 12 min 注入）。
判定：**不构成产品缺陷或数据安全风险**（同一 `catch_up`+写锁路径，全量 128 个样本 / 93 次真重叠 0 拒绝），
但卡片字面验收在该形状上未满足 ⇒ 请裁决：①接受“纯文字形状 ≥30 + 忠实形状 16”的口径，或
②另立一张纯测量卡把忠实形状补到 ≥30（不属本卡范围）。

**裁决（用户，2026-09-28）：采纳①——接受“纯文字形状 ≥30（实现者 55、主审追加 20/14）+ 忠实形状 16”的口径。**
因此 F1 关闭为**口径接受**，不再是未决项：本卡的验收口径 = 纯文字形状满足 ≥30 真重叠且 0 拒绝、忠实形状记录 16 个真重叠且 0 拒绝；
忠实形状补测**不再**作为本卡前置，若要 ≥30 由后续纯测量卡决定。

### F2（非阻断，分层/耦合建议）
`search/documents.py` 现在 `from photo_search.indexing.maintenance import is_busy_rejection`：**读路径依赖 drain 模块**
（并传递依赖 `indexing.store`、`scheduler.projection`）。本主审核对导入图**无环**、模块级无 lancedb/pyarrow 原生导入，
因此不是缺陷；但 `is_busy_rejection` 是通用 SQLite busy 分类器，更合适的位置是 `catalog/errors.py` 或 `catalog/sqlite_io.py`。
建议后续（非本卡）迁移，避免读路径继续依赖维护模块。

### F3（非阻断，行为取舍需记录）
100 ms 有界等待意味着：**非 drain 的短时写者**（例如用户自己的地点/人物改动、扫描批次）若让交互式追赶等不到锁，
搜索会直接以 `LAGGING` 返回，而不是等到最多 5 s 后追赶成功。用户可见差别 = 刚改动的文档可能这一次还没进文字投影
（队列持久、状态可观测、下一次追赶补齐）。本主审认为这与 §4.5.3「短事务 + 分批派生 + 如实报告滞后」一致、
且不新增任何错误语义，故不阻断；但这是**产品行为取舍**，如需对外承诺“搜索必然反映刚完成的编辑”，需另立契约。

### F4（已核查并排除，并发副作用）
`_bounded_catch_up_write` 在 `Catalog.write()` 取得进程内写锁**之前**就收窄了连接级 `PRAGMA busy_timeout`。
本主审专门探针检查“同连接上另一个在途事务能否观察到 100 ms”：
`sqlite3` 默认 `check_same_thread=True` ⇒ 跨线程使用同一 Catalog 连接直接 `ProgrammingError`（已复现打印），
`Catalog.write` 又拒绝嵌套，且 `api/routes.py` 的 56 个路由处理器全为 `async def`（单事件循环线程）
⇒ **该交错不可达**；`finally` 恢复亦经单测（5000 → 5000）与本主审探针（探针最终读回 5000）双重确认。
建议把这条“为什么不可达”的理由写进 `_bounded_catch_up_write` 的 docstring（当前只写了“会恢复”）。

### F5（已核查，证据机制）
`tools/diagnostics/p4_02u_lock_attribution.py::install_legacy_catch_up` 与本次 diff 删除的 pre-fix 算法**逐行等价**，
因此报告里的 before 数字是同一驱动、同一夹具、同一 drain 入口下的真实对照；驱动已按上一轮审查要求按运行命名输出。

## 6. Sol 必要性判定

无 ADR/companion 文本改动、无错误码/信封/游标/`query_hash` 改动、无 §11 清单中的数据安全不变量改动（未触
SQLite 约束/迁移/lease/revision/幂等/outbox/space 隔离/人工人物地点状态/profile 激活/备份恢复）。
响应模型本就含 `SYNCED|LAGGING`，且 §4.5.3/§12.2 要求本卡所做的方向 ⇒ 按 V3.1 §2.3 记 **`SOL_NOT_REQUIRED`**
（理由：DS-H 已证明无高风险语义变化、无未决 ADR/数据安全项；F1 是验收口径问题、F2/F3 是非阻断建议）。
若用户希望把 F3 的“搜索必然反映刚完成的编辑”提升为对外承诺，那属于新的契约诉求，应另立卡而不是用 Sol 追认本卡。

## 7. 未验证 / 不授权

- 未测真实中文 CLIP 文本塔 / `POST /api/v1/search` / 浏览器首屏 / GTX 1660 / 冷缓存 / 长时 soak / 40 万档。
- 未授予 **§12.2 后台档（P95 ≤ 8 s）达标**：本卡只关闭「不得因内部争用拒绝合法验证请求」这一条；
  4 并发 + drain 下全量检索 30 s 量级是既有性能问题（P4-02J/K 已知），不在本卡。
- 未评估 `IndexCoordinator.schedule_projection_refresh`（实现者观察 ~69.6 ms/asset）等维护侧开销；属后续卡。
- 本报告未改产品/测试/schema/依赖/ADR；新增证据在 `test-results/`、`tmp/`（忽略）。

## 8. 复现命令（本主审实际执行）

```powershell
uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit/test_p4_02v_catch_up_contention.py `
  tests/integration/test_p2_search_projection.py tests/integration/test_p2_search_documents.py `
  tests/unit/test_p2_search_upload.py --junitxml=test-results/p4-02v-review-focus.xml      # 62/0，exit 0
uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration `
  --junitxml=test-results/p4-02v-review-full.xml                                            # 1787/0/0/48，exit 0
uv run --no-sync ruff check . ; uv lock --check ; git diff --check                          # 全 exit 0
python tools/diagnostics/p4_02u_lock_attribution.py seed-bulk --events 30000 --output test-results/p4-02v-review-seed-before.json
python tools/diagnostics/p4_02u_lock_attribution.py arm-concurrent --events 30000 --samples 20 --clients 1 --text-only --legacy --output test-results/p4-02v-review-before-legacy.json
python tools/diagnostics/p4_02u_lock_attribution.py seed-bulk --events 30000 --output test-results/p4-02v-review-seed-after.json
python tools/diagnostics/p4_02u_lock_attribution.py arm-concurrent --events 30000 --samples 20 --clients 1 --text-only --output test-results/p4-02v-review-after-fixed.json
python tmp/p402v-review/busy_timeout_window_probe.py                                        # 跨线程使用同一连接 ⇒ ProgrammingError（F4 证据）
```
