# P4-02Y-L-IDENTITY-A — OCR 版本化身份与 GPU 优先、CPU 兜底实现

- 日期：2026-10-09。
- 基线 HEAD：`14567c2afd5755ff5e151f95576f6454905f6dcc`；index 起始无暂存改动。
- 当前状态：**ENGINEERING REVIEW PASS / F1–F7 CLOSED / SOL_NOT_REQUIRED / PINNED-WEIGHT VALIDATION PENDING**。原 required 与修复验证的历史证据如下；独立闭环见[复核报告文末](P4-02Y-L-review-dsh.md)，独立定向 60/60、0 failures/errors/skips，Ruff/diff-check 通过。结论限于工程与合成范围，真实 weights/session/GPU 仍 PENDING。
- 本实现由当前 Codex 会话完成；当前会话不是 DS-H。未提交或推送。
- 依据：[任务卡](../tasks/P4-02Y-L-IDENTITY-A.md)、[ADR-0018](../decisions/ADR-0018-ocr-artifact-identity-and-fallback.md)、[Sol 身份裁决](P4-02Y-L-artifact-identity-sol.md)及此前[OCR 主审报告](P4-02Y-L-review-dsh.md)。

## 实现

- 将新写身份拆分为 `ocr-text-cuda-fp32@2` 和 `ocr-text-cpu-fp32@2`，分别使用冻结的 CUDA/CPU spec revision、单设备 device policy 与独立 `ocr-execution-config.v2` 配置 hash。Golden SHA-256：CPU `b34753ca9d2bd6f2c824d792b9af8debe1ecd788258e7bafbb2c56b76fd478f3`；CUDA `6936ffae2b03479335a09ed69bb18d5d821ca4d2a1029c27d56f007c66aef5c6`。
- 默认 `auto` 只创建 CUDA 身份任务；显式 `cpu`、`gpu` 使用各自身份。只有 GPU 资格阻塞、设备不可用或 OCR worker 明确报告 CUDA 初始化/推理故障，才可创建 CPU 分支。worker 不改设备。权重/输入/lease/provider/产物错误及未知异常不触发兜底。
- GPU 资格阻塞或可恢复失败与 CPU 幂等入队在一个 SQLite 短事务中完成；被取消的 GPU 分支保留故障码，后续恢复不会重新竞争。CPU 能力/资格独立记录。自动兜底分支通过 jobs 持久化，重启继续已有 CPU job。
- 跨 profile 排队会串行化；仍有任一 OCR profile 或 legacy `ocr-text@1` 的有效 lease 时拒绝切换。未运行的相反 profile 通过允许的状态迁移取消。legacy pending job 不改身份、不被当前 scheduler 领取；legacy live job 会保护选择。
- 两个新 profile 共用原 scheduler OCR 事务 hook。artifact、唯一 current text、regions、text revision 和 FTS 投影待办在同一事务提交；失败、取消、过期提交与 checksum 冲突不改当前结果。CPU/GPU artifact ID 各含 processor ID，strict checksum 和低置信度原始分数不变。
- 保留 `ocr-text@1` artifact reader、旧 payload 验证、current projection 与历史 checksum。CLI run 摘要、read-only OCR status 和 refresh 进度可显示实际 profile 与已知 CPU fallback 原因；无新设置页面。

## 验证

最终任务卡命令把新增合成文件加入 required 列表：

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.uv-cache'
$env:PYTHONPATH = (Get-Location).Path
uv run --no-sync pytest -q -p no:cacheprovider --basetemp=.pytest-tmp/p4-02y-l-identity-a --junitxml=test-results/p4-02y-l-identity-a.xml tests/unit/test_p2_contracts.py tests/unit/test_p2_scheduler.py tests/unit/test_p2_artifacts.py tests/unit/test_p3_ocr_contract.py tests/unit/test_p3_ocr_geometry.py tests/integration/test_p2_index_submission.py tests/integration/test_p3_ocr_cli.py tests/integration/test_p3_ocr_lifecycle.py tests/integration/test_p3_ocr_end_to_end.py tests/integration/test_p3_ocr_migration.py tests/integration/test_p4_02y_l_identity.py
uv run --no-sync ruff check src/photo_search/ocr/contract.py src/photo_search/ocr/processor.py src/photo_search/ocr/service.py src/photo_search/ocr/engine.py src/photo_search/ocr/cli.py src/photo_search/scheduler/engine.py src/photo_search/scheduler/projection.py src/photo_search/refresh/progress.py src/photo_search/refresh/pipeline.py tests/integration/test_p3_ocr_lifecycle.py tests/integration/test_p4_02y_l_identity.py tests/unit/test_p3_ocr_contract.py
git diff --check
```

- Pytest exit code **0**; JUnit `test-results/p4-02y-l-identity-a.xml`: **152 tests / 133 passed / 0 failed / 0 errors / 19 skipped**. All 19 skips are real-weight OCR integration nodes because pinned PP-OCRv4 weights are absent under the configured P0-04 model root. All **15 new synthetic identity/fallback nodes executed with zero skips**. No synthetic path depends on ignored local fixtures.
- Ruff exit code **0** (`All checks passed!`); `git diff --check` exit code **0**.
- The CPU/CUDA config hash golden values are asserted by `tests/unit/test_p3_ocr_contract.py`. Synthetic cases cover GPU success including empty output, unavailable GPU and both recognized CUDA failure stages, strict ONNX CUDA error allowlisting, one durable CPU fallback across restart, both force directions, same-byte replay, different-byte `ARTIFACT_CONFLICT` with unchanged current/FTS queue, CPU failure/cancel, expired lease, input change, unknown exception, live-lease refusal, low-confidence raw values, legacy artifact identity, read-only status and combined refresh progress.

## 待验证与边界

- The required real-weight nodes remain **PENDING**, not passing: this host lacks pinned OCR weights. No model was downloaded or loaded; no real ONNX session/provider or GPU fault was exercised in this task. The exact ONNX Runtime exception classification therefore still needs the independent review to check against the installed code path and retained evidence.
- No private photo library, user service, full repository suite, 300k run, GPU benchmark, packaging, migration, dependency change, commit, or push was performed.
- Starting worktree already contained parallel OCR document, review, fixture, diagnostic and `.tmp` changes. They were preserved; this report does not claim them as implementation output.

## 交接

新增面独立聚焦审核已按用户要求在当前审核会话完成，未声称 DS-H。下一步仅[五处定向修复](../tasks/P4-02Y-L-IDENTITY-A-fix.md)，修复后复核原发现、新 diff 与直接回归，不另跑整轮 DS-H 或重复 Sol。不标 OCR 总 PASS，真实 weights/session/GPU 保留 PENDING，不启动模型切换审查。

最新交接：F1–F7 已独立关闭，ENGINEERING REVIEW PASS / SOL_NOT_REQUIRED，详见复核报告末段；上面交接与后续 finding 段保留为历史。无需重复审核或自动接续其他任务，后续真实资源验证由用户另行决定。

## 2026-10-09 定向修复记录

执行[五处修复卡](../tasks/P4-02Y-L-IDENTITY-A-fix.md)：

- CPU/GPU 资格探测分别执行并分别记录；CPU 设备故障不会阻断 GPU probe。共同权重或 RapidOCR 安装错误仍直接拒绝。
- auto GPU session 初始化发生 provider mismatch 时记为 `OCR_GPU_INITIALIZATION_FAILED`；显式 GPU 仍无 CPU 兜底。result envelope 的 provider/device 校验未放宽。
- CUDA 异常分类只沿已知 RapidOCR `ONNXRuntimeError` 的 `__cause__` 到具体 ORT runtime exception；InvalidArgument、未知异常及仅含 CUDA 文本的普通异常不符合条件。
- auto 选择会持久标记被取消/失败的 CPU 竞争分支。fallback 在事务内幂等恢复 lease-free 的旧 CPU `CANCELLED`/`FAILED` job；后续手动 CPU/GPU 请求覆盖 request marker，历史 auto GPU failure 不会重新夺回选择。普通 jobs 终态矩阵保持原样；特殊恢复仅由 OCR service 内限定 task/profile/request marker/lease 的 CAS 执行，不修改 job 唯一键。
- queue limit 只按 scheduler 实际执行数计算。额度耗尽时已入队的 CPU fallback 保持可恢复。

验证命令和结果：

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.uv-cache'
$env:PYTHONPATH = ((Get-Location).Path, (Join-Path (Get-Location) 'tests'), (Join-Path (Get-Location) 'tests/integration')) -join ';'
uv run --no-sync pytest -q -p no:cacheprovider --basetemp=.pytest-tmp/p4-02y-l-identity-fix-final4 --junitxml=test-results/p4-02y-l-identity-fix-final4.xml tests/integration/test_p4_02y_l_identity.py tests/unit/test_p2_scheduler.py
uv run --no-sync ruff check src/photo_search/ocr/engine.py src/photo_search/ocr/service.py src/photo_search/jobs/queue.py tests/integration/test_p4_02y_l_identity.py
git diff --check
```

JUnit **53 / 53 passed / 0 failed / 0 errors / 0 skipped**；Ruff 与 diff-check 通过。覆盖旧 CPU 取消/失败恢复、实际 catalog 关闭重开后的幂等接续、手动选择优先、RapidOCR 包装异常、provider mismatch、独立探测、共享缺失权重、current/FTS 保持及 queue limit（含额度耗尽后保留 CPU pending）。真实 pinned weights/session/GPU 仍未执行，OCR 总 PASS 仍 PENDING。产品 diff 与本轮测试由当前实现会话完成；独立聚焦复核尚待执行，未提交或推送。

## 2026-10-09 F6/F7 收口

- 恢复 `jobs.ALLOWED_TRANSITIONS` 中 `FAILED`、`CANCELLED` 的终态定义；通用 `resume_blocked` 额外要求 source state 为 `BLOCKED`。OCR auto fallback 仍能恢复已有 CPU `CANCELLED`/`FAILED` job，但仅通过 OCR service 私有 CAS，匹配 asset task、CPU processor、`requested_device='auto'`、原终态及无 lease 字段。
- `OCR_SESSION_PROVIDER_UNVERIFIED` 不再被通用 `RUNTIME_CAPABILITY_UNAVAILABLE` 覆盖为兜底许可。协调器将具体码持久写回 GPU job，并在后续 run 的能力快照变化时继续拒绝 CPU fallback。新资格证据可由 scheduler 的 blocked recovery 单独验证后恢复 GPU 分支。
- 跟踪用例覆盖普通非 OCR `CANCELLED`/`FAILED` 不可 resume、普通 `BLOCKED` 可 resume、OCR auto 旧 CPU 终态仍可恢复，以及 provider 未验证在能力快照刷新后仍不兜底。
- 回归命令：

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.uv-cache'
$env:PYTHONPATH = ((Get-Location).Path, (Join-Path (Get-Location) 'tests'), (Join-Path (Get-Location) 'tests/integration')) -join ';'
uv run --no-sync pytest -q -p no:cacheprovider --basetemp=.pytest-tmp/p4-02y-l-f67-fix-1 --junitxml=test-results/p4-02y-l-f67-fix-1.xml tests/integration/test_p4_02y_l_identity.py tests/unit/test_p2_scheduler.py tmp/p4-02y-l/identity-focused-review-20261009/test_review_counterexamples.py tmp/p4-02y-l/identity-focused-rereview-20261009/test_rereview_boundaries.py
```

- 最终回归 JUnit `test-results/p4-02y-l-f67-fix-1.xml`：65 tests / 65 passed / 0 failed / 0 errors / 0 skipped；包含 tracked identity/scheduler 节点和旧五条独立反例、三个 F6/F7 反例。受影响 Ruff、`git diff --check` 均通过。此为实现会话证据，F6/F7 独立复核仍待执行；真实 weights/session/GPU 未验证。

后续独立聚焦复核已执行：53 个 tracked 节点＋五个旧反例均通过，F1–F5 关闭；另补三个节点确认 F6 普通终态恢复被扩大、F7 不可验证 provider 被误授权兜底。JUnit `test-results/p4-02y-l-focused-rereview-20261009.xml` 为 **61/58 passed/3 failures/0 errors/0 skips**，Ruff/diff-check 通过；修复仍未 PASS。下一步只收紧 F6/F7，详见复核报告及原修复卡文末，不重做原五条或整轮 DS-H/Sol。未修改产品代码、未加载模型或提交/推送。
