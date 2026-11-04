# P4-PLACE-BUNDLED-DATA-A 执行报告

日期：2026-11-04。状态：**IMPLEMENTED / SYNTHETIC VALIDATION PASS / LUNAH ENGINEERING REVIEW PASS / REAL RELEASE VALIDATION PENDING**。

## 实现

- `build_onedir.py --place-dataset <dir>` 增加正式资源参数；用户数据由发行资源清单锁定 dataset ID、数据内容 SHA 和 Manifest SHA，完整校验后复制到 `PhotoSearch/_internal/place-datasets/<dataset_id>/`。`release_windows.ps1` 要求指定数据目录；onedir/ZIP 校验拒绝缺包、错包、缺文件、错误 size/SHA、原始 PBF/GeoNames dump 和临时构建文件。
- 新增冻结包首次安装器。只有空 registry 且用户 `places/datasets/` 为空时才安装；先复制到数据根内隔离临时目录并完整复验，之后同卷原子发布并复用 `restore_surviving_dataset()` 注册/激活。缺失前驱只进入既有空 registry 恢复特例，Manifest 不改。已登记状态不触发内置包读取。
- 安装失败留日志且不阻断启动；仅删除带本安装器所有权标记的中断暂存。文档已说明离线内置、首次安装和失败查看方式。未改 GPS/匹配、Qwen、OCR、人脸、Schema、真实图库或模型路径。

## 资源与发行边界

候选目录 `.tmp/p4-place-cn/composite-261003/` 不存在；用户实际提供的 `D:\SZ\project\PhotoSearchData\places\datasets\geonames-asia-cities500-v1-cn-c3c03b41ffe4cbe3` 存在。2026-11-04 通过项目 `load_content()` 只读完整校验：期望 dataset ID 正确、Schema 18–18、8 文件合计 76,804,462 bytes；`content_sha256=4a5ed1d4a45ca95be15bce38415f0db95266b665a096c06eef9191b37c2b78c4`，Manifest 原始字节 SHA-256 `bb0781448e27d7a24c0cbe197c7f06165f2e761c355df9b10625bb29aba79d7a`。两个摘要已写入 `tools/places/bundled-dataset-release.json`。未重新下载或构建地名数据。

实际 ZIP 增量大小、真实 ZIP 完整性以及中国区县和国外城市查询仍 **PENDING**。76,804,462 bytes 是候选解压文件总量，不是 ZIP 增量大小。用户选择自行执行最终验证；本轮未运行测试或发行构建。

许可审查亦 **PENDING**：P4-PLACE-CN 工程审查明确保留数据包许可/交付签核。GeoNames 官方页面指向 CC BY 4.0，OSM Copyright 页面标明数据使用 ODbL；本轮未获得真实候选及其中逐项 license/attribution 文本，未完成归属位置、衍生数据库及对外再分发义务审查。见 [GeoNames](https://www.geonames.org/)、[OpenStreetMap Copyright and License](https://www.openstreetmap.org/copyright)、[ODbL 1.0 文本](https://opendatacommons.org/licenses/odbl/1-0/)。

## 验证

上次代码复核版本的定向 JUnit：上述三组测试 **54 passed / 0 failed / 0 errors / 0 skipped**，退出码 0，证据 `test-results/P4-PLACE-BUNDLED-DATA-A/review-fixes-r2.xml`。覆盖首次离线安装及 ACTIVE、空 registry 缺前驱恢复、CatalogService 第二次启动不重装/活动 revision 不变、既有 ACTIVE/STAGED/VALIDATED/RETAINED 包保护、marker 故障恢复与预置 marker 保留、损坏/Manifest/Schema 拒绝、ZIP 文件哈希及 PBF/GeoNames dump/temp 拒绝。该结果早于本轮发行 pin 更新；本轮遵照用户要求未重跑。

独立 `lunah` 初审及跟审 PASS；其结论覆盖候选 pin 更新前的工程实现。真实发行构建尚未执行。GeoNames/OSM许可归属与再分发义务仍未核准，不对外分发。

## 验收剩余项

- **已验证**：合成首次安装、缺前驱恢复、重复启动、旧注册包保护、损坏/Schema/Manifest 拒绝、owner marker 冲突保留/写入失败清理、安装中断恢复、合成 ZIP 成员校验及原始/临时数据拒绝；独立 `lunah` 工程复核 PASS；未联网。
- **待做**：执行真实 onedir + ZIP 完整校验并记录 ZIP 增量；以真实包查询中国区县及一座海外城市。
- **仍待审**：公开分发 GeoNames CC BY 4.0、OSM ODbL 1.0 的署名、许可副本、衍生数据库/数据提供义务；未通过前不得对外分发。
- **仍待验**：独立 DS-H 全受影响面工程审查；另一台干净 Windows 离线启动/跨电脑安装；公开发行流程与模型资格不在本轮证明范围。

未访问照片图库或真实数据根，未下载/构建地名数据，未执行 Qwen 工作流，无 GitHub push 或 Release 操作。
