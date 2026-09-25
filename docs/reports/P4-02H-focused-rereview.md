# P4-02H — F1/F2 修复聚焦短审

**结论：F1 通过；F2 暂不通过；P4-02H 继续停在最终冻结前。** 被审提交 `7ecd363`，本轮另读取未提交的 [独立复跑报告](P4-02H-independent-recheck.md) 与对应本机 JSON/JUnit。未改 `src/**`、ADR-0012、companion 或契约用例。

## F1 — `place_state` 资格：通过

ADR-0012 §2.2、companion `EligibilityRule`、矩阵第 10 行与新增契约用例均显式要求 `place_state=any`。独立复跑使用真实迁移库、真实 `scope_condition` 检查 240 组（168 组可评估），仅全中性一组返回恒真 `"1"`，与冻结谓词零反例。此项覆盖了原阻断项；不需再次扩大到产品实现。

## F2 — revision 窗口：仍有冻结文本与可达性冲突

ADR-0012 §3.1/§3.2 将现行 SQL 的 ID 成员集 `C_sql` 与额外的行级证书集 `C_strict` 分开，且 §3.3 要求二者不等时回退；这个**判据方向正确**，原先将两者混作 C 的矛盾已消除。但以下例子与真实状态机不符：

- ADR §3.3 第 66 行与 §6 第 11 行把 `CONFIRMED` 条目（confirmation revision 1）与 Lance 新行（projection revision 2）的并存标为 `after_lance` 崩溃窗口；新增 `test_real_oracle_is_revision_blind_in_the_after_lance_window` 也按此命名。
- 实际产物发布/重排程先提交 `PENDING_REPAIR`；`IndexCoordinator.process_next(failpoint="after_lance")` 在 Lance 写后、SQLite 确认前抛错，此时条目仍是 `PENDING_REPAIR`。现有集成用例 `test_the_crash_window_leaves_the_asset_out_of_semantic_recall_until_replay` 和独立复跑的真实 Lance/调度器交织都断言这一状态。因此该资产不在 `C_sql`，也不在 `C_strict`，两集在该窗口**相等**，回退应由“引擎有额外未确认行”的证书失败触发。
- 新增 F2 单元用例自己建 `CONFIRMED` 行及替身索引，表里甚至没有 `confirmation_revision` 列；它证明现行 `vector_channel` 对**手工构造的越序状态** revision-blind，但未证明该状态由 `after_lance` 可达，也未实际计算 `C_sql`/`C_strict`。11 例绿色不能为 ADR 的窗口归因背书。

最小修正边界：将真实 `after_lance` 放入未确认/PENDING_REPAIR 反例；把 `CONFIRMED × Lance revision 不一致` 明确标为越序写入、损坏或外部替换时的**防御性**状态，或给出真实产品链路的可达交织。同步修正用例名称/断言与交接包的窗口说明；保留 `C_sql`/`C_strict` 区分和无法证明时回退的要求。M2/M3 与矩阵第 9 行仍使用未定义的裸 `C`，修订时应明确指向哪个集合，并显式保持 `C_strict=C_sql` 的发布前门禁。此轮不代替 DS-H 改冻结契约。

## 终审前置与验证口径

- 压缩交接包已在 [修复交接报告](P4-02H-fix-and-handoff.md) §4 补齐；其中 F2 的 `after_lance` 说明需随上项同步更正。
- 独立复跑报告来自另一会话，且有真实谓词与真实 Lance 证据；它**只复跑三个点名边界**，报告 §6 自己说明未替代任务卡要求的独立 DS-H 完整受影响面主审。因此该前置仍未完成，不能把本轮短审记为 Sol high 冻结通过。
- 已核对 `test-results/p4-02h-fix-contracts.xml`：11 / 0 / 0 / 0；`p4-02h-fix-full.xml`：1709 / 0 / 0 / 48。独立复跑窗口两例为 2 / 2（JSON，因本机 `tmp_path` 权限限制以同函数体直接调用，未形成 JUnit）。这些结果只证明已执行用例及其断言，不证明上述错误前提。
- 未重跑全量或性能基准。P4-02F 仍非性能 PASS；A3 未实现/未启用；§12.2 未改。修正 F2 后只复审该边界与直接回归；独立主审前置按任务卡如实补齐。
