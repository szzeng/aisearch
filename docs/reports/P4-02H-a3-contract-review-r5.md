# P4-02H — A3 按机制门禁修订面的独立主审（r5）

**结论：修订面主审不予 PASS —— 1 处判据级冲突需裁决、2 处面级陈述需修正、1 处点名断言缺口。**
被审基线 `9f5f14d`（含 `2900a0a`、`6cd526e` 的累积修订面；点名提交 **`6cd526e`** 与 **`9f5f14d`**）。
本轮**只读**：未改产品、ADR、companion、契约用例，只新增本报告与状态条目。
维持 **A3 未实现/未启用**、**§12.2 未改**、**P4-02F 非性能 PASS**。

本报告按用户点名的 **4 项边界**（按机制门禁表 / §6 第 11 行 / 配对约束 / 两条新增用例）做修订面主审，
不重复 `P4-02H-a3-contract-review-r2.md`（基线 `2900a0a`）的 7 项完整受影响面；未重跑 B 级全量。

## 1. 被审文件与独立复跑

| 文件 | SHA-256 前 16 位 |
|---|---|
| `docs/decisions/ADR-0012-a3-global-candidate-contract.md` | `FA18D768061E29CD` |
| `docs/decisions/contracts/p4-a3-contracts.v1.json` | `2D2FEB8D6F9393BD` |
| `tests/unit/test_p4_02g_a3_contracts.py` | `D1E54E29704502A4` |
| `docs/reports/P4-02H-fix-and-handoff.md`（交接包） | `4C3BB20072629FCF` |

- 契约用例（本轮独立复跑）：**13 / 0 / 0 / 0**（`test-results/p4-02h-review-r5-contracts.xml`）。
- 独立探针（不改仓库任何冻结面）：`tmp/p402h-review/probe_r5.py` ⇒
  `test-results/p4-02h-review-r5-probes.json`（复现 §3–§6 的每一条结论；`tmp/` 与 `test-results/` 均被忽略）。
- `ruff check .`、`git diff --check`、`uv lock --check` 全 exit 0；开工时 `git status --short` 为空。
- **未重跑 B 级全量**（本轮零产品/契约改动；最近一次为 r4 的 1711 / 0 / 0 / 48，其中第二次复跑 0 失败）。
  **未做**等价性实测、A3 P50/P95、回退率/回退时延（属冻结后的隔离实验卡）。

## 2. 四项点名边界的逐项结论

| # | 点名边界 | 结论 | 关键证据 |
|---:|---|---|---|
| 1 | 按机制门禁表（ADR §3.3 表 + §4 表 + §6 第 1/9 行 + companion 枚举） | **通过** | 三处表与 companion 的 const 配对逐项一致；第 1 行已按 r4 要求写成分机制可用性；§3.3/§4 的 M1/M2/M3 定义互不冲突（正面证据见 §8.1） |
| 2 | §6 第 11 行（多余引擎行 + M3 停止证书） | **不通过（判据级冲突需裁决）** | 第 11 行的正例与第 1 行「真实 `after_lance` 窗口」、第 2 行「`PENDING_REPAIR`」是**同一物理状态**，但第 2 行是无条件回退 ⇒ 同一状态被两行相反裁决（§3）；可执行模型同时把两种读法都留在场（§4） |
| 3 | 配对约束（companion `CertificateRule` + 参考模型 `decide`） | **部分通过** | 名称级一对一已成立：3 组正配全部被接受、6 组交叉全部被 schema 拒绝（独立字段级复核）；但 `decide` 完全不读 `mechanism` ⇒ 状态级门禁未绑定（§4），且 `evidence` 非必填（§7 O-1） |
| 4 | 两条新增用例 | **需修正（1 处断言缺口 + 1 处与模型不符）** | r3 用例的 `gate_holds` 正确按机制建模，但未覆盖第 11 行自身写明的负分支「失格判据不可证/发布前失效 ⇒ 扩大或回退」（§6）；两条用例都没有交叉检验 `decide`/`STATE_MATRIX` 的按机制可用性（§4） |

## 3. F-A（判据级，需裁决）：§6 第 1 行 / 第 11 行 与 第 2 行 对同一状态给出相反决策

**状态（同一物理状态，三条路径都指向它）**

1. ADR §6 第 1 行自称「**含真实 `after_lance` 窗口**」，其状态文本写明「条目未 `CONFIRMED` **或已降为
   `PENDING_REPAIR`**（`process_next(failpoint="after_lance")` 的实际状态）」；决策列允许
   「**M3 只在停止证书成立时可用**」。
2. 第 2 行的状态文本就是「`index_entries = PENDING_REPAIR`（**引擎可能仍留旧行**）」，决策列是**无条件
   `⇒ 回退`**。
3. 第 11 行（本轮点名）把「引擎有多余、已检查且判为不合格的行 + 有效 M3 停止证书」判为
   **M3 可用、页 = `{B}` = oracle**。其状态未给出「A 的条目必须不是 `PENDING_REPAIR`」的限定。

仓库自身证据表明真实窗口恰好落在第 2 行的状态：`tests/integration/test_p2_search_topup.py:75` 与
`:119` 断言 `after_lance` 之后 `entry_state == "PENDING_REPAIR"`；`_CONFIRMED_BLOCK_SQL` 只选
`state = 'CONFIRMED'`（`src/photo_search/search/retrieval.py`），故该资产只在引擎侧留下多余行。

同一重叠也被契约用例的矩阵自己写明：`tests/unit/test_p4_02g_a3_contracts.py:40-45` 的
`unconfirmed_row` 权威状态是「entry not CONFIRMED (**the publish path left it PENDING_REPAIR**)」，
而 `:46-51` 的 `pending_repair` 权威状态是「`index_entries.state = PENDING_REPAIR`」——两个 case key
描述的是同一权威状态，却一个属于允许 M3 的第 1 行族、一个属于无条件回退的第 2 行。

**可执行证据**（同一有效 M3 证书，`tmp/p402h-review/probe_r5.py`）：

```
decide(case="pending_repair")                          -> FALLBACK_COMPLETE_PATH    (第 2 行的读法)
decide(case="unconfirmed_row")                         -> FALLBACK_COMPLETE_PATH
decide(case="extra_engine_row_with_m3_certificate")    -> A3_WITH_CERTIFICATE       (第 11 行的读法)
```

三条记录描述的物理状态相同（`C_sql = C_strict = {B}`、引擎另有不在两集内的 A、M3 证书成立），
决策却相反。实现方无法从冻结面判断「`PENDING_REPAIR` 资产仍留引擎行时到底走第 2 行还是第 11 行」。

**两个可能的闭合方向（都属判据级，须裁决）**

- 方向甲：把第 2 行改为按机制的条件回退（「回退，除非该机制的引擎侧门禁成立」，同第 1/11 行）
  ⇒ 这是对已冻结行的**放宽**；
- 方向乙：把第 1/11 行限定为「多余行所属条目**不是** `PENDING_REPAIR`」，并从第 1 行撤掉
  「含真实 `after_lance` 窗口」 ⇒ 真实窗口只剩回退，**三机制可行性陈述退化为两机制**，
  而 §3.3 已明说这属「需另裁的语义变化」。

按 `docs/tasks/P4-02H.md`「若主审判定判据不可实现或需放宽 ⇒ 停止，写清冲突与最小反例，交回用户裁决」，
本轮**在此停止**：F-A 不由主审自行挑选方向，请用户/后续 ADR 级裁决。
最小反例即上面的三行 `decide` 输出，不需新造夹具。

## 4. F-B（面级，需修正）：可执行参考模型不读机制，与 §3.3 按机制门禁表相反

`tests/unit/test_p4_02g_a3_contracts.py` 的参考模型 `decide` 先从 `STATE_MATRIX[case]` 取**烘焙好的决策**，只用
`mechanism` 做名称配对（`GATE_BY_MECHANISM`），**从不检查该机制的引擎侧门禁在该行状态是否成立**
（探针：`decide` 的代码名表里没有 `mechanism` 之外的判定分支；`decide_reads_mechanism_at_all=false`）。后果：

| 输入 | §3.3/§4 要求的结论 | 现有模型结论 | 差异 |
|---|---|---|---|
| 第 11 行状态 + **M1** 证书 | M1 不可用（物理集 `{A,B}` ≠ `C_strict={B}`） | `A3_WITH_CERTIFICATE` | **相反** |
| 第 1 行状态 + **M3** 停止证书 | M3 可用（第 1 行明文） | `FALLBACK_COMPLETE_PATH` | **相反** |

即 r4 的「一对一」只做到**名字级**配对，状态级门禁仍靠 `STATE_MATRIX` 里按行烘焙的决策；而 r4 恰恰把
第 11 行原有的「**M1/M2 在同状态必须回退**」一句删掉了（`git show 9f5f14d -- .../ADR-0012...md` 第 11 行
diff），使这条约束在冻结文字里也不再显式。两条新用例都没有覆盖这两个分支（r3 用例只调 `gate_holds`，
r4 用例全部走 `positive_control_certificate` 行）。

**最小修正（不放宽任何判据）**：① 第 11 行恢复「M1/M2 在该状态必须回退」的显式条款；② 让
`decide`（或新增一条断言）在取行决策前先按 `mechanism` 求值 `gate_holds(...)`，把「M1 × 多余行 ⇒ 回退」
与「M3 × 停止证书 ⇒ 允许」两条分支写成可执行断言。

## 5. F-C（面级，需修正）：冻结面/交接包里仍有「多余引擎行使证书失败」的旧陈述

| 位置 | 现文 | 与冻结面的冲突 |
|---|---|---|
| `tests/unit/test_p4_02g_a3_contracts.py:512` | `# the extra engine row fails M1/M2/M3` | §3.3 第 73 行与第 11 行都明确「M3 不要求行集相等」，该注释把三机制写成一种门禁（该注释由 `2900a0a` 引入，r3/r4 未同步） |
| `docs/reports/P4-02H-fix-and-handoff.md:106` | 「证书因多余引擎行失败 ⇒ 回退」（F2「真实窗口」条目） | 未带按机制限定；交接包是 Sol 的主要输入（历史状态节 3317/3320 行同族表述同理） |

修法只需加限定语（例如「M1 因多余引擎行失败；M3 须以停止证书替代行集相等」），不改任何判据。

## 6. F-D（点名断言缺口）：第 11 行自己的负分支没有可执行断言

ADR §6 第 11 行末句冻结了三条失败路径：「若未检查行下界更近，或 **A 的失格判据不可证/发布前失效**，
则必须继续扩大或回退」。r3 用例的 `gate_holds` 只建模了距离下界（`uninspected_distance_lower_bound >
kth`，且用严格 `>`，与「下界**劣于** 0.20」一致——这两处经复核**正确**），**没有**建模
「失格判据是否可证」（探针：`new_test_models_disqualification_provability=false`，
而 `adr_requires_unprovable_disqualification_to_expand_or_fallback=true`）。
即：把 A 换成「已检查但失格判据不可证/发布前失效」时，模型仍会判证书成立，与冻结文字相反。

**最小修正**：给 `gate_holds` 增加 `disqualification_provable` 入参并断言其为 `False` 时证书不成立
（连带断言该状态回退）。属补断言，不放宽判据。

## 7. 非阻断观察（供 Sol 裁决，不构成本轮阻断）

- **O-1 `evidence` 非必填**：三个证书变体的 `required` 均为
  `["mechanism", "engine_side_gate", "holds_at_publication"]`（companion :119/:130/:142），无 `evidence`
  的证书**零错误**通过；而 ADR §4 该列列名就是「**必须的证据**」。注意 r4 在 r3 用例体内把这条
  `required` 列表**写进了断言**（`git log -S` 指向 `9f5f14d`），故关闭它需要同时改用例。`$defs.CertificateMechanism` / `$defs.EngineSideGate` 在 r4 后
  已无任何 `$ref`（孤儿定义），其中 `CertificateMechanism` 枚举无任何用例校验，存在漂移面。
- **O-2 `certificate` 非必填、且与 `decision` 未绑定**：`A3Request.required` 不含 `certificate`
  （companion :187），故 `{decision: "A3_WITH_CERTIFICATE", certificate: null}` 零错误通过。JSON Schema
  子集内可用 `oneOf`+`const` 表达，参考模型 `decide` 已覆盖；若要求机器记录自洽，需裁决是否补。
- **O-3 M2 的模型与文字不同**：r3 用例的 M2 分支写作 `pushdown_proven and c_strict == c_sql`，
  把「公共门禁」当成 M2 的引擎侧门禁；ADR §3.3 的 M2 要求是「**过滤后行集 == `C_strict`**」。
  实验卡实现 M2 时必须按 ADR 文字，不能照抄该分支。
- **O-4 第 11 行与 `STATE_MATRIX` 的状态文本用词不同**：ADR 说「已检查且判为不合格」，用例写
  `engine also holds an unconfirmed row A (nearer)`。「unconfirmed」与「判为不合格」在本文语境等价，
  但建议统一用词，以免与第 1/2 行的 `unconfirmed_row` 混淆。

## 8. 已核实为一致的正面证据（本轮独立复核）

1. **§3.3 表 / §4 表**：M1 = 全物理行集 == `C_strict`；M2 = 已证明等价的下推谓词过滤后行集 ==
   `C_strict`（全物理集可多余）；M3 = 不要求行集相等 + 可判定停止证书；公共门禁只有
   `C_strict == C_sql` + 发布前窗口复核。两表与 §6 第 1 行的「M1 不可用 / M2 需下推等价证明 /
   M3 需停止证书」一致，未发现判据放宽。
2. **配对约束（名称级）**：独立复核 3 组正配（`M1↔M1_PHYSICAL_SET_EQUALS_C_STRICT`、
   `M2↔M2_PUSHDOWN_RESTRICTED_SET_EQUALS_C_STRICT`、`M3↔M3_STOP_CERTIFICATE_OVER_ENGINE_STREAM`）
   全部零错误；6 组交叉组合全部被 `A3Request` 拒绝；§3.3 第 76–78 行的配对文字与 companion 的 `const`
   逐字一致。
3. **一致性三件套**：ADR §6 恰 11 行、编号 1–11 连续；companion `StateMatrixRow.case` 枚举与
   契约用例 `STATE_MATRIX` 键集完全相等；`EngineSideGate` 枚举 == 三组配对值。
4. **停止规则的严格方向**：`(distance, asset_id)` 全序下，未检查行的距离下界必须**严格劣于**第 K 行
   （下界相等即可能被更小 `asset_id` 的未检查行先于第 K 行），第 11 行「劣于 0.20」与用例的严格 `>`
   一致，属正确收紧而非放宽。

## 9. 未验证 / 不在本轮范围

- 未重跑 B 级全量与 P4-02F 性能基准；未做 A3 等价性（`Recall@K=1.000` 对 oracle）、五类注入计数、
  回退率与回退时延 —— 均属冻结通过后的隔离实验卡。
- 未评估 M2/M3 构造成本、`PENDING_REPAIR` 的真实回退率（同 r2 主审 §6 的未验证项）。
- r2 主审的 7 项完整受影响面（基线 `2900a0a`）本轮未重复；本轮只覆盖 r3/r4 的修订面
  （`6cd526e`、`9f5f14d`）。是否据此认定卡面「独立 DS-H 完整受影响面」前置已满足，请 Sol/用户确认。

## 10. 最小闭合清单与停止点

1. **F-A（判据级）**：请用户/ADR 裁决「第 2 行 vs 第 1/11 行」谁优先；主审不自行选择方向。
2. **F-B**：恢复第 11 行「M1/M2 在该状态必须回退」条款；把 `decide`/断言改为按机制求值门禁。
3. **F-C**：修 `test_p4_02g_a3_contracts.py:512` 注释与交接包 :106（+ 历史状态节同族表述）加按机制限定语。
4. **F-D**：给 `gate_holds` 增 `disqualification_provable` 并断言不可证 ⇒ 不成立/回退。
5. **O-1/O-2/O-3/O-4**：交 Sol 裁决是否纳入本次冻结（均不必放宽 §2–§7）。

上述闭合完成后只短审 F-A 的裁决结果与 F-B/F-C/F-D 的修订面，再做 **Sol high 聚焦终审**；
冻结通过前**不生成隔离实验卡、不立 A3 实现卡，不实现、不启用 A3**。模型切换由用户在 Codex 中手动完成。
