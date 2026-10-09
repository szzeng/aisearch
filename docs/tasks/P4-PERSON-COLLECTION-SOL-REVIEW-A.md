# P4-PERSON-COLLECTION-SOL-REVIEW-A — 人物合集高风险聚焦终审

日期：2026-10-09。状态：**当前会话聚焦复核完成 / F1–F6 CLOSED（未宣称指定Sol模型结论）**。推荐模型：**GPT-5.6 Sol / high**；用户在 Codex 中手动切换。此卡只做最终聚焦审核，不接管普通代码修复。

## 输入

按序读取 `AGENTS.md`、`docs/development-status.md`、本卡、`docs/tasks/P4-PERSON-COLLECTION-A.md`、`docs/reports/P4-PERSON-COLLECTION-A-focused-review.md`、`docs/reports/P4-PERSON-COLLECTION-A-implementation.md`。只核对 V3.1 §11、V3.0 §8.3.3–8.3.5 与 §11.2 指向的高风险边界，以及下列关键 diff、JUnit 和最小反例；无需重读全仓或复跑完整主审。

修复轮基线仍为工作树中的合集实现；实施期间前置提交 `9b06b286113d2566b3f024f2b03deb2ffc951f92` 已保留。核对实际 HEAD、index、worktree 后继续，不回退或重写前置提交。

## 聚焦范围

- F1 列表首屏与续页 SQL 参数数量；空列表、非空列表和分页断言。
- F2 `AUTOINCREMENT` 对删除后 ID 永不复用的保证；旧游标拒绝、旧 DELETE 不可能指向新集合；schema 验证与升级迁移一致。
- F3 canonical 成员去重、有效 `(asset_id, face_id)` 去重、按 face_pixels 稳定排序、前 8 张与 `has_more` 的边界；正式合并后的反例。
- F4 `DELETE ... RETURNING` 实际行数、重复移出 no-op、collection revision 仅在关系变化时递增，以及 SQLite 运行时兼容性证据。
- 这些修复是否破坏原有稳定 person ID/撤销、原子加入并忽略、成员有效性、资产分页前去重与普通 `person_ids <= 100` 契约。

主要证据：`test-results/p4-person-collection-review-counterexamples.junit.xml`（修复前反例）、`test-results/p4-person-collection-fix.junit.xml`（F1–F4 修复）、`test-results/p4-person-collection-f5-fix.junit.xml`（F5 修复后 31 项全通过）、`test-results/p4-person-collection-f6-final-check.junit.xml`（F6 升级前已删除 ID、旧人物/照片游标、错误实例 token、合集 API/DOM/迁移回归）、`docs/reports/P4-PERSON-COLLECTION-A-focused-review.md` 及相关代码/测试 diff。

## 输出与停止点

将结论和最小必要证据追加到聚焦审核报告。若发现实现缺陷，只给出失败反例和影响边界，交回原实现会话修复；修复后只短审相关边界。没有证据命中 V3.1 §2.2 条件时不建议 DS-MAX。不得标总 PASS：真实浏览器试用仍待用户确认。完成终审后更新开发状态并停止，不开始新产品任务。

## 本轮复核交接

用户要求继续聚焦审核后发现 F5：直接修改 0024 会令旧 v24 catalog 被严格 schema 检查拒绝。现已恢复 0024，新增 0025 重建并保留父/成员数据，最终定向 JUnit 31/0/0/0；v24→v25 备份/数据/FK/ID 分配回归通过。请短审迁移兼容性和 F2 ID 不复用在升级后的完整性；当前仍未宣称 Sol 终审完成。

## 最新复核结果

F5已通过实际v24→v25重开、数据及备份复核。F6 修复在 schema v26 新增不可变实例 token，将旧 ID-only 路径拒绝，并把人物/照片游标绑定 token；v24 删除最大 ID、升级后复用相同整数的回归覆盖旧两类游标与错误 token 删除。修复后定向 JUnit 22/0/0/0，Ruff、Node、diff-check 通过。请仅短审 F6 反例、迁移 token 持久性、所有实例 API/游标绑定及直接前端调用面；不重复全 diff 主审，也不能据此标真实浏览器或总验收 PASS。

## 用户指定当前会话聚焦复核结果

F6独立定向13/0/0/0，无新增阻断问题；结论FOCUSED REVIEW PASS / F1–F6 CLOSED，完整证据见聚焦报告末节。未自动切换或声称指定Sol模型终审，真实页面试用/总验收未标PASS；本轮审核停止，不自动继续其他任务。
