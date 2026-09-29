# P4-02Y-K — Face 检测 CUDA/CPU 混合执行

## 结论

**IMPLEMENTED / DS-H 窄受影响面复核待执行（2026-09-29）。** 本卡只改变 Face 的 ONNX session
配置与其运行时证据：固定 `det_10g` 在 CUDA 请求下允许显式的 CUDA+CPU EP stack；`w600k_r50`
仍保留 strict CUDA gate。没有改变 Chinese-CLIP、OCR、全局 device policy、权重、阈值、向量空间、聚类、
调度或数据库。

## 基线与改动

* 基线：`master @ 146cd3e`，开工前工作树干净。
* 改动：`src/photo_search/face/engine.py`、`tests/unit/test_p3_face_geometry.py`。
* `FaceEngine` 在 CUDA 时给 detection 传入
  `[CUDAExecutionProvider, CPUExecutionProvider]`，且不设置 `session.disable_cpu_ep_fallback`；
  session 的实际 provider stack 必须同时含这两个 provider。
* recognition 仍只请求 CUDA 并设置 `session.disable_cpu_ep_fallback=1`。ONNX Runtime 仍可能在
  `get_providers()` 的库存中列出 CPU，因此 probe 额外记录
  `recognition_cpu_fallback_disabled=true`，这个 strict gate 才是 recognition 的权威事实。
* probe 的成功记录新增 `execution.{detection_providers,recognition_providers,mixed_cuda_detection,
  recognition_cpu_fallback_disabled}`，不把 GPU Face 记成纯 CUDA。

## 验证

所有 smoke 只使用工具自带的公开 fixture，工作目录为项目内 `tmp/p4-02y-k/`；它从
`D:\SZ\project\PhotoSearchData` 读取固定权重并复制到临时数据根，未读取、扫描或修改真实照片库。

| 检查 | 结果 |
|---|---|
| `test_p3_face_geometry.py` | **18/0/0/0**，JUnit `test-results/p4-02y-k-unit.xml` |
| CPU Face smoke | `status=ok`，12 faces、8 persons、1 same-image conflict，33 295 ms |
| mixed GPU Face smoke | `status=ok`，12 faces、8 persons、1 same-image conflict，9 683 ms；`qualified_devices=[cpu,gpu]` |
| GPU probe | usable；detector `[CUDAExecutionProvider, CPUExecutionProvider]`；`mixed_cuda_detection=true`；recognition strict gate=true |
| 静态检查 | `ruff check .`、`uv lock --check`、`git diff --check` exit 0 |

证据：`test-results/p4-02y-k-face-smoke-cpu.json`、
`test-results/p4-02y-k-face-smoke-mixed-gpu.json`、
`test-results/p4-02y-k-face-mixed-probe.json`。

执行命令：`.venv\\Scripts\\python.exe -m pytest -q tests\\unit\\test_p3_face_geometry.py
--junitxml=test-results\\p4-02y-k-unit.xml --basetemp=.pytest-tmp\\p4-02y-k-unit`（正常 Windows
权限路径；沙箱首次建临时目录报既知 `WinError 5`，非产品失败）、`.venv\\Scripts\\python.exe -m ruff
check .`、`UV_CACHE_DIR=.uv-cache uv lock --check`、`git diff --check`。两次 smoke 均设置
`PHOTO_SEARCH_DATA_ROOT=D:\\SZ\\project\\PhotoSearchData`，并传入各自的项目内 `--work-dir`。

CPU 与 mixed GPU 的输出在任务要求的业务结果上相同（faces、persons、same-image conflict、`problems=[]`）；
本卡只记录总墙钟来排除明显反向退化，未设性能门槛，也未作逐节点 profiling 归因。

## 未完成与下一步

任务卡要求独立 **DS-H 窄受影响面复核**。该复核应覆盖两个改动文件、Face engine→cache→probe→service
调用链、严格 recognition gate、mixed 状态证据和上述 JUnit/两次 smoke；若发现全局 device policy、
调度语义或 ADR-0001 必须改变，应停止并转 C2 / GPT-5.6 Sol / high。复核前本卡不是 PASS，且不生成下一任务卡。

### F1 修复轮（2026-09-29）

独立复核发现的唯一失败项是 GPU 可用分支把 `catalog_scan` 的 `resolved_device=NULL` 混入 Face
job 断言。修复仅在 `tests/integration/test_p3_face_lifecycle.py` 为该查询加
`WHERE task_type = ?`（`face_detection`）；CPU 兄弟用例原本已有同一过滤。以
`PHOTO_SEARCH_DATA_ROOT=D:\\SZ\\project\\PhotoSearchData` 重跑完整 lifecycle 文件得到 **16/0/0/0**，
JUnit 为 `test-results/p4-02y-k-face-lifecycle-fix.xml`，`ruff check .`、`uv lock --check`、
`git diff --check` 仍 exit 0。产品代码、契约、全局 device policy、ADR 与真实图库均未改动。

这是对审核 F1 的实现修复与回归证据，仍待独立 DS-H 窄受影响面复审确认后才可将本卡标为 PASS。
