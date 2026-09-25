# P4-02H — ADR-0012 契约聚焦审核

**结论：暂不通过最终冻结；退回 DS-H 修正契约并补直接反例。** 审核基线 `77557db`，开工工作树干净。本轮只读核对 ADR-0012、companion、契约用例与直接调用链；未修改产品代码、既有 ADR 或契约。P4-02F 仍非性能 PASS，A3 仍未实现、未启用，§12.2 未改。

## 聚焦终审输入核对

P4-02H 卡面要求的输入是冻结契约、最小反例、主审报告的独立性限制和压缩交接包。本轮已读取 ADR-0012/companion、P4-02G 的反例决策与 P4-02H 主审报告；以下两项不能当成已完成的终审前置：

- **主审独立性未满足**：P4-02H 主审报告 §0 明示主审与契约起草在同一会话，§2.1–§2.5 的“通过”不能当作另一会话的独立复现。本轮是聚焦核对，发现 F1/F2；它不替代卡面要求的独立 DS-H 完整受影响面主审。
- **压缩交接包未单独交付**：P4-02H 任务卡“必须交付”第 4 项要求基线、改动文件/结论表、待裁决问题、ADR 预期/实际差异、符号定位、最小反例、未验证项及禁止范围。现有主审报告分散记录了其中部分内容，却没有按该项交付可直接用于终审的完整压缩交接包。DS-H 修正 F1/F2 时一并补齐。

本报告是对上述输入的聚焦审核记录，**不声称已经完成卡面指定模型/档位的 Sol high 终审**。

## 阻断项

### F1 — `place_state` 漏出 A3 资格谓词

ADR-0012 §2.2（第 26–27 行）把 `place_ids` 为空等同于 `scope_condition` 恒真；companion 的 `EligibilityRule`（第 50–75 行）同样没有 `place_state_is_any`。但 `SearchScope` 有独立的 `place_state`，`places/filter.py::place_condition` 在 `place_ids=()` 时仍对 `assigned`、`unassigned`、`manual_unassigned`、`suggested` 返回非恒真谓词（第 58–82 行）。P4-02H 主审 §2 第 1 项的“一一对应、未漏条件”因此不成立。

最小反例：无来源/人物/时间/地点 ID 筛选，`availability=any`，绑定 READY 代，`place_state=assigned`，资产 A 有 `MANUAL_ASSIGNED`，资产 B 没有；向量距离 B=0.1、A=0.2，`top_k=1`。ADR 当前资格字段全部为真，却允许考虑无筛选 A3；完整路径的 `scope_condition` 只让 A 入围。若对全代表取 1 得 B，再过滤就漏 A。即使实现最终因无证书回退，**资格判据本身已错误**，不能按现文冻结。

只读直接探针：在内存 SQLite 上调用真实 `scope_condition(SearchScope(place_state=...))`，`any` 返回 `1`，上述四种状态分别返回 `EXISTS` / `NOT EXISTS` / 建议条件；未触碰真实照片或产品文件。应把 `place_state=any` 纳入 ADR、companion 与契约用例，并补“空 `place_ids` + `place_state=assigned` + 距离逆序”的断言。修正由 DS-H 执行。

### F2 — C 的 revision/可见性定义与现行 oracle 不一致

ADR §1/§3（第 16–17、35–44 行）将 C 写成当前有效 revision 且符合可见性/离线事实的集合，§4 的 M1/M2 又要求与 C 逐项等价；但 §5.5（第 63–66 行）明确以现行完整路径为 oracle，并承认其 revision-blind 窗口。实际 `_CONFIRMED_BLOCK_SQL` 只约束 generation、space、`CONFIRMED` 和 `scope_condition`，不比较 `index_entries.confirmation_revision` 与 Lance `projection_revision`；`availability=any` 时 `scope_condition` 也不加入资产可见性条件。P4-02H 主审 §2 第 2/5 项据此不能判为无矛盾通过。

最小 revision 反例：同代同 space 两个 `CONFIRMED` 资产 A/B，SQLite 的 `confirmation_revision` 均为 1；Lance 中 A 已被更新为未确认的 revision 2，距离 0.1，B revision 1、距离 0.2，`top_k=1`。调用真实 `vector_channel`、内存 SQLite 与只返回这两行的索引替身，得到 `oracle_hits=[A(revision=2)]`、`truncated=false`。`IndexCoordinator` 的 `after_lance` 故障点可在 Lance 写后、SQLite 确认前形成这个窗口。按 §3/§4 的最新 revision 资格或证书口径，A 的 r2 不应被当成已确认行；按 §5 的 oracle，它却是第一名。冻结前必须明确定义：C 是现行 SQL 的 ID 入口，还是额外要求行 revision/可见性一致；同时明确这种窗口是强制回退、以及回退页与 §3 的关系。不能在 A3 契约里悄悄改变完整路径业务语义。

## 验证边界与交回

- 已核对 `test-results/p4-02h-contracts.xml`：9 tests、0 failures/errors/skips；这些用例测试的是 ADR 的自写 reference model，未覆盖真实 `scope_condition` 的 `place_state` 或现行 `vector_channel` 的 revision 窗口。因此绿灯不能消除 F1/F2。
- 未重跑全量测试或性能基准；本轮无产品实现改动。P4-02G 既有全量 JUnit、P4-02H focused JUnit 与 P4-02F 测量仍按原报告口径保留，不能据此授予 A3 等价或 §12.2 性能 PASS。
- 交 DS-H 只修 ADR-0012、companion、契约用例和主审结论/状态；补两条最小反例后复核直接边界。若要改变现行完整路径的 revision/可见性行为，须另行裁决和立卡。修复后再做聚焦短审；此前不生成 A3 实现卡。
