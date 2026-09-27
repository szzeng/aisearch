# P4-02N 实现报告 — API 进程真实查询编码器接线

**状态：IMPLEMENTED / INDEPENDENT REVIEW PENDING**（本报告是执行者自检，不是独立审查，也不代表 PASS）。
基线 `549075f`（`master`，开工工作树干净）。缺口来源：[P4-02M 入口核对](P4-02M-exact-search-optimization.md) §1～2；
权重来源：[中文 CLIP 权重准备](2026-09-27-chinese-clip-weights-preparation.md)。

## 1. 交付（改动面）

| 文件 | 改动 |
|---|---|
| `src/photo_search/workers/query_encoder.py`（新增） | `pinned_embedding_spec()`（只读 manifest JSON，space/processor/dimension 与建库侧同一推导）、`WorkerQueryEncoder`（`spec()`/`encode_queries()`/`encode_images()`/`close()`，单请求锁，上传图落 PNG 暂存）、`build_worker_query_encoder()`（启动 worker、`health()` 握手、校验 worker 报告的空间、失败即停 worker）、`resolve_query_encoder()`→`EncoderWiring`、`default_model_dir()` |
| `src/photo_search/__main__.py` | 真实启动把 `encoder_factory` 交给 `ApiContext`；新增 `--model-dir`/`--model-manifest`/`--model-device` 与同义环境变量；`_default_model_dir()` 改为按**解析后的数据根**（不再硬编码 LOCALAPPDATA）；启动打印 `semantic channel <reason> (model_dir=…, manifest=…)` |
| `src/photo_search/api/context.py` | `close()` 停掉接入的编码器（鸭子类型：无 `close()` 的替身不受影响）；`query_encoder()` docstring 更正 |
| `tests/unit/test_p4_02n_query_encoder_wiring.py`（新增） | 13 个用例：spec 与空间、适配器协议、畸形向量拒绝、上传暂存、空间不一致拒绝、未加载拒绝、live runtime 事实、无模型分支、启动接线与关闭、无 `close()` 替身安全、启动入口按数据根解析 |
| `tools/diagnostics/p4_02n_real_search.py`、`tools/diagnostics/p4_02n_no_model_startup.py`（新增） | 两条分支的可复现证据（§4、§5） |

**未改**：schema/迁移/依赖/`uv.lock`/`PhotoSearch.spec`；API 信封/游标/错误码/`total`/RRF/BM25/距离全序/权威补查/发布资格/outbox 写入与提交协议/调度与锁策略；`search/service.py`、`search/retrieval.py`、`indexing/store.py`；§12.2、ADR、既有 manifest 与 validation 记录。

## 2. 模型目录解析：本卡的最小决定

- **默认**：`<data root>/models/chinese_clip/vit-b-16`（P0-04 布局，由解析后的数据根推导）+ 随包 manifest。
  默认数据根仍在 `%LOCALAPPDATA%\PhotoSearchData`，所以**默认启动仍然是今天的诚实拒绝**（该目录没有模型）。
- **覆盖**：`--model-dir` / `PHOTO_SEARCH_MODEL_DIR`（以及 `--model-manifest`/`PHOTO_SEARCH_MODEL_MANIFEST`、
  `--model-device`/`PHOTO_SEARCH_MODEL_DEVICE`）。**没有把用户绝对路径写进源码**，也没有把「数据根迁到 D」
  这个更大的决定塞进本卡。
- 本机启用真实搜索的启动方式（本报告两条分支的验证方式）：

```powershell
uv run --no-sync python -m photo_search --model-dir D:\SZ\project\PhotoSearchData\models\chinese_clip\vit-b-16
```

- 设备：默认 `auto`＝ADR-0001 策略（默认 CUDA；显式 `cpu` 是如实报告的降级，CUDA 不可用时结构化失败而不是静默降级）。

## 3. 关键不变量核对

| 不变量 | 证据 |
|---|---|
| 能力诚实 | 有权重 → `query_encoder_available=true`（§4）；无权重 → `false` + 既有 409（§5）。两条都是**真实启动/真实请求**观测，不是替身注入 |
| 依赖方向（ADR-0003 §7） | 新模块不 import ONNX/PyTorch（只经 `AiWorkerClient` 协议）；`search/**`、`api/**` 未被新增重型 import；`__main__` 原本就 import worker client |
| 一次编码、续页复用 | `search/service.py`、`search/sessions.py` 零 diff；编码仍在 `encode_query` 内逐次校验 binding |
| 无权重分支逐字不变 | §5 的 409 信封逐字引用，与 P2-07/P2-08 记录的拒绝一致；未创建 `models/`，无 traceback |
| 资源与代码分离 | 权重准备是已授权的独立步骤；本卡不下载任何东西（两条诊断命令都设 `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE` 并清空代理） |

## 4. 有权重分支：真实模型 → 真实索引 → 真实 HTTP

工具：`tools/diagnostics/p4_02n_real_search.py`（单进程内完成建库、发布、握手、搜索）。**口径说明（DS-H 审查 F9）**：该驱动用冻结的 P2 夹具 `p2_search_fixtures.submit_vector` 发布向量（输入修订是伪 `rev-<asset_id>`，不是资产 `sha256`），**不是产品建库入口**；产品建库入口的真实闭环见 [P4-02O 报告](P4-02O-semantic-index-build.md) §4 与 §6。

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path
$env:HF_HUB_OFFLINE='1'; $env:TRANSFORMERS_OFFLINE='1'
Remove-Item Env:HTTP_PROXY,Env:HTTPS_PROXY,Env:ALL_PROXY -ErrorAction SilentlyContinue
uv run --no-sync python tools/diagnostics/p4_02n_real_search.py `
  --model-dir 'D:\SZ\project\PhotoSearchData\models\chinese_clip\vit-b-16' --device cuda `
  --keep --output test-results/p4-02n-real-search.json
```

| 观测 | 值 |
|---|---|
| 接线 | `{"available":true,"reason":"READY","model_dir":"D:\\SZ\\project\\PhotoSearchData\\models\\chinese_clip\\vit-b-16",…}` |
| 索引身份 | `processor_id=chinese-clip-vit-b-16@36e679e65c2a`、`space_id=chinese-clip-vit-b-16:c8ac42d3907c57b0`、`dimension=512`、`runtime_package_id=onnxruntime-gpu@1.30.0`、`supported_devices=["gpu","cpu"]` |
| 真实图像塔（CUDA） | 3 张合成图编码 `163.2 / 17.6 / 16.8 ms`；`confirmed_entries=3`、`confirmed_outbox_events=3`、generation `READY` |
| HTTP | `/capabilities` **3917.7 ms**（该请求付 worker 冷启动）、冷搜索 **158.3 ms**、暖搜索 **23.0 ms** |
| 文本塔 | `query_encode_ms=12.1`、`query_vector_dimension=512`（单次采样，不是 P95） |
| 结果 | `state=OK`、`total_candidates=3`、`channels.vector={engaged:true,candidates:3,engine_blocks:1}`、`channels.fts.candidates=0`、top1=`red.png`（`hit_reason=SEMANTIC`，`matched_by={"vector":1}`） |
| 结论 | 7/7 检查通过（`wiring_available`、`capability_reports_encoder`、`generation_ready`、`all_assets_confirmed`、`search_answers_200`、`vector_channel_engaged`、`top_hit_is_the_red_image`）⇒ `status: pass`（**本卡范围**，见 §7 边界） |

## 5. 无权重分支：真实启动入口 + 既有拒绝

工具：`tools/diagnostics/p4_02n_no_model_startup.py`（拉起 `python -m photo_search` 子进程，用打印的
一次性凭据做真实 HTTP 调用；catalog 是 §4 留下的临时根，因此请求能走到编码器闸门）。

```powershell
uv run --no-sync python tools/diagnostics/p4_02n_no_model_startup.py `
  --data-root 'tmp/p4-02n-real/PhotoSearchData' --port 8129 `
  --output test-results/p4-02n-no-model-startup.json
```

| 观测 | 值 |
|---|---|
| 启动行 | `photo-search: semantic channel MODEL_DIR_MISSING (model_dir=tmp\p4-02n-real\PhotoSearchData\models\chinese_clip\vit-b-16, manifest=…\chinese-clip-vit-b-16.json)` |
| 能力 | `GET /api/v1/capabilities` → 200，`query_encoder_available=false` |
| 文本搜索 | 409 `{"code":"RUNTIME_CAPABILITY_UNAVAILABLE","details":{"reason":"QUERY_ENCODER_UNAVAILABLE",…}}` |
| 上传搜图 | 409 `{"code":"RUNTIME_CAPABILITY_UNAVAILABLE","details":{"reason":"IMAGE_ENCODER_UNAVAILABLE",…}}` |
| 副作用 | 未创建 `models/`；stderr 无 traceback；7/7 检查通过 ⇒ `status: pass`（本卡范围） |

## 6. 测试与静态验证

| 命令 | 结果 |
|---|---|
| `pytest -q tests/unit/test_p4_02n_query_encoder_wiring.py --basetemp=.pytest-basetemp-p402n/u2 --junitxml=test-results/p4-02n-unit.xml` | **13 passed / 0 failed**（1.9 s） |
| 卡面点名集成：`pytest -q tests/integration/test_p4_01c_retrieval_blocks.py tests/integration/test_p2_search_authority.py tests/integration/test_p2_search_topup.py --basetemp=.pytest-basetemp-p402n/u3 --junitxml=test-results/p4-02n-focused.xml` | **17 passed / 0 failed**（9.8 s） |
| **B 级**：`pytest -q tests/unit tests/integration --basetemp=.pytest-basetemp-p402n/u4 --junitxml=test-results/p4-02n-full.xml` | **1761 tests / 0 failed / 0 errors / 48 skipped**（273.0 s） |
| `uv run --no-sync ruff check .` | exit 0（`All checks passed!`） |
| `uv lock --check` | exit 0（`Resolved 96 packages`） |
| `git diff --check` | exit 0 |

环境：受限令牌下 pytest 的 `--basetemp`/`tmp_path` 会 `WinError 5`，按仓库既有正常权限路由改用项目内
`.pytest-basetemp-p402n/u*`，**未改 ACL**；worker 子进程同样需要正常权限（`multiprocessing` 管道）。
`test-results/*.json|xml` 与 `tmp/p4-02n-real*` 按 `.gitignore` 不入库，关键数字已逐条引用。

## 7. 未验证 / 边界 / 已知限制

- **独立 DS-H 审查未做** ⇒ 状态 IMPLEMENTED / INDEPENDENT REVIEW PENDING，不得当 PASS 使用。
- **§12.2 未测**：本卡只测 3 张合成图与一次查询；5 s/6 s/5 s/8 s 门槛、GTX 1660 最低配置、真实图库规模
  （8 万/10 万/30 万）、冷缓存、长时运行、并发搜索与上传路径性能**都未验证**。§4 的 23 ms 暖搜索**不是**
  §12.2 证据（没有 30 万档、没有混合负载、不是 P95）。
- **上传（图像塔）路径**只做了协议与接线层面的单测（暂存 PNG、维度校验、无编码器时的既有 409），
  未做真实上传端到端与质量验证。
- **首请求付冷启动**：本机 CUDA 冷启动约 3.9 s 出现在第一个解析编码器的请求上（§4 的 `/capabilities`）。
  预热/常驻策略不在本卡（会改变进程行为与资源占用）。
- **文档口径（DS-H 审查 F4，本次修复轮已改）**：`docs/diagnostics/search-smoke-checklist.md` 的“本进程没有编码器”已改为条件式（有权重/无权重两分支）。
- **打包产物未重建、`packaged_api_smoke.py` 未重跑**：本卡只验证源码入口。冻结包在没有模型时仍然
  `query_encoder_available=false` + 同一批 409（§5 的路径与包内 `_default_model_dir()` 同源），但这属于
  下一轮打包验收，不能据本报告当已通过。
- **数据根位置**：D 盘模型通过显式 `--model-dir` 使用；把整个应用数据根迁到 D 需要单独决定（本卡未改
  `config.default_data_root()` 语义，`PHOTO_SEARCH_DATA_ROOT` 仍只是诊断/测试覆盖）。

## 8. 审查路由

产品改动 ⇒ **独立 DS-H 完整受影响面主审（必须）**，至少独立复跑 §4/§5 两个工具、13 个单测与 B 级全量，
并核对 `git diff` 只含 3 个产品文件 + 1 个测试文件。
本卡判断 **SOL_NOT_REQUIRED**：未改冻结协议/信封/错误码/`total`/游标语义，未改高风险状态与恢复边界，能力字段
语义未变；该判断由独立主审复核，若主审认为「启动边界」构成 §11 风险则升级 Sol 聚焦终审。DeepSeek max 仅按
V3.1 §2.2 具名条件升级。

## 9. 下一任务

**不预生成**。真实入口已可用，下一步由用户裁决：
(a) 用**真实图库**做个人试用冒烟（需要用户显式授权扫描；§12.2 的目标图/首屏/上传/后台建库分别记录），
或 (b) 按 [P4-02M §3](P4-02M-exact-search-optimization.md) 的候选面另立**一张**性能卡
（块内全序的最少返回量、`pending_projection_page` 的 359 ms、候选 walk 执行计划）。
P4-02I（A3）、P4-02L（ANN）、P4-03B（模型升级）保持后置。
