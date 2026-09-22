# P4-00B 独立 DS-H 全量工程主审报告

- 任务卡：[docs/tasks/P4-00B.md](../tasks/P4-00B.md)
- 被审实现报告：[docs/reports/P4-00B-taken-time-implementation.md](P4-00B-taken-time-implementation.md)
- 冻结契约：[ADR-0011](../decisions/ADR-0011-taken-time-timezone-and-order-contract.md)（Accepted）、
  `docs/decisions/contracts/p4-time-contracts.v1.json`
- 主审会话：独立 DS-H / DeepSeek V4.1 Flash / 高推理（新会话，只读实现报告 + 真实 diff/证据，不信报告结论）
- 观察基线：`8e48fc7` + P4-00A/P4-00B 全部未提交交付物
  （主审开始时 `git status --short` 为 29 modified + 16 untracked = 45 项；主审结束时被审交付物仍是同一 45 项，
  未增删生产代码/迁移/既有测试/契约文件；本次主审只新增本报告与下一任务卡）
- 收口方式：P4-00A/P4-00B 交付物 + 本主审报告 + `P4-00C-A`/`P4-00C-B` 任务卡已作为**一个版本提交**入库，
  工作树干净（提交信息 `P4-00A/B: ADR-0011 taken-time contract, 0015 migration, extraction, API/UI,
  packaged closure and DS-H review`；用 `git log -1` 取当前提交号）
- 结论：**P4-00B：PASS（独立 DS-H 全量工程主审；`SOL_NOT_REQUIRED`，未命中 V3.1 §2.2）**

本主审没有改任何生产代码、迁移、既有测试、ADR、companion、依赖或 `uv.lock`；唯一闭环的缺陷是
**证据陈旧**（见 §5 F1），通过在本会话内真实重建冻结包并重跑 C 级三条验收闭环，未扩大改动面。

## 1. 主审做了什么（不是复述报告）

1. 读任务卡、被审报告、ADR-0011 全文与 P4 companion，逐条把 ADR 条款映射到真实实现（§3）。
2. 读完整 diff（`src/photo_search/{taken_time,api,search,catalog,scheduler,web}`、测试、打包工具），
   不是只读报告摘要。
3. 独立重跑 B 级（全量 + 聚焦）、Ruff、`git diff --check`、`uv lock --check`。
4. 独立重跑 C 级：真实重建 onedir → 逐个资源哈希比对 → 中文/空格目录 `packaged_api_smoke` →
   独立副本缺 `0015` 反例。
5. 独立重建浏览器夹具并与被审报告记录的期望集合逐字段比对；再用**真实内置浏览器**复跑关键步骤。
6. 复核报告 §9 的 6 处契约留白裁定，逐条给出主审裁定（§6）。

## 2. 独立复现的证据

| 证据 | 命令（要点） | 主审实测 |
|---|---|---|
| B 全量 | `uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration --junitxml=test-results/p4-00b-review-full.xml` | **1598 / 0 / 0 / 48**（205.877 s），与被审报告逐数一致 |
| B 聚焦 | 同一命令限定 P4 契约/实现/迁移/抽取/API/D 级 + P3 echo 契约 + 迁移表集合 + 前端静态 | **353 / 0 / 0 / 0**（25.928 s，`test-results/p4-00b-review-focused.xml`） |
| 静态 | `ruff check .` / `git diff --check` / `uv lock --check` | 全 clean（Ruff exit 0；lock 仍 95 packages） |
| C-1 真实重建 | `uv run --no-sync python tools/packaging/build_onedir.py` | `dist/PhotoSearch` 重建成 15 个迁移 + 27 个 `photo_search` 数据资源**逐个 SHA-256 与源码一致**（§5 F1） |
| C-2 打包 smoke | `tools/diagnostics/packaged_api_smoke.py --bundle dist/PhotoSearch --work-dir "<中文 空格>"` | `status=ok`、`problems=[]`；`packaged_taken_time_schema={schema_version:15, has_asset_metadata:true, has_closed_sources:true, has_capture_index:true, rules_version:1}`；`bundled_migrations.matches_source=true`；包内 `/web/app.js` 实测 45629 B = 源码字节数（`test-results/p4-00b-review-packaged-smoke.json`） |
| C-3 缺件反例 | 独立副本删除 `_internal/photo_search/catalog/migrations/0015_taken_time.sql`，全新 data root，真实 HTTP | **409 `SCHEMA_INCONSISTENT`**，`versions=[1..14]` vs `expected_versions=[1..15]`；data root 只有 `runtime/session.json` + 0 个 SQLite 对象的空 `catalog.db` ⇒ **零写**（`test-results/p4-00b-review-missing-0015.json`） |
| 夹具重现 | `tools/diagnostics/taken_time_smoke_fixture.py --reset`（全新目录） | `status=ok`、`problems=[]`、地点 #8 已归类、`search-smoke-profile` 已激活、`state=READY(8/8, 3)`；`expected` 块与记录文件 `test-results/p4-00b-browser-fixture.json` **逐字段相同**（`capture_desc_order=[8,1,2,3,7,6,5,4]`、尾桶 `[7,6,5,4]`、`capture_range_2022=[1,8]`、`search_hits_2022=[1,8]`、`#8` 偏移 480 / `#1` 偏移 null、两者 civil key 同为 `63792642667000`） |

### 2.1 记录式真实浏览器（主审复跑，Codex 内置浏览器）

| 复核点 | 实测 |
|---|---|
| `capture_desc` 排序与「文件时间」 | 顺序 `8,1,2,3,7,6,5,4`；`#3`（EXIF 根时间）与 `#7..#4`（文件修改时间）显示「文件时间」，`#8/#1/#2` 显示「拍摄时间」 |
| 有无偏移 | `#8` = `2022-07-05T18:31:07.000（+08:00）` HIGH；`#1` = 同一墙钟时间、**无任何时区**、MEDIUM；页面上不存在 `+00:00` 或 UTC 推断 |
| 左闭右开（ADR §11.4，主审补测） | `capture_from=2022-07-05T18:31:07.000`（无上界）⇒ `{#8,#1}`；`capture_to_exclusive=2022-07-05T18:31:07.000`（无下界）⇒ `{#2}`，恰好等于右端的 `#1/#8` **不出现** |
| 搜索面 | `/search` 回显 `capture_from=2022-01-01T00:00:00.000`、`capture_to_exclusive=2023-01-01T00:00:00.000`，命中 `#1,#8`；空范围文案为「拍摄时间已抽取完成…这个范围内确实没有命中」 |
| 上传闭集 | `#upload-form` 内无任何 capture 字段 |
| 隐私/窄宽 | 正文无 `2022:07:05 18:31:07`、无盘符路径、无坐标；URL 无 `token=`；`clientWidth=scrollWidth=380`（无横向溢出） |

## 3. ADR-0011 → 实现逐条核对

| ADR | 实现位置 | 主审核对结果 |
|---|---|---|
| §2 表与列 | `migrations/0015_taken_time.sql` | 主键 + 六列逐列一致；五组 `(source,confidence)`（用 OR 等式，规避 SQLite CHECK 的 row-value `IN` 限制）；`FILE_MTIME ⇔ sort IS NULL`；`FILE_MTIME ⇒ offset IS NULL`；sort ∈ `[0,315537897599999]`；offset ∈ `[-720,840]`；camera 1..128；索引 `(time_source, taken_at_sort, asset_id)`；`metadata.taken_time_rules_version=1`；无回填 |
| §2 行生命周期 | `taken_time/extract.py::persist_taken_time_row` | 只由抽取路径写入；`scheduler/engine.py` 在**同一** `catalog.write()` 事务内写行并在真正变化时 `+1 CAPTURE_TIME_CHANGED`；相同行 no-op（幂等） |
| §3.1 civil key | `taken_time/contract.py::civil_key` | 原点 `0001-01-01`、非负、偏移永不参与；`normalize_bound` 定宽定长，展示与键同序 |
| §3.2/§3.4 | `taken_time/exif.py` | 只接受显式整分偏移（`±HH:MM`/`±HHMM`/`±HH`），越界/缺失 → `NULL` 且 `EXIF_ORIGINAL` 降 `MEDIUM`；亚秒取前 3 位；非法日历不产键 |
| §4.1/§4.2/§4.3 | `extract.py::_choose_from_fields`/`_fact_from_positions`/`_read_fields` | 子 IFD 用 `getexif().get_ifd(0x8769)`；`location_id` 升序走 `PRESENT`；坏值逐级吞掉；`FILE_MTIME` 取**全部**位置（含 MISSING）最小 `mtime_ns`；读后 `stat` 与录制观察不一致即不采纳；无扫描/当前/`created_at` 兜底 |
| §5.1/§5.2/§5.3 | `api/queries.py` | `order` 闭集含 `capture_desc`；`c1` arity 4、三字段形状，`known∈{0,1}`、尾桶 sort 恒 0、`asset_id≥1`；`v1`/`t1`/`c1` 互不解释（`_decode_cursor` 对前缀/arity 不匹配抛 `INVALID_CURSOR`）；续页 revision 失配 409 且 details 只用既有键；revision 不变时 keyset 不重不漏 |
| §6.1–§6.5 | `taken_time/contract.py::parse_capture_range`、`api/queries.py`、`search/{contracts,retrieval,service,sessions}.py` | 两个传输拼写同一语义；规范化失败四种 reason + `field`；左闭右开只对 `label=capture`；与其它条件 AND；只复用既有 `catalog_filter_revision`；`query_hash` 载荷 = ADR-0009 字段集 + 2 个规范化字段 |
| §6.6 | `extract.py` + `scheduler/engine.py` | 每事务至多一次 `CAPTURE_TIME_CHANGED`、`changed_asset_ids` 为该资产（D3 实测 4 事件 × `changed_count=1`） |
| §7 | `search/*` | 不新增 projection cause、不动 FTS/`search_projection` 8 键；`taken_time` 是兄弟块 |
| §8.1 | `api/models.py`、`api/queries.py`、`taken_time/read.py` | 列表恒有 2 个回显键 + 条件性四键块；详情 7 键闭合对象；四态只是三计数纯函数；规则版本失配时计数规范化为 0 且详情 `null` |
| §8.2 | `search/retrieval.py::scope_view`、`search/service.py::_filters_echo` | live echo = ADR-0009 的 11 键 + 2 = 13；P3 companion 未被修改；只扩展了那一条 live 键集断言并**只加键** |
| §8.3 | `catalog/errors.py`、`api/errors.py` | 新增唯一 `INVALID_TIME_RANGE`(422)，details 只用 `reason`/`field`；stale 复用 409 `CATALOG_FILTER_REVISION_STALE` 且不带会话键 |
| §8.4 | `models.py`/`web/**`/`read.py` | 响应/错误/URL 无 `taken_at_raw`、无 instant、无路径/坐标/身份标签；`camera` 仅详情页且规范化限长 |
| §9/§10 | `0015` + `schema.SUPPORTED_SCHEMA_VERSION=15` + `tools/diagnostics/packaged_api_smoke.py` | `0001～0014` 未改动；事务回滚/`SCHEMA_TOO_NEW` 由既有迁移路径保证；C 级三条已由主审真实重跑（§2） |

## 4. 冻结面未被破坏

- `git status` 中**没有** `0001`～`0014`、任何 ADR/companion、`pyproject.toml`、`uv.lock`、`PhotoSearch.spec` 的改动；
  新增 migration 只有 `0015_taken_time.sql`。
- P4 companion SHA-256 实测 `80189349bb4ea306225f9eae77ae9266c6d81c42de52c0b72bdc940a0702afd7`，与 ADR §13/状态一致。
- P3 person-search companion 未被修改（`test_p3_person_search_contracts.py` 的哈希断言仍在跑）。
- 依赖未变（`uv lock --check` 95 packages）；HEIC 相关依赖属于 P4-00C 范围。
- 未访问任何真实照片库：全部夹具为合成 JPEG，写在项目内/`tmp_path`；主审的浏览器与打包验证同样只用合成夹具。

## 5. 主审发现

### F1（阻断级证据缺陷，**已在主审会话内闭环**）：被审报告 C 级证据包不覆盖最后一版前端资源

- 观察：`dist/PhotoSearch` 的构建时间 `19:51:23`，而 `src/photo_search/web/app.js` 的修改时间 `19:57:41`；
  包内 `_internal/photo_search/web/app.js` 为 **43369 B**，最终源码为 **45629 B**（SHA-256 不同）。
- 根因：本卡新增的包内检查只比对 `catalog/migrations/*.sql`（`packaged_api_smoke.py::bundled_migrations`），
  没有覆盖 `collect_data_files('photo_search')` 的其它数据资源；`app.js` 在重建之后还被修过一次（报告 §8.5）。
- 影响：被审报告 §5 的两条 C 级证据（真实重建、打包 smoke）在**当时**的包上是真的，但**没有**证明最终工作树的冻结包
  形状；`0015` 迁移本身不受影响。
- 处置（同一主审会话闭环，无生产改动）：用最终源码**真实重建** onedir，重跑 `packaged_api_smoke`，并重做缺件反例；
  结果见 §2 的 C-1/C-2/C-3。重建后包内 15 个迁移与 27 个数据资源与源码逐个 SHA-256 一致，包内 `/web/app.js`
  实测 45629 B。**该发现不得再计为未决项。**
- 遗留建议（R1，P2）：把 `bundled_migrations` 扩展为「`collect_data_files('photo_search')` 的全部数据资源与源码逐字节一致」，
  或在 smoke 中至少加入 `web/**`，否则同类陈旧资源今后仍可能静默通过 C 级。

### R2（P3，非阻断）：`/search` 的 `#capture-state` 是无写入路径的死元素

`search.js` 只对 `els.captureState` 调用 `setStatus(...,"")`（重置/失败/清空），从无成功路径写入；诚实状态实际由
`resultNotices`/`queryStatus` 表达（主审在浏览器确认两者都正确）。元素本身无害，但建议接线或删除。

### R3（P3，非阻断）：超长 capture 边界返回 `VALIDATION_ERROR` 而非 `INVALID_TIME_RANGE`

两个面都对边界加了 `max_length=32`，因此 >32 字符的输入先被 Pydantic/FastAPI 拒成 422 `VALIDATION_ERROR`。
ADR §6.2 未枚举该形状，两者同为结构化 422，主审裁定**可接受**；若契约希望逐码闭集覆盖，应由后续契约修订明确，不在本卡改。

### R4（P3，文档勘误）：任务卡与 ADR §10 的构建命令路径不准确

两者写 `tools/build_onedir.py`，真实脚本为 `tools/packaging/build_onedir.py`（被审报告用的是正确路径）。
ADR 已冻结，本主审不改；后续文档修订时同步。

### R5（P3，纵深防御）：`EXIF_ORIGINAL + HIGH ⇒ offset 非空` 只在载荷校验器里强制

表级 CHECK 只冻结 ADR §2 要求的两条与五组配对（符合契约），`HIGH` 必带偏移的不变量在
`validate_payload` 中强制。属可接受的实现分工；若担心手工改库，可在未来加一条表级 CHECK。

### R6（P3，覆盖率）：ADR §11.4「恰好等于右端」缺少自动化用例

API 集成用例覆盖了左端包含、空区间拒绝与全域闭区间，但没有一条把 `capture_to_exclusive` 精确设为某资产的
`taken_at`。主审已在真实浏览器与夹具路径上手工验证（§2.1）；建议后续卡补一条最小用例，不改本卡结论。

## 6. 对报告 §9 契约留白裁定的复核

| 报告 §9 | 主审裁定 |
|---|---|
| §9.1 规则版本失配 ⇒ 详情 `null`、计数归零 | **接受**。ADR §2 只排除「信任与过滤」，§8.1 要求状态是纯函数；把不可信派生事实发布为详情才是违约 |
| §9.2 输入修订 = 内容哈希 + 录制观察摘要 | **接受**。ADR §2 明确观察值以 `file_locations` 为准；不并入观察则「留待重抽」在产物层不可实现；D4 证明同一内容在观察变化后成为新逻辑作业且旧产物保留 |
| §9.3 失配时 `capture_desc` 仍确定性排序 | **接受**。ADR §2 只排除状态与过滤，§5.1 的 SQL 无门禁；若失配时改尾桶，会让同一 revision 下游标语义随规则版本漂移，风险更高 |
| §9.4 示例地名包 `maximum` 14 → 15 | **接受**。`tools/places/examples/*` 是可替换的示例数据集，不是任何冻结 companion；工具仍按第三方包自身声明拒绝 |
| §9.5 浏览页逐卡懒加载详情 | **接受**。ADR §8.1 刻意不给列表加逐行投影；代价是 N+1 读，30 万规模延迟已列未验证项，不得当 PASS |
| §9.6 非模型处理器记录纯 Python 运行时能力 | **接受**。与 GPS 抽取器同一运行时包语义；用 ONNX 包做资格判定会让非模型处理器被错误 BLOCKED |

## 7. 未验证项（继承被审报告，仍不得当 PASS）

30 万规模下 `capture_desc`/时间过滤延迟与最终索引列序、全库抽取耗时、多进程并发抽取、`0015` 在真实库上的
迁移耗时、真实内部样本（大小写、坏相机样本）覆盖率、GTX 1660 实机；浏览器窄宽为内置浏览器真实视口
（380 CSS px）而非设备级模拟；上传路径在无图像编码器时只能观察到结构化拒绝。

## 8. 结论

- 冻结契约（ADR-0011）与实现逐条一致，B/D/C/浏览器四类必需证据经主审独立重跑成立，冻结面未被破坏。
- 唯一阻断级问题 F1 是**证据陈旧**而非实现缺陷，已在同一主审会话用真实重建 + 重跑 C 级闭环，未扩大范围。
- 未命中 V3.1 §2.2 六类条件：根因明确、无多状态互相矛盾、测试与 ADR 无冲突、无二次回归、无需长程大 diff 推演、
  无 Sol 待回答问题。因此**不升 DS-MAX**。
- 本卡未引入需要 Sol 聚焦终审的新高风险协议语义（§9 裁定均为 ADR 已覆盖口径的实现选择），故记
  **`SOL_NOT_REQUIRED`**。

**P4-00B：PASS。** 下一步按用户要求把 P4-00C 重新规划为两段式（一次会话只用一个模型）：

1. **P4-00C-A — HEIC 业务实现**（`docs/tasks/P4-00C-A.md`）：**DS-H / DeepSeek V4.1 Flash / 高推理** 实现
   格式策略、方向/缩略图/预览、GPS 与拍摄时间一致性、坏文件隔离，并做 **B + D** 验收；本段不碰
   `PhotoSearch.spec`/打包工具/打包 smoke。
2. **P4-00C-B — HEIC 环境与正式包兼容**（`docs/tasks/P4-00C-B.md`）：**C1 / GPT-5.6 Terra / medium**
   只做环境段——onedir 真实重建、pillow-heif/libheif 进包清单与哈希、打包后 HEIC 端到端、降级 fail-closed
   反例、`packaged_api_smoke` 扩展（含本报告 R1 的全量数据资源审计），**C 级**；不得改业务语义。

两段完成后另开独立 DS-H 会话做 A+B 全量工程主审，通过后才标 **P4-00C PASS** 并生成 P4-01。
模型切换始终由用户在 Codex 中手动完成。
