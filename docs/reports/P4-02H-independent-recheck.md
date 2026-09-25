# P4-02H §5 独立复跑 — `scope_condition` 恒真性 / revision 窗口 / 交接包 §4 三项

- 日期：2026-09-25
- 被审基线：`7ecd363`（工作树干净，`SUPPORTED_SCHEMA_VERSION = 16`）
- 复跑范围：交接包 §5 点名的三项 —— ① 真实 `scope_condition` 的**全部字段**恒真性核对；
  ② `vector_channel` 的 revision 窗口；③ 交接包 §4 的「待裁决高风险问题」三项。
- **只读复跑**：本轮不改 `src/**`、不改 ADR-0012 / companion / 契约用例、不启用 A3、不改 §12.2、
  不接触真实照片库。新增物只有 `tmp/p402h-recheck/**`（探针）与 `test-results/p4-02h-recheck*.json`。
- 复跑物证（`test-results/` 为本机产物目录，不入库）：`p4-02h-recheck.json`、
  `p4-02h-recheck-contracts.xml`、`p4-02h-recheck-window-tests.json`。

## 0. 被审文件指纹（SHA-256 前 16 位）

| 文件 | 前 16 位 |
|---|---|
| `docs/decisions/ADR-0012-a3-global-candidate-contract.md` | `719A5E93CCDB8B4B` |
| `docs/decisions/contracts/p4-a3-contracts.v1.json` | `805BED2C71BF63C3` |
| `tests/unit/test_p4_02g_a3_contracts.py` | `0368B525506B8A10` |
| `src/photo_search/search/retrieval.py` | `E337752B74FEA839` |
| `src/photo_search/indexing/store.py` | `AF19998C5E9D9A0C` |
| `src/photo_search/places/filter.py` | `43F528B96AAE9925` |

## 1. 结论摘要

1. **F1 的核心命题成立且更完整**：以**真实** `scope_condition` 在**真实迁移库**上跑
   `SearchScope` 八个字段的 240 组交叉（168 组可评估），**只有全中性一处得到恒真 `"1"`，
   与 ADR §2.2 的六字段判据零反例**；`place_state` 确实是唯一「列表为空但谓词非恒真」的字段
   （§2）。
2. **F2 的判据方向正确，但前提描述与现行状态机不符**：现行 `after_lance` 崩溃窗口里
   资格条目**已经**是 `PENDING_REPAIR`（因此在 `C_sql` 之外），真实窗口形状是
   「引擎有行、oracle 没有」，**不是** ADR §3.3/§6 第 11 行写的「CONFIRMED 条目 × 更新的引擎行」；
   后者需要一次**越序引擎写入**才构造得出（§3）。这是**前提/表述缺陷，不是安全漏洞**——
   回退义务仍覆盖真实窗口，但 `C_strict` 的逐行 revision 比较在现行状态机下是**空转**的。
3. **交接包 §4 ①**：现行产品**不存在**跨库栅栏/版本对（`LanceVectorIndex._table` 取最新表、
   无 version pin），M1 需要新机制；实测「读全代每行 revision」成本 ≈ **一次整代 pass**
   （20 000 行 36.4–37.6 ms），是候选块路径的 1/6.5，但叠在 A3 自身的 0.37 s 上对 §12.2 仍偏紧
   （§4.1）。
4. **交接包 §4 ② 无法在实现前测量**：可测的只有**静态资格面**（168 组 scope 形状中仅 1 组合格），
   真实回退率必须留给隔离实验卡（§4.2）。
5. **交接包 §4 ③ 已答**：`place_state` 之外无同类字段（§2.4）。

本轮**不**授予 P4-02F 性能 PASS，**不**冻结 ADR，**不**立 A3 实现卡。状态仍为
`ENGINEERING REVIEW PASS (revised) / SOL FOCUSED REVIEW PENDING`，并把本文作为 Sol high 聚焦终审的
**独立输入**。

## 2. 复跑物 A：`scope_condition` 全字段恒真性

### 2.1 方法（不复用起草方的单字段用例）

1. 字段清单**从真实 dataclass 取**（`SearchScope.__dataclass_fields__`），不手写：
   `source_id / availability / place_ids / include_descendants / place_state / person_ids /
   person_match / capture` 共 8 个。
2. 在**真实迁移库**（真实 schema + 真实 `places` 行 + 真实 `taken_time_rules_version = 1`）上，
   对 6 个资格字段做**全交叉**：`source_id∈{None,1}` × `availability∈{any,present,missing}` ×
   `place_ids∈{(),(p,)}` × `place_state∈` 全部 5 值 × `person_ids∈{(),(1,)}` × `capture∈{None,set}`
   = 240 组。
3. 判据**由真实谓词决定**：调真实 `scope_condition()`，只有返回字面量 `"1"` 才算恒真；
   与 ADR §2.2 的（`source_id is None` ∧ `availability=="any"` ∧ `place_ids` 空 ∧
   `place_state=="any"` ∧ `person_ids` 空 ∧ `capture is None`）逐组比对。
4. 拒绝（`CatalogError`）**算「不资格」而非「恒真」**，不掩盖为通过。

### 2.2 结果

| 指标 | 值 |
|---|---|
| 组合总数 | 240 |
| 被真实代码结构化拒绝 | 72（`place_ids` 非空 × `place_state∉{any,assigned}`） |
| 实际评估 | 168 |
| 判为恒真（`predicate == "1"`） | **1** |
| 与 ADR §2.2 判据**不一致**的组 | **0** |

单字段偏离时真实谓词（节选，完整见 JSON）：

| 输入 | 真实谓词 |
|---|---|
| 全中性 | `1` |
| `source_id=1` | `a.asset_id IN (SELECT l.asset_id FROM file_locations AS l WHERE l.source_id = ?)` |
| `availability=present`/`missing` | `EXISTS(...availability='PRESENT')` / `NOT EXISTS(...)` |
| `place_state=assigned` | `EXISTS(... ap.mode='MANUAL_ASSIGNED')` |
| `place_state=unassigned` | `NOT EXISTS(... ap.mode='MANUAL_ASSIGNED')` |
| `place_state=manual_unassigned` | `EXISTS(... ap.mode='MANUAL_UNASSIGNED')` |
| `place_state=suggested` | `NOT EXISTS(assigned) AND EXISTS(place_resolutions … CANDIDATES …)` |
| `place_ids=(p,)`（含 `include_descendants=False`） | `EXISTS(... ap.place_id IN (?))` |
| `person_ids=(1,)` / `(1,2)+all` | 基于 `MEMBER_PREDICATE` 的 `IN (…)`，`all` 为逐人 AND |
| `capture` 已设（规则版本匹配） | `EXISTS(... m.taken_at_sort >= ? AND < ?)` |
| `capture` 已设（规则版本不匹配） | `0`（不可满足，非恒真） |

### 2.3 两处**保守方向**的观察（不构成漏洞）

- `CaptureRange()`（非 `None` 但两个键都未设）的谓词是 `"1"`，而 ADR 把该字段记为 `capture is None`：
  这是**过度拒绝**（更安全）。协议侧不可达：`parse_capture_range` 在两个边界都缺省时返回 `None`。
- `person_match="all"` 而 `person_ids` 为空时**被拒绝**（`INVALID_PERSON_FILTER`），
  不会静默放宽为「无筛选」。`include_descendants=False` 单独出现时谓词仍为 `"1"`（`place_ids`
  为空时它不改变结果集），它也不是 ADR §2.2 的资格字段。

### 2.4 交接包 §4 ③ 的回答

在 8 个字段中，**只有 `place_state`** 属于「列表/入口为空但真实谓词仍非恒真」这一类：
`person_ids` 为空时 `person_condition` 直接短路返回 `"1"`（纯函数、不触库），`place_ids` 为空时的
贡献由 `place_state` 决定，其余字段无「空入口」语义。**F1 的修复边界是完备的**，无需再扩字段。

## 3. 复跑物 B：`vector_channel` 的 revision 窗口

### 3.1 方法（真实代码全链路，不复用起草方的替身索引）

在真实夹具库（真实 JPEG + 真实扫描 + 真实调度器 + **真实 Lance 引擎** + 真实 SQLite 权威）上：

1. `index_vector` 让湖照片的投影**完全确认**（条目与引擎行同 revision）；
2. 用一次真实**事实变更**（地点指派，`catalog_filter_revision` 前进）触发该资产的**重投影**，
   再用文档化的 `IndexCoordinator.process_next(failpoint="after_lance")` 制造崩溃窗口；
3. 崩溃后重放同一事件；
4. 另外 5 个**可达交织步骤**（第二批资产的事实变更 + 批量 `process_batch(failpoint="after_lance")`
   崩溃 → 批量重放 → 第三资产事实变更 → `audit_and_schedule_repairs` + 重放），
   每步计算 `C_sql`（真实 `_CONFIRMED_BLOCK_SQL` 选择集）与 `C_strict`（引擎行唯一 + space 匹配 +
   行 `projection_revision` = 条目 `confirmation_revision`）；
5. 最后**手工构造** ADR §6 第 11 行的字面状态（越序引擎写入）并再次跑真实 `vector_channel`。

### 3.2 结果

| 状态 | `index_entries` | 引擎行 rev | `C_sql` | `C_strict` | 真实 `vector_channel(top_k=1)` |
|---|---|---|---|---|---|
| 基线（已确认） | `CONFIRMED` rev 0 | 0 | `[3]` | `[3]` | `[A(rev0)]` |
| 事实变更排队后 | `PENDING_REPAIR` rev 1 | 0 | `[]` | `[]` | `[]` |
| **`after_lance` 崩溃窗口** | `PENDING_REPAIR` rev 1 | **1** | `[]` | `[]` | `[]` |
| 重放后 | `CONFIRMED` rev 1 | 1 | `[3]` | `[3]` | `[A(rev1)]` |
| 字面 §6 第 11 行（越序构造） | `CONFIRMED` rev 1 | **2** | `[3]` | `[]` | **`[A(rev2)]`** |

可达交织 5 步的 `C_sql == C_strict` **每步成立**（`invariant_held_at_every_step = true`）；
「字面第 11 行」那一步的差集是 `[3]`，且真实 `vector_channel` 仍返回 rev2 的更近行 ——
即**起草方的最小反例在该状态下可复现**。

### 3.3 关键差异：真实窗口不是 §3.3/§6 描述的那个

`after_lance` 崩溃窗口里，被重投影的资产**已经**是 `PENDING_REPAIR`，因此**根本不在 `C_sql` 里**；
真实窗口的形状是「**引擎有行、oracle 没有**」，即 §6 第 1/2 行，而不是第 11 行的
「`CONFIRMED` 条目 × 更新的引擎行」。这不是推理猜测，而是本轮实测 + 仓库自身用例的同一结论
（`tests/integration/test_p2_search_topup.py` 的崩溃窗口用例断言 `entry_state == "PENDING_REPAIR"`，
本轮以同等断言复跑通过）。

机制上的原因是**写入次序**（符号级定位）：

1. 任何为同一 `(space, asset, generation)` 发布新产物的路径，都会先把条目降为 `PENDING_REPAIR`
   并提交：`src/photo_search/artifacts/store.py:378`（产物发布）与
   `src/photo_search/indexing/store.py:404/1187/1233`（`_mark_entry_pending_repair`，
   `_schedule_existing` 的两个分支；唯一例外是同 revision 事件已 `CONFIRMED` 的早退，该分支本就不改状态）；
2. **产品链路**的引擎写入只有两处，且都在**同一写事务**内：`src/photo_search/indexing/store.py:596`
   （单事件）与 `:775`（批量；`upsert` 只是 `upsert_batch` 的包装，见 `:151`），紧随的确认语句把
   `confirmation_revision` 置为**刚写入行的同一 revision**（`:606`、`:803`）。
   （`tools/diagnostics/**` 有自己的基准写入，但不在产品链路，也不改权威条目。）
3. `record_id = f"{generation}:{asset}"` 的 `merge_insert` 实测**每代每资产只留一行**（ADR §3.2
   第 1 条成立），因此不存在「旧行 + 新行并存」。

⇒ 「条目 `CONFIRMED`（rev R）而引擎行 rev ≠ R」需要**越序引擎写入**（本探针第 5 步做的正是这件事）。

### 3.4 对 ADR/用例的影响（本轮只报告，不改冻结面）

- ADR §3.3 的举例「例如 `after_lance` 崩溃窗口」与 §6 第 11 行的 `engine_state`/`authority_state`
  描述**与实际状态机不符**；`test_real_oracle_is_revision_blind_in_the_after_lance_window`
  的**断言**（该状态 ⇒ 回退）正确，但它构造的**不是** `after_lance` 窗口，而是需要越序写入的状态。
- 安全面**未**受影响：真实窗口（§6 第 1/2 行）本来就要求回退，回退义务照旧。
- 但 `C_strict` 的「行 revision = 确认 revision」比较在现行状态机下**没有可命中对象**，
  它当前是**防御性冗余**。建议 Sol 终审在两条路线中裁决：
  (a) 保留 `C_strict` 作为防御性超集，但**修正 §3.3 的举例与 §6 第 11 行的状态描述**，并说明
  真正的保护来自写入次序（需另立收紧卡）；或 (b) 用「写入次序不变式 + 代级栅栏」替换逐行比较
  （成本见 §4.1）。**两条都属于冻结面修改，必须走 §11 的变更控制，本轮不自行改动。**

### 3.5 残余风险（必须随结论一起读）

上述是**推理 + 5 步可达交织 + 1 步合成构造**的证据，**不是机器检查的不变式证明**。未覆盖：
generation `RETIRED`/复用、引擎表被外部删除或替换、多 space 同代、手工改库、
`STALE` 复兴路径的更多交织；也没有形式化/穷举检查。因此这是一个**对前提的挑战（供 Sol 裁决）**，
不能当作已完成的证明。

## 4. 复跑物 C：交接包 §4 的三项高风险问题

### 4.1 ① `C_strict` 的跨库同窗口证明（M1）与 M2/M3 的构造成本

**栅栏现状**：产品里**没有**可用的跨库栅栏/版本对。`LanceVectorIndex._table`
（`src/photo_search/indexing/store.py:134`）总是 `open_table` 取**最新**版本，无 version pin；
`store.py` 全文没有任何 Lance version/timestamp 读取；SQLite 侧读走独立快照。
因此 M1 的「可验证写者栅栏 + 同窗口 ID/revision 集合相等证明」需要**新增机制**，
不是现有能力的组合。

**证书核对的量级**（本轮实测，真实 Lance 表）：

| 项 | 20 000 行（dim=2 合成行，真实引擎/真实路径） |
|---|---|
| 读全代每行 `(record_id, asset_id, space_id, projection_revision)` | **36.4 / 37.6 ms**（≈ 一次整代 pass） |
| 真实 `vector_channel(top_k=20, block=5 000)` | 226.9 / 231.6 / 253.9 ms（4 块） |
| 路径 / 证书 | **≈ 6.5×** |

读法（含口径提醒）：证书核对**比它所加速的候选块路径便宜一个量级**，这与「一次全代扫描」
（P4-02F：30 万档单次 pass 347 ms、A3 无过滤一次全局查询 ≈0.37 s）叠加后说明：
**逐行 revision 核对本身不便宜到可以忽略** —— A3 若要求每查询做一次行级 M1 核对，
≈0.37 s + ≈0.35 s 已在 §12.2 的 1 s 预算内几乎用尽（尚未含真实文本编码/HTTP）。
更可行的形态是**代级栅栏**（例如「该代无未确认 outbox 事件」这样一次 SQLite 查询）
加上 §3.3 的写入次序不变式，而不是每查询一次整代 revision 读。
本表是 dim=2 合成向量（真实 512 维下路径的传输成本更高），因此**只用于同一夹具内的相对比较**，
不得外推为 30 万档结论。

**M2/M3**：仓库内**没有任何**下推等价或停止证书的实现与基准，本轮也无法给出构造成本评估。
结论：**未评估**，不得按「已有依据」看待。

### 4.2 ② 回退率

- **可测的静态面**：本轮 240 组 `scope_condition` 交叉中**只有 1 组**合格（168 组可评估里 1 组，
  ≈0.6%）；其余输入按 §2.2 一律走完整路径。这给出的是**资格面**，不是负载回退率。
- **不可测的部分**：A3 未实现，因此「无筛选查询中因状态/窗口而回退」的比例没有载体可测；
  它依赖真实混合负载与真实文本塔，属隔离实验卡的必测指标（ADR §7.3）。
- **本节能提供的方向性输入**：§3 表明「行 revision 不一致」驱动的回退在现行状态机下**几乎不可命中**
  （该状态不可达），而真实会命中的是 §6 第 1/2 行的「引擎有额外未确认行」窗口 ——
  也就是说回退率取决于**维护/发布与查询的并发密度**，而不是 revision 比较。
  结论：**未验证，实现前不可测**，不得写成「回退率低」。

### 4.3 ③ `place_state` 之外的同类字段

见 §2.4：**没有**。`person_ids`（纯短路）、`place_ids`（由 `place_state` 决定）之外
不存在「空入口但谓词非恒真」的字段；`person_match="all"` + 空 `person_ids` 是显式拒绝。

## 5. 本轮验收（客观结果）

| 项 | 命令/产物 | 结果 |
|---|---|---|
| 契约用例 | `pytest tests/unit/test_p4_02g_a3_contracts.py` → `test-results/p4-02h-recheck-contracts.xml` | **11 / 0 / 0 / 0** |
| 仓库自身窗口用例（2 例） | `tmp/p402h-recheck/run_window_tests.py` → `test-results/p4-02h-recheck-window-tests.json` | **2 / 2 PASSED** |
| 复跑探针 | `tmp/p402h-recheck/recheck.py` → `test-results/p4-02h-recheck.json` | exit 0 |
| `ruff check .` | — | exit 0 |
| `git diff --check` | — | exit 0 |
| `uv lock --check` | — | exit 0 |
| `git status --short` | — | 空（`src/**`、ADR、companion、契约用例零 diff） |

**未做**：B 级全量（`tests/unit tests/integration`）本轮**未重跑**（无产品代码改动；
最近一次全量为 P4-02H 的 **1709 / 0 / 0 / 48**，本轮未复核其有效性）。

### 5.1 环境限制（如实记录）

本沙箱内 `Path.mkdir(mode=0o700)` 生成的目录随后**无法枚举**（`WinError 5`）；pytest 的
`tmp_path` 工厂与 `--basetemp` 正是以 `mkdir(mode=0o700)` 建根（`_pytest/tmpdir.py`），
因此**任何用 `tmp_path` 的 pytest 用例在本沙箱内都会 setup 报错**（本轮已最小复现：
`tmp/p402h-recheck/mk700b`）。这不是产品缺陷，也未因此修改任何 ACL；§5 的两条窗口用例改用
**同一测试函数体、同一断言**的直接调用方式复跑（`run_window_tests.py`）。
未使用 pytest 的 JUnit 时，相应证据为 `p4-02h-recheck-window-tests.json`。

## 6. 独立性限制

- 本轮是**另一个会话、另一份探针**（不从起草方的 `p4_02g` 用例复制断言，而是重算 `C_sql`/`C_strict`
  与谓词交叉表），但**同一个 Codex 宿主**；由哪个模型执行需用户按 Codex 实际会话记录，
  本文不冒称「另一模型」。
- 本轮只复跑点名项，未做完整受影响面主审；P4-02H §5 要求的「独立 DS-H 完整受影响面审查」
  仍以既有报告为准，本文是其**补充输入**，不替代主审，也不冒称卡面指定的 **Sol high 聚焦终审**。

## 7. 未验证项与停止点

- **未验证**：A3 与 oracle 的 `Recall@K=1.000` 等价性；五类注入的真实前后计数；A3 端到端 P50/P95
  （含真实文本编码/HTTP/回退率/回退时延）；真实中文 CLIP 权重；GTX 1660；冷缓存与长时 soak；
  M2/M3 的构造成本；§4.2 的真实回退率。
- **停止点**：状态停在 `ENGINEERING REVIEW PASS (revised) / SOL FOCUSED REVIEW PENDING`。
  **Sol 冻结通过前不生成 A3 实现卡，也不立隔离实验卡**。
- **交由 Sol/DS-H 裁决的输入**：(a) §3.4 的 ADR §3.3/§6 第 11 行表述修正与 `C_strict` 的去留；
  (b) §4.1 的 M1 形态（逐行核对 vs 代级栅栏 + 写入次序不变式）；(c) §5.1 的环境限制是否影响
  「必须交付」的 JUnit 形式要求。任何冻结面修改都必须走 ADR §11 变更控制，本轮不自行改动。
