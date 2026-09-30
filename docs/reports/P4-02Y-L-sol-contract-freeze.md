# P4-02Y-L — Sol 短契约裁决与冻结

- 日期：2026-09-30；基线 HEAD：`d256b93`。
- 结论：**`SOL CONTRACT FREEZE PASS`**。仅冻结 OCR GPU 所需的契约形态；P4-02Y-L 产品实现和端到端验收尚未 PASS。
- 输入：[DS-H 最小裁决](P4-02Y-L-dsh-contract-arbitration.md)、[任务卡](../tasks/P4-02Y-L.md)、ADR-0003 §2/§4/§5、`p2-contracts.v1.json`、解析器及定点反例。
- 工作树在本次 Sol 开始时已有其他未提交改动，包括契约、OCR 和 P4-02Y-W；本次保留这些改动，仅收紧契约边界、修正直接测试并记录裁决。不能把整份工作树 diff 归因于本报告。

## 冻结决定

1. `contract_version` 保持 `p2-contracts.v1`，增加第 4 个 `processor_kind=structured_model`。它必须有 `model_id`、`model_revision`、至少一项含 `name/revision/sha256` 的 `weights`；不得声明向量字段、`output_type=embedding` 或发布 embedding artifact。JSON companion 和 Python 解析器拒绝非法 spec；既有 `artifacts/store.py` 在提交处拒绝非 embedding processor 的 embedding artifact。
2. `NonModelProcessorSpec`、解析器的 `non_model` CPU 约束及既有 GPS/place/taken-time 设备语义不变。OCR 不得改挂 embedding/reranker，不得靠 scheduler 特判取得 GPU。
3. OCR 目标 spec：`structured_model`、`CPU_ALLOWED`、`["gpu","cpu"]`、`FP32`。`auto` 只在 GPU provider 健康且有当前 OCR 专项资格证据时选 GPU；显式 `gpu` 缺资格为 `BLOCKED`；已派发 GPU 后实际只有 CPU provider 必须结构化失败，不可回退。
4. 冻结 OCR 身份字面量：`model_id="rapidocr-ppocrv4-mobile"`；`model_revision="rapidocr-onnxruntime@1.4.4"`，由 manifest 的 `engine` 与 `package_version` 组成；`weights` 仅为 manifest 钉住的 detection/classification/recognition 三个 ONNX SHA-256，配置文件继续由 `config_hash` 表达。DS-H 报告所称 manifest 的“pack revision”字段实际不存在，不能据此取值。
5. 这是同一应用版本内同步更新读写端的加性分支，**旧版闭集解析器不认识新 kind**；不得把新 spec 交给旧版读取者并宣称跨版本兼容。`processor_id`/artifact 身份复用的前提是 CPU/GPU 输出通过任务卡要求的固定夹具对照；若语义不兼容，先重新裁决身份版本。

## 验证与剩余边界

- `tests/unit/test_p2_contracts.py`、`test_p2_scheduler.py`、`test_p3_ocr_contract.py`：**66 passed**；JUnit：[p4-02y-l-sol-contract.xml](../../test-results/p4-02y-l-sol-contract.xml)。第一次沙箱运行因项目内 pytest 临时目录 `WinError 5` 出现环境错误；按正常权限重跑通过。另修正 OCR 测试误将必需的 `model_id/weights` 当作向量字段的旧断言。
- 定点 Ruff、`uv lock --check`、`git diff --check` 均通过。`NonModelProcessorSpec` 和解析器 `non_model` 校验段相对 HEAD 无修改。
- 本报告未验证真实 CUDA session、provider 真值、CPU/GPU 质量和加速、library build 回写；这些仍是 P4-02Y-L 实现卡的验收项。**不得据此标记总任务 PASS**。实现中若需改 ADR-0003 §2/§3/§5/§6/§8 或放宽 `non_model`，停止并重新提交契约裁决。

下一步按同一 [P4-02Y-L 任务卡](../tasks/P4-02Y-L.md)完成 OCR CUDA 实现与真机验收，然后按 V3.1 §2.4 做独立 DS-H 完整受影响面工程主审。模型切换由用户在 Codex 中手动完成。
