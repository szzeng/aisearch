# P4-PLACE-RECOVERY-B — 单复合地名包清库后自动恢复

日期：2026-10-08。状态：**READY / 待实现，用户手动验收**。
仓库 `szzeng/aisearch`；立卡参考 HEAD `43628c7`，开工先核对实际 HEAD、工作区和已有改动。
定位：个人相册最小修复，不重构包生命周期，不制作新地名包。

## 1. 现象与目标

用户实际运行 `HEAD 5d7bc13`、Schema 20、数据根 `D:\SZ\project\PhotoSearchData`。
磁盘上原 GeoNames 与中国复合包均通过 `load_content()` 校验，但 catalog 重建后 `geo_datasets=[]`，页面 `PLACE_DATASET_NOT_INSTALLED`。当磁盘只剩复合包时，启动日志明确报告：
`surviving place package needs a predecessor; automatic recovery skipped`。

期望以后在 `places/datasets` **仅保留**
`geonames-asia-cities500-v1-cn-c3c03b41ffe4cbe3`
这一个完整复合包；清空照片 catalog/派生索引后重启，应用自动注册为 `ACTIVE`。复合包已经包含 GeoNames 45,217 条地点与中国 OSM 3,013 条行政区；国外仍用 GeoNames，中国行政区仍用 OSM。**原始 GeoNames 包不应是必装项。**

注意：当前实际目录曾同时有两个有效包。**两个包时仍不自动选择**；用户自行把旧包移到 `places/datasets` 之外后才满足“唯一包”前提。

## 2. 最小实现

重点查 `src/photo_search/places/datasets.py::restore_surviving_dataset`、`_insert_dataset_rows`、`_verify_stored` 和 `0005_place_dataset_resolution.sql` 的 `previous_dataset_id` 外键；沿用 `CatalogService.open` 的现有入口。

1. 仅当 `geo_datasets` 完全为空、`places/datasets` **恰好一个**安全直接子目录，且完整包校验通过时，执行已有恢复事务；复用现在的 checksum、manifest、Schema、目录安全、内容格式与层级校验。
2. 若这个唯一包的 `previous_dataset_id` 指向数据库中不存在的旧地名包，只在**新写入 catalog 注册记录时**将该回退引用设为 `NULL`，避免 FK 失败；**磁盘 Manifest 完全不变**。在日志中简短说明“忽略不可用的历史回退目标”。
3. 依然在一个事务里完成注册、`VALIDATED`、`ACTIVE`、GeoNames 与中国行政区物化；任一步失败整体回滚。重启幂等，不重复注册或物化。
4. 检查状态投影、`_verify_stored`、手动重新激活等路径是否会错误要求 catalog 回退引用与原 Manifest 一致；仅处理本恢复例外，不取消身份、内容校验，不修改通用 `stage/validate/activate/rollback` 的正常约束。
5. 有任何现存注册行、多包、缺件、哈希不匹配、Schema 不兼容、危险目录或其他异常时，保持原有拒绝行为。**不要**自动删除包、伪造旧包记录或悄悄切换现有活动包。
6. 该包在新库中没有回退目标属于预期行为；不要求增加历史回退功能。

不修改 `manifest.json`、不更换包 ID／版本、不复制／重建 76.8 MB 数据、不下载 PBF；不新增 Schema/migration/表，不改 GPS、照片搜索、人物、地图 UI 或模型。

## 3. 验证：用户自行手动测试

**实现者不需运行 pytest、浏览器自动化、全量回归或 30 万数据测试。** 最多运行 Ruff 与 `git diff --check` 这种轻量静态检查，不能宣称运行时已通过。

交付简单的手动验收步骤；用户操作真实数据前须**关闭 PhotoSearch 并备份旧库**：

1. 将原 GeoNames 包移到 `places/datasets` 之外，仅保留完整中国复合包；不得由实现者直接操作真实 `PhotoSearchData`。
2. 用户自愿在已备份前提下创建空 catalog／重建照片库，再运行 `tools/dev/start_personal_trial.ps1`，打开地点页触发正常服务恢复。
3. 页面或 `manage_dataset.py --data-root ... status` 应显示 `state=active`、`resolver_available=true`、活动包为中国复合包；启动日志不再有 `needs a predecessor; ... skipped`。
4. 关闭并重启后仍 `ACTIVE`，简单抽查中国区县和一处国外城市。确认后由用户自行决定是否删除旧包备份。

`manage_dataset.py status` 本身直接打开 `Catalog`，不触发启动自动恢复；应在应用已打开相关 API 之后检查，不将先运行 status 得到的空注册表误判为修复失败。

如手动验收失败，要求报告具体错误，不扩大任务范围。用户验收前状态只能是 **IMPLEMENTED / MANUAL TEST PENDING**，不能写 PASS。

## 4. 交付与边界

- 最小代码修改 + `docs/reports/P4-PLACE-RECOVERY-B.md` 短报告：改动文件、如何保护外键/完整性、未验证事项、用户手动步骤。
- 如需更正 `ADR-0015`，仅补充**空 registry + 唯一健康包 + 缺失历史回退目标**这一例外，其他既有规则不变。
- 同步 `docs/development-status.md`，但不重写其他进行中的任务状态。
- 按项目审查策略保留未审状态，不做全仓审核；用户自己决定后续审查与验收。
- 不碰个人照片和实际 catalog／地名包，不删数据、不自动提交或推送实现。

**停止条件：** 如果不放宽通用完整性规则就无法实现上述窄例外，记录具体阻塞并停止，不发展为多包恢复／新包重封装任务。
