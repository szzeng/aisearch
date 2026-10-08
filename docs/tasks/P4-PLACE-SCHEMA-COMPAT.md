# P4-PLACE-SCHEMA-COMPAT — 地名包 Schema 兼容规则收敛

日期：2026-10-08（基线校准：2026-10-09）。状态：**IMPLEMENTED / 定向验证通过**。
仓库：`szzeng/aisearch`；立卡参考 HEAD `a5aaab19`，开工核对实际 HEAD 和工作区。  
建议执行模型：**GPT-6 Luna / High**；个人相册最小维护任务，定向测试即可。

## 1. 基线校准与目标

本卡立卡时记录 Catalog Schema 20；实际开工 HEAD `30cd536` 已包含 migration `0021_cn_manual_place_refs.sql`，当前 `SUPPORTED_SCHEMA_VERSION=21`。已核对 0021 只增加 OSM/GeoNames 来源身份到既有 Place FK 的 catalog 映射表，不改变地点包字节、GeoNames 投影或 CN addon 契约。故本卡将审核上限校准到 21；不修改 migration、Schema 版本或 Manifest。

## 2. 问题与目标

当前 Catalog `SUPPORTED_SCHEMA_VERSION=21`。`geo/dataset.py::validate_manifest` 对 Schema 17～21 逐版追加旧包兼容分支；原 GeoNames 包的上限为 16，中国 OSM + GeoNames 复合包的 Manifest 上限为 18。以后数据库升级，即使只修改人脸、OCR 等无关功能，也容易重复触发地点包不兼容。

**目标**：把已经审核过的 17～21 兼容例外收敛为一个明确、有审核上限的规则。未来无关地点契约的 Schema 升级，经开发者核对后只提升审核上限；用户继续使用**原样不变**的复合包，不重制、不重装、不重算 GPS。

**本任务不创建新 Schema，不修改 `SUPPORTED_SCHEMA_VERSION`、数据库迁移或包 Manifest。**

## 3. 最小实现

主要修改 `src/photo_search/geo/dataset.py::validate_manifest`，将四组硬编码分支改为统一判断，建议集中定义：

```python
PLACE_LEGACY_MAX_FLOOR = 16
PLACE_COMPAT_REVIEWED_THROUGH = 21
```

判断语义：

- **正常声明区间**：`compatible_min <= supported_schema_version <= compatible_max` 时照常接受。
- **已批准的历史桥接**：仅当 `compatible_min <= supported_schema_version`、`compatible_max >= 16`、`compatible_max < supported_schema_version`、`17 <= supported_schema_version <= PLACE_COMPAT_REVIEWED_THROUGH` 时接受。
- 其他情况拒绝，继续返回现有 `PLACE_DATASET_INVALID`；不引入全未来版本通配。
- 保留 `minimum <= maximum`、格式、身份、文件大小/hash、内容 hash、目录与层级校验；中国行政区 addon 仍要求 **Catalog Schema >= 18**。

**等价性是硬要求**：Schema 16～20 的已有接受/拒绝矩阵不得变化（尤其 maximum=15 必须继续拒绝；minimum 大于当前版本不得绕过）。不要仅依据包 `maximum >= 16` 放宽 minimum 条件。

未来创建真实 Schema 时，先核对新增迁移是否涉及地点表、包投影、解析规则、行政区数据或稳定 ID：
1. **未改变地点契约**：将 `PLACE_COMPAT_REVIEWED_THROUGH` 提升到该 Schema，跑定向测试，并在该迁移报告中记录判断。
2. **只增加向后兼容地点能力**：先核对实际所需的数据库派生数据迁移/缓存影响，再决定是否批准兼容。
3. **地点契约不兼容**：不得抬高审核上限绕过问题；另立针对性的迁移或包转换任务。

不引入独立地点 Schema 版本体系、新表、映射配置、Manifest v2 或 UI 改造。

## 4. 保留现有运行路径

复用已有中央校验链：地名包导入、验证、激活、状态、回退、GPS resolver 加载、空 registry 唯一复合包恢复。不分别添加 Schema 特判；只有发现本卡直接引起的问题才允许窄修。

保持现有 GeoNames 国外地点、中国 OSM 行政区、用户人工地点/排除、地图搜索、活动包指针和已存 GPS 归属语义。

特别注意：`P4-PLACE-RECOVERY-B` 已实现「空 registry + 唯一健康复合包 + Manifest 指向缺失的前驱包」的恢复例外，**不得回退该行为**。如旧测试 `test_place_package_startup_recovery.py` 的 missing-predecessor 断言仍与现实现冲突，只窄幅更新过时预期。

## 5. 最少测试

优先扩充 `tests/integration/test_place_dataset_schema17.py` 的现有兼容矩阵：

| 包声明 minimum～maximum | 当前 Schema | 结果 |
|---|---:|---|
| 5～16 | 20 | 接受 |
| 18～18 | 20 | 接受 |
| 5～15 | 20 | 拒绝 |
| 21～21 | 20 | 拒绝 |
| 18～18 | 21（审核上限 20） | 拒绝历史桥接 |
| 18～18 | 21（审核上限 21） | 接受历史桥接 |
| 5～20 | 21 | 接受历史桥接 |
| 5～15 | 21 | 拒绝 |
| 带 CN addon 的包 | 17 | 拒绝加载 |
| 文件被篡改或 hash 错误 | 已接受的版本 | 继续拒绝 |

Schema 22 **仅测试兼容政策函数**并确认未审核版本拒绝；不伪造或创建 `0022` migration，不声称完成实际数据库升级。当前 Schema 21 由现有 migration 支持。

利用已有小型临时夹具做一次复合包活动状态、重新加载及中外各一处地点的基础验证；确认包文件/Manifest 字节不变。复用现有用例，避免重复构建大型集成测试。

只运行变更所涉及的定向 pytest、Ruff、`git diff --check`；**不运行**全量测试、30 万图库性能测试、真实相册建库、GPU 或浏览器自动化。

## 6. 禁止范围与交付

禁止访问/修改真实 `PhotoSearchData`；不下载 OSM PBF，不重建约 76.8 MB 复合包，不变更 package ID/version、Manifest、hash；不清理 Catalog、重算 GPS/图文向量/OCR/人脸或缩略图。

交付：
- 最小生产代码修改，主要限 `geo/dataset.py`；
- 必要的原有定向测试更新；
- 简短报告 `docs/reports/P4-PLACE-SCHEMA-COMPAT.md` 和 `docs/development-status.md` 状态记录。

**验收**：Schema 16～20 旧行为不变；当前已审核 Schema 21 正确加载旧包；未审核 Schema 22 不能自动桥接；旧包字节不变；定向检查通过。

不修改冻结 ADR/JSON 契约，不默认安排 Sol / 全仓审查。若发现必须修改冻结语义、地点包格式或数据库迁移，停止扩大范围并报告。开发完成后按实际验证结果标注状态，不把未执行的真实环境验证写成 PASS。

本次立卡仅创建此任务文档；执行阶段是否 commit/push 由用户另行决定。
