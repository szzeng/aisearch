# P4-EMBEDDING-OFFLINE-SELECTION-FIX — 离线历史资产不再无条件阻止搜索模型切换

- **状态**：IMPLEMENTED / ACCEPTANCE PARTIAL；创建日期：2026-10-20；执行报告：[P4-EMBEDDING-OFFLINE-SELECTION-FIX](../reports/P4-EMBEDDING-OFFLINE-SELECTION-FIX.md)。
- **优先级**：P1（当前 Qwen 候选覆盖齐全却无法切换）。
- **建议模型**：GPT-6 High（涉及搜索 Profile 激活安全门禁）；按 `AGENTS.md` / V3.1 对完整受影响面进行独立高风险复核。个人项目只做定向合成验收，不做 30 万大库性能认证。
- **仓库**：`szzeng/aisearch` / `master`；执行前先读 `AGENTS.md`、`docs/development-status.md` 和本卡，核对实际工作树与最新代码。与 `P4-EMBEDDING-CHECK-UX-FIX` 的界面修复并行时保留已有改动，避免覆盖。
- **目的**：修复“有效向量 4265；应索引 4265；覆盖 100%；但有离线资产 461 → `SELECTION_COVERAGE_INCOMPLETE` → 无法安排切换”的规则问题。这组数字是用户现场观察，**不是仓库测试数据或对真实库的验证结论**。

## 1. 已核对的实际原因

- `src/photo_search/indexing/selection.py` 的 `_SEARCHABLE_SQL` / `indexable_assets()` 只对具有 `PRESENT` 文件位置、且符合当前语义编码策略的图片建立“必须覆盖”集合。`coverage_report()` 由此得出 `required/covered/missing`，与离线资产不是同一分母。
- 同文件 `_OFFLINE_SQL` 却统计**所有 ACTIVE 且不存在任何 PRESENT 位置**的资产：包括真正的 MISSING 图片、未连接来源、删除来源登记留下的零位置资产，以及旧非图片历史记录；`require_selection_ready()` 直接以 `if report.offline_assets` 拒绝；`CoverageReport.complete` 也把 `offline_assets==0` 当作完整条件。
- `src/photo_search/catalog/image_policy.py` 的 `photo_asset_sql()` 明确保留零位置历史资产，搜索侧支持 `availability=any/present/missing`。这些历史记录不能被当作“必须重建 Qwen 向量的当前在线图片”，也不能为切换而删除。
- `src/photo_search/api/routes.py` 的 `POST /embedding-management/providers/{id}/check` 和 `.../switch` 使用同一 `selection_report/require_selection_ready` 门禁；停机后的 `src/photo_search/models/embedding/cli.py::_select_profile` 通过 `selection_precondition` 再次验证。因此**不能仅在 UI 隐藏 461 条警告或放行 HTTP 入口**；必须修正共享权威门禁，保证 UI 安排与重启激活口径一致。
- `selection_precondition()` 在移动 active Profile 指针的事务内复核图库摘要、覆盖、outbox、generation 和 `offline_assets` 快照。此机制必须保留；“允许历史离线”不等于允许检查期间图库发生变化。
- `semantic.image_eligibility(path)` 对登记为 `PRESENT` 但无法读取的文件可能返回 `UNREADABLE` 并列入 `out_of_scope`；本卡要区分**已在 catalog 标记无 PRESENT 的历史资产**与**仍声称 PRESENT 却实际读取失败的异常路径**，不得把后者冒充“已安全忽略的离线历史”。

## 2. 修复后规则（核心约束）

**仅对当前真实可用、符合编码策略的照片要求完整覆盖；无 PRESENT 位置的历史资产单独计数并提醒，但不再单凭其数量拒绝 Profile 切换。**

| 条件 | 处理 |
| --- | --- |
| 仅有历史离线资产（MISSING / 全局零 PRESENT / 零位置 / 历史非图片）；在线可索引照片 `required>0` 且 `covered=required`，其它门禁正常 | **允许**安排切换及停机激活；保留离线数量提示 |
| 在线可索引图片缺向量、旧 SHA 向量或输入修订不匹配 | **拒绝**，真实缺口 `missing>0`；不可拿离线数量抵消 |
| `generation!=READY`、空候选、pending outbox、PENDING_REPAIR、STALE、损坏/丢失 artifact、Lance 不健康、Profile/space/processor 不匹配 | **继续拒绝**；原代码/错误语义维持 |
| 某资产 A 来源 MISSING、B 来源 PRESENT 且为可索引图片 | 按一张当前在线图片要求向量覆盖；不能因 A 离线而排除 |
| 数据库仍记为 PRESENT，但检查时原文件无法读取（`UNREADABLE` 或等效） | 不当作离线历史豁免；明确失败或维持更严格的拒绝，不静默报告 100% 通过 |
| 检查后、事务切换前离线图恢复为 PRESENT 并进入当前可索引集合 | 旧报告失效，拒绝本次切换并要求补齐目标模型向量/重新检查 |
| 当前全部可索引图片为零、仅有离线历史 | 仍按现有空 generation 规则拒绝，不因 `0/0` 被算成 100% |

**切换后的明确语义**：历史记录、人工人脸/地点/合集、OCR/缩略图/旧模型向量均不删除；但没有目标模型向量的离线旧照片，**不能保证**仍能通过新模型的语义通道命中。恢复原图后，应由已有图库刷新/增量建库补目标 generation 的向量；不得跨 Qwen 和 Chinese-CLIP 的向量空间混用、自动退回旧模型、伪造已覆盖。

## 3. 最小实施范围

1. **共享门禁**：优先改 `src/photo_search/indexing/selection.py`，将 `offline_assets` 从阻断条件变成独立诊断/提示指标；同步审视 `CoverageReport.complete` 的语义，确保不出现 API 判断“已通过”而其完整性字段仍为“未完成”的矛盾。不要全局忽略其它 `SELECTION_COVERAGE_INCOMPLETE`，也不能把 `required` 改成所有 ACTIVE 资产。
2. **分类安全**：保留现有 `PRESENT` 图片资格及字节/Lance 核验；将 `PRESENT` 文件读取失败与历史 MISSING 区分，并最小化地做到 fail-closed（尤其 `UNREADABLE`），不扩大到重新设计媒体解码策略。旧视频/非图片以及 `UNIDENTIFIED` 等既有明确不可索引媒体规则不得被意外收紧；若需要处理的例外超出本卡，记录具体反例再评审。
3. **检查、安排、实际激活口径必须相同**：重用现有 `selection_report` → `require_selection_ready` → `selection_precondition`；保留停机租约、原子激活与二次复核。事务内仍比较 `offline_assets` 以及在线目录摘要/覆盖等准备态与提交态，防止 TOCTOU；离线数量不构成长期资格证书。
4. **真实结果诚实展示**：现有 `POST .../check` 响应已有 `coverage.offline_assets`；通过时应保留该字段，`switch_eligible=true` 与非零 `offline_assets` 可同时成立。界面 `src/photo_search/web/app.js`（以执行时 UX 修复版为基线）在通过但有离线历史时显示类似「在线可索引图片 4265/4265；另有 461 条无可用位置的历史资产，不计入覆盖率；切换后这些资产可能缺少新模型语义结果」。此处称“离线资产”而不是“永久丢失照片”。已有切换确认对话框应带上简短影响提醒；不新增强制二次确认表单或跳过门禁选项。CLI 如输出 `coverage`，保持相同告警字段。
5. **不做数据操作**：不删除/改写 `assets`、`file_locations`、source、人工事实、artifact、index_entries、outbox 或 Lance 数据；不改扫描器把 MISSING 标记为 PRESENT 的规则、不迁移 schema、不自动对真实库重扫/重建、不引入“强制切换”开关。
6. **文档同步**：把 `selection.py` 顶部“所有 MISSING 均拒绝”的过时注释及直接关联的任务/说明文字更新为“只要求当前可用且可编码图片覆盖；保留离线风险提示”。如果和冻结 ADR/其他升级路径存在实质冲突，先列明冲突及边界，不擅自重写冻结契约。

### 性能设计与验收（三行）

- **链路/风险**：显式深度检查与停机激活已有一次当前图库分类 + artifact/Lance 完整性检查，不能把它塞进每次普通搜索/页面打开；非零离线资产可能很多。
- **做法/边界**：复用当前 SQL 汇总、摘要和原有门禁；最多少量集合级统计；不得逐条 `stat` 离线历史或按离线资产 N+1 查询，UI 不新增自动检查、轮询或模型加载。
- **验证/停止**：隔离小型合成库按下面矩阵做真实性和故障测试；若出现新全表/N+1 热路径或扩大到数据库迁移，停止并先评估。不要求 30 万数据集容量认证。

## 4. 定向验收（必需）

复用 `tests/integration/test_p4_embedding_provider_flow.py` 的 CLIP/Qwen 合成库、`tests/unit/test_p4_embedding_management_fix.py` 等直接相关用例。**必须改写原“有一张 MISSING 就拒绝”的旧期望**，不能简单删测试或 `xfail`：

1. **离线但允许**：1 张在线已覆盖图片 + 1 张已扫描成 MISSING 的历史图片（以及可选旧非图片、零位置） → `offline_assets>0`、在线覆盖 100% → 检查 `switch_eligible=true`；受控切换成功，离线人工历史数据与原 active 旧向量不动。
2. **离线统计准确**：MISSING 位置数 vs 无任何 PRESENT 的唯一 ACTIVE asset 数不能混淆；双来源一失效一正常、仅非图片、删除来源后零位置，分别正确；不得称它们都已永久丢失。
3. **在线缺口仍拒绝**：在线新增可索引图片但目标 generation 未编码 → `COVERAGE_MISSING`；修复向量后才可通过。
4. **其他门禁不放宽**：空 generation / 未 READY / 待确认 outbox / repair / stale / artifact 缺损 / Lance 行数不符 / Profile 失配继续拒绝，原活动模型保持。
5. **事务与重启**：显式检查通过后，激活前离线图片恢复并登记为 PRESENT → 旧选择事务拒绝；停机重启重新检查同样拒绝；库不变则正常激活。合成断言 active 指针只在最终成功时更新。
6. **PRESENT 不可读不豁免**：标记 PRESENT 但真实文件缺失/无读取权限（合成可控） → 不应以“历史离线允许”通过。
7. **界面提示**：`switch_eligible=true` 且 `coverage.offline_assets>0` 时展示警告并保留切换按钮；`missing>0` 时仍显示不可切换；网络错误仍显示检查失败而非历史离线放行。若此前检查 UX 卡的工作树尚未合并，至少做 API/契约与简单 UI 定向联测，不擅自覆盖并行改动。

运行相关 `pytest -q --junitxml=...`、`ruff check`、受影响 Python 语法、如改 JS 则 `node --check`、`git diff --check`；独立复核上述安全门禁与停机切换路径。浏览器只需隔离小样本的检查→提示→安排流程；无法完成须如实标为 PENDING。禁止访问真实 `PhotoSearchData`、删库、真正切换用户活动 Profile 或执行真实 GPU 建库。

## 5. 完成标准

当在线可索引图片确实 100% 覆盖、候选 generation/artifacts/Lance/outbox/Profile 均健康时，**仅因无 PRESENT 位置的历史资产而产生的拒绝被消除**；用户可查看真实离线数量与风险说明并按既有停机流程切换。任何真正的在线索引缺口、破损或重启时目录变化仍被拒绝；没有数据迁移/删除或额外常态性能负担。

**交付**：最小实现、受影响小测试、简短报告，明确哪些场景已通过/未测。此卡只创建任务，不代用户执行真实切换。

## 6. 执行结果（2026-10-20）

- 共享选择门禁、API 检查/安排、停机 CLI 复核与离线影响提示已实现；离线资产不再单独阻断在线 100% 覆盖的候选，其他覆盖和完整性门禁保持拒绝。
- 补充 API profile/provider identity mismatch 检查：profile 的 space/processor 必须与候选 generation 相同，且 `resolve_profile_binding` 必须通过。错配会禁用切换资格；安排返回 `PROFILE_GENERATION_MISMATCH`，不写待执行请求。
- 补充完整停机 CLI 重验：显式检查时存在离线图片；在 CLI 执行前恢复并 rescan 后，`_select_profile` 以 `COVERAGE_MISSING` 拒绝，活动 profile 保持不变。库未变化时原离线候选 CLI 合成激活用例通过。
- 最终受影响 JUnit：`test-results/p4-embedding-offline-selection-fix-final-r2.xml`，30 项、29 passed、0 failed、0 errors、1 skipped（既有 Windows symlink `WinError 1314`）。Ruff、相关 Python 编译、Node 语法、`git diff --check` 通过。
- Luna High 首轮两项 P2 均已修复；跟进复核无新增 finding。内置浏览器页面已加载且 status API 为 200，但当前 CUA 能力无法执行页面点击；检查→提示→安排确认的浏览器验收仍 **PENDING**，所以本卡保留 **IMPLEMENTED / ACCEPTANCE PARTIAL**。
- 合成浏览器夹具留在 `.tmp/P4-EMBEDDING-OFFLINE-SELECTION-FIX/browser-synthetic`。未访问真实图库、未加载模型或 GPU、未执行真实停机切换，未做大库容量认证。
