# P4-02W — 当前版本 30 万本机个人试用性能验收报告

**结论：`PERSONAL TRIAL PERFORMANCE FAIL / PARTIAL`**

性质：纯测量 / 验收 / 决策卡，产品代码零改动。执行顺序 A → D → B → C（用户主动裁决覆盖卡面「A FAIL 后立即停止」，以便一次取得完整 §12.2 现状）。

- 执行日期：2026-09-28
- 真实 Git HEAD：`f9b9708`（`master`）；产品代码基线 P4-05 收口 `97cee74`
- 模型 / 设备：真实中文 CLIP `vit-b-16`，CUDA

## 0. 判定总览

| 场景 | 结论 | P50 (ms) | 样本 P95 (ms) | max (ms) | 失败 | 门槛 |
| --- | --- | --- | --- | --- | --- | --- |
| A 30 万暖机普通文字搜索 | **FAIL** | 4508.0 | **5076.4** | 5172.4 | 0 | ≤ 5000（超 76.4） |
| D 后台建库 + 文字搜索 | **FAIL** | 6320.2 | **8144.9** | 8618.8 | 0 | ≤ 8000（超 144.9）；且真 overlap 仅 **26/30** |
| B 浏览器第一屏 | **BROWSER_TIMING BLOCKED** | — | — | — | — | ≤ 6000（无法可靠计时） |
| C 默认 CUDA 上传搜图 | **PASS** | 4526.2 | **4658.7** | 5035.4 | 0 | ≤ 5000 |
| §6 来源离线最小冒烟 | **PASS**（含说明） | — | — | — | — | 无明显阻断 |

A、D 未达标 ⇒ 记 **`PERSONAL TRIAL PERFORMANCE FAIL / PARTIAL`**。C 通过不足以改变总体结论；B 因无可靠计时记 `BLOCKED`。

---

## 1. 环境、入口、夹具（实测）

| 项 | 实测值 |
| --- | --- |
| Python | 3.12.10 |
| SQLite | 3.49.1 |
| GPU | NVIDIA GeForce RTX 4060 Laptop GPU |
| 驱动 / CUDA UMD | 616.92 / 13.4 |
| VRAM | 8188 MiB（compute 8.9） |
| 模型目录 | `D:\SZ\project\PhotoSearchData\models\chinese_clip\vit-b-16` |
| manifest | `tools/prepare_models/manifests/chinese-clip-vit-b-16.json` |
| API 启动入口 | `python -m photo_search --data-root … --model-dir … --model-manifest … --model-device cuda` |

启动日志：`photo-search: semantic channel READY (model_dir=…\chinese_clip\vit-b-16, manifest=…chinese-clip-vit-b-16.json)`。

### 1.1 30 万夹具与隔离副本（遵守「母版只读、勿逐样本复制」）

- 母版：`tmp/p402f-阶段0/scale-300000-wave/data`（**只读，未修改**）。
- 稳定工作目录（A/B/C 复用）：`tmp/p4-02w/warm/data`。
- D 一次性副本：`tmp/p4-02w/bg/data`。
- 形状：schema **16**；`assets=300000`、`file_locations=300000`、`index_entries` CONFIRMED **300000**；generation `image-gen-5bcba729dfda4897a94974b7`、`state=READY`、`dimension=512`、`space_id=chinese-clip-vit-b-16:c8ac42d3907c57b0`、`processor_id=chinese-clip-vit-b-16@36e679e65c2a`（**与真实 manifest 派生值逐字相同**）；`search_documents=300000`、`search_projection_queue=0`、`active_profile=NULL`。副本 `catalog.db` 与母版同为 **926 244 864** 字节。

---

## 2. 场景 A — 30 万暖机普通文字搜索：**FAIL**

真实链路：文本 → 真实中文 CLIP 文本塔（CUDA）→ 真实 HTTP `POST /api/v1/search` → 检索 → 融合 → metadata → response。固定查询 `西湖 sunset 全家福`，`top_k=20`，显式绑定 `space_id/image_generation_id/dimension`（夹具无激活 profile）。

- 冷启动首搜（单列，不混入）：**4809.8 ms**；smoke 4 次 `[3867.7, 3837.2, 3921.8, 3869.7]`。
- 正式样本 n=30，全 200，失败 0。

| 指标 | 值 (ms) |
| --- | --- |
| P50 | **4508.0** |
| 样本 P95 `sorted[ceil(0.95*30)-1]=sorted[28]` | **5076.4** |
| max | 5172.4 |

逐样本：`3944.1 3893.5 4875.5 4910.3 4718.6 5045.1 4881.4 4950.5 5076.4 5054.9 5172.4 4663.3 4589.8 4463.0 4210.6 4765.3 4295.6 4714.3 4508.0 4402.0 4278.3 4246.1 4324.6 4308.1 4281.1 4409.1 4532.3 4522.2 4314.5 4412.8`（4/30 > 5000）。

HTTP 信封可观测：每样本 `vector.engaged=true, candidates=20, truncated=true, engine_blocks=60`；`fts.candidates=0`；`search_projection.state=SYNCED`。分量耗时在信封中 `NOT OBSERVABLE`（见 §6）。

## 3. 场景 D — 后台建库 + 普通文字搜索：**FAIL**

真实后台建库：对稳定副本一次性扫描 12000 个合法 backlog 文件（→ 新资产 **10632**，其余为同内容去重），再以真实产品维护入口 `python -m photo_search.indexing.cli build`（真实图像塔、真实 outbox/claim/drain/index apply）消费该 backlog，与 30 个前台文字样本并发。**未暂停后台、未用纯文字投影或轻量重投影冒充。**

后台真实推进（构建进程返回）：`targets=10632`、`enqueue.queued=10632`、`run.steps=10632`、`drain.applied=10632`、`batches=54`、`confirmed_delta=10632`、`write_rejections=0`；收尾 `index_entries CONFIRMED=310632`、`search_documents=310632`、`search_projection_queue=0`。

- 正式样本 n=30，全 200，**失败 0**；后台在前 26 个样本期间持续前进（`background_overlap=true`），第 27–30 样本时后台已结束（backlog 被消费完）⇒ **真 overlap 26/30（< 卡面 ≥30）**。

| 指标 | 值 (ms) |
| --- | --- |
| P50 | **6320.2** |
| 样本 P95 | **8144.9** |
| max | 8618.8 |
| 仅 26 个真 overlap 样本的 P95 | **8144.9**（同样超 8000） |

逐样本（idx/http_ms/overlap/`search_projection.state`）：0/6743.1/是/LAGGING，1/5743.1/是/LAGGING，…，7/5319.3/是/LAGGING，8/5481.2/是/SYNCED，…，25/8618.8/是/SYNCED，26/7393.2/**否**/SYNCED，27/7744.6/**否**，28/7212.2/**否**，29/7181.8/**否**。前 8 个样本投影追赶以 `LAGGING` 返回（P4-02V 的有界降级路径），**0 次内部争用拒绝**。

判定：**P95 8144.9 > 8000 且真 overlap 26 < 30 ⇒ FAIL**（两个条件都不满足；即使只统计 26 个真 overlap 样本，P95 仍为 8144.9）。overlap 不足属 backlog 定容偏小的方法学事实，非产品缺陷。

## 4. 场景 B — 浏览器第一屏：**BROWSER_TIMING BLOCKED**

本 build 无「记录式真实浏览器」计时能力，卡面又禁止引入 Playwright/Selenium 或把本卡扩成浏览器自动化开发，故不做形式 P95：**`BROWSER_TIMING BLOCKED`**，缺的最小能力 = 不新增自动化依赖即可在真实前端给「首张图可见」计时的记录式浏览器 harness。

同环境真实 HTTP 层诊断（**非**浏览器首屏时间，仅参考）：搜索 P50 4095.5 / P95 4201.4 ms；首张缩略图约 2.7–3.5 ms；首屏 20 张缩略图顺序取完（搜索+缩略图）P50 4135.6 / P95 4243.3 ms；缩略图全部 200。真实浏览器会并行取图，故这是保守的 HTTP 层代理。

## 5. 场景 C — 默认 CUDA 上传图片搜索：**PASS**

真实链路：`上传 → 真实 HTTP POST /api/v1/search/image → 解码 → 真实图像塔（CUDA）→ 检索 → response`。固定受支持图片集（6 张库内 JPEG）。冷首传 8931.1 ms（含模型加载，单列）。

- 正式样本 n=30，全 200，失败 0；`image_query` 含 `format=JPEG, width=320, height=240`；`vector.engaged=true, engine_blocks=60`。

| 指标 | 值 (ms) |
| --- | --- |
| P50 | 4526.2 |
| 样本 P95 | **4658.7** |
| max | 5035.4 |

**P95 4658.7 ≤ 5000 ⇒ PASS。**

## 6. 归因（诊断证据，非 PASS）

同 30 万副本、同真实文本塔（CUDA），进程内分层计时（`test-results/p4-02w-attribution.json`，warm 1 轮后 5 轮）：

| 分量 | P50 (ms) | P95 (ms) |
| --- | --- | --- |
| query encode（真实文本塔） | 72.9 | 88.0 |
| fts_channel | 1.5 | 3.3 |
| **vector_channel** | **4598.8** | **4799.7** |
| authoritative_page | 27.6 | 30.3 |
| pending_projection_page | 273.7 | 297.6 |
| full service | 4921.1 | 4999.9 |

**主因 = 精确向量通道（`vector_channel`），≈ full service 的 93%**：30 万 CONFIRMED 权威候选按 `SCOPE_BLOCK_SIZE=5000` 做 keyset 分块，共 **60 个 block**；每块**优先**调用 `LanceVectorIndex.search_candidates_bounded`（只取 `top_k+1` 行——f9b9708 已合入 P4-02M 的该优化），**仅当**某块边界并列组无法证明（`resolved=False`）才回退到穷举 `search_candidates(limit=len(ids))`。因此当前版本的成本是 **60 次分块引擎往返 + keyset SQL 扫描 30 万候选 + 每块 top-k 合并**，**不是**「整行物化」（后者是 P4-02M §7 之前的基线形态；该措辞已按独立 DS-H 审查 F1 更正）。该分量同样是 D 的主因（D 相对 A 的额外 ~3 s 主要是后台写争用 + 前台投影追赶，前 8 个样本以 `LAGGING` 返回）。

### 6.1 口径与证据补记（回应独立 DS-H 审查 F1–F4）

- **F1（已更正）**：机制句原写成「每块 `search_candidates(limit=len(ids))` 整行物化」，与 f9b9708 代码不符——已按真实路径改写为 bounded 优先、并列未证明才回退（见上）。
- **F2（已补证）**：`test-results/p4-02w-runtime-provider.json` 记录 `onnxruntime` providers 含 `CUDAExecutionProvider`，`resolve_device("cuda")` = provider `CUDAExecutionProvider`、`cpu_fallback_disabled=true`、`precision=FP32` ⇒ 「真实 CUDA」由证据自证。
- **F3（口径）**：同名 `truncated` 两处语义不同——归因 JSON `vector_truncated` 指「是否因 `max_blocks` 截断」，HTTP 信封 `channels.vector.truncated` 指「`candidates >= top_k`」；二者各自成立，非矛盾。
- **F4（口径）**：归因 n=5 的「P95」= `sorted[ceil(0.95*5)-1]=sorted[4]` = max，仅用于 P50 归因；不得当作 ≥30 样本 P95 引用。

## 7. §6 来源离线最小冒烟：**PASS**（含说明）

合成夹具（5 张，非真实图库）经真实产品入口：

- 在线：列表 200，`availability=present`×5；缩略图 200（55 ms，首生成）。
- 部分离线（删 3/5 文件后真实重扫，rc 0，`locations_missing=3`）：列表 **2.2 ms** 返回且 `availability=[missing,missing,missing,present,present]`；详情 `availability=missing`（2.0 ms）⇒ **不因等待文件系统阻塞页面，且离线状态如实**。
- 整源缺失（移走根目录后重扫）：产品**结构化拒绝** `SOURCE_OFFLINE`（rc 1）；列表仍 4.7 ms 返回。
- 说明：对已缓存缩略图，产品仍返回 200 的缓存图（1.5 ms）；「如实离线态」由 `availability` 承载，而非占位图。

判定：无明显阻断 ⇒ PASS。

## 8. 未执行 / 未测 / BLOCKED

- **B 的形式 P95**：`BROWSER_TIMING BLOCKED`（见 §4）。
- 质量集（20–30 条人工标注查询）：未做（非本卡阻断项，卡面允许后续补）；**不宣称 Recall/nDCG/AI 准确率**。
- GTX 1660 最低配置、正式发行、P4-03B/P4-04、P4-05A/B、P4-07、P4-08、40 万档：**均未测**，不因本卡结论通过。

## 9. 证据性质区分

- **当前版本端到端**：§2 A、§3 D、§5 C（真实 HTTP + 真实模型塔 + 真实 CUDA；`test-results/p4-02w-warm-text.json`、`-background-overlap.json`、`-upload-search.json`）。
- **HTTP 层诊断（非浏览器）**：§4 B（`test-results/p4-02w-browser-first-screen.json`）。
- **诊断（非端到端 PASS）**：§6 归因（`test-results/p4-02w-attribution.json`）；§7 离线冒烟（`test-results/p4-02w-offline-smoke.json`）。
- **历史证据（仅背景/交叉印证）**：P4-02M（30 万 Flat-Exact 10.9 ms / 60 block）、P4-02J（争用）、P4-02K（预算）、P4-02U（写锁归因）、P4-02V（有界降级）。
- **BLOCKED**：§4 B。**未执行**：质量集、§8 所列门槛。

## 10. 判定与唯一最小修复卡建议（**不在本卡实施**）

判定：**`PERSONAL TRIAL PERFORMANCE FAIL / PARTIAL`**（A、D 未达标；C 通过；B BLOCKED）。

**P4-02W-F1 — 降低 30 万档「精确向量通道」单次检索执行成本（单点、保持结果语义等价，同时服务 A 与 D）**

- 失败场景：A（P95 5076.4 > 5000）、D（P95 8144.9 > 8000；后台写争用叠加同一向量通道）。
- 目标分量：**只处理 `vector_channel` 的 60 次 keyset 分块引擎往返 + 30 万候选分块 SQL 扫描与合并**（当前已走 bounded 路径；穷举回退仅出现在边界并列未证明时），使其不再是 A、D 的主成本；归因证据须补记每块 `resolved` / 实际调用路径。
- 硬约束：保持 `(distance, asset_id)` **完全有序结果等价**与既有契约（信封/错误码/游标/ADR 不变）；不改 SQL 语义、索引结构、RRF/BM25/距离语义、busy timeout、drain 调度。
- **明确不启动**：A3（P4-02I）、ANN/FAISS/第二后端（P4-02L）、scheduler 改造、模型替换；不得一张卡同时启动 SQL + A3 + ANN + scheduler + model。
- D 专项附注（同一张卡内可先只做测量复核，不并行开工）：先修向量通道；若修完 D 仍超 8 s，再另立一张只处理后台写争用/前台追赶的卡（本轮 D 数据已给出 `LAGGING` 窗口与 overlap 缺口）。

## 11. 命令、退出码、产物与自检

```
# A 30 样本 -> FAIL(exit 1)；D 30 样本 -> FAIL(exit 1)；C 30 样本 -> PASS(exit 0)
.venv\Scripts\python.exe tools\diagnostics\p4_02w_personal_trial.py --git-head f9b9708 \
  --samples 30 --smoke 4 --output test-results\p4-02w-warm-text.json warm-text --data-root tmp\p4-02w\warm\data
.venv\Scripts\python.exe tools\diagnostics\p4_02w_personal_trial.py --git-head f9b9708 \
  --samples 30 --smoke 4 --output test-results\p4-02w-background-overlap.json background \
  --data-root tmp\p4-02w\bg\data --backlog-library tmp\p4-02w\backlog-library --backlog 12000 --background-grace-s 900
.venv\Scripts\python.exe tools\diagnostics\p4_02w_personal_trial.py --git-head f9b9708 \
  --samples 30 --smoke 4 --output test-results\p4-02w-upload-search.json upload --data-root tmp\p4-02w\warm\data --image …
.venv\Scripts\python.exe tools\diagnostics\p4_02w_personal_trial.py --git-head f9b9708 \
  --output test-results\p4-02w-browser-first-screen.json first-screen --data-root tmp\p4-02w\warm\data --rounds 5 --first-screen 24
.venv\Scripts\python.exe tools\diagnostics\p4_02w_personal_trial.py --git-head f9b9708 \
  --output test-results\p4-02w-attribution.json attribution --data-root tmp\p4-02w\warm\data --rounds 5
# 独立复算：stats --input <json> => matches=true
uv run --no-sync ruff check .   # exit 0
git diff --check                # exit 0
```

产物：本报告；`test-results/p4-02w-warm-text.json`、`p4-02w-background-overlap.json`、`p4-02w-browser-first-screen.json`、`p4-02w-upload-search.json`、`p4-02w-attribution.json`、`p4-02w-offline-smoke.json`；`tools/diagnostics/p4_02w_personal_trial.py`。

范围核对：未修改 `src/**`、`tests/**`、schema/migration/SQL/索引/搜索算法/调度/`pyproject.toml`/`uv.lock`；未实现 A3/ANN/P4-03B/P4-04；未启动后置任务；未 push。

环境事实：默认沙箱会拒绝 AI worker 的 multiprocessing 管道（`WinError 5`），属仓库既记的沙箱环境事实；本卡测量均在**正常权限路由**下重跑，未改 ACL。

## 12. 独立审查状态

本报告已按用户裁决扩展到 A/D/B/C/§6。仓库中既有的 `docs/reports/P4-02W-dsh-capacity-evidence-review.md`（18:23，`REVIEW PASS`）是在本扩展**之前**、仅针对 A-only 版本所作（其「D/B/C/§6 未执行」一栏已不适用）；其 F1–F4 已在本报告 §6.1 逐条落实。**该审查须对新版本重跑**；本节不改动该审查文件（保留在制品）。

## 13. 说明

本卡 PASS 不等于 GTX 1660、正式发行、P4-03B/P4-04、P4-05A/B、P4-07、P4-08。总体结论 **FAIL / PARTIAL**：不进入真实日常使用、不结束性能专项，只提出 §10 的一张最小修复卡；随后交新的独立 DS-H 会话执行「容量证据专项审查」。