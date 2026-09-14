# P2-04 — 文件名／有效地点 FTS、受控 MATCH 与改名重投影报告

日期：2026-09-14。状态：**PASS（B 级完整 unit/integration + D 级故障注入/恢复 + C 级真实打包 smoke）**。

本任务只实现主架构 §4.3／§4.5.3／§4.5.4／§9.3～§9.5 与 ADR-0005 §10／§11 已冻结的语义：
FTS 存储结构、tokenizer 世代、MATCH 策略与「候选不得混为有效地点」都不是本任务重新定义的。
全部夹具都在 `%TEMP%`／`dist/` 内，没有读取、移动或删除任何真实图库，没有联网，没有新增依赖。

## 1. 基线与交付边界

| 项 | 实际值 |
|---|---|
| 起始 HEAD | `6cc75a9`（P2-03C），开始时 `git status --short` 为空 |
| 前置 | `SUPPORTED_SCHEMA_VERSION = 5`，migration 连续 0001～0005，P2-03A/B/C 的目录、人工归属、建议、数据包与页面闭环已 PASS |
| 新 migration | `catalog/migrations/0006_search_fts.sql`（10,413 B）；schema_version 5 → 6 |
| 新生产模块 | `src/photo_search/search/{__init__,tokens,documents,query}.py` |
| 改动生产文件 | `catalog/schema.py`（版本号+注释）、`api/{queries,routes,models}.py`（加性 `text=` 参数与投影摘要）、`scheduler/projection.py`（只读投影摘要） |
| 新增测试 | 5 个文件、**35 个用例**（tokenizer 11、migration 7、document 8、projection 5、api 4） |
| 改动测试 | 只改版本相对断言：`tests/unit/test_catalog_migrations.py` 的 `CORE_TABLES`、`tests/integration/test_p2_scheduler_recovery.py` 的 `V5_TABLES`→`V6_TABLES`、`tests/integration/test_p2_dataset_migration.py` 的硬编码 `5` |
| 工具 | `tools/diagnostics/packaged_api_smoke.py` 新增一条打包内文本检索检查 |

未修改：ADR-0001～0005 与冻结 JSON schema、`0001`～`0005`、五字段 job 身份、lease/attempts/迟到提交、
资产/位置/缺失判定、P2-03A/B 的人工锁/排除/幂等/批量/撤销语义、`catalog_filter_revision`/
`suggestion_revision` 触发矩阵、候选 `SUGGESTION_ONLY` 语义、错误码、`places/**`、`geo/**`、
`ingestion/**`、`workers/**`、打包入口、`pyproject.toml`、`uv.lock`、任何页面资源。
没有实现 `POST /search`、融合排名、search session/游标、SearchProfile 切换、语义召回（P2-05/06）
或搜索页面（P2-07）；没有新增 `jieba` 或任何第三方依赖。

## 2. `0006` 迁移：`search_documents` + FTS5 外部内容表 + 受影响集队列

```
search_documents(asset_id PK → assets(asset_id) ON DELETE CASCADE,
                 filename_text, user_text, ocr_text, caption_text, tag_text, place_text,
                 source_fingerprint, tokenizer_version, fts_generation_id, updated_at)
search_documents_fts  USING fts5(6 列, content='search_documents',
                                 content_rowid='asset_id', tokenize='unicode61')
search_projection_queue(asset_id PK → assets ON DELETE CASCADE, cause, queued_at)
```

- **外部内容表 + 显式 rowid**：FTS 行的 `rowid` 就是 `asset_id`，检索用
  `search_documents_fts MATCH ? AND rowid = <alias>.asset_id` 的下推谓词，绝不依赖「另一张表刚好
  rowid 相同」。`INSERT/UPDATE/DELETE` 三个触发器在调用方事务内维护索引，因此**资产级联删除**
  （`ON DELETE CASCADE`）也会一并清掉索引行；`search_documents_fts('integrity-check')` 是本仓库
  可执行的独立一致性断言（`search.documents.verify_index`）。
- **只写真实来源**：本任务只填 `filename_text` 与 `place_text`；`user_text`/`ocr_text`/
  `caption_text`/`tag_text` 一律写入 `''`（列已预留，P2-05/P3 负责），没有任何伪造内容。
- **`source_fingerprint`**（额外记账列，卡外最小补充）：六列文本 + tokenizer 世代的 canonical JSON
  SHA-256。重投影因此可判定「已是最新」而跳过写入，也可被测试独立核对（`stale_document_asset_ids`）。
- **受影响集持久化**：`search_projection_queue` 由触发器在事实变更的同一短事务写入，覆盖
  文件名变化（`file_locations` 插入/改名/改名绑定/删除）、有效归属变化（`asset_place_assignments`
  插入/更新）、地点改名或退休（`places.canonical_name`/`lifecycle`）与别名增改（`place_aliases`）。
  地点类触发器用递归 CTE 展开**子树**，因此「改父级、只有后代被归属」的资产同样入队。
- 迁移仍是既有 `_apply_migration` 的单事务 + 迁移前备份 + 目标 schema 复核；FTS5 影子表
  （`_data`/`_idx`/`_docsize`/`_config`）与虚拟表一起被 `verify_schema` 逐对象比对。

## 3. tokenizer v1 与受控 MATCH 策略（写进契约，可测试）

| 规则 | v1 行为 |
|---|---|
| 规范化 | NFKC（全角→半角）后 casefold，再做一次 NFKC（幂等） |
| token 字符 | 与 `unicode61` 默认类别一致：`L*`/`N*`/`Co`；其余全部是分隔符 |
| CJK/假名 | **逐字成 token**（`杭州西湖.jpg` → `杭 州 西 湖 jpg`），因此中文可做子串命中；其它文字保持整词（`café` 不拆字） |
| 默认查询 | 按空白切段；连续 CJK 成一个**短语**，拉丁/数字成**前缀项**，全部 AND |
| 显式短语 | 双引号内容原样成为短语（不加通配、不删词） |
| 停用词 | 拉丁语法词（the/and/of…）与 CJK 语法字（的/了/是…）在存在真实项时被丢弃；CJK 停用字同时结束当前 run |
| 特殊字符 | `"`、`*`、`(`、`)`、`-`、`NEAR`、`MATCH`、`AND`… 一律只是普通字符/分隔符，永远不成为 FTS 语法；每个 token 引号转义后绑定参数 |
| 结构化 422 | 空查询、只有分隔符/特殊字符、只有停用词、>256 字符、>32 项 → `VALIDATION_ERROR`（`reason=NO_SEARCHABLE_TERM`/`TOO_MANY_TERMS`），绝不变 500，也不静默返回空集 |

`TOKENIZER_VERSION = 1`、`FTS_GENERATION_ID = "fts-v1"` 同时出现在 `search_documents` 行与
`GET /assets?text=` 响应的 `search` 块里；`documents.rebuild_all` 是「只重建 FTS、不重跑 OCR/模型」的
入口（幂等：已最新的行不重写）。

## 4. `place_text` 来源规则

只写**当前有效地点**：`MANUAL_ASSIGNED` 的那一个 `place_id`，按「根 → 叶」输出规范名与
**ACTIVE 别名的并集**（含 `DATASET` 来源别名）。逐项排除（都有用例）：

`SUGGESTION_ONLY` 候选（`place_resolution_candidates` 不参与投影）、被排除地点、
`MANUAL_UNASSIGNED`、`AUTO_ALLOWED`（无有效地点）、`RETIRED` 目录项。

`filename_text` 只放**PRESENT 位置**的 basename（含扩展名），不写目录名、不写绝对路径；同一资产
多个 PRESENT 位置的文件名都进文本（同 basename 去重）。文本命中只代表文字命中：地点条件仍由
SQLite 关系过滤（`place_ids`/`place_state`）权威判定，命中说明与「人工指定／有效归类」的区分保持不变。

## 5. 重投影路径（短事务记录 + 分批派生 + 可识别滞后）

1. 目录/归属/文件名变更 → 同一事务写 `search_projection_queue`（`catalog_filter_revision` 仍由
   P2-03A/B 的既有入口至多 +1，触发器不参与版本推进）。
2. `search.documents.drain_queue` 在一个短事务里最多处理 `DRAIN_BATCH_SIZE=200` 个资产（重算文本 +
   写 `search_documents` + 删除队列行）；`catch_up` 一次最多 4 批（默认 2000 资产）。
3. `GET /assets?text=` **先做一次有界 catch-up，再执行 MATCH**，并把
   `search={state: SYNCED|LAGGING, queued_assets, processed_assets, documents, tokenizer_version,
   fts_generation_id, terms, match_expression}` 一并返回；队列为空时**不写任何东西**（保持纯读）。
4. 队列仍未清空时如实返回 `LAGGING`：权威的 SQLite 过滤与去重结果不受影响，文本召回则可能少最近
   改名的资产（这正是给调用方的「滞后/补查信号」，P2-05 接入调度后由后台继续追赶）。
5. `GET /capabilities` 的 `search` 块（只读、无文本、无路径）报告 `documents/queued_assets/
   generations/tokenizer_version/fts_generation_id/state`。

## 6. 验收命令与结果

| 命令 | 结果 |
|---|---|
| `uv run --no-sync pytest -q -p no:cacheprovider tests/unit tests/integration` | exit 0；`--collect-only` 计 **599** 例，进度行 1 个 `s` → **598 passed / 1 skipped** |
| 本任务 5 个新文件 | **35 passed**（tokenizer 11、migration 7、document 8、projection 5、api 4） |
| `uv run --no-sync ruff check .` | exit 0，`All checks passed!` |
| `git diff --check` | exit 0，无输出 |
| `uv run --no-sync uv lock --check` | exit 0，`Resolved 95 packages`（依赖未变） |
| 版本耦合回归 | `tests/unit/test_catalog_migrations.py`、`tests/integration/test_p2_scheduler_recovery.py`、`tests/integration/test_p2_dataset_migration.py` 改为版本相对后原样通过 |

本环境沿用既有事实：`uv` 默认缓存在沙箱内被拒绝、pytest basetemp 在沙箱内 ACL 被拒绝，
因此验证命令在沙箱外运行（无功能影响）。

## 7. D 级故障注入与恢复（逐项）

| # | 场景 | 实测 |
|---|---|---|
| 1 | v5→v6 正常升级 | 备份先生成（备份内 `schema_version=5` 且无 7 张新表）；单事务推进 `applied=(6,)`；资产/位置/地点/归属/两个 revision 逐行逐列不变；新库零 `search_documents`、零队列行 |
| 1b | 注入损坏 0006 / 缺目标对象 / 过新库 | 损坏脚本 → `MIGRATION_FAILED`、版本仍 5、无半成品表/探测键、旧行不变；`DROP search_projection_queue` → `SCHEMA_INCONSISTENT`（`missing_objects` 指名）；`SUPPORTED+1` → `SCHEMA_TOO_NEW` 且主库字节与目录列表不变（迁移与 `Catalog.open` 两条路径） |
| 1c | 非法写入 | 非 64-hex 指纹 / `tokenizer_version=0` / 队列未知 cause / 不存在 asset_id 全部被 CHECK/FK 拒绝 |
| 2 | FTS 内容一致性 | 插入→`MATCH` 命中；改名 update 后旧词条 0 命中、新词条 1 命中；`integrity-check` 通过；删除资产（级联）后 `search_documents` 与 FTS 行都消失；重扫改名后只剩新名称（`COUNT(search_documents)=1`、`COUNT(fts rowid)=1`） |
| 3 | 重投影 | 改名 → 同一事务持久记录子树受影响集（含只被后代归属的资产）、`catalog_filter_revision` 恰好 +1、`suggestion_revision` 不变；分批派生；**在批次第 3 个资产注入异常 → 整批回滚**（队列完整、文本未变、新名称 0 命中），重跑后无重复行、无重复索引项、新名称可见、`stale_document_asset_ids` 为空 |
| 3b | 有界与滞后信号 | `catch_up(batches=1, batch_size=2)` → `processed=2 / remaining=3 / state=LAGGING`；同一时刻权威 `place_ids` 过滤已返回全部 3 个资产；继续 catch_up 后 `SYNCED` |
| 4 | 候选隔离 | 资产只有 `place_resolution_candidates`（无有效归属）时 `place_text` 为空且候选地名 0 命中，候选行本身仍在；人工指定后立即（补投影后）命中祖先链与别名；`MANUAL_UNASSIGNED`、被排除地点、`RETIRED` 地点都不进文本 |
| 5 | MATCH 边界 | `""`/空白/`"`/`***`/`()`/257 字符 → 422 `VALIDATION_ERROR`；`(西湖)`、`C:\Users\me\photo.jpg`、`NEAR('a','b')` 都被当普通文本处理并命中/不命中而不报错；中英数字混排、显式短语、全半角、停用词策略逐条断言（含 `uv` 内 unicode61 词表对照） |
| 6 | 既有不变量 | 全部既有测试原样通过；一次目录改动 `catalog_filter_revision` 至多 +1；`suggestion_revision` 不受文本投影影响；改名/归属不产生任何 `artifacts`/`index_outbox`/`index_entries`/`index_generations` 行，`assets.created_at` 不变 |

## 8. 隐私与访问边界

- FTS 文本只含 basename 与地名（含别名），断言不含盘符/目录名/绝对路径/EXIF 坐标
  （`30.2741`、`120.1551`、`latitude` 均被显式断言排除）。
- `/assets?text=` 与 `GET /capabilities` 的 `search` 块都不含坐标与任何本机路径；坐标仍只在会话保护的
  `/assets/{id}/geo`。
- 沿用既有 loopback Host／内存 session／Origin／double-submit CSRF：无 session 的 `text=` 请求 401，
  参数错误 422，响应中不出现凭证；错误信封与 Redactor 未改。

## 9. C 级：真实打包（因导入图与新 migration 变化而执行）

任务卡的 C 触发条件是「新增依赖、`workers/**`、打包入口或新增 package-data 目录」——本次都没变
（第 6 个 `.sql` 落在既有 package-data 目录）。但本任务**改了应用导入图**（`api/queries.py` 现在 import
`photo_search.search`）并新增 migration，正是 P1-04 曾经踩过的「冻结包缺 `.sql`／缺模块」失效面，
因此按 P2-03B 的约定补做真实打包验收：

| 检查 | 结果 |
|---|---|
| `uv run --no-sync python tools/packaging/build_onedir.py` | exit 0（PyInstaller 53 s + bundling 6 s），产物 `dist/PhotoSearch`，21 个 NVIDIA DLL |
| bundle 内 package-data 审计 | `_internal/photo_search/catalog/migrations/` 含 **0001～0006** 全部 6 个 `.sql`；bundle 合计 3,541,272,714 B |
| `pyi-archive_viewer -r -l` 审计 | 归档内含 `photo_search.search`、`photo_search.search.documents`、`photo_search.search.query`、`photo_search.search.tokens` |
| `packaged_api_smoke.py --bundle dist/PhotoSearch --work-dir dist\p2-04-smoke` | exit 0，`status=ok`、`problems=[]`：中文/空格安装路径下启动 1.0 s、扫描 `SUCCEEDED`、缩略图/离线/会话/页面检查全通过；**新增** `GET /assets?text=照片` → 200、命中资产 `[1]`、`search.state=SYNCED`、`fts_generation_id=fts-v1`、`documents=1`、`queued_assets=0`、响应无本机路径 |
| 磁盘与清理 | 本次把 smoke 工作目录放在 **D 盘**（`dist\p2-04-smoke`）以避免 C 盘压力；结束后只删除本任务创建的这一目录（约 3.5 GB），其中应用用 `icacls` 收紧过 DACL 的 `runtime\session.json`（约 1 KB）在沙箱内无法删除而残留（与 P2-03C §7 同一副作用） |

打包进程成功迁移到 `schema_version=6` 并能在冻结解释器里执行 FTS5 MATCH，本身就是「0006 已入包 +
SQLite FTS5 在冻结运行时可用」的证明。

## 10. 未验证项与残留边界

1. **有界追赶**：单次请求最多追赶 2000 个资产。一次改名若影响更多资产，响应会如实报 `LAGGING`，
   需要后续请求（或 P2-05 接入后台调度）继续派生；本任务没有常驻调度线程。
2. **没有重建 CLI 工具**：`search.documents.rebuild_all` 是「tokenizer 世代升级只重建 FTS」的入口，
   但尚未接到 `tools/**` 或调度器；世代升级目前仍需在代码/测试中显式调用。
3. **`filename_text` 只取 PRESENT 位置**：整库离线（全部位置 MISSING）的资产按名称检索不到；
   这是本任务记录在案的边界，改它需要先更新架构说明（卡 §1）。
4. **同一资产多个 PRESENT 名称之间的短语边界**：多个 basename 以空格连接进同一列，理论上存在
   「短语跨两个文件名」的相邻命中；只影响同一资产多位置的语义边界，单资产单位置不受影响。
5. **停用词只有语法词**：拉丁 24 词 + CJK 18 字为 v1 固定表；只含停用词的查询按 422 拒绝（策略已写入
   §3，不是「悄悄返回空集」）。
6. **`search` 是加性响应字段**：不带 `text=` 的 `/assets` 响应只多了显式 `"search": null`，
   排序/游标空间/筛选语义与 P2-04 之前完全一致，既有断言原样通过。
7. **候选/数据包链路未重跑**：候选隔离用例直接写入 `place_resolutions`/`place_resolution_candidates`
   行（不经解析器）；解析器本身的行为仍由 P2-03B 验收，本任务只证明候选绝不进入 `place_text`。
8. **前端未接**：没有任何页面调用 `text=`，因此不需要浏览器 smoke 与新的页面 C 级证据（P2-07）；
   本次 C 级只覆盖打包后的 API 行为。
9. **`GET /assets` 仍不是快照隔离读**（沿用 P2-03A §10.2／P2-03C §9）；本任务只新增了有界 catch-up
   与其摘要，没有引入 search session 语义。
10. **`fts_generation_id` 只作记账**：不同世代的行不会被自动重写，需要显式 `rebuild_all`；
    `stale_documents` 计数已在投影摘要中暴露。

## 11. 结论与交接

P2-04 的「`0006` 派生检索层（`search_documents` + 外部内容 FTS5 + 受影响集队列）+ 版本化
tokenizer/受控 MATCH + 有效地点文本投影 + 改名重投影与恢复 + 加性 `text=` 检索面」闭环已按 **B 级 +
D 级故障注入 + C 级打包**真实验收，因此标记 PASS。`schema_version` 由 5 推进到 6；`0001`～`0005`、
冻结 ADR/JSON schema、依赖、`places/**`、`ingestion/**`、`workers/**` 与打包入口均未改动。

下一任务是 **P2-05 — 图文 + FTS 并行召回、RRF 融合、地点候选预过滤与权威补查**，推荐
**DS-H（DeepSeek V4.1 Flash / 高推理）**，验收 **B + 确定性检索对照**；任务卡见
[docs/tasks/P2-05.md](../tasks/P2-05.md)。
