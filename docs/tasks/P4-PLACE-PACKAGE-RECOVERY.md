# P4-PLACE-PACKAGE-RECOVERY — catalog 重建后恢复现存只读地名包

> **2026-10-09个人待审范围更新（当前优先）：** 按[个人审查约定](../plans/P4-PERSONAL-REVIEW.md)与[本组唯一入口](P4-PERSONAL-REVIEW-PLACE-A.md)收口。下文保留历史实现要求；冲突的重复全量/30万/模型/打包/固定多轮审查及浏览器组合要求，以本次个人基础范围为准。先复用可核对证据，缺口才补最小测试；首次审查覆盖完整未审新增面，身份、人工状态、撤销、迁移/恢复保护仍必留。此更新未执行审查或新增PASS；已有现场未验事项照实保留。

状态：**IMPLEMENTED / 定向验证通过 / DS-H 独立审查待做**。协议仍遵循 ADR-0005 与 P2-03B。

## 目标

catalog 被删除或重建、但 `places/datasets/<dataset_id>` 原包仍存在时，应用启动必须自动校验并恢复唯一完整包的登记和活动指针。上一轮只完成显式命令恢复，未满足自动恢复目标；本轮继续同一任务。恢复必须只读包目录；`vectors` 的删除不应成为地名包的输入或依赖。

## 本次范围

- `places.datasets.stage_package` 检测 source 与数据根目标目录为同一规范路径时，跳过文件复制；仍须先用 manifest/文件校验器完整校验，再写入 catalog 登记。
- 集成回归：合成只读包已位于目标目录且 catalog 无登记时，完成 stage/validate/activate，核对状态 ACTIVE 且所有包文件字节不变。
- 在用户指定的 `D:\SZ\project\PhotoSearchData` 对现存 GeoNames 包执行显式恢复，并核对活动状态。
- 在应用的 `CatalogService.open` 添加恢复钩子：仅在 geo registry 为空、包目录只有一个安全直接子目录时校验并在单个事务内注册/验证/激活。已有 registry 不改活动选择；多包、坏包、错目录或目录链接不擅自激活。
- 验证真正删除合成 catalog/vectors 后重新打开应用服务和读取 API，避免再次仅测试手动导入；覆盖重复启动、已安装但未激活、坏包、多包和事务失败回滚。

## 本轮读取

主架构 §3.2/§4.5/§11.1/§12.4 第33条；V3.1 §3.2/§11；ADR-0005 §8；`catalog/db.py`、`ingestion/service.py`、`places/datasets.py`、API lazy service factory 及相关生命周期/API测试。跨模块启动恢复决策记录于 ADR-0015（实现中，独立审查待做），不改既有冻结 companion 或历史 migration。

## 验收与边界

- 不访问真实照片、不操作 `vectors`、不下载数据、不覆盖/删除地名包。
- 定向 pytest 和最终 unit/integration 回归使用 JUnit XML；记录实际失败/环境问题，不运行性能套件、真实库重扫或模型测试。
- 记录 `git diff --check`。
- 状态/事务恢复工作需要 DS-H 独立全受影响面审查；通过前不标 PASS。无需因此切换执行模型。

## 关联

- 实现报告：[P4-PLACE-PACKAGE-RECOVERY](../reports/P4-PLACE-PACKAGE-RECOVERY.md)
- 相关现有实现与约束：P2-03B、ADR-0005。
