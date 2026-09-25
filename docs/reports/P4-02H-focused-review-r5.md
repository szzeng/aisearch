# P4-02H — F-A 方向甲与 r5 修订面的聚焦短审

**结论：暂不通过最终冻结。** 被审提交 `73b7232`，开工时工作树干净。本轮只审 F-A 方向甲、F-B/F-C/F-D 修订面及 r5 的 O-1～O-4 纳入判断；未改 ADR、companion、契约用例或产品代码。

## F-A / F-B：方向甲的第 1/2 行已对齐，第 11 行仍与 M2 相冲突

§6 第 2 行已从无条件回退改为按机制判断：`PENDING_REPAIR` 的多余引擎行使 M1 失败，M2 需证明排序前下推等价，M3 需停止证书与失格判据。这与第 1 行和 §3.3 的方向甲一致。`MECHANISM_AVAILABILITY` 也使 M1 × 多余行回退、M3 × 第 1/2 行可用、防御性行回退；这些直接分支通过。

但第 11 行在同一物理状态写「**M1 与 M2 在该状态必须回退**」，其状态列未限定「没有已证明的 M2 下推谓词」。最小反例：`C_sql=C_strict={B}`、引擎物理行 `{A,B}`、A 较近且未确认，另有已证明在排序前只返回 `{B}` 的 M2 谓词。§3.3、第 1/2 行及现有 `gate_holds(... pushdown_proven=True)` 允许 M2，然而第 11 行和 `MECHANISM_AVAILABILITY["extra_engine_row_with_m3_certificate"]` 禁止 M2。直接调用 `decide`：同一 M2 证书在 `unconfirmed_row` 得到 `A3_WITH_CERTIFICATE`，在 `extra_engine_row_with_m3_certificate` 得到 `FALLBACK_COMPLETE_PATH`。

需把第 11 行的「M2 回退」明确限定为**本例未证明下推谓词**，并使 case 状态/参考模型体现这个限定；若要对所有多余行无条件禁用 M2，就会反向修改 §3.3 与第 1/2 行，不能作为文字修补处理。方向甲本身可保留。

## F-C：旧的统一失败陈述尚未清完

新用例注释和交接包 §4 最小反例已改成分机制说法。但 ADR §3.3 的真实 `after_lance` 说明仍写「回退由……证书的额外行失败触发」（第 84 行）；真实代码反例的 docstring 仍写「certificate fails on the extra row」「Both shapes fall back」（用例第 453–454 行）；交接包前文亦保留同族旧话。这些未限定 M1，与方向甲允许 M2/M3 的条款相反。应将其改为「M1 失败；M2/M3 证书不成立时回退」，并保留该反例只验证状态和 oracle 的原有断言。

## F-D：失格负分支已有局部断言，尚未连到决定

`gate_holds(disqualification_provable=False)` 现在确实返回 `False`，关闭了距离下界以外的局部断言缺口。但 r5 主审要求的「该状态回退」尚无 `decide`/`request` 断言：新用例只检查 `gate_holds`，而 `decide` 不接收失格判据是否可证，仍可在第 11 行用形式正确的 M3 证书给出 A3。需用同一个负例将门禁失败传到最终回退决定；证据不可证或发布前失效两个入口至少各有一次断言。

## O-1～O-4 是否纳入本次冻结

| 项 | 判断 | 理由与最小闭合 |
|---|---|---|
| O-1 `evidence` 非必填 | **纳入** | 三个证书变体均接受无 `evidence` 的记录；直接 `contract_errors` 零错误，而 §4 要求「必须的证据」。将三个变体的 `evidence` 设为 required，修订现有 required 列表断言，并增加缺失负例。非空字符串仍需由外部证明逻辑验证内容，不可冒称 schema 已证明证书。孤儿枚举可同时清理/绑定，不另设冻结门禁。 |
| O-2 A3 决定与证书未绑定 | **纳入** | `{decision: A3_WITH_CERTIFICATE, certificate: null}` 直接通过 `A3Request`，违反「无证书即回退」。用 schema 分支或等价机器断言绑定 A3 决定与非空证书，增加反例。 |
| O-3 M2 用例缺过滤后行集 | **纳入** | `gate_holds` 的 M2 分支只看 `pushdown_proven and c_strict == c_sql`；若真实过滤后为 `{A}`、`C_strict=C_sql={B}`，仍返回真。应显式建模并比较过滤后行集与 `C_strict`，同时覆盖本报告的 M2 第 1/11 行反例。 |
| O-4 第 11 行状态用词 | **随 F-A 文本修正纳入，不单列阻断** | 把「未确认」与「已检查且经权威视图判不合格」、以及「本例无 M2 下推证明」写在同一状态描述中，避免与第 1/2 行的同一物理状态混淆。 |

## 验证与停止点

已核对现存 `test-results/p4-02h-r5-contracts.xml`：14 / 0 / 0 / 0；`p4-02h-r5-full.xml`：1712 / 0 / 0 / 48，本轮未重跑。用仓库当前参考模型做只读探针，复现 M2 两个 case 相反决定，以及 O-1/O-2 的零错误接受。上述绿色用例没有覆盖这些反例。

修订面闭合前不授予 ADR-0012 最终冻结，不生成隔离实验卡或 A3 实现卡。维持 A3 未实现/未启用、§12.2 未改、P4-02F 非性能 PASS。
