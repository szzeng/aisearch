# P4-00A 阶段 A 报告（DS-H 契约草拟与工程预审）

- 结论：**`ENGINEERING CONTRACT REVIEW PASS / SOL FREEZE PENDING`**（未命中 V3.1 §2.2 任何具名条件，
  不建议升 DS-MAX）
- 日期：2026-09-22
- 基线：`8e48fc7`（P3-08D 收口提交），开始前 `git status --short` 仅有一条 `%TEMP%` 目录的
  `Permission denied` 警告，无用户未提交改动
- 任务卡：[docs/tasks/P4-00A.md](../tasks/P4-00A.md)
- 阶段 A 未实现任何拍摄时间行为、未新增或修改 migration、未改 `schema_version`、未改依赖/`uv.lock`/
  `PhotoSearch.spec`/`web/**`、未改任何 `src/photo_search/**` 生产代码、未读真实照片库；ADR 保持
  **Proposed**，未生成 `docs/tasks/P4-00B.md`。

## 1. 交付物

| 文件 | 内容 |
|---|---|
| `docs/decisions/ADR-0011-taken-time-timezone-and-order-contract.md` | 13 节契约：列形态、civil 键与展示值、时区/夏令时、来源闭集与回退、`capture_desc` 与游标、时间条件与单一 revision、失效/投影、API/错误/隐私、迁移与兼容、P4-00B 前置清单、反例、后果、伴随物哈希（新增） |
| `docs/decisions/contracts/p4-time-contracts.v1.json` | 机器可读伴随物；SHA-256 `80189349bb4ea306225f9eae77ae9266c6d81c42de52c0b72bdc940a0702afd7`（新增；修复轮后的最终值，阶段 A 初版为 `8385b2b8…f5cf11`） |
| `tests/unit/test_p4_time_contracts.py` | 39 个 A 级契约用例（10 组必测 + ADR 条款 + Pillow 读取路径证据 + 修复轮的三条游标反例）（新增） |
| `test-results/p4-00a-contracts.xml` | 阶段 A/修复轮 JUnit（`39 / 0 / 0 / 0`） |
| `docs/development-status.md` | 只写阶段结论与「Sol 待冻结」，不写 PASS（本报告同轮更新） |

本轮共 **4 个新文件**（ADR-0011、P4 伴随物、契约测试、本报告；阶段 B 又新增 Sol 报告）加
`docs/development-status.md` 的修改；新文件均为 LF、无行尾空白、以换行结尾。阶段 A 交接里曾写成“3 个新
文件”，属计数笔误，已按 Git 实态纠正（阶段 B 报告 §1 已指出）。

## 2. 结论表：任务卡「契约必须冻结的内容」逐条落地

| # | 任务卡要求 | 冻结结论 | 证据 |
|---:|---|---|---|
| 1 | 列与表形态、权威边界 | `asset_metadata` **主键 + 六列**与 §4.2 逐列一致（`taken_at_raw`/`taken_at_sort`/`time_source`/`time_confidence`/`timezone_offset`/`camera`），表级 `CHECK` 只允许五种来源/置信度组合；不含坐标/GPS，不复制 `asset_geo_facts`；路径与文件时间观察值仍属 `file_locations` | ADR §2；`test_companion_identity_and_closed_public_surface`、`test_the_source_mapping_table_is_frozen_and_fully_ordered` |
| 2 | 时区与排序键 | `taken_at_sort` 是**非负 civil 键**（原点 `0001-01-01T00:00:00.000`，范围 `0..315537897599999`），**不是 instant**；偏移永不参与键；展示值定宽 `YYYY-MM-DDTHH:MM:SS.mmm`（有偏移才附 `±HH:MM`）；同一墙上时间有/无偏移键完全相等 | ADR §3；`test_a_local_time_without_an_offset_is_never_treated_as_utc`、`test_the_civil_key_is_comparable_and_matches_the_displayed_wall_clock`、`test_timezone_offset_bounds_defaults_and_refusals` |
| 3 | 回退来源与「文件时间」 | 闭集 `EXIF_ORIGINAL → EXIF_DIGITIZED → EXIF_ROOT → FILE_MTIME`，逐级吞掉坏值；禁止扫描时间/当前时间/加入时间兜底；标签 `capture` 仅前两级，其余必须显示「文件时间」；`FILE_MTIME` 取全部位置（含 `MISSING`）`mtime_ns` 最小值 | ADR §4；`test_the_fallback_chain_skips_bad_values_and_never_invents_a_capture_time`、`test_only_capture_rows_participate_in_the_range_and_file_time_is_the_tail` |
| 4 | 新 order 与独立游标 | `order=capture_desc`；排序 `(taken_at_sort IS NULL, taken_at_sort DESC, asset_id DESC)`；游标前缀 `c1` arity 4 `[known, sort_or_0, asset_id, revision]`；三个游标空间互不解释；**既有 `time_desc`=加入时间的 SQL/游标逐字不变** | ADR §5；`test_the_order_value_set_is_closed_and_the_two_existing_values_survive`、`test_the_three_cursor_spaces_never_interpret_each_other`、`test_the_frozen_time_desc_ordering_is_untouched` |
| 5 | 时间范围条件 | 左闭右开；两个扁平字段 `capture_from`/`capture_to_exclusive`（`GET /assets` 查询参数与 `POST /search` 信封），§9.1 的 `time_range` 只作解析器内部结构；进入同一 AND 集合、同一 `catalog_filter_revision` 与同一 `query_hash`；不可靠来源行**不参与**过滤 | ADR §6/§8.1；`test_the_range_is_left_closed_and_right_open`、`test_the_capture_condition_joins_the_one_and_set_and_the_one_revision`、`test_the_continuation_digest_adds_exactly_the_two_capture_bounds` |
| 6 | 失效与投影 | 新增 cause `CAPTURE_TIME_CHANGED`（同事务 +1，`cause` 列无 `CHECK`，不需重建表）；**不新增** `search_projection_queue` cause、不改 `search_documents`/FTS、`search_projection` 8 键不变；`taken_time` 是兄弟块；既有错误码不新增 details 键 | ADR §6.6/§7/§8.3；`test_the_time_error_details_add_no_key_to_the_frozen_vocabulary`、`test_the_companion_does_not_restate_the_frozen_p3_blocks` |
| 7 | 隐私闭集 | 允许：规范化边界回显、`time_source`、`time_confidence`、`timezone_offset_minutes`、`taken_at`、`taken_at_sort`、`label`，外加详情页专属 `camera`（规范化 `Make Model` ≤128）；禁止：`taken_at_raw`、任何 instant、路径、坐标/`raw_gps`、序列号/`OwnerName`/`Artist`/`Copyright`/`Software`、原始 EXIF blob | ADR §8.4；`test_the_companion_names_no_private_property_anywhere` |
| 8 | 迁移与兼容 | `0015_taken_time.sql`、`SUPPORTED_SCHEMA_VERSION 14 → 15`、`0001`～`0014` 逐字节不变、事务内建表+推进版本、失败回滚到 14、旧程序 `SCHEMA_TOO_NEW`；未抽取旧库的既有字段逐字不变，新键只以 `null` 出现；**`0015` 是打包数据资源（`collect_data_files('photo_search')` + `migration_files()` 连续覆盖要求），因此 P4-00B 必须加 C 级真实打包 smoke**（阶段 B 边界 5 的修正）。可实现性证据为 Pillow EXIF 读取路径实测 | ADR §9/§10；`test_pillow_exif_time_reading_paths_are_the_frozen_ones`、`test_the_contract_lists_the_p4_00b_acceptance_prerequisites` |
| 9 | P4-00B 前置清单 | D 级 8 条故障注入 + 8 条记录式浏览器步骤 + 命令模板已写入 ADR §10，可直接作为 P4-00B 任务卡输入 | ADR §10；`test_the_contract_lists_the_p4_00b_acceptance_prerequisites` |

任务卡点名的 10 组必测反例全部有对应用例（第 1、2、3、4、5、6、7、8、9、10 组分别由伴随物/来源闭集、
时区与 civil 键、order 闭集、三游标空间、区间端点、回退链、单一 revision/摘要、`time_desc` 冻结、
隐私闭集覆盖）。

## 3. ADR 预期 / 实际差异（执行时先核，全部以仓库事实为准）

1. **§4.2 的列足够**：实测确认不需要给 `asset_metadata` 加第 7 列；“解析规则版本”改用库级
   `metadata.taken_time_rules_version`（ADR §2），因此 §4.2 的列保持逐字不变。
2. **civil 键原点必须非负**：三个游标空间共用“非负整数元素”的编解码（`api/queries.py::_decode_cursor`
   拒绝负数），因此 civil 键的原点固定为 `0001-01-01T00:00:00.000` 而不是 1970；这一选择只影响键的
   数值，不影响展示、比较与过滤。
3. **`filters` 回显无法“零改动”扩展**：`p3-person-search-contracts.v1.json` 的 `SearchFiltersEcho` 是
   `additionalProperties: false` + 固定 required 的闭合对象，其实测 `required` 是 **11** 键，所以
   ADR §8.2 明确声明一次**加性取代**（`SearchFiltersEchoV2` = 11 + 2 = **13** 键），既有 companion
   文件一律不修改；实测确认 P4-00B 只需扩展
   `tests/unit/test_p3_person_search_contracts.py::test_the_frozen_echo_keys_are_the_implemented_echo_keys`
   这一条断言，其它 P3 用例如 `test_filters_echo_keeps_the_frozen_place_and_binding_fields`
   使用自带 fixture，不受影响。
4. **新增 cause 不需要重建表**：实测 `0004_place_core.sql` 的 `catalog_filter_revision.cause` 是无
   `CHECK` 的 `TEXT`；`catalog_filter_revision_events.cause` 也只要求非空。
5. **p2 companion 的 cause 枚举已是 P2 时代列表**：`p2-contracts.v1.json::CatalogFilterRevision.cause`
   只列 `PLACE_*`/`PERSON_EFFECTIVE_ASSIGNMENT_CHANGED` 等，而 live 已使用 `PERSON_CHANGED`
   （`persons/contracts.py:101`）；新增 `CAPTURE_TIME_CHANGED` 属同一既成模式，ADR 记录 live 取值而非
   改写 p2 冻结。
6. **§9.1 的 `time_range` 与信封拼写**：§9.1 是解析器内部结构，本 ADR 把同一条件落到既有扁平信封的两个
   字段（与 `place_ids`/`person_ids` 一致），并明确禁止第二套条件表示。
7. **Pillow 读取路径**：合成 JPEG 实测 `Image.getexif()` 只给出 IFD0 标签（`0x0132`/`0x010F`/`0x0110`），
   **Exif 子 IFD 必须用 `getexif().get_ifd(0x8769)`**；在根对象上 `get(0x9003)` 返回 `None`。
   这条被写进 ADR §9 并被用例固定——它正是“naive 读取会静默退化成文件时间”的真实接缝。
8. **未命中 §2.2**：本轮没有跨模块未证根因、没有 transaction/revision/undo 多状态矛盾、没有
   “测试通过但行为与 ADR 不符”的判不清、没有连锁回归、没有多状态机大推演、Sol 也尚未提出问题，
   因此**不升 DS-MAX**。

## 4. 最小反例（ADR §11 的可执行形态）

| 反例 | 期望 | 覆盖用例 |
|---|---|---|
| `EXIF_ORIGINAL` 无 `0x9011` | `timezone_offset = NULL`、`MEDIUM`、civil 键与“有偏移的同一墙上时间”相等，绝不产生 instant | `test_a_local_time_without_an_offset_is_never_treated_as_utc` |
| `capture_from=2022-01-01T00:00:00Z` / `...+08:00` | 422 `INVALID_TIME_RANGE` / `capture_bound_has_offset` | `test_refused_bounds_carry_the_frozen_reason` |
| `capture_from == capture_to_exclusive` | 422 `capture_range_not_ascending`（空区间显式拒绝） | `test_an_empty_or_inverted_range_is_refused` |
| 恰好等于右端 / 等于左端 | 不出现 / 出现（左闭右开） | `test_the_range_is_left_closed_and_right_open` |
| `time_source='EXIF_GPS'`/`'SCAN_TIME'`/`'NOW'` | 被闭集拒绝 | `test_unknown_or_bad_sources_and_confidences_are_refused` |
| `0x9003` 非法日历 + 合法 `0x9004`；再全坏 | `EXIF_DIGITIZED`；再 `EXIF_ROOT`；全坏 `FILE_MTIME` 且 civil 为 `NULL` | `test_the_fallback_chain_skips_bad_values_and_never_invents_a_capture_time` |
| 跨游标空间互用 | 六个方向全部 422 `INVALID_CURSOR` | `test_the_three_cursor_spaces_never_interpret_each_other` |
| `filters` 回显出现 `capture_filter_revision`/`time_revision` | 被拒绝（只有 `catalog_filter_revision` + `suggestion_revision`） | `test_the_capture_condition_joins_the_one_and_set_and_the_one_revision` |
| 只有 `0x0132` 的资产参与 capture 过滤 | 不命中，但仍进 `ordered` 桶且标签 `file_time` | `test_only_capture_rows_participate_in_the_range_and_file_time_is_the_tail` |

## 5. 命令与结果摘要

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path
uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit/test_p4_time_contracts.py `
  --basetemp=.pytest-basetemp-p400a/a5 --junitxml=test-results/p4-00a-contracts.xml
uv run --no-sync ruff check .
git diff --check
uv lock --check
```

- 契约测试：**39 tests / 0 failures / 0 errors / 0 skipped**（`test-results/p4-00a-contracts.xml`；
  阶段 A 为 38，修复轮新增 `test_the_capture_cursor_freezes_the_three_probed_invariants`）。
- 阶段 B 三条游标探针在修复后由契约层直接拒绝（复跑同一 `contract_errors(..., "CaptureCursor")`）：
  `values=[2,0,5,12]` → `$.values[0]: enum`、`values=[0,99,5,12]` → `$.values[1]: const`、
  `values=[1,0,0,12]` → `$.values[2]: minimum`、另加多余 `catalog_filter_revision` 字段 →
  `$: unexpected catalog_filter_revision`；合法对象 `values=[1,0,5,12]` → `[]`。
- Ruff：`All checks passed!`（exit 0）。`git diff --check`：clean（exit 0）。`uv lock --check`：
  `Resolved 95 packages`（exit 0，未改依赖）。
- 环境事实（未改 ACL）：
  - 受限令牌下 pytest 的 basetemp/`tmp_path` 目录枚举仍报 `PermissionError [WinError 5]`（`.pytest-tmp*`
    与新建 `.pytest-basetemp-p400a/*` 同现象），因此本卡测试改经**正常 Windows 权限路由**执行；
  - 受限令牌下 `ruff check .` 会打印一条 `os error 5` 目录遍历警告（随后仍 `All checks passed!`），
    在正常权限路由下同一命令**无警告**且通过。
  - 两者都不构成产品失败，也未通过修改 ACL 规避。

## 6. 未验证项（不得当 PASS）

- 本卡是 A 级契约卡：**没有**实现任何抽取/API/UI，`0015`、`SUPPORTED_SCHEMA_VERSION`、
  `asset_metadata`、`capture_desc` 游标、`taken_time` 块、`INVALID_TIME_RANGE` 全部**尚未存在**；
- 30 万资产下 `capture_desc`/时间过滤延迟与最终索引列序、全库抽取耗时、多进程并发抽取、
  `0015` 在真实库上的实测迁移耗时、真实内部存储格式样本（大小写、坏相机）覆盖率、GTX 1660 实机均为
  P4-00B/P4-01/P4-02/P4-04 与 C1 范围；**C 级打包不再属于“未验证项”**，而是 ADR §9/§10 对 P4-00B 的
  硬性要求（阶段 B 边界 5 的修正）；
- 本卡未跑 `tests/unit tests/integration` 全量套件（A 级不要求）；阶段 B 也不重跑完整成功套件。

## 7. 给 Sol 的压缩交接包（阶段 B 唯一输入）

基线 `8e48fc7`；改动文件为本报告 §1 的 **4** 个新文件 + `docs/development-status.md`（阶段结论段，
阶段 B 又新增 `docs/reports/P4-00A-sol-contract-freeze.md`）。

**结论表**：§2（9 条契约内容）与 §3（8 条预期/实际差异）。

**待 Sol 终审的高风险边界**（按任务卡阶段 B 点名）：

1. 时区/排序键：`taken_at_sort` 是「按墙上字段折算的非负 civil 键、明确**不是** instant」，偏移永不参与
   计算，时间范围也在同一 civil 空间比较（ADR §3、§6.4）——是否有任何路径会把它当 instant 使用或
   让无偏移本地时间冒充 UTC；
2. 回退来源闭集与「文件时间」诚实性：`EXIF_ROOT` 有 civil 键但 `label=file_time` 且不参与过滤；
   `FILE_MTIME` 无 civil 键、进尾部桶（ADR §4、§5.1）——标签、排序桶与过滤参与三者是否自洽；
3. 新 order 与既有 `time_desc` 隔离：`c1` arity 4 游标绑定单一 `catalog_filter_revision`，续页失配即
   409 stale（有意 over-invalidation），`v1`/`t1` 逐字不变（ADR §5）——是否引入第二套失效协议或破坏
   P1-05 语义；
4. 时间条件与单一 revision/会话摘要：同一 AND 集合、同一 `catalog_filter_revision`、同一 `query_hash`
   载荷 = ADR-0009 §6.2 字段集 + 两个规范化 capture 字段（ADR §6.5）——是否真的没有第二个计数器；
5. 迁移契约可实现性：`0015` 只建表+索引+库级规则版本键，不回填，事务内推进版本，`0001`～`0014`
   逐字节不变，旧库既有字段逐字不变（ADR §9）——是否可被 P4-00B 无歧义实现；
6. `filters` 回显加性取代（ADR §8.2）与「只扩展一条 P3 断言、不改任何既有 companion 文件」的取舍；
7. `INVALID_TIME_RANGE` 新 code 与 details 只用既有键（ADR §8.3）、`taken_time` 四态纯函数与
   兄弟块位置（ADR §8.1）。

**关键定位**（不复制 diff）：`docs/decisions/contracts/p4-time-contracts.v1.json` 的 `$defs`
（`TimeSourceRules`、`CaptureBoundRaw/Normalized`、`CaptureCursor`、`SearchFiltersEchoV2`、
`TakenTimeBlock`、`AssetTakenTimeBlock`、`TimeErrorDetails`）；ADR §3.1/§5.2/§6.2/§8.2/§9；
用例 `test_the_three_cursor_spaces_never_interpret_each_other`、
`test_pillow_exif_time_reading_paths_are_the_frozen_ones`。

**最小反例与命令结果**：§4 与 §5（修复轮后 39 / 0 / 0 / 0；Ruff、diff-check、lock-check clean）；
阶段 B 四条失败边界的闭合见 §9。

**禁止扩大范围**：阶段 B 只读 `AGENTS.md → docs/development-status.md → 任务卡 → 本报告` 与本报告点名
的条款/关键字子集/最小反例；不改任何生产代码、migration、companion、前端、依赖；不启动 P4-00B 实现；
不把 §6 的未验证项改写成 PASS；通过后只需把 ADR 转 **Accepted**、标 P4-00A PASS、补状态并生成
`docs/tasks/P4-00B.md`。

## 8. 阶段停止点

阶段 A 收口时状态为 `ENGINEERING CONTRACT REVIEW PASS / SOL FREEZE PENDING`；阶段 B 判定
`FAIL / DO NOT FREEZE`（4 条边界不通过），本报告 §9 为修复轮记录，收口状态改写为
**`CORRECTIONS APPLIED / SOL SHORT RE-REVIEW PENDING`**。ADR-0011 仍保持 **Proposed**；仍**未**标
P4-00A PASS、仍**未**生成 `docs/tasks/P4-00B.md`。下一步为 **C2 / GPT-5.6 Sol / high 新会话**只复审
§9 的四条失败边界（边界 1/2/4 无需重审）；模型切换由用户在 Codex 中手动完成。

## 9. 修复轮：阶段 B 四条失败边界的闭合（DS-H，2026-09-22）

输入是 [P4-00A 阶段 B 报告](P4-00A-sol-contract-freeze.md) §2/§3。只改契约层（ADR-0011、P4 伴随物、
契约测试与本报告），未触碰生产代码、migration、`schema_version`、依赖、`uv.lock`、`PhotoSearch.spec`、
`web/**`、ADR-0001～0010、既有 companion 与既有测试语义。

| # | 阶段 B 判定 | 最小反例（Sol 探针） | 本修复轮的处理 |
|---:|---|---|---|
| 3 | 不通过：游标不变量没冻结 | `values=[2,0,5,12]`、`[0,99,5,12]`、`values[3]=12` 而 `catalog_filter_revision=13` 三个对象都被接受（`contract_errors(..., "CaptureCursor") == []`） | 伴随物 `CaptureCursor` 改为**三字段** `prefix`/`arity`/`values`，用 `prefixItems` 逐位冻结 `known ∈ {0,1}`（`CaptureCursorKnown`）、sort 元素 `≥0`、`asset_id ≥ 1`、revision `≥0`，并用 `allOf`+`if/then` 冻结“尾部桶 sort 元素恒为 0”；**取消**重复的 `catalog_filter_revision` 字段（第 4 个元素本身就是绑定值），因此不再可能双表示。契约测试新增 `test_the_capture_cursor_freezes_the_three_probed_invariants`（三条探针 + 多余字段 + arity/负值），并给参考解码器补同样的拒绝；复跑同一探针现在分别得到 `enum`/`const`/`minimum`/`unexpected catalog_filter_revision` |
| 5 | 不通过：C 级判定错误 | 冻结包漏带 `0015` 时 `migration_files()` 得 `0001`～`0014`，与 `SUPPORTED_SCHEMA_VERSION=15` 的期望序列不等并抛 `SCHEMA_INCONSISTENT` | ADR §9 改写：`0015_taken_time.sql` **是打包数据资源**（`PhotoSearch.spec` 的 `collect_data_files('photo_search')` + `migration_files()` 的连续覆盖要求），删除“不触发 C 级”；ADR §10 标题改为「D + C + 浏览器」并新增 3 条 C 级打包验收（bundle 内 `0001`～`0015` 逐个 SHA-256 一致、`packaged_api_smoke.py` 且打包 data root 实测 `schema_version=15`、缺件必须报 `SCHEMA_INCONSISTENT`）；§12 把 C 级从“未验证项”移出 |
| 6 | 不通过：键数写错 | 直接计数两份 companion：P3 是 **11**、P4 V2 是 **13** | ADR §8.1/§8.2/§11 与本报告统一改为 **11 + 2 = 13**，并点名 P3 `SearchFiltersEcho.required` 的 11 个键；V2 仍为 13 键的严格超集，既有 companion 仍逐字节不变、只扩展一条 P3 断言 |
| 7 | 不通过：四态不是三计数纯函数 | `(assets_total=10, extracted=10, capture=1)` 在纯函数下 `READY`，但规则版本失配时又要求 `PENDING` | 采用阶段 B 推荐口径：`extracted_assets` 定义为**按当前 `taken_time_rules_version` 可信**的已抽取资产数，版本失配时该计数与 `capture_time_assets` 一并**规范化为 `0`**，`state` 仍只是这三个数的纯函数；ADR §2/§8.1 与伴随物 `TakenTimeBlock` 的 `description` 同步，契约测试新增 `normalize_taken_time_block(...)` 与失配最小反例（`(10,10,1)` 失配 ⇒ `(10,0,0)` ⇒ `PENDING`；`assets_total=0` 仍 `READY`；块只有四个键） |

修复轮证据：专项 `test-results/p4-00a-contracts.xml` **39 / 0 / 0 / 0**；三条游标探针的复跑输出见 §5；
伴随物哈希按修复后内容更新为
`80189349bb4ea306225f9eae77ae9266c6d81c42de52c0b72bdc940a0702afd7`（ADR §13 同步）。

未重审也不必重审的边界：阶段 B 判定通过的 **1（时区/civil 键）、2（回退闭集与「文件时间」）、
4（单一 AND 集合/单一 revision/摘要）** 本次未改动其冻结语义；Sol 短审只需覆盖上表四条，外加
“这四条修复没有波及 1/2/4”这一项。
