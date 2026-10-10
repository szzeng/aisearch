# P4-TEST-BASELINE-LITE-A 执行报告

日期：2026-10-11。状态：**PARTIAL**。仅改测试 fixture / 断言；未改 `src/`、schema、migration、依赖、搜索或地点规则。未访问真实 PhotoSearchData、照片、模型或 GPU；未运行全量 unit/integration。

## 基线与比较

- 开工时位于 `master`，worktree/index 无既有改动；HEAD 为 `e8f38e144460a036ba994f29b1a9d0856c75663f`，与任务卡立卡基线 `8e7258b` 不同。按当前开发状态继续执行，并保留此基线漂移记录。
- GC-A 全量历史 JUnit：`test-results/P4-STORAGE-GC-A-full.xml`，2,338 项：2,176 passed、70 failures、43 errors、49 skipped。结果生成于本次修复前，没有重跑或覆盖。
- 可用旧基线 `tmp/test-reports/P4-PERSON-SEMANTIC-REPAIR-A-D-with-import-path.xml`：2,253 项：2,091 passed、70 failures、43 errors、49 skipped。两份 XML 的 162 个非通过 nodeid 全部重合；70 个 failure 中 18 个原始签名完全一致，另外 52 个签名未完成规范化，标为 **UNKNOWN**。43 个 error 的 nodeid 和 `PLACE_NOT_FOUND` setup 根因一致（临时路径不同）；49 个 skipped 的 nodeid 与原因一致。总数相同本身不作为同一基线的证据。
- 全量历史失败分类（以 GC-A XML 为准；分类不是全仓复测）：

| 数量 | 分类 | 代表 nodeid | 复跑 / 处置 |
|---:|---|---|---|
| 43 errors | 地点来源资格导致的 ASSIGN fixture 级联 | `tests.integration.test_p2_search_authority::test_descendants_or_and_state_are_the_adr0005_semantics` | 本次修改其共享输入及直接调用方；定向复跑后 41 项通过、2 项进入下方独立断言失败 |
| 32 failures | 其他旧地点输入 / `PLACE_NOT_FOUND` | `tests.unit.test_p2_place_directory::test_retiring_a_referenced_place_is_refused` | 未整体复跑；其中备份恢复代表项已修 fixture 并通过，其他保留 |
| 12 failures | 前端静态 / DOM 断言 | `tests.unit.test_api_frontend::test_the_persons_page_has_no_external_reference_credential_or_html_sink` | 未复跑，未核实 |
| 10 failures | API / operation / 迁移等差异 | `tests.integration.test_p2_place_api::test_assignment_batch_and_undo_over_the_api` | 未复跑，未核实 |
| 8 failures | schema / migration / 冻结契约断言 | `tests.unit.test_p4_upgrade_contracts::test_migration_contract_and_byte_identity` | 未复跑，未核实 |
| 3 failures | 人物搜索 fixture 缺 `face_detail_auto_assets` | `tests.unit.test_p3_person_search_contracts::test_person_hit_set_matches_the_person_asset_count` | 未复跑，未核实 |
| 2 failures | OCR / 原生运行时拒绝码 | `tests.unit.test_p3_ocr_geometry::test_a_gpu_request_refuses_a_cpu_only_rapidocr_session` | 未复跑，未核实 |
| 2 failures | 人物列表性能计数断言 | `tests.integration.test_p4_person_album_perf::test_people_page_batches_only_the_selected_forty_people` | 未复跑，未核实 |
| 1 failure | 地点同名选择响应断言 | `tests.integration.test_p4_place_cn::test_cn_same_name_requires_parent_choice` | 未复跑，未核实 |

## 修改

- 在 `tests/p2_search_fixtures.py` 新增 `install_source_places()`：写入测试临时 data root 下的微型合成包，走正式 stage → validate → activate，再从 active dataset 的 `geo_dataset_place_refs` 取得真实 fixture place ID。保留 country → Hangzhou → West Lake 祖先链及 Beijing 城市，不伪造来源 ID。
- `test_p2_search_authority.py`、`test_p3_person_search_filter.py` 和 `test_p4_05_backup_restore.py` 改用该来源 fixture；资产、查询、筛选、revision、备份恢复预期均保留。
- 在现有 `test_p4_place_unified.py` 中增强无来源 single/batch ASSIGN 反例：断言 `PLACE_NOT_FOUND`，并比较 assignment、history、operation 行数及 filter revision，证明拒绝零写入。相同测试还覆盖合法来源 ASSIGN、batch 与 undo。
- 未使用 skip、xfail、deselect、monkeypatch 或降级断言；未修改产品代码。初次构造发现包格式仅允许 country/city，后调整为 city 子记录；随后将来源 external ID 排序改为与测试所依赖的祖先展开顺序一致。

## 最小验证

最终命令：

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.uv-cache'
$env:PYTHONPATH = 'src;tests;.'
uv run --no-sync pytest -q --basetemp=.pytest-tmp/P4-TEST-BASELINE-LITE-A-final --junitxml=test-results/P4-TEST-BASELINE-LITE-A.xml tests/integration/test_p2_search_authority.py tests/integration/test_p3_person_search_filter.py tests/integration/test_p4_place_unified.py::test_manual_osm_district_assignment_uses_existing_lock_membership_undo_and_rejects_bypass tests/integration/test_p4_05_backup_restore.py::test_empty_restore_keeps_text_and_semantic_pages_identical
```

- pytest 退出码 **1**；JUnit `test-results/P4-TEST-BASELINE-LITE-A.xml`：**50 tests / 48 passed / 2 failures / 0 errors / 0 skipped**。受影响的 43 个地点 setup node 中 41 项通过，2 项在执行主体后失败；备份恢复代表项通过。
- 剩余失败：`test_the_frozen_contracts_migrations_and_schema_version_are_untouched` 的 `p2-contracts.v1.json` 哈希为 `4270997c…0325fa`，测试仍期望 `ec7de5f0…e4a57`；不改冻结契约或哈希预期。`test_the_person_list_page_still_counts_the_same_members` 期望未命名 person C 出现在 person page 并计数为 0，当前返回不含该 ID；未确认这是旧 oracle 还是产品问题。
- `ruff check`（五个修改的 Python 文件）、`python -m py_compile`（同五个文件）、`git diff --check` 均退出码 **0**。未执行完整回归。

## 收口与后续

合法来源地点、组合人物/地点搜索、备份恢复及非法来源拒绝零写入均有直接定向证据；但两项被 setup error 遮住的断言仍失败，且全量历史 failures 未刷新。因此本卡为 **PARTIAL**，不宣称 SCOPED PASS 或全仓 PASS。其余非通过项保留上述分类；52 个原始 failure 签名差异仍 UNKNOWN。后续应由对应功能卡确认人物列表/冻结哈希预期，并按需处理其余 fixture/API/迁移问题；本卡未修改产品逻辑。
