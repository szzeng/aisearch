# P4-02O 实现报告 — 语义索引生产建库入口接线

**状态：IMPLEMENTED / INDEPENDENT REVIEW PENDING**（执行者自检，不是独立审查，也不代表 PASS）。
基线 `549075f`（`master`）；缺口来源见 [P4-02M](P4-02M-exact-search-optimization.md) §1～2 与
[P4-02N](P4-02N-real-query-encoder-wiring.md)；权重见
[中文 CLIP 权重准备](2026-09-27-chinese-clip-weights-preparation.md)。

## 1. 缺口（开工核对，硬证据）

| 事实 | 证据 |
|---|---|
| 没有任何生产入口给扫描到的资产登记图文嵌入任务 | `src/` 里 `jobs.enqueue` 调用点只有 `ingestion/service.py:219`（扫描）、`face/service.py:105`、`ocr/service.py:89`、`taken_time/service.py:157` |
| 没有嵌入处理器的运行时证据入口 | `record_capability`/`record_qualification` 的生产调用只有 `face/service.py:260,265`、`ocr/service.py:181,182`、`taken_time/service.py:264,265` |
| 维护侧只有确认与恢复 | `indexing/cli.py` 命令 = `status/drain/reexecute`；`drain` 只确认已发布事件，`reexecute` 只重跑 `RECLAIMED` artifact（`maintenance.py:372`） |
| 后果 | 文字查询必须有 binding → binding 必须有 generation → generation 必须有已确认的 embedding artifact ⇒ 不建库就搜不到 |

## 2. 交付（改动面）

| 文件 | 改动 |
|---|---|
| `src/photo_search/indexing/semantic.py`（新增） | `semantic_targets`（按内容修订增量选目标）、`enqueue_targets`/`job_device`（job 协议拼写转换）、`embedding_artifact`/`artifact_id_for`、`semantic_executor`（真实图像塔 → embedding artifact）、`adapter_runtime_facts`/`semantic_spec`/`record_runtime_evidence`、`build_index`、`ensure_search_profile`、`generation_summary`/`job_counts` |
| `src/photo_search/indexing/cli.py` | 新增 `build` 子命令（`--limit/--source-id/--batch-size/--dry-run/--model-dir/--manifest/--device/--device-policy/--profile-id/--skip-profile`），`--dry-run` 只列目标（不加载模型、不写库） |
| `tests/unit/test_p4_02o_semantic_build.py`（新增） | 5 个用例（见 §5） |
| `tools/diagnostics/p4_02o_semantic_build.py`（新增） | 真实端到端驱动（§4） |

**未改**：schema/迁移/依赖/`uv.lock`；API 信封/游标/错误码/`total`/RRF/BM25/距离全序/权威补查/发布资格/outbox 写入与提交协议/调度与锁策略；`workers/query_encoder.py`（P4-02N 待审面）、`search/**`、`indexing/{store,maintenance}.py`、`artifacts/store.py`、`§12.2`、ADR。

## 3. 关键不变量（逐条）

| 不变量 | 实现与证据 |
|---|---|
| 空间来自 manifest | `build_index` 用 `semantic_spec(...)` → `workers.query_encoder.pinned_embedding_spec`；§4 实测 `processor_id=chinese-clip-vit-b-16@36e679e65c2a`、`space_id=chinese-clip-vit-b-16:c8ac42d3907c57b0`、`dimension=512`，与查询侧同一函数导出 |
| 设备如实记录 | 能力快照与 qualification 直接来自加载后的 adapter（§4：`gpu/CUDAExecutionProvider/FP32`、`onnxruntime-gpu@1.30.0`），profile 激活门复读同一批事实 |
| 增量按内容修订 | 目标谓词 = 「不存在 CONFIRMED 条目且其 artifact 的 `input_revision` 等于该资产当前 `sha256`」；§4 第二次 `build --dry-run` 目标数为 0 |
| 单点错误不中断 | 执行器把缺失/失败收敛为结构化 `last_error_code`（`SOURCE_FILE_MISSING` / `RUNTIME_RESOLUTION_MISMATCH`）并让其余 job 继续；单测覆盖 |
| 零契约改动 | artifact 仍是 `embedding.v1` + `payload={"vector": [...]}`（与 P2/P4 容量工具同一逻辑键），outbox/generation/profile 门与错误码未改 |

## 4. 真实端到端证据（真实权重 + 产品 CLI）

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path
$env:HF_HUB_OFFLINE='1'; $env:TRANSFORMERS_OFFLINE='1'
Remove-Item Env:HTTP_PROXY,Env:HTTPS_PROXY,Env:ALL_PROXY -ErrorAction SilentlyContinue
uv run --no-sync python tools/diagnostics/p4_02o_semantic_build.py `
  --model-dir 'D:\SZ\project\PhotoSearchData\models\chinese_clip\vit-b-16' --device cuda `
  --per-family 6 --keep --output test-results/p4-02o-semantic-build.json
```

驱动做四件事：合成 18 张图（红/绿/蓝三族，各 6 张）→ 生产扫描器入库；执行**产品命令**
`python -m photo_search.indexing.cli build`；再执行一次 `build --dry-run`；最后用
`resolve_query_encoder` 建真实应用并**不带任何显式绑定**发中文查询（绑定由已激活 profile 提供）。

| 观测 | 值 |
|---|---|
| 建库命令 | exit 0；`targets=18`；`run.jobs={"SUCCEEDED":18}`；`drain.applied=18` |
| 耗时 | 5453.2 ms（**含 CUDA 模型冷加载**）⇒ 303 ms/图（顺序执行、单进程、无并行批） |
| 运行证据 | `resolved_device=gpu`、`CUDAExecutionProvider`、`FP32`、`onnxruntime-gpu@1.30.0` |
| 索引状态 | `generation=image-gen-5bcba729dfda4897a94974b7`、`state=READY`、`entries={"CONFIRMED":18}`、`is_active=true` |
| profile | `semantic-image-gen-5bcba729dfda4897a94974b7` 创建并激活（`active_profile_id` 相同） |
| 第二次 dry-run | exit 0；`targets=0`（增量幂等） |
| 搜索 | HTTP **200**；`profile_id` = 激活的 profile；`channels.vector={engaged:true,candidates:18,engine_blocks:1}`；HTTP 972.3 ms（该请求付了查询 worker 冷启动） |
| 排名 | top1=`red-00.png`；三族均值秩 红(4.83) < 绿(8.33) < 蓝(15.3)；13/13 检查通过 ⇒ `status: pass`（本卡范围） |

## 5. 测试与静态验证

| 命令 | 结果 |
|---|---|
| `pytest -q tests/unit/test_p4_02o_semantic_build.py` | **5 passed / 0 failed**（artifact 形状、建库→READY/激活、幂等、缺文件只失败自身、维度不符拒绝、dry-run 目标） |
| 点名集成：`test_p4_02a_maintenance_entry.py test_p4_02c_recovery_and_idempotency.py test_p2_index_submission.py test_p2_search_endpoint.py test_p2_search_authority.py`（+ 本卡单测） | **59 passed / 0 failed**（JUnit `test-results/p4-02o-focused.xml`） |
| **B 级**：`pytest -q tests/unit tests/integration` | **1766 tests / 0 failed / 0 errors / 48 skipped**（247.3 s，JUnit `test-results/p4-02o-full.xml`） |
| `uv run --no-sync ruff check .` / `uv lock --check` / `git diff --check` | exit 0 / exit 0（96 packages）/ exit 0 |

环境：受限令牌下 `--basetemp`/`tmp_path` 会 `WinError 5`，按仓库既有正常权限路由改用项目内
`.pytest-basetemp-p402o/u*`，**未改 ACL**。`test-results/*`、`tmp/p4-02o-build*` 按 `.gitignore` 不入库。

## 6. 未验证 / 边界

- **独立 DS-H 审查未做** ⇒ IMPLEMENTED / INDEPENDENT REVIEW PENDING，不得当 PASS 使用。
- **不是 §12.2**：18 张合成图、单进程、无并行、不是 30 万档、不是 P95、没有混合负载与首屏/上传/后台门槛记录；
  §4 的 303 ms/图与 972 ms 搜索只是本夹具的量级，**不能**外推为真实图库吞吐或达标证据。
- **真实图库未扫描**（无授权）：真实图片的格式/尺寸分布、解码成本、坏文件比例、`HEIC`、长时运行与
  磁盘增长都未验证；大库建库必须作为单独的资源步骤。
- **并行/续跑策略未实现**：命令按顺序处理 `--limit` 个资产；中断后重跑靠逻辑键幂等而非断点续跑。
- **打包产物未重建**、`packaged_api_smoke.py` 未重跑；GTX 1660 未测；`--device cpu` 的建库未实测。
- 上传（图像塔查询）路径本卡未触及，仍只有 P4-02N 的接线级证据。
- 与 P4-02N 诊断夹具的一个已知交互：那个夹具发布 artifact 时用的是 `rev-<asset_id>` 伪修订，
  不等于资产 `sha256`，所以对它跑 `build` 会（按设计）判定全部目标为「需要按当前内容修订重建」。
  真实建库路径（本卡）一律使用 `assets.sha256`。

## 7. 审查路由

产品改动 ⇒ **独立 DS-H 完整受影响面主审（必须）**：至少独立复跑 §4 驱动、5 个新单测与 B 级全量，
并核对 `git diff` 只含 `indexing/semantic.py`（新增）、`indexing/cli.py`、测试与驱动。
本卡判断 **SOL_NOT_REQUIRED**（未改冻结协议/信封/错误码/高风险状态；artifact/outbox/generation/profile 语义未变），
由主审复核；若主审认为建库入口属于 §11 风险，再升级 Sol 聚焦终审。

## 8. 下一任务

**不预生成**。真实建库闭环已可用，下一步由用户裁决：
(a) **真实图库试建库/试用**（需用户显式授权目录与规模；建议先 2k～1 万张，记录吞吐、§12.2 各门槛、失败样本），
或 (b) 按 [P4-02M §3](P4-02M-exact-search-optimization.md) 的候选面另立**一张**性能卡。
P4-02I（A3）、P4-02L（ANN）、P4-03B（模型升级）保持后置。
