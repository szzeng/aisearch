# P4-TEST-BASELINE-CORE-A

日期：2026-10-11。状态：**PARTIAL / 本阶段选定范围定向通过**。

## 结果

- P3 成员计数反例原先用手写 SQLite 表，遗漏 `face_detail_auto_assets`。改用 `PersonHarness` 建立当前完整迁移 catalog，并插入最少合成资产/人脸/关系；继续验证人工取消、人工排除、建议脸不算成员、照片 distinct 计数与成员谓词一致。
- 复用 LITE-B 已核实的批准 `structured_model` 加性契约扩展和当前 companion SHA；在 P3/P4 冻结 companion 检查中更新到该批准值。未编辑正式契约文件。
- P4 upgrade-run 合同依然固定描述历史 v16→v17 的方案：合同参考 catalog 只执行 0001–0016，检查这些迁移字节仍冻结，并只确认特定 `0017_upgrade_runs.sql` 尚未占用；不再要求当前应用 schema 停在 v16 或 0017 前缀全空。
- P4-02E 测试固定模拟 v15/v16 老版本：current migration chain 仍完整检查至当前支持版本；v15→v16、重开幂等和 v15 build 拒绝 v16 catalog 各自使用版本固定的合成迁移目录。未修改正式 migration。
- GPU 请求拿到 CPU-only RapidOCR session 时，当前实现明确返回 `OCR_GPU_INITIALIZATION_FAILED`，并报告 `cls/det/rec` 三个不匹配角色。更新旧测试拒绝码断言。此节点使用替身 Engine，不是 ONNX/CUDA runtime 验收；真实原生运行时记为 **RUNTIME_UNCONFIRMED**。
- 未改 `src/`、生产 SQL 或正式 migration。历史分类中未逐一复核的 schema/migration、provider/face-spec 和 OCR 原生环境节点仍为 UNKNOWN；本报告不将旧全量门禁标 PASS。

## 验证

- 最终定向 JUnit：`test-results/P4-TEST-BASELINE-CORE-A.xml`，覆盖修改的 4 个测试文件，**108 passed / 0 failed / 0 errors / 0 skipped**。
- Ruff、修改 Python 文件 `py_compile`、`git diff --check` 通过。
- 未执行真实 ONNX/CUDA/GPU、OCR 模型、真实图库或全仓回归。

## 总控结束

四阶段均已提交。总控按 PARTIAL 结束：PLACE 同名查询的 `needs_scope_choice`/`needs_place_choice` 仍需协议判断；旧人工归属和退休保护需要明确历史合成状态；OCR 原生环境与未复跑的全量历史失败仍待后续专项处理。
