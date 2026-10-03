# P4-PLACE-PACKAGE-RECOVERY — 实现与恢复记录

日期：2026-10-03。状态：**IMPLEMENTED / 定向和故障回归通过 / 全量有已复现既有失败 / DS-H 独立审查待做**。

## 现象与根因

用户删除 `PhotoSearchData/catalog` 与 `vectors` 后，地点页报告 `PLACE_DATASET_NOT_INSTALLED`。只读检查发现 catalog 已重建，`geo_datasets` 为空且 `active_geo_dataset.dataset_id IS NULL`；GeoNames 包目录仍在，manifest 声明 `geonames-asia-cities500-v1`、45,217 条记录。

`vectors` 是独立的向量索引目录，不参与地名包生命周期。地名包文件在 `places/datasets`，但已导入/活动登记、活动指针和地点物化数据存于 catalog。删 catalog 会丢这些注册状态；磁盘包本身不因此删除。页面的 unavailable 是对 catalog 当前状态的准确反映。

尝试用已有导入命令直接从包自身路径恢复时，发现第二个缺陷：`stage_package` 会在 source 与最终数据目录相同时仍尝试重写应按只读原则管理的包文件，首个 LICENSE 打开写入时被权限拒绝（`PermissionError`；没有检查或修改 ACL，不以该异常断言文件实际只读属性）。该文件及其余包文件未被修改。

## 修复

在 `stage_package` 中先完成现有的完整 manifest/内容校验，再规范化 source 和最终目录路径；路径相同则跳过复制，仅重建 catalog 行。来源位于其他目录时，原有复制行为不变。激活仍走既有事务与物化流程。

定向测试把合成包预置在数据根目标目录，阻止复制函数被调用，再验证 stage/validate/activate 后状态为 active 且所有包文件字节逐一不变。

## 验证证据

- 定向命令：`uv run --no-sync pytest tests/integration/test_p2_dataset_lifecycle.py -k surviving_read_only_package --basetemp .pytest-tmp/geo-package-recovery --junitxml test-results/geo-package-recovery.xml -q`
- 第一次沙箱运行在 pytest 创建 `.pytest-tmp` 时遇到已知 Windows `WinError 5`，未进入测试；不改 ACL，通过正常权限通道重跑成功：**1 passed**。JUnit：`test-results/geo-package-recovery.xml`。
- `git diff --check`：通过。
- PhotoSearchData 显式恢复：`tools/places/manage_dataset.py --data-root D:\SZ\project\PhotoSearchData import --source D:\SZ\project\PhotoSearchData\places\datasets\geonames-asia-cities500-v1 --activate` 成功；随后 `status` 确认 `state=active`、`resolver_available=true`、dataset `ACTIVE`、`problems=[]`、45,217 条记录。
- 导入命令在修复前的首次尝试被只读文件拒绝；修复后同目录恢复跳过文件复制。包目录中文件未被删除或覆盖。真实照片库、向量索引均未访问/操作。

## 第一轮限制与后续

catalog 重建会丢失其中的地点目录、人工归属、解析产物和活动包指针；这次通过从现存包显式重导恢复了登记/活动状态，不会复原已删除的人工数据或历史建议。以后如果希望这些 catalog 状态也在 catalog 删除后保留，需要另行裁决备份/数据隔离策略。

本次改动涉及状态恢复，独立 DS-H 全受影响面审查待做；不标 PASS。

## 第二轮：删除 catalog 后应用自动恢复

用户反馈“还是没修复好”。重新只读检查确认用户数据根的 catalog 再次重建，`geo_datasets` 为空、活动指针 NULL；唯一 GeoNames 包仍在。第一轮 `6523233` 仅修复了显式重导命令，不满足删除后自动可用的目标。

本轮实现基线 `7acfa6b`，开始时 tracked 工作区干净；既有 `.tmp` 与临时清理报告未纳入本次改动/提交。

本轮按 [ADR-0015](../decisions/ADR-0015-place-package-catalog-recovery.md) 增加 `restore_surviving_dataset`，由 `CatalogService.open` 在数据库打开后调用。只有 registry 完全为空且磁盘只有一个安全直接子目录时自动完整校验：包 hash/size/manifest/schema/格式/层级和 dataset_id 与目录名均须正确。一个事务内完成注册、VALIDATED、ACTIVE 和地点物化；失败整体回滚。已有注册行保持原活动或未激活选择，多包不随意激活；缺件/坏包/不兼容/错目录/子目录链接不激活。包不复制、不覆盖，vectors 不参与。

`create_app` 保持无 I/O；通过默认 API lazy service factory 第一次读取地点数据时恢复。恢复路径只读参考包，不扫描或读取真实照片、不初始化模型、不重建图文索引。用户删除 catalog/vectors 后须关闭旧应用并用新版源码重新启动；仅刷新仍持有旧 catalog 的进程无法切换到新启动逻辑。

### 本轮验证

| 命令/场景 | 实测结果与证据 |
|---|---|
| `uv run --no-sync pytest -q tests/integration/test_place_package_startup_recovery.py tests/integration/test_p2_dataset_lifecycle.py tests/integration/test_p2_place_dataset_api.py --basetemp .pytest-tmp/place-startup-recovery --junitxml test-results/place-startup-recovery.xml` | **29 tests / 0 failures / 0 errors / 0 skipped**。真实删除合成 catalog/vectors 后默认 API 首次打开恢复 ACTIVE；包字节/mtime 不变，二次重启不重复注册/物化。另覆盖坏包、多包、未激活和事务中断。 |
| 新增并发打开及缺失回退前驱保护后，重跑 `uv run --no-sync pytest -q tests/integration/test_place_package_startup_recovery.py --basetemp .pytest-tmp/place-startup-final-v2 --junitxml test-results/place-startup-final.xml` | 最终恢复文件 **10/0/0/0**；两个 opener 同时完成包校验后，仍仅一条注册、一次 active revision、无重复地点；缺失 previous_dataset_id 所需前驱时跳过而不因 FK 错误阻止启动。 |
| `uv run --no-sync pytest -q tests/unit tests/integration --basetemp .pytest-tmp/place-startup-full --junitxml test-results/place-startup-full.xml` | **1966 tests / 6 failures / 0 errors / 48 skipped**（1912 passed），exit 1。全量运行期间 ONNX/CUDA DLL preload 打印 Windows `0xc0000139` native exception，进程继续并最终写出 JUnit；未修改该运行时。该次收集发生在新增并发测试之前，最后新增项由上行覆盖。 |
| 补齐缺失前驱保护后的最终全量：`uv run --no-sync pytest -q tests/unit tests/integration --basetemp .pytest-tmp/place-startup-full-final --junitxml test-results/place-startup-full-final.xml` | **1968 tests / 6 failures / 0 errors / 48 skipped**（1914 passed），exit 1；与前轮同样六项失败，没有新增失败。完整日志 `.tmp/place-startup-full-final.log`。 |
| 对照：在进程内令 `datasets.restore_surviving_dataset = lambda catalog: None`，用 `pytest.main` 重跑全部六个失败 node，`--basetemp .pytest-tmp/place-startup-baseline --junitxml test-results/place-startup-baseline.xml --tb=short` | **6/6/0/0**，同一失败全部复现；这是禁用恢复钩子的行为对照，不冒充完整 HEAD checkout 基线。 |
| `uv run --no-sync ruff check src/photo_search/places/datasets.py src/photo_search/ingestion/service.py tests/integration/test_place_package_startup_recovery.py`、`git diff --check` | 通过。 |
| 用户数据根的 `CatalogService.open(data_root=D:\SZ\project\PhotoSearchData)`，前后比较包文件 SHA-256 与 mtime | 自动恢复成功：`state=active`、`reason=null`、`resolver_available=true`、45,217 条、`problems=[]`、`package_files_unchanged=true`。未扫描真实图库或操作其 vectors。 |

全量六项既有失败：

- `test_p3_person_search_contracts::test_existing_companions_are_byte_identical`、`test_p4_time_contracts::test_adr_records_the_companion_hash_and_every_frozen_companion_is_byte_identical`、`test_p4_upgrade_contracts::test_adr_records_the_companion_hash_and_the_earlier_companions_are_untouched`、`test_p3_person_search_filter::test_the_frozen_contracts_migrations_and_schema_version_are_untouched`：仍断言旧 `p2-contracts.v1.json` 哈希。本轮没有修改 companion/历史 migration。
- `test_p2_search_endpoint::test_strong_match_annotation_does_not_change_search_order_or_candidate_count`：旧测试仍 monkeypatch 已移除的 `STRONG_MATCH_MAX_DISTANCE`。
- `test_p4_embedding_provider_flow::test_the_selected_provider_serves_search_and_hides_the_strong_match_statistic`：旧断言要求 Qwen `strong_match_available=false`，与此前恢复高度匹配的功能不一致。

### 当前边界

健康的唯一现存地名包在删除 catalog/vectors 后会随应用打开自动恢复。已删除的人工归属、历史照片和旧建议不会恢复；有多个现存包、包不完整或缺少 manifest 引用的回退前驱时需要显式选择/修复，不能伪造活动状态。独立 DS-H 全受影响面审查仍待完成，ADR-0015 尚未正式冻结；本轮不声称总任务或完整回归 PASS。
