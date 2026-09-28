# P4-02W — 容量证据专项审查（独立 DS-H）

**结论：`REVIEW PASS`**（容量证据专项审查通过）。

报告 `PERSONAL TRIAL PERFORMANCE FAIL / PARTIAL` 的 FAIL 判定成立；报告数字与两份 JSON 可独立复算；无「诊断冒充端到端 PASS」；无 `src/**` / `tests/**` / 依赖 / schema 越界；A FAIL 即停符合卡面。附 4 条非阻断发现，其中 **F1 必须在下一张修复卡内修正措辞并补证**。

审查性质：只读。未修改 `src/**`、`tests/**`、schema、依赖、`uv.lock`、任务卡或实现报告。

审查对象（仅此四份，未扩到全仓历史）：`docs/reports/P4-02W-personal-trial-performance.md`、`test-results/p4-02w-warm-text.json`、`test-results/p4-02w-attribution.json`、`docs/tasks/P4-02W.md`、`docs/development-status.md`。仅当出现数字/归因矛盾时才回看 P4-02M 相关段落 —— 本轮命中 1 处（F1），故只读了 P4-02M §7/§8.2/§8.3 及 `search/retrieval.py::vector_channel`、`indexing/store.py` 的直接实现。

---

## 1. 卡面 §9 逐项核对

| 卡面审查项 | 结论 | 依据 |
| --- | --- | --- |
| 30 万夹具复用/重建得当、形状可信 | PASS（元数据级） | 副本 `tmp/p4-02w/warm/data` 与源 `tmp/p402f-阶段0/scale-300000-wave/data` 的 `catalog.db` 字节数（926 244 864）与 `LastWriteTime` 相同；两端 DB 的 schema=16 / assets=300000 / index_entries CONFIRMED=300000 / search_documents=300000 / search_projection_queue=0 / index_outbox=CONFIRMED 300000 / generation（id·space·processor·dim·state）逐项相同 ⇒ 源目录未被写入 |
| 是否走真实产品入口 | PASS | JSON `launcher_lines` 与 `tmp/p4-02w/logs/warm-text-server.log` 均为 `python -m photo_search … --model-device cuda`；日志逐条为真实 HTTP 的 health / session / capabilities / search |
| 是否真实模型 / CUDA | PASS（结构性保证 + 反证；见 F2） | manifest 派生的 `space/processor/dimension/generation` 与 JSON `binding` 逐字相同；`resolve_device("cuda")` 要求 `CUDAExecutionProvider`、缺失即抛错，且会话禁用 CPU EP 回退（`session.disable_cpu_ep_fallback=1`）⇒ 无静默 CPU 回退；本机 `onnxruntime` providers 含 `CUDAExecutionProvider`，本机 `nvidia-smi` 与 JSON `gpu.query` 逐字相同 |
| 样本数与 P95 算法 | PASS | n=30；`ceil(0.95*30)-1 = 28`；独立复算 = 报告值 |
| 是否保留失败 | PASS | 30 样本全 HTTP 200 / 全 `ok`；`failure_count=0`、`failures=[]`；4 个 > 5000 ms 样本原样保留；冷启动 1 次与 smoke 4 次单列、不入统计 |
| D 真实后台建库 / ≥30 真 overlap / 后台推进 | 未执行（符合卡面） | A FAIL 即停 |
| 浏览器图片可见 / timing 可靠 | 未执行 | 同上 |
| JSON 是否可独立复算报告数字 | PASS | 见 §2；`stats` 子命令 `matches=true` |
| 是否把诊断下界冒充端到端 PASS | PASS | 报告 §3 明确标注「诊断证据，非 PASS」；FAIL 判定只用 §2 的真实 HTTP 端到端证据 |
| 是否发生 `src/**` / `tests/**` / 依赖越界 | PASS | `git status` 仅 `docs/development-status.md`（+5/−1）与两处新增（实现报告、`tools/diagnostics/p4_02w_personal_trial.py`）；无 `src/**` / `tests/**` / `pyproject.toml` / `uv.lock` 变更 |
| 提前停止是否符合本卡规则 | PASS | A 场景 P95 超门槛 76.4 ms ⇒ 记 FAIL、停 D/B/C；报告 §4/§5 未执行项与不得追认清单齐全 |

---

## 2. 报告数字的可复算性（独立复算）

场景 A（`test-results/p4-02w-warm-text.json`，n=30）：

| 报表项 | 报告值 | 独立复算 | 结论 |
| --- | --- | --- | --- |
| 样本数 | 30 | 30 | 一致 |
| P95 规则 | `sorted[ceil(0.95*N)-1]` = `sorted[28]` | index 28 | 一致 |
| min | 3893.5 | 3893.5 | 一致 |
| P50 | 4508.0 | 4508.0 | 一致 |
| P95 | 5076.4 | 5076.4 | 一致 |
| max | 5172.4 | 5172.4 | 一致 |
| mean | 4558.8 | 4558.8 | 一致 |
| 失败数 | 0 | 0（全 200 / 全 `ok`） | 一致 |
| >5000 ms 样本 | 4 | 4（5045.1 / 5076.4 / 5054.9 / 5172.4） | 一致 |
| 冷启动（单列） | 4809.8 | 4809.8 | 一致 |
| smoke（不计入） | [3867.7, 3837.2, 3921.8, 3869.7] | 同左 | 一致 |
| 每样本 `vector.engine_blocks` | 60 | 30/30 = 60 | 一致 |
| 每样本 `vector.truncated` / `candidates` | true / 20 | 30/30 | 一致 |
| 每样本 `fts.candidates` | 0 | 30/30 | 一致 |
| 每样本 `authoritative.total` | 300000 | 30/30 | 一致 |
| 每样本 `search_projection.state` / `queued_assets` | SYNCED / 0 | 30/30 | 一致 |
| `fixture_before == fixture_after` | 未变 | True | 一致 |
| `p95_excess_ms` | 76.4 | 5076.4 − 5000 = 76.4 | 一致 |

旁证：`tmp/p4-02w/logs/warm-text-server.log` 计 **35** 次 `POST /api/v1/search`（= 1 冷 + 4 smoke + 30 正式）与 **38** 次 `200 OK`（= 35 + health + session + capabilities），与 JSON 结构一致。

归因（`test-results/p4-02w-attribution.json`，n=5）：6 个分量 `encode_ms / fts_ms / vector_ms / authority_ms / pending_page_ms / full_service_ms` 的 min/p50/p95/max/mean **全部**从 `rounds` 逐字复算一致；报告 §3 表与 JSON `component_stats` 一致；`vector p50 4598.8 / full p50 4921.1 = 93.4% ≈ 93%` 成立；`warm 1 轮丢弃 + 5 轮计时` 与 JSON 只有 5 个 `rounds` 一致。

`docs/development-status.md` 与报告一致（同 P50/P95/max、同 `engine_blocks=60` / `scanned=300000`、同 `4/30 > 5 s`、同归因 p50 串）。

---

## 3. 发现

### F1（非阻断，但必须进下一卡）归因机制句与实际代码路径不一致，且不可从 JSON 复算

报告 §3 与 §6 修复卡把「每块调用」写成 `search_candidates(..., limit=len(ids))`、把瓶颈写成「**整行物化**」。但在 f9b9708 上：

- `search/service.py::default_vector_index` 返回 `LanceVectorIndex`（本轮实测 `has search_candidates_bounded = True`）；
- `search/retrieval.py::vector_channel`（500–528 行）**优先**调用 `search_candidates_bounded`（每块只取 `top_k+1` 行），**仅当**某块边界并列未被证明（`resolved=False`）才回退到 `search_candidates(limit=len(ids))`；
- 这正是 P4-02M §8.2 已合入的优化。

归因 JSON 未记录每块 `resolved` / 实际调用的方法，因此「每块走 `limit=len(ids)` 全行物化」这一断言**既不可从 JSON 复算，也与当前代码不符**（它描述的是 P4-02M §7 之前的基线形态）。

影响：FAIL 判定、主耗时分量（`vector_channel`）、`engine_blocks=60` / `scanned_assets=300000` 都不受影响。下一卡第一目标「**60 次分块引擎往返**」成立；但第二目标「`search_candidates(limit=len(ids))` 的整行物化」应改写为「按 `max_blocks` 分块的引擎往返 + keyset SQL 扫描」，并要求归因证据记录每块 `resolved` / 实际路径。

### F2（非阻断）CUDA/模型证据是结构性推断，JSON 未直接记录 provider

两份 JSON 仅有 `device:"cuda"`、`gpu.query` 与 `semantic channel READY`，没有解析出的 `provider`（`CUDAExecutionProvider`）与 `runtime_capabilities`。`/api/v1/capabilities` 本就返回 `runtime` 字段，而工具只保留了 `search`。建议下一卡在 JSON 中落 `resolve_device().as_dict()` 或 `runtime.providers`，使「真实 CUDA」由证据自证而非仅靠「不失败」推断。

### F3（非阻断）同名 `truncated` 两处语义不同

归因 JSON `vector_truncated=false`（= 是否因 `max_blocks` 截断），HTTP 信封 `channels.vector.truncated=true`（= `candidates >= top_k`）。二者各自都对，但同名易被误读为一处矛盾；建议在报告/JSON 注明口径。

### F4（非阻断，方法学）n=5 的「P95」等于 max

归因 `ceil(0.95*5)-1 = 4` ⇒ 其「P95」就是最大值。报告已标 n=5 且只用于 P50 归因，可接受；下游不得把它当 ≥30 样本 P95 引用。

---

## 4. 不得追认

本次 FAIL 只授予「30 万暖机普通文字搜索 P95 ≤ 5 s 未达」这一条。D（后台建库 + 文字搜索）、B（浏览器首屏）、C（CUDA 上传搜图）、§6（来源离线冒烟）**未执行**；§12.2 后台档、真实模型端到端上传、GTX 1660、打包、真实图库、质量集均未测，不因本审通过而改变。

本审查亦未复核该夹具 304 583 文件 / 4.9 GB 的**逐字节**一致（按报告所记 `robocopy` 结论采信）；本轮独立复核到 `catalog.db` 字节数 + `LastWriteTime` 一致，以及源/副本 DB 内 7 项形状事实一致。

---

## 5. 复算命令与退出码

```
# 报告数字（P50/P95/max/mean/失败数）
.venv\Scripts\python.exe -c "<读 JSON 就近秩复算>"            # 全部与报告一致
.venv\Scripts\python.exe tools\diagnostics\p4_02w_personal_trial.py stats --input test-results\p4-02w-warm-text.json
                                                              # matches=true; verdict FAIL（exit 0）

# 归因分量复算
.venv\Scripts\python.exe -c "<读 rounds 复算 component_stats>" # 6/6 分量逐字一致

# 入口 / 真实 HTTP / CUDA
Get-Content tmp\p4-02w\logs\warm-text-server.log              # 35×POST, 38×200 OK
.venv\Scripts\python.exe -c "import onnxruntime; from photo_search.runtime.device import resolve_device; print(onnxruntime.get_available_providers()); print(resolve_device('cuda').as_dict())"
                                                              # CUDAExecutionProvider 在列; provider=CUDAExecutionProvider, cpu_fallback_disabled=True
nvidia-smi --query-gpu=name,driver_version,memory.total,compute_cap --format=csv,noheader
                                                              # 与 JSON gpu.query 逐字相同

# 夹具形状（只读）
.venv\Scripts\python.exe -c "<sqlite file:...?mode=ro 读 schema/assets/entries/docs/queue/outbox/generation>"

# 范围与静态门
git status --porcelain=v1 ; git diff --stat                  # 仅 development-status + 两个新增文件
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path; uv run --no-sync ruff check .   # All checks passed!（exit 0）
git diff --check                                             # exit 0

# 归因路径反证
.venv\Scripts\python.exe -c "<default_vector_index(catalog) 的类型与 hasattr(search_candidates_bounded)>"
                                                              # LanceVectorIndex, has bounded=True
```

（默认沙箱下 `uv` 会因 `C:\Users\28288\AppData\Local\uv\cache` 拒绝访问而失败，属环境事实；改用项目内 `.uv-cache` 后 `ruff` exit 0，未改 ACL。）

---

## 6. 判定

- **`REVIEW PASS`**：容量证据专项审查通过。无「数字不可复算」，无「诊断冒充 PASS」，无范围越界，提前停止合规。
- **F1 必须进入下一张修复卡**（修正 §3/§6 的机制措辞 + 归因证据补记每块路径）；F2–F4 为非阻断加固项。
- 无 ADR / 协议未决项 ⇒ **`SOL_NOT_REQUIRED`**。
- 下一阶段仍为卡面 §6 的最小修复卡（精确向量通道单点优化）；本审查不启动任何后置任务，不自动进入 D/B/C，未 push。
