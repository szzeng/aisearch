# P4-02O / P4-02Q 修复轮 — `build --source-id` SQL 缺陷 + DS-H 审查文档/证据项

**状态：FIXED / 有界复审 PASS（产品面，见 §6）。** 由 [P4-02N/O/Q/S 独立 DS-H 完整受影响面审查](P4-02N-O-Q-S-dsh-affected-surface-review.md)
的 F1 产生；F2/F3/F4/F5/F6–F9 的处置见 §4。产品改动只有 `indexing/semantic.py` 的谓语合成与
`indexing/cli.py` 的一句帮助文案，未改 schema/迁移/依赖/`uv.lock`/API 信封/错误码/检索与 outbox 语义。

## 1. 改动面

| 文件 | 改动 |
|---|---|
| `src/photo_search/indexing/semantic.py` | `_TARGET_SQL` 的 `"{source_filter}"` 行改为 `"WHERE {source_predicate}NOT EXISTS ("`；`semantic_selection` 生成 `""` 或 `"f.source_id = ? AND "`；补两行注释说明该谓词必须与 `NOT EXISTS` 合成 |
| `src/photo_search/indexing/cli.py` | `build --retry-failed` 帮助文案改为如实列出可回退状态（F7） |
| `tests/unit/test_p4_02o_semantic_build.py` | 新增 `test_a_source_scoped_build_selects_only_that_source`（+ `write_jpeg` import） |
| `docs/diagnostics/search-smoke-checklist.md` | F4：编码器断言改为条件式两分支 |
| `docs/reports/P4-02S-mpo-support-and-quality-smoke.md` | F2 归因更正 + F3 升级口径 |
| `docs/reports/P4-02N-real-query-encoder-wiring.md` | F9 建库口径说明（§4）+ F4 已改说明（§7） |

## 2. F1 根因与修复

根因：来源谓词被插在 `JOIN source_roots …` 与 `WHERE NOT EXISTS (…)` 之间，而谓词文案自带 `WHERE`。

```text
修复前（--source-id 1）：
  … JOIN source_roots AS r ON r.source_id = f.source_id WHERE f.source_id = ? WHERE NOT EXISTS (…)
  → sqlite3.OperationalError: near "WHERE": syntax error

修复后（--source-id 1）：
  … JOIN source_roots AS r ON r.source_id = f.source_id WHERE f.source_id = ? AND NOT EXISTS (…)

修复后（无 --source-id，SQL 与修复前逐字相同）：
  … JOIN source_roots AS r ON r.source_id = f.source_id WHERE NOT EXISTS (…)
```

`source_predicate` 只有两种取值（`""` 或 `"f.source_id = ? AND "`），占位符顺序仍是「来源参数 → space_id →
generation_id」，与 `semantic_selection` 的传参顺序一致。

## 3. 验证（本次修复轮自检）

| 项 | 结果 |
|---|---|
| `pytest -q tests/unit/test_p4_02o_semantic_build.py` | **9 passed / 0 failed**（修复前 8，`test-results/p402-fix-unit.xml`） |
| **B 级** `pytest -q tests/unit tests/integration` | **1774 / 0 failed / 0 errors / 48 skipped**（236.5 s，`test-results/p402-fix-full.xml`）= 审查基线 1773 + 新增 1 |
| `ruff check .` / `uv lock --check` / `git diff --check` | exit 0 / exit 0（96 packages）/ exit 0 |
| 真实库 CLI `build --dry-run`（无来源过滤） | exit 0、`targets=0`、`out_of_scope=479` |
| 真实库 CLI `build --dry-run --source-id 3` | exit 0、`targets=0`、`out_of_scope=91`（**修复前该命令 exit 1 + SQL 语法错误**） |
| 真实库 CLI `build --dry-run --source-id 999` | exit 0、`targets=0`、`out_of_scope=0`（负例：不存在的来源不报错） |

三个 case 的原始 JSON 落在 `test-results/p402-fix-o-dry-run.json`（同时补上了审查 F5 缺失的 dry-run 产物；
它独立复现了审查时只读库核对的 `pending=479`）。真实库命令均为只读 dry-run：不加载模型、不写索引、不扫描新目录。

新单测的四条断言：无过滤时 pending 覆盖两个来源；`source_id=<库根>` 只选中该来源的 3 个目标；
另一个来源只选中 1 个；不存在的来源 0 目标 0 检查；并且 `build_index(source_id=…)` 只对那 3 个目标调用
图像塔（`adapter.paths` 不含第 4 张），生成 generation 的 `CONFIRMED=3`。

## 4. 审查发现的处置

| 编号 | 严重度 | 处置 |
|---|---|---|
| F1 | 阻塞 | **已修**（§1/§2），待有界复审 |
| F2 | 裁决 | 报告 §4.1 归因已更正 + 明确「MPO/HEIC 只能被索引，不能作上传查询」；**是否放宽 P2-07 上传白名单=未决**（要动冻结断言，须另立契约卡） |
| F3 | 裁决 | 报告已写明升级口径「已装库升级后需跑一次 `catalog thumbnail --retry-failed`」；**是否 bump `catalog-thumbnail@N` 以自动失效旧失败缓存=未决**（属打包/升级卡） |
| F4 | 文档 | **已改**：`search-smoke-checklist.md` 的三处绝对断言改为条件式（有权重/无权重两分支） |
| F5 | 证据 | **已补**：真实库 dry-run 产物落 `test-results/`（479/91/0） |
| F6 | 低 | **不修**：`queued` 计「尝试数」、`live` 恒 0 与 face 处理器同形（face 也按尝试计数）；要改应 face/OCR/taken-time 一起改，超出本修复轮 |
| F7 | 低 | **已改**：`--retry-failed` 帮助文案列出真实可回退状态（行为不变，仍与 face `_rewind` 同形） |
| F8 | 低 | **不修**（避免改调度语义）：`steps >= len(targets)` 与 drain `len(targets)+16` 两个上界写入边界；需要时用 `indexing.cli drain` |
| F9 | 低 | **已改**：P4-02N 报告点明 §4 用冻结 P2 夹具 `submit_vector`（伪 `rev-<asset_id>`）发布向量，不是产品建库入口 |

## 5. 复审范围（有界）与残留

只复核：`semantic.py` 的谓词合成与选择结果、新增单测、`cli.py` 文案，以及点名集成
（`test_p4_02a_maintenance_entry.py`、`test_p4_02c_recovery_and_idempotency.py`、`test_p2_index_submission.py`）
与 B 级全量。用户裁决「先 1 后 2」后，本轮已完成有界复审（§6）：只复核原失败边界、新增 diff 与直接回归 ⇒ **P4-02O/Q 产品面记为 `ENGINEERING REVIEW PASS`**；F2/F3 两个未决项属 P4-02S，不受此影响。

**未验证 / 边界**：未重跑真实图库建库/缩略图/自检索（无新授权，且 F1 只影响目标选择；`--source-id` 的真实
建库路径只用 dry-run 验证）；未重建打包产物、未跑 `packaged_api_smoke.py`；GTX 1660 与 §12.2 门槛仍未验证。
F2/F3 的两个未决项属于 P4-02S 的收口条件，与本次修复无关。
## 6. 有界复审（修复轮，只复核原失败边界 + 新增 diff + 直接回归）

| 复核项 | 证据 | 结论 |
|---|---|---|
| 无过滤 SQL 未被改动 | 用修复前模板渲染 `{source_filter}=""` 与现在 `source_predicate=""` 的渲染结果比较 | **逐字相同** |
| 来源过滤是纯增量条件 | `filtered.replace("f.source_id = ? AND NOT EXISTS", "NOT EXISTS") == unfiltered` | **True**（唯一新增的是那一个合取项） |
| 谓词位置 | 生成的 SQL 里 `source_id = ? AND` 出现在 `JOIN source_roots` 之后、`NOT EXISTS` 之前 | 通过 |
| 占位符顺序 | 过滤 SQL 仍只有 3 个 `?`（来源 → space_id → generation_id），与 `semantic_selection` 传参顺序一致 | 通过 |
| `limit` × 来源过滤 | 夹具（来源1=3 张 / 来源2=1 张）：`source_id=1, limit=2` → targets 2、checked 2、`coverage_complete=False`；`build_index(source_id=1, limit=2)` → `CONFIRMED=2` | 通过 |
| 负例 | 真实库 `--source-id 999` → exit 0 / 0 目标 / 0 检查 | 通过 |
| 真实库正例 | `--source-id 3` → exit 0 / `out_of_scope=91`；无过滤 → 479 | 通过 |
| 同类写法排查 | 全仓搜 `{source_filter}`：`face/service.py`、`ocr/service.py` 只注入一个 WHERE（后接 `ORDER BY`），`media/status.py` 用 ` AND l.source_id = ?` 合成 | **无同类缺陷**；`NOT EXISTS` + 注入 WHERE 的组合只出现在已修的 `semantic.py` |
| 回归 | 聚焦五文件 67/0、B 级 1774/0/0/48、ruff/lock/diff-check 全绿 | 通过 |

注明：本轮的实现与复审是**同一执行者**（用户裁决先修后审、且不单独起会话）；因此上面的结论按「有界复审」而非「独立审查」记账。
若要严格独立，可另起一轮只读该谓词与新增用例，但这不再是闭合 F1 的必需条件。