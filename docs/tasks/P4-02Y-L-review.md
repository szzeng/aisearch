# P4-02Y-L — 独立 DS-H 全受影响面工程主审

**状态：READY**

**审查模型：DS-H / DeepSeek V4.1 Flash / 高推理。** 在独立新会话执行，不由 P4-02Y-L 实现者自审；用户在 Codex 中手动切换模型。此为 `ProcessorSpec.device_policy`、provider 资格和公共契约变更，按 V3.1 §2.4 采用全受影响面主审，不适用展示/测量等窄审查。

## 目标与停止点

独立检查冻结契约实现、真实 OCR device/provider、故障边界、CPU/GPU 输出等价、加速与 refresh/build 证据，结论为 `ENGINEERING REVIEW PASS` 或 `ENGINEERING REVIEW FAIL / FIX REQUIRED`。本卡只审查，不接管修复。若发现问题，记录最小反例、受影响文件和修复范围，返回实现修复轮。

实现审查通过后，若仍有 V3.1 §11 或 Sol 冻结报告点名的未决高风险不变量，按策略另建 Sol focused review 阶段；不得在 DS-H 报告中代替 Sol，也不得在该阶段完成前标 P4-02Y-L 总任务 PASS。只在证据命中 V3.1 §2.2 六项命名升级条件时升级 DS-MAX，报告条件编号与事实依据。

## 必读顺序和材料

1. `AGENTS.md` → `docs/development-status.md` → `docs/tasks/P4-02Y-L.md` → `docs/reports/P4-02Y-L-ocr-cuda.md` → 本审查卡。
2. 冻结依据：`docs/reports/P4-02Y-L-sol-contract-freeze.md`、`docs/reports/P4-02Y-L-dsh-contract-arbitration.md`。
3. 核对真实 Git HEAD/status 与相对于 `d256b93` 的 diff；同一工作树可能含 P4-02Y-W 未提交变更。将 L 与 W 改动准确分开，不能把 W 的 `search/service.py`、W 报告/任务卡/测试变化归因给 L。
4. 核对 JUnit `test-results/p4-02y-l-required.xml`（报告 138 项：119 passed、0 failed、0 errors、19 skipped）及本机 `test-results/p4-02y-l-*.json` smoke/对照/build 原始证据。若忽略目录文件不可用，依据实现报告的压缩数据记录该限制，不推断缺失证据通过。
5. 读取 ADR-0003 §2、§4.2、§5、§6、§8 与 `docs/decisions/contracts/p2-contracts.v1.json`；只读任务直接相关实现/测试，不全文重读架构或策略。按需看 V3.1 §2.2、§2.4、§3.4、§11。

## 必审范围

- 契约：`src/photo_search/scheduler/contracts.py`、`docs/decisions/contracts/p2-contracts.v1.json`、`docs/decisions/ADR-0003-p2-processing-runtime-and-search-profile-contract.md`、`tests/unit/test_p2_contracts.py`。确认新增 `structured_model` 有真实模型身份和非空 weights，禁止向量字段/embedding output，`non_model` CPU const 与 GPS/place/taken-time 行为未松动，旧版本兼容说明如实。
- OCR 接入与设备事实：`src/photo_search/ocr/contract.py`、`processor.py`、`engine.py`、`service.py`、`cli.py`。沿 enqueue→qualification→eligibility→dispatch→engine→JobResult→artifact/search projection 检查。逐一确认 det/cls/rec 实际 session 首选 provider、CPU/GPU cache 隔离、GPU 缺资格为 BLOCKED、构造后 provider 不符时结构化失败、真值来自 session 而非请求回显、provider 资格不预写。
- OCR 回归：`tests/unit/test_p3_ocr_contract.py`、`tests/unit/test_p3_ocr_geometry.py`、`tests/integration/test_p3_ocr_lifecycle.py` 及其它当前 P3 OCR integration tests。核验冻结的 payload/config hash、质量阈值、2000 px 长边、slice 参数、batch=1、worker=1、依赖没有变化。
- 夹具与证据生成器：`tools/diagnostics/ocr_smoke_fixture.py`、`tools/diagnostics/ocr_cuda_compare_fixture.py`、`tools/diagnostics/ocr_refresh_build_fixture.py`。确认只操作 synthetic fixture，并且 JSON 记录、返回码/门限与报告数字相符。
- 交接文档：`docs/tasks/P4-02Y-L.md`、实现报告、`docs/development-status.md`。确认状态未提前总 PASS，19 个 skip 原因及 face/place build fixture skip 已正确说明。

## 必审验收不变量

- Sol 冻结的字面量与结构被完整实现：`structured_model`、`CPU_ALLOWED`、`["gpu","cpu"]`、FP32、`rapidocr-ppocrv4-mobile` / `rapidocr-onnxruntime@1.4.4`、manifest 三个 ONNX SHA；payload 不添加 provider/device。
- 既有 `non_model` 契约段未放宽。新 kind 必须封闭拒绝 `space_id`、`dimension`、`metric`、`output_type=embedding` 与 embedding artifact。
- `auto` 仅在实际 GPU sessions 创建和 provider 校验之后取得 GPU 专项资格；显式 CPU 留在 CPU；显式 GPU 无资格时不消耗重试、不落 OCR 文档；已派发 GPU 但 session 只有 CPU 时不可成功。
- 真实 smoke 中 3 个 auto OCR job 与 3 个 explicit CPU job 的 `requested_device/resolved_device/runtime_provider/precision` 均与请求及实际 session 一致；三个 GPU OCR sessions 首选 CUDA。
- CPU/GPU invoice、dense、blank 逐字文本相同，region 数相同，坐标差 ≤1 px，置信度差 ≤0.03；实测 GPU median 至少快 2×，原始 per-image/round total 与 GPU 使用/显存证据一致。
- synthetic refresh/build 完成且 3 个 OCR job 为 GPU/CUDA/FP32；禁止用 Face 或 place 的 fixture skip 代替 OCR 验收。
- P2/OCR JUnit、Ruff、lock check、diff check 与报告一致。19 个 skipped 是无默认 OCR weights 的既有 skip 条件，不得误写为失败或通过执行过的 OCR 测试。

## 审查输出

写入 `docs/reports/P4-02Y-L-review-dsh.md`。记录实际检查文件/调用链、每条不变量结论、核过的命令和证据、差异/遗漏风险、V3.1 §2.2 升级判定，以及 `ENGINEERING REVIEW PASS` 或 `ENGINEERING REVIEW FAIL / FIX REQUIRED`。若 PASS 但需 Sol 聚焦终审，状态写 `ENGINEERING REVIEW PASS / SOL FOCUSED REVIEW PENDING`，停止并只准备压缩交接包；若无 Sol 必选项，依据证据写明 `SOL_NOT_REQUIRED`。FAIL 时仅交接最小反例与准确修复边界。
