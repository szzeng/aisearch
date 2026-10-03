# P4-PLACE-PACKAGE-RECOVERY — catalog 重建后恢复现存只读地名包

状态：**IMPLEMENTED / 定向验证通过 / DS-H 独立审查待做**。协议仍遵循 ADR-0005 与 P2-03B。

## 目标

catalog 被删除或重建、但 `places/datasets/<dataset_id>` 原包仍存在时，允许从该目录校验并重新建立 catalog 登记与活动指针。恢复必须只读包目录；`vectors` 的删除不应成为地名包的输入或依赖。

## 本次范围

- `places.datasets.stage_package` 检测 source 与数据根目标目录为同一规范路径时，跳过文件复制；仍须先用 manifest/文件校验器完整校验，再写入 catalog 登记。
- 集成回归：合成只读包已位于目标目录且 catalog 无登记时，完成 stage/validate/activate，核对状态 ACTIVE 且所有包文件字节不变。
- 在用户指定的 `D:\SZ\project\PhotoSearchData` 对现存 GeoNames 包执行显式恢复，并核对活动状态。

## 验收与边界

- 不访问真实照片、不操作 `vectors`、不下载数据、不覆盖/删除地名包。
- 定向 pytest 使用 JUnit XML；记录实际失败/环境问题，不运行全量或性能套件。
- 记录 `git diff --check`。
- 状态/事务恢复工作需要 DS-H 独立全受影响面审查；通过前不标 PASS。无需因此切换执行模型。

## 关联

- 实现报告：[P4-PLACE-PACKAGE-RECOVERY](../reports/P4-PLACE-PACKAGE-RECOVERY.md)
- 相关现有实现与约束：P2-03B、ADR-0005。
