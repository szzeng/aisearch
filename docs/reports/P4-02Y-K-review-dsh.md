# P4-02Y-K 独立 DS-H 窄受影响面复核 — Face mixed CUDA/CPU EP

**复审结论（F1 已关闭）：PASS。** 修复方式与本复核建议一致——`test_an_explicit_gpu_request_never_falls_back_to_cpu`
的 GPU 成功分支断言改为按 Face task type 过滤；产品代码零变动。本复核独立复跑受影响面
`tests\unit\test_p3_face_geometry.py` + `tests\integration\test_p3_face_lifecycle.py` = **34/0/0/0**
（JUnit `test-results/p4-02y-k-review-rerun.xml`），F1 不再复现。其余 6 条边界与 3 条通过条件的初轮
独立证据仍然有效（产品侧证据未变，因为这次只改了测试文件）。

**初轮结论（保留记录）：NEED FIX（1 项，测试侧）。** 任务卡 6 条边界检查与 3 条通过条件
（修改范围符合任务卡、Face smoke PASS、无新增全局 fallback）在独立复现下全部成立；但同一受影响面内的
`tests/integration/test_p3_face_lifecycle.py::test_an_explicit_gpu_request_never_falls_back_to_cpu`
在本次改动后由休眠分支变为可达并稳定失败，而任务卡与报告的验证集（定点单测 + Face smoke）没有覆盖它。
按“未通过独立复核不得 PASS”的既有规则，初轮停在 NEED FIX。

## 独立复现方式与结果

全部证据在本次复核中重新生成，未沿用实现者结论。

| 复核项 | 证据 | 结果 |
|---|---|---|
| 定点单测 | `pytest -q tests\unit\test_p3_face_geometry.py` → `test-results/p4-02y-k-review-unit.xml` | **18/0/0/0**（沙箱内 `--basetemp` 报既知 `WinError 5`，走正常权限路径重跑通过） |
| ORT provider 事实 | `tmp/p4-02y-k-review/ort_provider_probe.py`（真实固定权重；先走产品 `preload_runtime_dlls()`） | 见下表 |
| Face smoke（CPU） | `tools/diagnostics/face_smoke_fixture.py --device cpu` → `p4-02y-k-review-smoke-cpu.json` | `status=ok` |
| Face smoke（mixed GPU） | 同上 `--device gpu` → `p4-02y-k-review-smoke-gpu.json` | `status=ok`、`qualified_devices=[cpu,gpu]` |
| 业务结果等价 | `tmp/p4-02y-k-review/compare_smoke.py` 与实现者两份 artifact 逐字段比对 | 12 脸 / 8 组 / 1 同图冲突 / 逐脸质量 / 人物成员 **完全一致** |
| 受影响面集成守门 | `pytest -q tests\integration\test_p3_face_lifecycle.py` → `p4-02y-k-review-face-lifecycle.xml` | **15/16，1 FAIL（F1）** |
| 静默降级注入 | `ort_provider_probe.py::silent_cuda_loss()`：session 工厂丢弃 CUDA EP 后构造 `FaceEngine(device="gpu")` | `FACE_DEVICE_UNAVAILABLE`，`role=detection, expected=CUDA, actual=[CPU]` |

### ORT session 事实（真实权重、CUDA DLL 已 preload）

| session | 请求 | 结果 |
|---|---|---|
| `det_10g` | `[CUDA]` + `disable_cpu_ep_fallback=1` | **打不开**：`graph nodes ... assigned to the default CPU EP, but fallback ... disabled` —— 改动前提成立 |
| `det_10g` | `[CUDA, CPU]`，不设 strict gate | 打开，`get_providers=[CUDA, CPU]` —— 与实现一致 |
| `det_10g` | `[CUDA, CPU]` + strict gate | **`INVALID_ARGUMENT`**：`explicitly added the CPU EP ... but also disabled fallback` —— 证明检测侧必须 `disable_cpu_fallback=False`，实现确实如此 |
| `w600k_r50` | `[CUDA]` + strict gate | 打开成功（无节点落到默认 CPU EP），`get_providers` 仍列出 CPU |
| `w600k_r50` | `[CUDA, CPU]` | 打开，`get_providers=[CUDA, CPU]` |

`get_providers()` 对本 runtime 的 CUDA session 一律列上 CPU，strict gate 与不设 gate 表现相同，因此
“recognition 是否 strict CUDA”不能由 provider 清单判定；该清单在改动前后都是同一事实，未发生回归。

## 任务卡边界检查

| 检查项 | 结论 | 依据 |
|---|---|---|
| 仅 `det_10g` 使用 CUDA + CPU mixed EP | ✅ | `face/engine.py` 的混合 provider 只出现在 detection 分支；真实 session `get_providers=[CUDA, CPU]` |
| `w600k_r50` 仍 CUDA only | ✅ | recognition 只请求 `[CUDA]` 且保留 `session.disable_cpu_ep_fallback=1`（与改动前同）；strict session 能打开即说明无节点落到 CPU |
| Chinese-CLIP 未修改 | ✅ | `git status/diff` 仅 Face engine + 定点单测 + docs |
| OCR 未修改 | ✅ | 同上 |
| 全局 device policy / ADR 未修改 | ✅ | `runtime/device.py`、`docs/decisions/ADR-0001-gpu-required.md`、两份架构/策略文档零改动 |
| Face 输出、阈值、向量空间未变化 | ✅ | 无阈值/几何/聚类改动；两次 smoke 的 `space_id`、`model_fingerprint`、`config_hash`、12 脸/8 组/1 冲突、逐脸质量、人物成员与实现者 artifact 完全一致 |

CPU 与 mixed 的 `degraded_jobs`（6 / 0）与 `qualified_devices`（`[cpu]` / `[cpu,gpu]`）差异是显式设备口径
差异，改动前后语义一致，也与实现者 artifact 一致。

## F1（阻断项，测试侧）

* 文件：`tests/integration/test_p3_face_lifecycle.py:422`
* 复现：`$env:PHOTO_SEARCH_DATA_ROOT='D:\SZ\project\PhotoSearchData'; pytest -q tests\integration\test_p3_face_lifecycle.py`
  （或用 `tmp/p4-02y-k-review/repro_gpu_jobs.py` 直接打印 jobs 行）
* 实际：`assert {'None', 'gpu'} == {'gpu'}`；多出的那一行是
  `job_id=1, task_type=catalog_scan, requested_device=auto, resolved_device=NULL`。
* 根因：该断言用未过滤的 `SELECT resolved_device FROM jobs`。`catalog_scan` 是 catalog 自有任务
  （`scheduler/engine.py` 明确其 claim/heartbeat 独立），不解析设备，`resolved_device IS NULL` 属既有设计；
  同一文件的 CPU 兄弟用例 `test_the_cpu_run_is_reported_as_a_degradation` 就带 `WHERE task_type = ?`
  且通过。
* 为何现在才失败：改动前 `det_10g` strict CUDA 打不开（本复核已独立复现），GPU Face 不可用，该用例走
  `else` 分支（断言 BLOCKED）通过；本卡让 GPU Face 首次可用，才第一次进入 `if` 分支。
* 定性：**不是产品回归**（产品行为正确，scan 作业本不属于 device-resolved 作业），是受影响面内既有测试
  缺陷被本卡的新行为首次触发；但受影响面未全绿，不能判 PASS。
* 最小修复（测试侧，不碰产品语义）：把该断言过滤到 Face 任务类型（`WHERE task_type = ?` +
  `contract.FACE_TASK_TYPE`），与 CPU 兄弟用例一致；随后重跑 Face lifecycle 套件取绿、把 JUnit 记入
  任务卡/报告，再复核。不需要改产品、provider 策略、契约或 ADR。

## 非阻断备注

* **N1（文档一致性，非阻断）**：`runtime/device.py` 仍在陈述 “CUDA sessions therefore disable the CPU
  execution provider fallback”，而 Face detector 现为显式记录的例外。因为 ADR-0001/device.py 零改动、
  混合 session 仍拒绝整图 CPU 降级且仍以 CUDA 起始，全局语义未变，不触发 C2/Sol；若希望 ADR 字面穷尽，
  可在 ADR-0001 补一行 Face detector 例外说明。
* **N2（GPU 证据口径，非阻断）**：本卡无法使用“禁用 CPU fallback 跑一次”这一类证据，只能记录实际
  provider 与 mixed 状态（+ 9 683 ms vs 33 295 ms 墙钟）。没有节点级 fallback 归因，任务卡已预先声明
  “不做逐节点 profiling”，故不阻断；若要对齐 V3.1 的完整口径，可用 ORT profiler 汇总 node→provider
  （仓库已有此做法的既有用例）。

## 未验证项

未在 PyInstaller 打包产物内复验 mixed detector；未做逐节点 provider 归因；未跑全量测试套件（本次只跑定点
单测、Face lifecycle 集成文件、CPU 与 mixed GPU 两次真实 Face smoke，全部使用项目内临时工作目录与公共
fixture，未读改真实图库）。

## 复审记录（F1 关闭）

* 修复 diff：`tests/integration/test_p3_face_lifecycle.py:419-427` 的断言改为
  `SELECT resolved_device FROM jobs WHERE task_type = ?` + `contract.FACE_TASK_TYPE`（= `image_face`），
  与 CPU 兄弟用例口径一致；`src/**` 相对初轮复核**逐字未变**（`git diff` 中 `face/engine.py` 的 hunk
  与初轮完全相同），因此初轮的产品侧证据（两次真实 Face smoke、ORT provider 事实、静默降级注入）
  无需重做。
* 独立复跑：`$env:PHOTO_SEARCH_DATA_ROOT='D:\SZ\project\PhotoSearchData'; pytest -q
  tests\unit\test_p3_face_geometry.py tests\integration\test_p3_face_lifecycle.py`
  → **34/0/0/0**（18 定点单测 + 16 lifecycle），JUnit `test-results/p4-02y-k-review-rerun.xml`。
  实现者自留的 `test-results/p4-02y-k-face-lifecycle-fix.xml` 亦为 16/0/0/0，与本复核一致。
* 文档口径小瑕疵（非阻断，建议顺手改）：任务卡把过滤值写成 `task_type=face_detection`，实际是
  `contract.FACE_TASK_TYPE = "image_face"`。
* 结论：F1 关闭，**P4-02Y-K 窄受影响面复核 = PASS**。未触发 DS-H 全受影响面复审或 C2/Sol 升级；
  本卡可收口，未验证项仍为初轮所列（打包产物内 mixed detector、逐节点 provider 归因、全量套件）。

## 下一步

1. 本卡可标 PASS/收口；建议把任务卡里的 `face_detection` 改成 `image_face`（或直接写
   `contract.FACE_TASK_TYPE`）。
2. 复核用临时件：`test-results/p4-02y-k-review-*`（JUnit + 两次 smoke 输出）与
   `tmp/p4-02y-k-review/`（三个复核脚本；F1 复现时复制的权重副本已删除）。

## F1 修复轮交接（待独立复审）

实现侧仅把该 GPU 成功分支的 jobs 查询过滤为 `task_type=face_detection`，未改任何产品文件。
修复后以相同的真实固定权重和 `PHOTO_SEARCH_DATA_ROOT` 重跑
`tests/integration/test_p3_face_lifecycle.py`，结果 **16/0/0/0**，JUnit
`test-results/p4-02y-k-face-lifecycle-fix.xml`；`ruff check .`、`uv lock --check` 与
`git diff --check` exit 0。应由独立 DS-H 只复核 F1、该新增 diff 与直接 lifecycle 回归；若确认查询已只
覆盖 Face task 且无产品面扩展，即可关闭本报告的 NEED FIX，不需要 Sol/C2。
