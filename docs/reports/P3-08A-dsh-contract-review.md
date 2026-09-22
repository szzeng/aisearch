# P3-08A 阶段 A — 新旧 `face_id` 可审查映射与 P3 故障验收矩阵契约草拟（DS-H）

- 状态：**ENGINEERING CONTRACT REVIEW PASS / SOL FREEZE PENDING**（阶段 A 完结，阶段 B 未开始）
- 日期：2026-09-22
- 实际模型：**DS-H（DeepSeek V4.1 Flash / 高推理）**，新会话，由用户在 Codex 中手动选择；未发生自动切换
- 起点基线：`7dd392d`（P3-07C 收尾提交，工作树干净）；本阶段只新增 4 个文件 + 状态更新，
  **未改任何生产代码、migration、既有 ADR/companion、`uv.lock`、`PhotoSearch.spec`、前端或既有测试语义**
- 任务卡：[docs/tasks/P3-08A.md](../tasks/P3-08A.md)
- 冻结件（阶段 A 状态）：
  [ADR-0010](../decisions/ADR-0010-face-redetection-mapping-and-p3-acceptance-contract.md)（**Proposed**）、
  [p3-face-redetection-contracts.v1.json](../decisions/contracts/p3-face-redetection-contracts.v1.json)、
  [p3-acceptance-matrix.v1.json](../decisions/contracts/p3-acceptance-matrix.v1.json)、
  [契约测试](../../tests/unit/test_p3_face_redetection_contracts.py)
- 后续：阶段 B Sol 终审 `FAIL`（四条边界）与本轮 DS-H 修正见
  [阶段 B 修复报告](P3-08A-dsh-contract-fix.md)；矩阵与契约测试在该轮扩到 **65 条 / 43 例**

**是否升级 DeepSeek max：否。** V3.1 §2.2 六类具名条件逐条核对：①无跨模块异常待定根因（缺口是“尚未定义”，
不是“行为矛盾”）；②transaction/revision/undo 的多状态在本卡内可一次推演清楚（§4 转移矩阵 + §5.4 撤销
revision 规则）；③无“测试通过但行为与 ADR 不符”的矛盾；④本卡不修复、只冻结，未产生回归；⑤改动面是两份
契约文档 + 一处已知顺序缺陷的最小反例，不是多个耦合状态机的大 diff；⑥Sol 阶段尚未开始，无 Sol 提出的
反查问题。故按 DS-H 收敛，不升 max。

## 1. 审查范围与方法

按任务卡“固定读取范围”执行：`AGENTS.md` → `docs/development-status.md` → 本卡 →
`docs/reports/P3-07C-sol-focused-review.md` §9 → `docs/reports/P3-07B-person-search-implementation.md` §6 →
主架构 §8.3（含 §8.3.1～§8.3.5）与 §12.4 → 策略 §2.2～§2.3、§8、§11～§14 → ADR-0005～ADR-0009 中与权威/
revision/人工控制/撤销/隐私直接相关的条款。未读两份文档全文（无冲突证据，符合 `AGENTS.md` 例外条件）。

只读实现面（不修改）：`face/processor.py`（`face_id_for()`、`human_controlled_face_ids()`、
`_retire_stale_faces()`、`_park_rewritten_ordinals()`、`persist_face_document()`）、
`catalog/migrations/0010_face_core.sql`、`catalog/schema.py`（`SUPPORTED_SCHEMA_VERSION = 13`）、
`persons/{operations,face_correction,split,read}.py`、`persons/service.py`、`api/{routes,models,errors,queries}.py`、
`api/web.py`，以及既有契约测试 `tests/unit/test_p3_face_correction_contracts.py`、
`test_p3_person_search_contracts.py` 的形状与写法。

## 2. gap 表与拆分理由

| # | 契约点 | 当前实现 / 既有冻结件 | gap | 本卡落点 |
|---|---|---|---|---|
| 1 | 新旧 `face_id` 映射身份、锚点、审核状态 | 无映射表、无 ADR；ADR-0007 §1、ADR-0008 §1 明确把该语义留给 P3-08 | 完全未定义 | ADR-0010 §2、§3 |
| 2 | 候选生成 | `persist_face_document()` 只保留 human-controlled 旧行（`_retire_stale_faces`），没有候选概念 | 无 | ADR-0010 §3.1～§3.3 |
| 3 | 未确认前不得自动填回 | `human_controlled_face_ids()` 只看 0012 的 control/exclusion | 缺“OPEN 映射候选”这一来源 | ADR-0010 §3.4 |
| 4 | 人工知识转移 | ADR-0007 §3 已冻结 assign/unassign/restore_auto 的语义 | 无“跨 face_id 转移”规则 | ADR-0010 §4 |
| 5 | 操作权威、幂等、补偿撤销 | 0012 把 `person_operations.type` / item `entity_type` 写成闭合 CHECK，只到 `FACE_*` | 缺 `FACE_MAPPING_*` 与 `FACE_MAPPING_GROUP` | ADR-0010 §5 |
| 6 | 权威到派生的提交顺序 | ADR-0007 §8 的顺序与 cause 集合可直接复用 | 需明确“纯审核不推进 revision” | ADR-0010 §6 |
| 7 | 重启/重扫/升级/回退/晚到 worker/备份 | 0010 的 spec/generation 隔离 + P2 调度恢复 | 映射持久状态语义未定义 | ADR-0010 §7 |
| 8 | API、错误、隐私 | `errors.py` 统一 envelope；0012 的隐私闭集 | 无映射只读面/写面/错误码 | ADR-0010 §8 |
| 9 | 能力自描述 | `/capabilities.face_summary` 已有若干只读计数 | 缺映射计数与 `schema_version >= 14` 门禁 | ADR-0010 §8.3 |
| 10 | `merge` 撤销顺序缺陷 | P3-07B §6 已独立复现（目标 id 大于源 id 时 `ux_face_person_links_effective` 冲突） | 未修，且无回归 | ADR-0010 §10（P3-08B 必修） |
| 11 | P3 故障/纠错验收全集 | 既有任务各自验收片段，无统一矩阵 | 缺可枚举、可核对的权威清单 | 矩阵 companion + ADR-0010 §11 |

**拆分理由（不改总体路线）。** 本卡需要“先定义映射身份—再审事务与权威—再冻结 API/隐私—再给出可执行的
故障与浏览器矩阵”，属 V3.1 §11 列表中的“人工人物锁定/排除/同名合并/拆分/撤销”高风险类，必须由 Sol high
聚焦终审后才可转 Accepted；而实现、`0014` 迁移、D 级故障全集与真实浏览器闭环是另一个可独立验收的闭环。
因此阶段 A 只到 `ENGINEERING CONTRACT REVIEW PASS / SOL FREEZE PENDING`，不生成 P3-08B 卡。

## 3. ADR 与两个 companion 的可实现性结论

| 冻结点 | 结论 | 依据 |
|---|---|---|
| 映射身份 `mapping_id` 由 canonical JSON sha256 得到 | **可实现** | 与 `face_id_for()`（`processor.py:198`）同构，只是输入换成 `(asset, old, new, old_spec, new_spec)`，不依赖数据库状态 |
| 只对携带人工知识的旧脸建队列 | **可实现** | `human_controlled_face_ids()` 已在每次提交时读取 control/exclusion；判定集合可直接复用 |
| 几何匹配只读 0010 的方向修正 bbox/landmarks | **可实现** | 0010 注释与列定义已把 `bbox_json`/`landmarks_json` 固定为 orientation-normalised；匹配不需要解码原图 |
| 临时保持不新增第四种 control mode | **可实现** | 自动路径的唯一入口是 `human_controlled_face_ids()`；扩展其来源即可，不触碰 ADR-0007 §9 的计数口径 |
| 转移矩阵（1:1、1:N、N:1、0 候选） | **可实现** | 复用 ADR-0007 §3 的 assign/unassign 语义与同一条 `BATCH` 事务形状；`at most one CONFIRMED` 由 `ux_face_person_links_effective` 保证 |
| 操作权威仍只有 `person_operations` | **可实现** | 0012/0013 已有两次“重建操作表扩展闭合 CHECK”的先例，`0014` 按同一路径追加 `FACE_MAPPING_*`、`FACE_MAPPING_GROUP` |
| 幂等门先于 revision 检查 | **可实现** | `persons/operations.py::undo_person_operation()` 与 face/split 家族已经这样排序，映射家族按同一模板 |
| revision 只前进 | **可实现** | 既有 `_restore_link_rows()` 已用 `max(before, live) + 1`；本 ADR 只把该规则推广到 group/映射行 |
| 纯审核不推进 `catalog_filter_revision` | **可实现且必要** | 与 ADR-0007 §3 no-op 规则一致；避免把“审核”伪装成人物事实变化而误伤搜索会话 |
| API 隐私闭集 | **可实现** | 只投影受控 ID、枚举、revision 与既有 `face-thumbnail` 模板 URL；`test_p3_face_redetection_contracts.py` 已做正反例 |
| 故障矩阵可执行 | **可实现** | 每条给出 fixture、注入点、期望权威状态、允许/禁止副作用与 P3-08B 测试路径；覆盖主架构 §12.4 第 8、14～22、29～33 项 |

**未越权确认：** 本阶段未修改任何 ADR-0006～0009 的已冻结语义，也未把契约写成“已实现”。ADR-0010 状态仍为
**Proposed**，`test_adr_is_still_a_proposal_and_scopes_the_implementation` 会持续守住这一点。

## 4. P3-08B 最小修改面建议（不含实现）

```text
catalog/migrations/0014_face_redetection_mapping.sql   新增两表 + 重建操作表 CHECK（schema_version 13 → 14）
face/processor.py                                     候选生成（同事务）+ human_controlled_face_ids() 的临时保持来源
persons/mapping.py（新）                              decide_face_mappings() / undo_face_mapping_operation()
persons/operations.py                                 operation_family() 增加 mapping 家族 + §10 顺序修复
persons/read.py                                       mapping_page() / mapping_group() 只读投影
api/{models,routes,errors,queries}.py                 冻结路径、DTO、错误码与四个只读计数
src/photo_search/web/**                               /persons 页“重检测待确认”视图（无新依赖、无 CDN）
tests/…                                               契约之外的 D 级故障、真实浏览器 smoke 与 DEF-001～005 回归
```

`0014` 必须遵守 ADR-0007 §10 / ADR-0002 §7 的只读预检、备份、单事务推进、缺件/损坏整体回滚、
`0001`～`0013` 逐字节不变与过新库零写；`MEMBER_PREDICATE`（ADR-0009 §3.1）与 `search_projection` 的字段
级冻结不需要改动。

## 5. P3-07B §6 缺陷：最小反例与修复不变量

最小反例（P3-07B 报告 §6，本阶段按只读走查确认其成因仍成立）：

```text
merge(target_person_id=2, sources=[{person_id: 1}])  →  提交成功
undo(operation_id=...)                              →  IntegrityError:
   UNIQUE constraint failed: face_person_links.face_id
merge(target_person_id=1, sources=[{person_id: 2}])  →  undo OK
```

成因：`undo_person_operation()` 按 `item_index`（即 `sorted(before)` 的 person id 升序）逐人恢复，
`_restore_link_rows()` 对每个 person 内部“先删后插”；源人物的 `CONFIRMED` 行插回时目标仍持有同一
`face_id` 的有效行，撞上部分唯一索引。修复不变量与回归面已冻结在 ADR-0010 §10（两点式恢复、影响集合严格
来自快照、`max(before, live) + 1`、五条回归场景），并与矩阵 `P3-08B-DEF-001`～`DEF-005` 一一对应。

## 6. P3-08B 验收矩阵：已有证据 / 必须重跑 / 尚未实现

矩阵共 **56** 条，均为 `P3-08B-*` 稳定 ID；证据状态分布（由契约测试持续核对，不接受“未跑即 PASS”）：

| 状态 | 数量 | 代表条目 |
|---|---:|---|
| `existing`（既有证据，改动后仍需回归重跑） | 16 | §12.4 第 14～22、29～33 项的既有单点验收 |
| `must_rerun`（机制已有、场景组合需重跑） | 10 | 升级失败、晚到 worker、备份恢复、跨 authority 交错、merge/undo 顺序修复 |
| `not_implemented`（P3-08B 全新实现） | 30 | 映射生成/确认/拒绝/撤销/重启/重扫/升级/回退与真实浏览器闭环 |

主架构 §12.4 覆盖核对（`architecture_coverage` 已逐项列出对应 ID）：人物 **8、14、15、16、17、18、19、
20、21、22**；地点交叉 **29、30、31、32、33**。真实浏览器闭环计划固定为
`/persons → /places → /search` 的“纠错→筛选→撤销/冲突→重搜 + 窄宽视口 + 键盘 + 泄露扫描 +
访问日志边界”，并且**只允许**称“记录的真实浏览器 smoke”。

## 7. 未验证项

- 30 万容量下候选生成与审核队列的成本、`0014` 在真实数据上的迁移耗时：未测（P4-01/P4-02 与 P3-08B 范围）。
- 多进程并发下的审核决策与撤销：会话/进程边界沿用既有范围，本卡未验证。
- 真实权重下的模型升级/回退链路（含 GPU 与小显存双驻留窗口）：属 C1；本卡只冻结持久状态语义。
- 低质量/几何极端脸的长期 `AMBIGUOUS`/`NO_CANDIDATE` 队列长度：未测。
- C 级打包：本卡无生产改动，未运行打包 smoke（`PhotoSearch.spec` 与打包输入集合未变）。
- 真实浏览器闭环：**尚未执行**，只在矩阵中冻结步骤与判定口径。

## 8. 环境事实与实测命令

| 证据 | 结果 |
|---|---|
| `test-results/p3-08a-contract.xml` | **33 tests / 0 failures / 0 errors / 0 skipped**（exit 0） |
| `test-results/p3-08a-unit.xml` | **792 tests / 0 failures / 0 errors / 0 skipped**（完整 `tests/unit`，exit 0） |
| `ruff check tests/unit/test_p3_face_redetection_contracts.py` | clean（exit 0） |
| `git diff --check` | clean（exit 0） |
| `uv lock --check` | exit 0，95 packages |

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path
uv run --no-sync python -m pytest -q -p no:cacheprovider `
  tests/unit/test_p3_face_redetection_contracts.py `
  --basetemp=.pytest-tmp/P3-08A-contract `
  --junitxml=test-results/p3-08a-contract.xml
uv run --no-sync ruff check tests/unit/test_p3_face_redetection_contracts.py
git diff --check
uv lock --check
```

- `.pytest-tmp` 对受限令牌仍返回 `PermissionError [WinError 5]`（既有环境事实）；需要 `tmp_path` 的完整
  `tests/unit` 因此经**已授权的正常 Windows 权限路由**执行，basetemp 仍在项目内，**未修改任何 ACL**，
  uv 缓存仍在项目内 `.uv-cache`，JUnit 写入 `test-results/`。
- 夹具只使用内存对象与项目内临时目录；**未扫描、未读取、未修改任何真实照片库**，未下载模型。

## 9. 给 Sol 的压缩交接包（阶段 B 输入）

**基线 / 改动文件。** 起点 `7dd392d`（干净工作树）；新增
`docs/decisions/ADR-0010-…md`（Proposed）、
`docs/decisions/contracts/p3-face-redetection-contracts.v1.json`、
`docs/decisions/contracts/p3-acceptance-matrix.v1.json`、
`tests/unit/test_p3_face_redetection_contracts.py`，外加 `docs/development-status.md` 与本卡阶段交接。
未改 `src/photo_search/**`、migration、既有 ADR/companion、`uv.lock`、`PhotoSearch.spec`、前端与既有测试。

**四条必审边界（Sol 只需读这些 + 对应条款 + 契约测试）。**

1. **未确认映射绝不自动转移人工 authority。** 条款 ADR-0010 §3.3（服务端永不自动确认）、§3.4（临时保持
   只扩 `human_controlled_face_ids()` 的来源，不新增第四种 mode）、§4 总则。最小反例：矩阵
   `P3-08B-MAP-002`（UNIQUE_HIGH 也不自动确认）。请审“保持期内的自动路径是否确实零写权威关系”。
2. **确认/拒绝/撤销的事务边界闭合。** 条款 §5.1～§5.4（唯一操作权威、全局 `request_id` 幂等门先于
   revision 检查、`BEGIN IMMEDIATE` 整批提交、撤销 `max(before, live)+1`）。最小反例：矩阵
   `P3-08B-MAP-014`、`P3-08B-MAP-015`、`P3-08B-X-004`。
3. **身份、歧义与升级/回退/备份可证明且不丢旧审计链。** 条款 §2（sha256 身份、跨资产禁止）、§3.3
   （0/1/多候选）、§7（重启/重扫/升级失败/回退/晚到 worker/备份）。最小反例：矩阵
   `P3-08B-MAP-005`、`P3-08B-MAP-013`、`P3-08B-MAP-018`、`P3-08B-MAP-023`。
4. **API/错误/details/隐私闭集足够生成 P3-08B，且 §10 缺陷已纳入。** 条款 §8（11 个错误码、无 bbox/
   landmark/IoU/路径/坐标/内部快照）、§10（merge/undo 顺序不变量 + 五条回归）、§11（矩阵形状）。
   最小反例：矩阵 `P3-08B-DEF-001`～`DEF-005`、`P3-08B-MAP-024`。

**关键定位（不复制完整 diff）。** 缺口位置：
`src/photo_search/face/processor.py:682`（`human_controlled_face_ids`）、`:798`（`persist_face_document`
内的候选生成插入点）、`src/photo_search/persons/operations.py:911`（`_restore_link_rows`）、
`:973`（`undo_person_operation` 的家族路由与逐项恢复）、`src/photo_search/catalog/schema.py:47`
（`SUPPORTED_SCHEMA_VERSION`）。

**ADR 预期 / 实际差异。** 本阶段无“实现与 ADR 不符”的差异：ADR-0010 为 Proposed 契约，尚无实现；
唯一已知的真实缺陷差异是 P3-07B §6，已被 ADR-0010 §10 明确纳入 P3-08B 必修范围。

**契约测试与命令结果。** `tests/unit/test_p3_face_redetection_contracts.py` 33 例（含正反例、跨项规则、
矩阵覆盖与 ADR 漂移检查），`test-results/p3-08a-contract.xml` 33/0/0/0；完整 `tests/unit` 792/0/0/0；
Ruff、`git diff --check`、`uv lock --check` 全 clean。

**未验证项与禁止扩大的范围。** 见 §7。向 Sol 明确禁止：不得借本卡改写 ADR-0006～0009 已冻结语义、
不得把 ADR-0010 之外的映射语义（如按 `person_id`/ordinal 建映射、自动确认、第四种 control mode、
跨资产继承）写入冻结件、不得要求 P3-08A 实现生产代码或运行真实照片库、不得提前生成 P3-08B。

## 10. 阶段交接

```text
任务 ID / 状态：P3-08A / 阶段 A 完成 —— ENGINEERING CONTRACT REVIEW PASS / SOL FREEZE PENDING
起点基线：7dd392d（工作树干净）
本阶段产物：ADR-0010（Proposed）、p3-face-redetection-contracts.v1.json、
p3-acceptance-matrix.v1.json（56 条；修复轮后 65 条）、tests/unit/test_p3_face_redetection_contracts.py（33 例；修复轮后 43 例）
未改：src/photo_search/**、catalog/migrations/**、既有 ADR/companion、uv.lock、PhotoSearch.spec、前端、既有测试
deepseek max：未升级（§2.2 六类条件逐条未命中，理由见文首）
实测：契约 33/0/0/0（p3-08a-contract.xml）；完整 tests/unit 792/0/0/0（p3-08a-unit.xml）；
      Ruff / git diff --check / uv lock --check 全 clean
下一阶段：P3-08A 阶段 B —— 独立新会话 C2 / GPT-5.6 Sol / high 聚焦终审并冻结（输入为本报告 §9）
通过后：ADR-0010 转 Accepted、输出 docs/reports/P3-08A-sol-contract-freeze.md、P3-08A 标 PASS、生成 docs/tasks/P3-08B.md
不通过：写最小反例退回 DS-H，修复后 Sol 只短审原失败边界
模型操作：请在 Codex 中手动切换模型/推理档后继续（不得自动切换或声称已切换）
```
