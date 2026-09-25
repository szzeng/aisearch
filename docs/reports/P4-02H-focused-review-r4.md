# P4-02H — 按机制门禁修复的聚焦审核

**结论：暂不通过最终冻结。** 被审提交 `6cd526e`，工作树起点干净；本轮只读审核 ADR-0012 §3.3/§4/§6、companion `CertificateRule`、新增 M3 用例与直接主审证据，未改产品或冻结契约。

## 已收敛部分

统一物理行集相等已从 M2/M3 移走；M1 要全物理集等价，M2 要排序前过滤集等价，M3 可容纳多余行并须停止证书。`C_strict=C_sql` 与发布前窗口复核仍是公共门禁。上一轮指出的“统一门禁使 M3 无法处理多余行”在结构上已修正。

## 阻断 1：M3 停止规则否定自己的正例

ADR §3.3 第 73 行及 §4 第 96 行都要求“**所有未检查或已被排除的行**在 `(distance, asset_id)` 全序上都不可能排在第 K 行之前”。但 ADR §6 第 11 行明确允许：未确认 A 距离 **0.10**、已确认 B 距离 **0.20**、`top_k=1`，M3 排除 A 后发布 B。A 已被排除且在全序中排在 B 前；按目前文字，该证书必然失败。新增 `test_gate_is_per_mechanism_and_m3_covers_the_extra_engine_row_state` 实际**不检查被排除的 A**，注释还明确说已检查且失格的行不阻断；测试实现与冻结文字相反。

最小修正：停止证书必须证明**未检查的潜在合格行**无法排在第 K 个合格行之前；已检查、经同一权威视图判为不合格的行可更近，但其失格判据及发布前有效性必须可证。或明确选择更保守规则并撤销 §6 第 11 行的 M3 正例。两种选择不能同时保留。

## 阻断 2：机器契约允许机制与门禁错配

companion `CertificateRule` 把 `mechanism` 和新增 `engine_side_gate` 分别作为两个独立枚举，未建立一对一关系；reference model `decide` 只核查 `holds_at_publication`，也不校验配对。直接调用现有 `contract_errors`，证书 `{mechanism: M1_EQUIVALENT_SNAPSHOT, engine_side_gate: M3_STOP_CERTIFICATE_OVER_ENGINE_STREAM, holds_at_publication: true, evidence: ...}` 被 `A3Request` **零错误接受**。这使机器契约可以把不满足 M1 全物理集相等的状态伪装成 M1 证书。需在 companion 与用例中把 M1/M2/M3 各自绑定到唯一门禁，并拒绝交叉组合；测试同时覆盖正配和错配。

## 直接一致性与主审输入

- ADR §6 第 1 行称多余引擎行下 “M1/M2 不可用”；新增用例 `gate_holds` 在同一状态且 `pushdown_proven=True` 时断言 **M2 为真**，符合 ADR §3.3 的下推等价定义。请把第 1 行写成 “M1 不可用；M2 只有证明排序前下推等价时可用；M3 只有停止证书时可用”，避免矩阵与机制表相反。
- 已核对 `test-results/p4-02h-r3-contracts.xml`：12 / 0 / 0 / 0；`p4-02h-r3-full.xml`：1710 / 0 / 0 / 48。本轮未重跑。绿色用例未覆盖上述错配，且 M3 测试采用的规则与 ADR 原文不同。
- 当前可见的最新整体主审为 `P4-02H-a3-contract-review-r2.md`，被审基线 `2900a0a`；既有 `P4-02H-fix-and-handoff.md` §4 压缩交接包仍写基线 `77557db`，改动文件与结论也未覆盖这次按机制修订。`6cd526e` 改了 ADR、companion、契约用例之后，仓库尚无点名该提交的独立主审报告或对应的更新交接包。按 P4-02H 卡面，修订面需补独立主审后再终审；不能将 r2 的 PASS 自动覆盖到 r3。用户若已在别处完成主审，需将可核对的基线、改动面和验证结果写回任务报告。

## 停止点

本轮不修改 ADR/companion/用例，不授予最终冻结。A3 未实现、未启用，§12.2 未改，P4-02F 非性能 PASS。DS-H 修正上述直接边界并补 r3 独立主审后，只短审修订条款与相应正反例。
