# P4-PLACE-BUNDLED-DATA-A 执行报告

日期：2026-11-04。状态：**IMPLEMENTED / SYNTHETIC VALIDATION PASS / REAL RELEASE VALIDATION PENDING**。

## 实现

- `build_onedir.py --place-dataset <dir>` 增加正式资源参数；用户数据由发行资源清单锁定，完整校验后复制到 `PhotoSearch/_internal/place-datasets/<dataset_id>/`。`release_windows.ps1` 要求指定数据目录；onedir/ZIP 校验拒绝缺包、错包、缺文件、错误 size/SHA 与清单外构建文件。
- 新增冻结包首次安装器。只有空 registry 且用户 `places/datasets/` 为空时才安装；先复制到数据根内隔离临时目录并完整复验，之后同卷原子发布并复用 `restore_surviving_dataset()` 注册/激活。缺失前驱只进入既有空 registry 恢复特例，Manifest 不改。已登记状态不触发内置包读取。
- 安装失败留日志且不阻断启动；仅删除带本安装器所有权标记的中断暂存。文档已说明离线内置、首次安装和失败查看方式。未改 GPS/匹配、Qwen、OCR、人脸、Schema、真实图库或模型路径。

## 资源与发行边界

预期 `dataset_id=geonames-asia-cities500-v1-cn-c3c03b41ffe4cbe3`，历史报告给出 8 个文件、未压缩 76,804,462 bytes。执行时本地候选目录 `.tmp/p4-place-cn/composite-261003/` 不存在。仓库历史摘要未包含可以独立锁定的顶层 `content_sha256`；因此 `tools/places/bundled-dataset-release.json` 将其保持为 `null` / `PENDING`。发行校验对该状态 fail-closed，未声称真实数据或正式 ZIP 已通过，也未重建或下载数据。

因此实际 ZIP 增量大小、真实 ZIP 完整性以及中国区县和国外城市真实查询均 **PENDING**。76,804,462 bytes 是历史候选解压文件总量，不是本次 ZIP 增量大小。合成 ZIP 只验证格式和拒绝路径，不作为真实复合包证据。

许可审查亦 **PENDING**：P4-PLACE-CN 工程审查明确保留数据包许可/交付签核。GeoNames 官方页面指向 CC BY 4.0，OSM Copyright 页面标明数据使用 ODbL；本轮未获得真实候选及其中逐项 license/attribution 文本，未完成归属位置、衍生数据库及对外再分发义务审查。见 [GeoNames](https://www.geonames.org/)、[OpenStreetMap Copyright and License](https://www.openstreetmap.org/copyright)、[ODbL 1.0 文本](https://opendatacommons.org/licenses/odbl/1-0/)。

## 验证

最终定向 JUnit：`tests/integration/test_bundled_place_dataset.py`、`tests/integration/test_place_package_startup_recovery.py`、`tests/unit/test_release_windows.py`，**42 passed / 0 failed / 0 errors / 0 skipped**，退出码 0；原始结果 `test-results/P4-PLACE-BUNDLED-DATA-A/final-r3.xml`。覆盖首次离线安装及 ACTIVE、空 registry 缺前驱恢复、CatalogService 第二次启动不重装/活动 revision 不变、既有 ACTIVE/STAGED/VALIDATED/RETAINED 包不覆盖、多个未登记包需人工处理、安装器暂存残留清理、复制中断后安全重试、损坏内容拒绝、Schema 不兼容拒绝、未标记临时目录不触碰、synthetic ZIP 数据文件改写拒绝并返回归档贡献字节统计。

Ruff：最终定向源/测试文件通过。Python `py_compile`、PowerShell AST 解析与 `git diff --check` 通过。初次 Ruff 曾发现导入排序与一个未使用导入，整理后复跑通过。测试首轮也因 synthetic pin fixture 缺失许可状态字段有 3 例失败；保留于 `test-results/P4-PLACE-BUNDLED-DATA-A/final-r1-failure.xml`，补齐 fixture 后完整重跑 42 项通过。未运行全量套件、30 万照片性能、模型、人脸/OCR任务、PyInstaller 真实 onedir/ZIP 构建或浏览器测试。当前 pin 为 PENDING，真实 release build 本应 fail-closed，因此不能将未执行真实构建写成通过。

## 验收剩余项

- **已验证**：合成首次安装、缺前驱恢复、重复启动、旧注册包保护、损坏/Schema 拒绝、安装器中断清理与重试、合成 ZIP 成员校验；未联网。
- **待做**：从指定本地候选读取并用 `load_content()` 复核真实 ID/Manifest/8 个文件 hash/Schema/GeoNames 与 OSM 许可文件；据此填充外部锁定 `content_sha256`；执行真实 onedir + ZIP 完整校验并记录 ZIP 增量；真实复合包区县和海外城市查询。
- **仍待审**：公开分发 GeoNames CC BY 4.0、OSM ODbL 1.0 的署名、许可副本、衍生数据库/数据提供义务；未通过前不得对外分发。
- **仍待验**：独立 DS-H 全受影响面工程审查；另一台干净 Windows 离线启动/跨电脑安装；公开发行流程与模型资格不在本轮证明范围。

未访问照片图库或真实数据根，未下载/构建地名数据，未执行 Qwen 工作流，无 GitHub push 或 Release 操作。
