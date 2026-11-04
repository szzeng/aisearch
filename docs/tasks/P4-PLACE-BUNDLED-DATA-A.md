# P4-PLACE-BUNDLED-DATA-A — Windows 发布包内置地名数据与首次自动安装

日期：2026-11-04。状态：**IMPLEMENTED / SYNTHETIC VALIDATION PASS / LUNAH ENGINEERING REVIEW PASS / REAL RELEASE VALIDATION PENDING**。

## 目标与边界

Windows onedir/ZIP 可以携带已整理的 GeoNames 亚洲城市 + 中国 OSM 行政区复合包；冻结应用在用户选定的 `PhotoSearchData` 中首次启动时，完成完整校验后安全安装并通过既有恢复流程激活。普通源码构建允许不携带地名包。不得下载/重建上游数据，不覆盖既有地点状态或照片、人工地点、排除记录和索引，不改变 Qwen、人脸或 OCR 安装流程。

期望 ID：`geonames-asia-cities500-v1-cn-c3c03b41ffe4cbe3`。已知历史包为 8 个文件、76,804,462 bytes。当前 `.tmp/p4-place-cn/composite-261003/` **不存在**；历史证据没有外部固定的 `content_sha256` 或 Manifest 原始字节摘要。发行资源清单因此将两项摘要和许可放行都保持 PENDING，并拒绝包含数据的发行构建，直到可校验正式候选后锁定数据与 Manifest。不得用 ID 后缀代替摘要。

## 必读范围

- 本架构：V3.0 §3.2、§4.5、§9.3、§11.3；V3.1 §2.4、§3.2/3.4、§9.3、§11、§12。
- 直接调用链：`build_onedir.py`、`release_windows.py`/`.ps1`、`manage_dataset.py`、`places/datasets.py::restore_surviving_dataset`、`CatalogService.open`、当前发行说明和相关 place recovery/package tests。
- 旧包数据与许可/交付边界：P4-PLACE-CN 报告、ADR-0016、P4-PERSONAL-REVIEW-PLACE-A。

## 实施不变量

1. 发行资源清单锁定 dataset ID、顶层 `content_sha256`、原始 `manifest_sha256`、文件数和字节数。构建、onedir 校验、ZIP 与运行时安装均拒绝 ID 相同但数据或 Manifest 不同的包；完整包校验使用 `load_content()`，清单外文件、symlink/reparse point 和临时构建文件拒绝。
2. ZIP 正式归档必须含完整包与两份来源许可/归属文件；Qwen 流程不变。ZIP 校验逐文件对照 Manifest size/SHA 与锁定的内容摘要。
3. Catalog 正常初始化后先调用既有 sole-package recovery；只在 registry 和数据目录均为空时检查 frozen bundle。先复制到数据根内带安装器所有权标记的临时目录，完整复验后在同卷原子 rename，再调 `restore_surviving_dataset()`。缺失前驱只由原恢复例外置 NULL；磁盘 Manifest 不改。
4. registry 非空时不碰任何包状态。已 ACTIVE、STAGED、VALIDATED、RETAINED 包均不覆盖/切换；多包、冲突、损坏和未标记临时路径保留。已登记包启动不读取内置包或重算其 SHA。
5. 可选地名安装失败写日志，不阻断应用启动；当前地点页状态仍由既有 API 投影提供。只测试隔离合成数据，不访问真实图册、真实 PhotoSearchData、模型或向量索引。

## 性能设计（PERF-BOUNDED）

- 链路与风险：`CatalogService.open`；普通重启不得散读或 hash 76.8 MB。既有唯一包恢复本来就需要完整验证；内置复制只在 registry 与用户包目录都空时一次执行。
- 做法与边界：空 registry/包目录走一次序列化复制、完整校验与原子发布；使用原 Catalog 恢复事务，无新调度系统、无后台长任务，普通已注册启动只查 registry。
- 验证与停止：合成数据覆盖重复启动、恢复、失败及 ZIP 条目；不做 30 万照片、模型或长期延迟测试。未运行真实包/干净第二台 Windows 时不宣称发布或跨机通过。

## 验证及当前结果

命令与最终证据见[执行报告](../reports/P4-PLACE-BUNDLED-DATA-A.md)。合成测试覆盖首装/ACTIVE、缺失前驱、重复启动、既有 ACTIVE/STAGED/VALIDATED/RETAINED 包保护、复制故障重试、marker-only 与 marker 写入故障恢复、预置 marker 保护、损坏/Manifest/Schema 拒绝、ZIP 内文件哈希，以及 ZIP 拒绝 PBF/GeoNames dump/临时文件。真实候选包、真实 ZIP、中国区县+国外城市查询、干净第二台 Windows、正式 license/attribution/ODbL 衍生数据库分发义务均保持 PENDING。

该任务包含 Catalog 启动、地点包注册/激活与数据恢复状态变化。独立复核由用户指定的子代理 `lunah` 完成：初审 findings 已修复，跟审确认全部关闭且无新 blocker。工程审查只覆盖本次代码和合成证据，不代替候选来源许可核准或真实发行验收。

## 交付与停止点

- 唯一任务报告：`docs/reports/P4-PLACE-BUNDLED-DATA-A.md`。
- 只有固定摘要来自真实候选、合成验证和真实 ZIP 验证通过、独立工程审查通过后，才可标记发行集成 PASS。未经 GeoNames CC BY 4.0 / OSM ODbL 1.0 正式审查的包不得对外分发。
- 不自动推送 GitHub、打 tag 或发布 Release；跨电脑、公开分发和模型资格是独立验收边界。
