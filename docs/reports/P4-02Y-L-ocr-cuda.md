# P4-02Y-L — OCR CUDA 实现与验收报告

- 日期：2026-09-30；实现基线：`d256b93`。
- 结论：**`IMPLEMENTATION ACCEPTANCE PASS / INDEPENDENT DS-H REVIEW PENDING`**。冻结契约已实现；本机 CUDA、显式 CPU、输出等价、性能门槛和 synthetic refresh/build 验收通过。P4-02Y-L 总任务仍待独立 DS-H 全受影响面审查，不能标总 PASS。
- 契约冻结：[Sol 冻结报告](P4-02Y-L-sol-contract-freeze.md)；裁决：[DS-H 契约裁决](P4-02Y-L-dsh-contract-arbitration.md)。

## 实现范围

- 按冻结形态新增 `structured_model` 的 ADR、JSON Schema 和 `ProcessorSpec` 解析分支；OCR spec 使用 `CPU_ALLOWED`、`["gpu", "cpu"]`、`FP32`，声明冻结的 model identity 与 manifest 三份 ONNX 权重 SHA。既有 `non_model` CPU 限制未放宽。
- RapidOCR det/cls/rec 按请求选择 CUDA 或 CPU，GPU 初始化前 preload 运行时 DLL；检查三个真实 session 的 provider 列表，并验证其首选 provider 与派发设备一致。不匹配以 `RUNTIME_RESOLUTION_MISMATCH` 失败，不生成成功 OCR 结果。
- Engine cache key 包含设备、provider、precision。能力资格只在 CPU/GPU session 构造并核对 provider 后登记。`auto` 依赖 GPU 健康及 OCR 专项资格，显式 CPU 保持可用，显式 GPU 无资格时由 scheduler 阻塞。
- CLI `enqueue` / `recognize` 增加 `--device auto|cpu|gpu`。OCR payload、权重、config hash、长边/切片/阈值、batch=1、单 worker 和依赖均未改变；运行 provider 事实保留在 job/result 与资格记录中。

## 验收证据

### CPU / GPU 实际路径与输出对照

使用本机 NVIDIA GeForce RTX 4060 Laptop GPU、`onnxruntime-gpu@1.30.0` 和项目既有权重。所有图像均由 fixture 生成在项目 `tmp/p4-02y-l` 下；没有扫描真实图库，也没有下载/安装模型、CUDA Toolkit、TensorRT 或 Python 依赖。

- auto smoke：invoice、dense、blank 共 **3/3 `SUCCEEDED`**；三条 job 均记录 `requested_device=auto`、`resolved_device=gpu`、`runtime_provider=CUDAExecutionProvider`、`precision=FP32`。OCR projection `SYNCED`，3 文档 / 25 regions；invoice 的 FTS 查询 `发票`、`1280`、`20260914` 均命中。
- 显式 CPU smoke：**3/3 `SUCCEEDED`**，均记录 `requested_device=cpu`、`resolved_device=cpu`、`CPUExecutionProvider`。能力探针仍识别 GPU，但显式 CPU job 未被改派。
- 对照夹具 warm-up 后各测 3 轮。CPU round-total 中位数 **5227.72 ms**，GPU **843.33 ms**，CPU/GPU **6.199×**，高于 2× 门槛。分图中位数：blank 296.31 / 59.89 ms，dense 3953.11 / 595.30 ms，invoice 935.02 / 163.50 ms（CPU / GPU）。
- CPU 三个 session 只报告 `CPUExecutionProvider`；GPU det/cls/rec 三个 session 的首选 provider 均为 `CUDAExecutionProvider`（session provider 列表也含 CPU）。GPU 前后显存由 **2866 / 8188 MiB、12%** 变为 **2930 / 8188 MiB、23%**；CPU 对照前后为 **2117 / 8188 MiB、0%**。
- 对照门限：文本精确一致；每图区域数量一致；最大 box/polygon 坐标差 **0 px**；最大置信度差 **0.0062746**（dense），低于 0.03。blank、dense、invoice 均 equivalent。

原始 smoke、对照和 build JSON 与 JUnit 输出在本机 `test-results/`（被 `.gitignore` 排除）；核心可复核指标及命令已记录在本报告。

### Refresh/build fixture

`ocr_refresh_build_fixture.py` 用 3 张 synthetic 图片运行真实 `library refresh/build` 流程，refresh `COMPLETE`，OCR stage `OK`；3/3 OCR jobs 最终均为 `SUCCEEDED`，记录 `auto → gpu`、`CUDAExecutionProvider`、`FP32`。Face 与 place 阶段因 fixture 未提供对应权重/数据而显示 `SKIPPED_UNAVAILABLE`，不影响 OCR 阶段断言。没有触碰真实照片目录。

### GPU 故障边界

新增回归覆盖两种边界：可用 provider 列表没有 CUDA 时，显式 GPU 请求返回 `RUNTIME_CAPABILITY_UNAVAILABLE`，engine factory 调用次数为 0；运行时声称有 CUDA、但 RapidOCR 返回的 det/cls/rec session 只有 CPU 时，拒绝并返回 `RUNTIME_RESOLUTION_MISMATCH`。这证明不会把不合格 GPU session 当作成功 CPU 推理。

### 自动化验证

```text
uv run --no-sync pytest -q -p no:cacheprovider --basetemp=.pytest-tmp/p4-02y-l-required --junitxml=test-results/p4-02y-l-required.xml tests/unit/test_p2_contracts.py tests/unit/test_p2_scheduler.py tests/integration/test_p2_scheduler_recovery.py tests/integration/test_p2_scheduler_api.py tests/unit/test_p3_ocr_contract.py tests/unit/test_p3_ocr_geometry.py tests/integration/test_p3_ocr_cli.py tests/integration/test_p3_ocr_end_to_end.py tests/integration/test_p3_ocr_lifecycle.py tests/integration/test_p3_ocr_migration.py
uv run --no-sync ruff check .
uv lock --check
git diff --check
```

- JUnit：**138 collected; 119 passed, 0 failed, 0 errors, 19 skipped**。19 项因 P0-04 model root 未安装 OCR 权重而跳过；真实 pinned weights 已通过上述本机 smoke/对照/build fixture 执行。
- 第一次沙箱 pytest 因项目内 `.pytest-tmp` 创建时报 Windows `WinError 5`；未改 ACL。经正常权限路径重跑，JUnit 全套退出码 0。
- Ruff、lock check、diff check 均退出码 0。无依赖或 lockfile 改动。

## 边界与后续

- 设备/输出/性能证据只代表当前 Windows + RTX 4060 Laptop + ORT 1.30.0；不代表 GTX 1660、FP16、TensorRT 或其它环境。
- 新 `structured_model` 是同应用版本内读写端同步升级的加性契约；旧版闭集解析器不接受此 kind。
- 冻结契约与产品验收已完成；仍需独立 DS-H 按 [审查卡](../tasks/P4-02Y-L-review.md)完整检查受影响 diff、调用链和证据。审查结论通过后再判断是否需要 Sol focused review；审查不得由本实现者自审。
