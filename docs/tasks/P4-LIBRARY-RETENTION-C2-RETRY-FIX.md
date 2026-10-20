# P4-LIBRARY-RETENTION-C2-RETRY-FIX — 一键清理失败恢复与最终验收

**状态**：IMPLEMENTED / ACCEPTANCE PARTIAL
**优先级**：P1
**推荐模型**：GPT-6 High
**代码基准**：`szzeng/aisearch` / `master @ aa8be7b`（2026-10-20）
**任务原则**：一次最小修复，不重做 C2，不新增任务框架，原则上不增加 migration。

## 一、背景与目标

C2-FIX 已实现全量候选快照、每批 20 张、机器/人工数据区分、目标 DELETE_VECTOR 和持久进度。

本卡只处理剩余问题：

1. 清理失败 3 次后，终结的 PARTIAL 可能仍被认定为活动任务。
2. 再次点击可能错误复用旧任务，游标却已越过失败资产。
3. 失败资产可能留下 ACTIVE barrier 和未确认 DELETE_VECTOR，必须安全接续。
4. 不同清理授权模式下，旧 barrier 可能绕过当前资格复核。
5. 浏览器实际点击确认、进度及返回流程尚未验收。

**最终目标：清理失败不永久卡住；用户再次确认即可在安全条件满足时继续清理。**

## 性能设计与验收（PERF-BOUNDED）

- **链路与风险**：创建/恢复任务会查找活动 run、当前最多 20 项批次与候选快照；改判定时检查 run_items 与 run_candidates 的 `(run_id, asset_id)` 主键范围，避免扫描历史已结算前缀。ACTIVE barrier 预检限单资产及其已有关联来源/证据。
- **做法与边界**：用一个 SQL 事实谓词供创建、启动恢复、进度 API 和 worker 收口共享；保留 schema 42、20 项批次、单资产 3 次尝试和现有向量事件预算，不增加常驻工作或索引。
- **验证与停止**：运行 retention 集成、schema 迁移相关（本卡不改迁移）、真实小型 Lance 删除重放及对应 API/UI 测试，检查关键查询计划；按卡执行 Ruff、Python/Node 语法和 diff-check。浏览器取消/确认、刷新返回若仍受工具原生 dialog 限制则记 PENDING；不新增规模性能结论。

## 二、修复范围

优先检查：

- `library_retention.py`
  - `create_library_retention_run()`
  - `prepare_library_retention_run_batch()`
  - `settle_library_retention_run_batch()`
  - `begin_asset_retention_cleanup()`
  - `finalize_asset_retention_cleanup()`
  - `_cleanup_preflight()`
- `api/retention_runner.py`
- `api/context.py`
- `api/routes.py`
- `indexing/store.py` 的现有 DELETE_VECTOR 协议
- 清理 UI、schema 42 和相关定向测试

不得修改无关索引、扫描、人脸或模型管理功能。

## 三、F1：修复 PARTIAL 生命周期

### 问题

当前活动任务查询将含 FAILED item 的 PARTIAL 视为未完成。

但失败 item 可能已经结算，`selection_after_asset_id` 已越过它，后续 worker 无法再次处理。

### 修复要求

统一采用一套“是否还有未结算工作”的判定，供以下入口复用：

- 新任务创建及模式互斥。
- 应用启动恢复。
- worker 继续执行。
- API/UI 运行状态。

判定规则：

| 情况 | 处理 |
|---|---|
| QUEUED/RUNNING 且有未完成工作 | 继续或恢复 |
| PARTIAL，仍有未结算批次或未遍历候选 | 恢复原 run |
| PARTIAL，游标已完成全部候选结算 | 历史终结，不阻塞新 run |
| COMPLETE | 正常终结 |
| 当前批次中 FAILED 且已耗尽尝试次数 | 先结算该批，不自动重试失败项 |

**不能仅依据 `has_more_candidates=1` 或存在任意 FAILED item 判定仍在运行。**

必须同时考虑：

- `selection_after_asset_id`
- 尚未结算的 run_items
- 尚未遍历的 run_candidates
- 当前 run 状态

终结 PARTIAL 的统计与失败详情继续保留。

不改变现有数据库状态枚举，除非实际代码证明无法兼容。

## 四、F2：再次点击必须能创建新任务

对于已经终结的 PARTIAL：

1. 用户点击“一键清除失效照片”。
2. 重新生成新的 `run_id` 和候选范围。
3. 已完成资产不重复删除。
4. 仍符合安全资格的历史失败资产可以再次进入候选。
5. 新 run 的尝试次数重新开始，但每次仍有上限。
6. 旧 run 的游标、统计和历史记录保持不变。
7. 真正运行中的任务仍实行互斥，不允许并行执行。

不得直接把旧 PARTIAL 的游标重置为零。

不得因为存在旧失败记录就永久返回 `RUN_IN_PROGRESS`。

新任务的候选快照应继续使用现有 schema 42；不额外复制整套失败记录。

## 五、F3：安全接续 ACTIVE barrier

**这是本卡的核心安全要求。**

目前 `begin_asset_retention_cleanup()` 遇到已存在的 ACTIVE barrier 时，可能直接进入 `activate_asset_retention_cleanup()`，跳过新的 preflight。

需要明确区分：

- 同一未结束 run 的恢复。
- 历史 run 已终结后的新授权清理。

### 新授权的规则

旧 ACTIVE barrier 不是永久删除许可证。

新 run 接手前，必须重新检查：

- 资产仍然存在。
- 是否恢复了 PRESENT 副本。
- 相关来源是否在线。
- 失效扫描证据是否可靠。
- 有无新的人工收藏、人工归属或操作记录。
- 来源异常是否解除。
- 本次是 MANUAL 还是 AUTOMATIC。
- 本次是否满足对应的 30 天资格要求。

### 授权边界

MANUAL：允许跳过 30 天等待，但不能跳过成功扫描及其他安全条件。

AUTOMATIC：必须满足 30 天和到期后重新成功扫描。

特别验证：

**一张照片未满 30 天，旧 MANUAL run 清理失败；新 AUTOMATIC run 不能因为旧 barrier 存在，就继续永久删除它。**

### 向量接续

资格通过后：

- 复用原有 barrier 身份。
- 复用已存在的 DELETE_VECTOR 事件。
- 已 CONFIRMED 事件不重新生产。
- 仅处理未确认的目标事件。
- 保持 IndexCoordinator 的 revision、generation 和重放规则。
- 全部删除确认后才允许最终清理 SQLite 资产。

资格不通过时：

- 出现 PRESENT 副本：沿用现有安全撤销与向量修复流程。
- 出现人工保护：禁止最终清理；按现有可证明安全的撤销/修复协议处理。
- 来源离线或扫描证据不足：停止永久删除，保留可诊断状态，等待重新复核。
- 未知引用：fail closed。

不能简单删除 barrier 或忽略未确认 outbox。

如果资产处于 ACTIVE barrier 但当前不符合新 run 的观察资格，应能够显示为“待恢复/待复核”，不能静默消失，更不能强制删除。

## 六、F4：防止失败恢复陷入死循环

保持：

- 每资产最多 3 次执行尝试。
- 每批最多 20 张。
- 目标向量删除现有有界执行预算。

修复以下情况：

1. FAILED 已达 3 次，恢复后不能在同一 run 自动无限重新尝试。
2. 当前批次已经全部结算，但 run 尚未标记终结时，可以安全完成状态收口。
3. worker 崩溃在批次结算之前，重新运行不会重复计数。
4. worker 崩溃在成功删除之后、item 标记 COMPLETE 之前，利用持久 tombstone 幂等恢复。
5. 其他资产正常清理不被单张失败照片永久阻塞。
6. 应用启动后不反复启动一个已经终结的 PARTIAL。

所有结束状态应能从持久数据库事实恢复，不能只依赖进程内标记。

### 统计要求

- 当前 run 的已检查、成功、跳过、失败数字真实。
- 同一资产在同一 run 中不重复计数。
- 新 run 不改写旧 run 的历史统计。
- 跨重启状态一致。

## 七、F5：UI 最小修复与交互验收

不改现有页面布局。

入口保持：

**图库管理 → 失效照片与数据占用 → 一键清除失效照片**

补充以下状态表达：

- 正在清理：显示当前进度。
- 本轮部分失败但已结束：明确显示“本轮已结束，部分照片未清理”。
- 存在可重新尝试的失败：提示可在故障解除后再次点击。
- 仍有未结算任务：保持正确的运行中状态。
- 来源或人工保护导致跳过：展示原因，不错误标记为系统失败。
- 再次点击：显示新的 run，而不是旧游标的进度。

保留原有确认框，不降低删除确认要求。

### 浏览器验收

在隔离合成图库上补齐此前 PENDING 的操作：

1. 进入失效照片页。
2. 点击一键清理。
3. 取消确认，验证不创建清理任务。
4. 再次点击并确认。
5. 验证跨批进度、最终结果和失败原因。
6. 刷新、离开页面再返回。
7. 修复故障后再次点击，确认新 run 启动并完成。

优先使用具备原生 dialog 处理能力的浏览器自动化，例如 Playwright 的 `dialog` 事件。

只在隔离测试代码中处理测试对话框，不修改正式产品来绕过确认。

若环境仍无法完成真实浏览器步骤，允许补 API 与前端行为测试，但必须将相应浏览器验收标记 PENDING。

## 八、定向测试矩阵

| 测试 | 场景 | 预期 |
|---|---|---|
| T1 | 25 张，最后一张失败 3 次 | 24 成功，1 失败，旧 PARTIAL 终结 |
| T2 | 故障解除后再次手动清理 | 新 run 成功处理失败资产 |
| T3 | 旧 ACTIVE barrier，部分 DELETE_VECTOR 已确认 | 安全接续，不重复生产 |
| T4 | 旧 MANUAL 未满 30 天，新 AUTOMATIC | 不越权清理 |
| T5 | 旧失败期间照片恢复、新增收藏或来源离线 | 安全阻止或撤销 |
| T6 | 第二批执行中崩溃 | 原 run 恢复，游标和统计不重复 |
| T7 | 已成功删除，但完成回执写入前崩溃 | 幂等恢复，不重复删除 |
| T8 | 已终结 PARTIAL 后应用重启 | 不无限自动重试，不错误互斥 |
| T9 | 同时触发手动/自动请求 | 只有一个受控执行任务 |
| T10 | 浏览器取消/确认、进度、刷新返回、再次清理 | 状态正确 |

复用已有真实小型 LanceDB 测试，确保跨 generation 删除与重放安全。

只运行直接受影响的 pytest、Ruff、Python 编译、Node 语法和 diff-check。

不要求 GPU、真实图库、全仓回归或 30 万照片压测。

## 九、性能边界

优先修改少量 SQL 和恢复判断：

- 不增加周期性全库扫描。
- 不修改 schema 42 的全量候选快照机制。
- 不重复构建向量索引。
- 不为历史任务做无界行扫描。
- 保留 Keyset 分页。
- 不增加新的常驻线程或服务。
- 原则上不增加 migration。

修复涉及的活动任务查找、ACTIVE barrier 恢复和候选读取应检查是否走现有索引。

只有出现明确的性能问题时才新增最小索引。

## 十、验收及交付

必须全部满足：

- 旧终结 PARTIAL 不再永久占用互斥。
- 再次确认产生新 run，能够重新处理安全合格的失败资产。
- 旧 ACTIVE barrier 可以安全接续。
- 不存在手动向自动授权泄漏。
- 照片恢复和人工数据变化不会被忽略。
- 崩溃、重启、重复请求均保持幂等。
- 旧 run 统计准确保留。
- 前端状态与后端一致。
- 定向测试通过。
- 原始照片文件零修改。

**交付**：最小修复代码、必要测试、简短执行报告。

如果真实浏览器交互无法完成，可以将功能测试结果单独说明，但整卡仍标记 `IMPLEMENTED / ACCEPTANCE PARTIAL`。

## 十一、明确排除

本卡不做：

- 30 天规则重设计。
- 一键清理候选分页重写。
- 人工记录删除功能。
- 物理缓存回收改造。
- 模型切换或搜索性能调整。
- 人脸识别算法修改。
- 新的清理任务框架。
- 全库 VACUUM、LanceDB 压缩。
- 大图库压力测试。

**任务执行要求：只解决本卡问题，完成必要测试后停止；不自动派生新任务，不修改真实 `PhotoSearchData`，未经要求不提交或推送。**

## 执行结果（2026-10-20）

共享未结算谓词现用于创建/互斥、启动恢复、worker 与进度展示；终结 PARTIAL 可由新点击创建新 run，旧 run 的失败统计保留。ACTIVE barrier 每次授权重试都会重新预检；新人工保护会撤销 barrier、清掉陈旧 DELETE_VECTOR 并排入既有 UPSERT 修复，来源/扫描证据不足则保留 barrier 供诊断。补充 25 项三次失败/新 run、模式授权和人工保护恢复回归。详见[执行报告](../reports/P4-LIBRARY-RETENTION-C2-RETRY-FIX.md)。

- pytest：retention 与 storage UI 定向组合 **40 passed, 0 failed, 0 errors, 0 skipped**（最终 JUnit `test-results/P4-LIBRARY-RETENTION-C2-RETRY-FIX-final-r4.xml`）。首次并发运行的 pytest 临时目录冲突以唯一项目内 basetemp 串行重跑通过。
- Ruff、相关 Python 编译、`app.js` Node 语法及 `git diff --check` 通过；恢复查询计划使用现有索引且无临时排序。
- 浏览器 T10 PENDING：隔离服务启动成功，单次会话打开图库管理页，失效照片面板返回了 65 张 synthetic 统计，证明认证 API 可用。确认框交互时 CUA 控制超时，未确认清理；只读 catalog 复核仍为 65 assets、0 runs、0 ACTIVE barriers。未访问真实图库。状态因此为 **IMPLEMENTED / ACCEPTANCE PARTIAL**。
- Luna H 独立复核指出的人工保护重试 blocker 已修复并确认关闭；其建议增加的旧 DELETE_VECTOR 清除断言已补入回归。
