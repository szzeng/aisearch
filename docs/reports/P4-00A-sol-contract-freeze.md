# P4-00A 阶段 B 报告（C2 / Sol high 聚焦终审）

- 结论：**`PASS AFTER CORRECTION / CONTRACT FROZEN`**（首次终审曾为 `FAIL / DO NOT FREEZE`，见 §2～§3）
- 日期：2026-09-22
- 基线：`8e48fc7`
- 最终裁决：DS-H 修复轮已闭合首次终审的四条失败边界；ADR-0011 转 **Accepted**，P4-00A 可标 PASS 并生成 P4-00B 任务卡。
- 范围：只审任务指定的 7 条边界、阶段 A 报告 §7 点名条款/伴随物定义与最小反例；未重扫全仓、未重跑完整成功套件、未读真实照片库。

## 1. 输入实态

HEAD 与指定基线一致。相对 `8e48fc7` 没有生产代码、migration、schema version、依赖、`uv.lock`、
`PhotoSearch.spec`、`web/**`、ADR-0001～0010 或既有 companion 改动。

交接中的“3 个新文件 + `docs/development-status.md`”是计数笔误：Git 实态是 4 个 untracked 文件
（ADR-0011、P4 companion、阶段 A 报告、P4 契约测试）加 `docs/development-status.md`。这不单独构成契约
失败，但本报告不沿用错误计数。

## 2. 首次七条边界裁决（历史）

| # | 边界 | 裁决 | 最小反例 / 结论 |
|---:|---|---|---|
| 1 | 时区、civil 排序键与范围空间 | **通过** | 同一墙上值 `2022-07-05T18:31:07.000` 分别带 `+08:00` 与无偏移时，civil key 相同；无偏移只把 `EXIF_ORIGINAL` 从 `HIGH` 降为 `MEDIUM`，不产生 instant/UTC。`capture_from=...Z` 或 `...+08:00` 被 `INVALID_TIME_RANGE/capture_bound_has_offset` 拒绝；范围与键均在同一 civil 空间比较。公开 companion 没有 instant/UTC 字段。 |
| 2 | 回退闭集与「文件时间」诚实性 | **通过** | 只有合法 `0x0132` 时得到 `EXIF_ROOT + file_time + ordered + 不参与过滤`；所有 EXIF 日期均坏时得到 `FILE_MTIME + file_time + taken_at_sort=null + tail + 不参与过滤`。二者均不能因覆盖其日期的 capture 范围而命中；扫描时间、当前时间和 `assets.created_at` 不在来源闭集。标签、过滤资格与排序桶自洽。 |
| 3 | `capture_desc` 与既有 `time_desc` 隔离 | **不通过** | ADR 的 `c1`/arity 4、单一 revision、409 stale 与 `v1`/`t1` 不变是清楚的；但机器 companion 的 `CaptureCursor` 接受三个违反该语义的对象：`values=[2,0,5,12]`（非法 `known`）、`[0,99,5,12]`（尾桶 sort 非 0）、`values[3]=12` 而 `catalog_filter_revision=13`（两个 revision 表示互相矛盾）。本轮探针调用契约测试自己的 `contract_errors(..., "CaptureCursor")`，三例结果均为 `[]`。因此 companion/测试没有冻结 ADR §5.2 的关键不变量。 |
| 4 | 时间条件、单一 revision 与会话摘要 | **通过** | `capture_from`/`capture_to_exclusive` 规范化后加入 ADR-0009 §6.2 的 10 字段摘要载荷；日期简写与 `.000` 得到同摘要，不同边界得到不同摘要。回显仍只有既有 `catalog_filter_revision` 与 `suggestion_revision`；注入 `capture_revision`、`time_revision` 或 `capture_filter_revision` 被拒绝，不存在第二计数器。 |
| 5 | 迁移契约可实现性与验收等级 | **不通过** | “0015 只建表/索引/规则版本键且不回填、14→15、0001～0014 不变”本身可实现；但“打包资源无变化，因此不触发 C”不成立。`PhotoSearch.spec` 使用 `collect_data_files('photo_search')`，而运行时 `migration_files()` 明确从 `src/photo_search/catalog/migrations/*.sql` 读取并要求连续覆盖到 `SUPPORTED_SCHEMA_VERSION`。新增 `0015_taken_time.sql` 会改变冻结包的数据资源集合。最小反例：程序声明支持 15、冻结包漏带 0015 时，`migration_files()` 得到 0001～0014，与期望 0001～0015 不等并报 `SCHEMA_INCONSISTENT`。P4-00B 必须加 C 级真实打包 smoke 与 bundled migration 清单核验。 |
| 6 | `SearchFiltersEchoV2` 加性取代 | **不通过** | 加性取代且不改历史 companion 的方案原则上可接受，但基数写错：P3 companion 的 `SearchFiltersEcho.required` 实际是 **11** 键，P4 的 V2 实际是 **11 + 2 = 13** 键，不是 ADR/交接声称的 **13 + 2**。机器 companion 当前也只有 13 个 required/properties。最小反例是直接计数两份 companion：`11` 与 `13`。在纠正为 11+2、保留历史 fixture/哈希断言且只扩展 live 键集断言后，该取舍可接受。 |
| 7 | `INVALID_TIME_RANGE`、四态与兄弟块 | **不通过** | 新 code 只用既有 `reason`/`field` 等 details 键，且 `taken_time` 位于 `search_projection` 旁边而不并入，均通过；四态“是三个计数的纯函数”却与同表第一行矛盾：`assets_total=10, extracted_assets=10, capture_time_assets=1` 在测试纯函数中为 `READY`，但规则版本失配时 ADR 又要求 `PENDING`。相同三计数得到两种状态，故不是三计数纯函数。companion 也没有规则版本/匹配位，测试无法表达该反例。 |

总裁决：边界 1、2、4 通过；边界 3、5、6、7 不通过。现有 38 个测试全绿不能覆盖或消除上述四个反例。

## 3. 首次回退与修复范围（已闭合）

仅回退到 DS-H 修改契约层，不进入生产实现：

1. `CaptureCursor`：在 companion 与契约测试中冻结 `known ∈ {0,1}`、尾桶 `sort_key_or_0 == 0`，并消除或强制
   `values[3] == catalog_filter_revision` 的双表示歧义；补三条负例。若调整 companion，联动更新 ADR §13 哈希。
2. C 级：修正 ADR §9/§10 的“打包资源无变化/C 不触发”，把 `0015` 的 bundled resource 清单与真实冻结包
   迁移 smoke 纳入 P4-00B 验收；不得只以 `PhotoSearch.spec` 文本未改判定资源未变。
3. filters：把 ADR §8.1/§8.2、阶段交接中的 `13 + 2` 改为实际的 `11 + 2 = 13`；既有 P3 companion
   继续逐字节不变，历史 fixture/哈希断言不得删除或削弱。
4. `taken_time`：二选一并唯一冻结。推荐把 `extracted_assets` 明确定义为“按当前
   `taken_time_rules_version` 可信的已抽取资产数”，规则版本失配时该计数规范化为 0，然后 state 才真正只由
   三计数决定；否则就必须撤回“三计数纯函数”并在闭合公共块中表达额外输入。补规则版本失配的最小反例。

修复不得触碰生产代码、migration、schema version、依赖、`uv.lock`、`PhotoSearch.spec`、`web/**`、
ADR-0001～0010、既有 companion 或既有测试语义。DS-H 修复后，Sol 只复审以上 4 个失败边界；边界 1、2、4
无需重审，完整成功套件无需重跑。

## 4. 命令结果摘要

```text
focused pytest: 38 tests / 0 failures / 0 errors / 0 skipped; exit 0
JUnit: test-results/p4-00a-sol-focused.xml
ruff check .: All checks passed!; exit 0（正常 Windows 权限路由，无 os error 5 警告）
git diff --check: clean; exit 0
uv lock --check: Resolved 95 packages; exit 0
```

pytest 使用全新的项目内 `.pytest-basetemp-p400a/sol1` 并经正常 Windows 权限路由执行；未修改 ACL。
阶段 A 报告 §6 的容量、迁移实测、多进程、真实样本、C 级打包与硬件项仍全部未验证，本报告不将其改写为 PASS。

## 5. 修复轮短审与最终冻结

短审只复核首次失败的边界 3/5/6/7 及其直接回归面；首次已通过的边界 1/2/4 未重审，语义未被修复轮改动。

| 边界 | 最终裁决 | 原反例复核 |
|---|---|---|
| 3 — `CaptureCursor` | **通过** | companion 只保留 `prefix`/`arity`/`values`；`values` 逐位约束 `known ∈ {0,1}`、sort ≥0、asset_id ≥1、revision ≥0，尾桶 sort 恒 0。原四例分别被 `enum`、`const`、`unexpected catalog_filter_revision`、`minimum` 拒绝，双 revision 表示已消失。 |
| 5 — C 级 | **通过** | ADR §9/§10 明确 `0015` 是 `collect_data_files('photo_search')` 收集的打包数据资源；P4-00B 固定执行 bundle migration SHA-256 清单、真实 packaged API/data-root smoke 和缺 0015 的 `SCHEMA_INCONSISTENT` 反例。 |
| 6 — filters 基数 | **通过** | P3 `SearchFiltersEcho` 实测 11 键；V2 为严格的 11+2=13 键，只增加两个 capture 字段，既有 companion 与历史 fixture/哈希断言不改。 |
| 7 — 四态纯函数 | **通过** | `extracted_assets`/`capture_time_assets` 明确定义为当前规则版本下的可信计数；版本失配时二者规范化为 0。原 `(10,10,1)` 反例变为公开块 `(10,0,0) → PENDING`，state 唯一由块内三计数决定。 |

原探针复跑：

```text
CaptureCursor known=2               -> $.values[0]: enum
CaptureCursor tail sort=99          -> $.values[1]: const
CaptureCursor duplicate revision    -> $: unexpected catalog_filter_revision
CaptureCursor asset_id=0            -> $.values[2]: minimum
rules version mismatch (10,10,1)    -> {state: PENDING, assets_total: 10, extracted_assets: 0, capture_time_assets: 0}
```

最终验证（全为 exit 0）：

```text
focused pytest: 39 tests / 0 failures / 0 errors / 0 skipped
JUnit: test-results/p4-00a-sol-rereview.xml
ruff check .: All checks passed!（正常 Windows 权限路由，无 os error 5 警告）
git diff --check: clean
uv lock --check: Resolved 95 packages
```

pytest 使用全新的项目内 `.pytest-basetemp-p400a/sol2`；未修改 ACL、未读真实照片库、未运行完整套件。
伴随物实际 SHA-256 为
`80189349bb4ea306225f9eae77ae9266c6d81c42de52c0b72bdc940a0702afd7`，与 ADR §13、阶段 A 报告和状态一致。

最终结论：**P4-00A PASS；ADR-0011 Accepted。** 这是契约冻结 PASS，不是 P4-00B 实现、迁移、浏览器或打包
验收 PASS；首次报告列出的容量、并发、真实迁移/样本与硬件未验证项继续后置。
