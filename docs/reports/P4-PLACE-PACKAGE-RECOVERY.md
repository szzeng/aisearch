# P4-PLACE-PACKAGE-RECOVERY — 实现与恢复记录

日期：2026-10-03。状态：**IMPLEMENTED / SCOPED VALIDATION COMPLETE / DS-H 独立审查待做**。

## 现象与根因

用户删除 `PhotoSearchData/catalog` 与 `vectors` 后，地点页报告 `PLACE_DATASET_NOT_INSTALLED`。只读检查发现 catalog 已重建，`geo_datasets` 为空且 `active_geo_dataset.dataset_id IS NULL`；GeoNames 包目录仍在，manifest 声明 `geonames-asia-cities500-v1`、45,217 条记录。

`vectors` 是独立的向量索引目录，不参与地名包生命周期。地名包文件在 `places/datasets`，但已导入/活动登记、活动指针和地点物化数据存于 catalog。删 catalog 会丢这些注册状态；磁盘包本身不因此删除。页面的 unavailable 是对 catalog 当前状态的准确反映。

尝试用已有导入命令直接从包自身路径恢复时，发现第二个缺陷：`stage_package` 会在 source 与最终数据目录相同时仍尝试重写包文件，而目录按设计只读，触发 `PermissionError`。本次失败发生在首个 LICENSE 文件打开写入时；该文件及其余包文件未被修改。

## 修复

在 `stage_package` 中先完成现有的完整 manifest/内容校验，再规范化 source 和最终目录路径；路径相同则跳过复制，仅重建 catalog 行。来源位于其他目录时，原有复制行为不变。激活仍走既有事务与物化流程。

定向测试把合成包预置在数据根目标目录，阻止复制函数被调用，再验证 stage/validate/activate 后状态为 active 且所有包文件字节逐一不变。

## 验证证据

- 定向命令：`uv run --no-sync pytest tests/integration/test_p2_dataset_lifecycle.py -k surviving_read_only_package --basetemp .pytest-tmp/geo-package-recovery --junitxml test-results/geo-package-recovery.xml -q`
- 第一次沙箱运行在 pytest 创建 `.pytest-tmp` 时遇到已知 Windows `WinError 5`，未进入测试；不改 ACL，通过正常权限通道重跑成功：**1 passed**。JUnit：`test-results/geo-package-recovery.xml`。
- `git diff --check`：通过。
- PhotoSearchData 显式恢复：`tools/places/manage_dataset.py --data-root D:\SZ\project\PhotoSearchData import --source D:\SZ\project\PhotoSearchData\places\datasets\geonames-asia-cities500-v1 --activate` 成功；随后 `status` 确认 `state=active`、`resolver_available=true`、dataset `ACTIVE`、`problems=[]`、45,217 条记录。
- 导入命令在修复前的首次尝试被只读文件拒绝；修复后同目录恢复跳过文件复制。包目录中文件未被删除或覆盖。真实照片库、向量索引均未访问/操作。

## 限制与后续

catalog 重建会丢失其中的地点目录、人工归属、解析产物和活动包指针；这次通过从现存包显式重导恢复了登记/活动状态，不会复原已删除的人工数据或历史建议。以后如果希望这些 catalog 状态也在 catalog 删除后保留，需要另行裁决备份/数据隔离策略。

本次改动涉及状态恢复，独立 DS-H 全受影响面审查待做；不标 PASS。
