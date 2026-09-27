# P4-02M 入口就绪核对与停止决定（30 万精确检索单点优化，未进入实现）

**性质**：P4-02M 卡只允许「先短核对真实启动/模型搜索入口，再修**一个**主要瓶颈」。本报告记录入口核对的
结论：**真实启动的 API 进程没有查询编码器接线**（产品缺线），因此按卡面第 3 条**停止性能实现**——不改
`src/**`、不改 schema/迁移/依赖、不改 §12.2、不新建合成评测平台。本报告不是性能 PASS，也不是任何「已达标」结论。

- 基线：`549075f`（`master`），开工 `git status --porcelain` 为空。与任务卡「工作树交接」的制卡观察
  `d370022` 不同：[项目范围调整提交](../reports/2026-09-27-personal-scope-alignment.md)（`549075f`）已把当时的
  未提交文档收入历史，本轮按真实 Git 状态核对，未覆盖、未重写任何既有产物。
- 相关卡：[P4-02M](../tasks/P4-02M.md)、[P4-02N](../tasks/P4-02N.md)；上游判断：
  [个人试用版重评](2026-09-27-personal-mvp-reassessment.md)、[全项目重评](2026-09-27-project-search-reevaluation.md)。
- 本轮新增：本报告、`docs/tasks/P4-02N.md`、`docs/tasks/P4-02M.md` 的状态行、`docs/development-status.md` 的更新。
  **`src/**`、`tests/**`、迁移、`pyproject.toml`/`uv.lock`、ADR/companion、既有报告、`tools/**` 零改动。**

## 1. 入口核对：真实启动路径没有查询编码器（产品缺线）

| 事实 | 证据 |
|---|---|
| 编码器只来自注入：`encoder_factory` 默认 `None`，无工厂时 `query_encoder()` 直接返回 `None` | `src/photo_search/api/context.py:35,87-105`（模块自述「this build has no built-in encoder: a deployment (or a test) supplies one through ``encoder_factory``」） |
| 应用工厂只在调用方显式提供时才转发 `encoder_factory` | `src/photo_search/app.py:35,40-44` |
| 模块级 `app = create_app()`（无参数）因此没有编码器 | `src/photo_search/app.py:76` |
| **真实默认启动** `python -m photo_search` → `_serve()` 构造 `ApiContext(dataclasses.replace(settings, session_token=bootstrap.token))`，**不传 `encoder_factory`**；随后 `create_app(settings=..., context=api)` 复用该 context | `src/photo_search/__main__.py:64-84` |
| 打包 onedir 的 Analysis 入口就是同一个 `__main__.py` ⇒ 冻结包与源码启动同一条路径 | `PhotoSearch.spec`（`Analysis(['src/photo_search/__main__.py'])`） |
| 能力报告如实回答：`query_encoder_available = context.query_encoder() is not None` | `src/photo_search/api/routes.py:336,349` |
| 文本搜索把同一 `None` 交给业务层：有文本、有 binding、无 encoder ⇒ 结构化拒绝 | `src/photo_search/api/routes.py:1309`、`src/photo_search/search/service.py:1004-1012`（`RUNTIME_CAPABILITY_UNAVAILABLE` / `reason=QUERY_ENCODER_UNAVAILABLE`） |
| 上传搜图同理：无图像编码器 ⇒ `IMAGE_ENCODER_UNAVAILABLE` | `src/photo_search/api/routes.py:1386-1405` |
| `api/runner.py` 只拥有扫描 worker（`ScanRunner`），与查询编码无关；`api/web.py` 只发静态前端，不构造编码器 | 两文件本轮全文读取 |
| 真实编码器**存在**，但只挂在别的入口：`workers/ai_worker.py:110-125`（`action == "encode_text"` → `ChineseClipOnnxAdapter.encode_text`）经 `workers/client.py:85-88` 使用；调用方只有 `demo-search` 与 `indexing/cli.py::_image_encoder`（建库/再执行） | `src/photo_search/workers/*`、`src/photo_search/indexing/cli.py:151-190`、`src/photo_search/__main__.py:120-147` |
| 实现面扫描：`src/photo_search` 内 `encode_queries` 只有 `search/embedding.py:36` 的 **Protocol** 定义，没有任何实现类；`encoder_factory` 只出现在 `api/context.py`/`api/app.py` 的参数与属性上（`api/app.py` 即 `app.py`） | 本轮 `source scan`（§5 命令） |

**运行时核对（只读，无磁盘副作用）**：`create_app`/`ApiContext` 不做 I/O，`query_encoder()` 只由注入的工厂
决定，因此可直接观测三个真实入口对象：

| 入口对象 | `query_encoder()` | `_encoder_factory` |
|---|---|---|
| 模块级 `photo_search.app.app`（打包/uvicorn 工厂实际加载的对象） | `None` | `None` |
| 无参 `create_app()` | `None` | `None` |
| 默认 `ApiContext(ApiSettings.create())` | `None` | `None` |

```powershell
uv run --no-sync python -c 'from photo_search.app import app,create_app;from photo_search.api.context import ApiContext;from photo_search.api.settings import ApiSettings;print(app.state.api.query_encoder(),create_app().state.api.query_encoder(),ApiContext(ApiSettings.create()).query_encoder());print(app.state.api._encoder_factory)'
# None None None
# None
```

这不是「测试注入替身编码器」的问题：测试确实通过文档化的 `encoder_factory` 缝注入替身
（`tests/integration/test_p2_search_endpoint.py` 及其夹具），而卡面明确禁止把这种注入当作默认启动可用。
本轮核对的对象是**默认启动本身**。

### 1.1 该状态是既有、已被文档记录的预期行为，不是本轮新缺陷

- `docs/diagnostics/search-smoke-checklist.md:21`「本进程**没有**查询编码器（与打包应用一致）」；
- `docs/tasks/P2-07.md:76`、`docs/tasks/P2-08.md:152`、`docs/tasks/P3-01.md:147` 把文本 409
  `QUERY_ENCODER_UNAVAILABLE`、上传 409 `IMAGE_ENCODER_UNAVAILABLE` 写成该构建的预期行为；
- `tools/diagnostics/packaged_api_smoke.py:1341,1494` 把「打包应用必须以 `QUERY_ENCODER_UNAVAILABLE` 回答、
  且 `query_encoder_available is False`」当作验收断言；
- `docs/reports/P2-06A-*.md:248-249` 记录「真实查询编码器未接入应用进程……属后续卡」，
  `docs/reports/2026-09-27-project-search-reevaluation.md` §3 的「真模型接入」行同样标注
  「发行闭环仍需核查」。

**结论**：ADR-0003 §7 的部署侧 adapter 至今没有任何实现 ⇒ **真实模型搜索入口未接线**（产品缺线）。

## 2. 资源核对：登记的模型目录不存在（资源缺口，与缺线分开）

| 项 | 值 | 存在 |
|---|---|---|
| 默认模型目录（`__main__._default_model_dir()`） | `C:\Users\28288\AppData\Local\PhotoSearchData\models\chinese_clip\vit-b-16` | **否** |
| 登记的清单（随包分发） | `tools/prepare_models/manifests/chinese-clip-vit-b-16.json` | 是 |
| 默认数据根（`config.default_data_root()`） | `C:\Users\28288\AppData\Local\PhotoSearchData` | 是，但**只含 `runtime\`**（无 `catalog`、无 `vectors`、无 `models`） |

登记的清单本身可核对：`OFA-Sys/chinese-clip-vit-base-patch16`、
`resolved_revision=36e679e65c2a2fead755ae21162091293ad37834`、dimension 512 / cosine / L2、
text tower `input_ids`/`attention_mask`/`token_type_ids` 均为 `[1,52]`，`onnx` 与 `repository_files`
两项都带 `bytes` 与 `sha256`。

核对当时，本轮**真实端到端（真模型 → HTTP → 结果）标 `BLOCKED`**：本机没有可加载的固定权重。
资源缺口**已于 2026-09-27 由用户授权闭合**：权重准备在 `D:\SZ\project\PhotoSearchData\models\chinese_clip\vit-b-16`，与仓库 manifest 逐字节一致、数值等价校验与真实 worker `demo-search --device cuda` 均通过（[权重准备报告](2026-09-27-chinese-clip-weights-preparation.md)）；该步骤原为需授权的模型准备（`tools/prepare_models/`，约 1.5 GB 权重），
**不并入代码接线**。核对阶段只读上述登记路径，没有扫描个人磁盘、没有访问任何照片目录；下载与导出是随后的用户授权步骤。

## 3. 为什么没有进入「单点优化」

卡面第 3 条：*「若真实搜索接线本身未实现，停止性能实现，输出最小缺口和唯一接线修复卡，不继续建大型合成
评测平台。」* 第 1 节已确认条件成立，故性能实现分支**未进入**。以下为既有证据留档（**本轮没有**重跑任何
容量/性能测量），供接线可用后选点：

- 主成本位置：`search/retrieval.py::vector_channel` 的确认候选 keyset 块走查（`SCOPE_BLOCK_SIZE=5000`，
  30 万约 **60 次引擎调用**）+ `indexing/store.py::search_candidates` 按 P4-01C B1 的全序要求向引擎要
  「每个候选一行」（`limit=len(ids)`）。
- 已有互斥预算（[P4-02K](P4-02K-budget-and-a3-decision.md) §1；30 万、暖缓存、替身编码器、2 样本）：
  `sqlite_read_all 589.34 = candidate_walk 229.014 + pending_topup 359.012 + other 1.314`、
  `engine_total 4686.572`（60 块）、`other_fusion_and_authority 77.348`、`service 中位 5353.26`；恒等式 8/8。
- 闸门 A（全覆盖 + 句柄复用 + 块大小/块数上界）在 [P4-02F](P4-02F-retrieval-latency.md) §13 已实测**不可达**：
  30 万最好的实测是块 50000/6 次调用 **7377.9 ms**、块 20000/15 次 **6416.7 ms**；机制是
  `limit=len(ids)` 让每次查询都要物化≈候选数的行，整代一次调用反而更差（10430.5 ms）。
- 单次无约束 pass 中位 **346.96 ms**（5 样本）只是**诊断下界**：它绕开权威候选集，不构成 A3 授权，
  也不能当作等价证据（P4-02F §9、P4-02K §2）。
- 后台争用与「投影未追赶时 4/9 搜索被拒」是**独立**问题（[P4-02J](P4-02J-contention-closure.md) §5，根因未定），
  不与本卡的检索单点混为一个修复。

即：单点优化的候选面（块内全序的最少返回量、`pending_projection_page` 的 359 ms 相关子查询、候选 walk 的
SQL 执行计划）仍有空间，但它们只有在**真实入口可用**后才有用户可见收益；卡面要求先接线，故本轮不动产品
代码，也不以小热点微调冒称主瓶颈已解决。

## 4. 停止决定与下一卡

| 项 | 决定 |
|---|---|
| 性能实现（`retrieval.py`/`store.py` 单点） | **未进入**（入口未就绪） |
| 真实端到端（真模型/HTTP/首屏） | **BLOCKED**（权重已就绪于 D 盘；剩余原因=默认启动没有查询编码器接线，见 [P4-02N](../tasks/P4-02N.md)） |
| 产品缺线 | **已确认**：默认启动无编码器、`query_encoder_available=false`、文本搜索 409 |
| 下一卡 | [P4-02N](../tasks/P4-02N.md)：**API 进程真实查询编码器接线（最小）**，只生成这一张 |
| 不启动 | P4-02I（A3）、P4-02L（ANN）、P4-03B（模型升级）按既有状态保持后置 |
| §12.2 / ADR / schema / 依赖 | **零改动**；不追认任何历史 1 秒或性能 PASS |
| 审查路由 | 本报告无产品改动 ⇒ 只需「只证据分支」的容量证据专项审查（V3.1 §2.4）；**P4-02N 是产品改动，必须有独立 DS-H 完整受影响面主审**，本卡不为它预先记 `SOL_NOT_REQUIRED` |

**2026-09-27 追加（历史残留修正）**：上表的停止条件**已消失** —— [P4-02N](../tasks/P4-02N.md) 已完成接线（真实模型 → 真实索引 → 真实 HTTP，两条分支 7/7），P4-02O/Q/S/T 产品面均 `ENGINEERING REVIEW PASS`，因此 [P4-02M](../tasks/P4-02M.md) 已重新记为 **READY**；本节其余内容保持首轮停止时的原文，不改写历史结论。选点改用本报告 §7 的 30 万 Flat-Exact 基线。

## 5. 本轮验收（就绪门禁停止分支：不跑无关全量测试）

按卡面「无产品改动的就绪门禁停止分支不跑无关全量测试，也不标优化 PASS」，本轮只做只读核对与静态检查。

| 命令 | 结果 |
|---|---|
| `uv run --no-sync python -c '<§1 的入口核对>'` | exit 0；`None None None` / `None` |
| `Select-String -Path src/photo_search/**/*.py,src/photo_search/*.py -Pattern 'encoder_factory\|def encode_queries'` | 只有 Protocol 定义与两处参数/属性；无实现类 |
| `Test-Path "$env:LOCALAPPDATA\PhotoSearchData\models\chinese_clip\vit-b-16"`；`Get-ChildItem "$env:LOCALAPPDATA\PhotoSearchData"` | 模型目录 `False`；数据根只列 `runtime` |
| `uv run --no-sync ruff check .` | exit 0（`All checks passed!`） |
| `uv lock --check` | exit 0（`Resolved 96 packages in 1ms`） |
| `git diff --check` | exit 0（无输出） |
| `git status --porcelain`（收尾） | 见 §6 |

## 6. 未验证 / BLOCKED / Git 状态

- **真实端到端**（真模型编码 → HTTP → 结果/首屏）：`BLOCKED`（缺线未修；**权重已于 2026-09-27 就绪于 D 盘**，见 [权重准备报告](2026-09-27-chinese-clip-weights-preparation.md)）。
- **§12.2 本机试用门槛**（暖机文字 API 样本 P95≤5 s 等）：**未判定**，本轮没有测量；合成 service 的
  5.35 s / 7.73 s / 20.47 s 等仍是既有证据，不能当作真实 API 结果。
- **P4-02J 的 4/9 搜索被拒**：归因仍未定，本报告不改其状态。
- **A3 / ANN / 渐进显示**：未被本报告授权；A3 契约（ADR-0012/0013 与 companion）保持冻结后置。
- **打包能力**：本轮只读代码与运行时对象，未重跑 `packaged_api_smoke.py`；其既有断言与本报告结论一致。

本卡结束时相对基线 `549075f` 只多出：`docs/reports/P4-02M-exact-search-optimization.md`、
`docs/tasks/P4-02N.md`、`docs/tasks/P4-02M.md` 的状态行、`docs/development-status.md` 的更新。
`src/**`、`tests/**`、迁移、`pyproject.toml`/`uv.lock`、`PhotoSearch.spec`、`tools/**`、`docs/decisions/**`、
既有报告**零 diff**。

## 7. 30 万 Flat-Exact 参考基线（2026-09-27 只读补充）

口径：复用既有 30 万夹具 `tmp/p402f-阶段0/scale-300000-wave/data`（300 000 assets / 300 000 CONFIRMED /
512 维 / 单位向量，Lance 表版本 1526 前后不变），同一查询向量、`top_k=20`，只用 `argpartition` 取 Top-K
（不全量排序），不建库、不跑模型、不扫真实图库；Lance 与产品行复用既有证据，不重复测。

| 路径 | 30 万延迟 | 说明 |
|---|---|---|
| NumPy flat-exact | **10.9 ms**（3 次中位；min 10.877 / max 11.778；另计读矩阵 **589.2 ms** / 586 MiB） | 纯连续矩阵参考 |
| Lance single-pass | **346.961 ms**（5 样本中位，P4-02F 既有证据） | 单次引擎参考 |
| Production exact | **≈4.85 s**（P4-02J 覆盖完整 P50 4 849.8 / P95 4 908.0 ms；P4-02K 合成口径中位 5 353.26 ms 同量级） | 产品完整精确路径 |

读数：纯距离计算比产品完整路径小约 3 个数量级 ⇒ 30 万档瓶颈**不在** flat-exact 距离计算，而在引擎往返与
整行物化 + SQLite/融合链路。旁证：NumPy Top-20 与引擎单次 Top-20 **顺序完全一致（20/20）**，且 `2 - 2·dot`
与引擎 L2² 距离逐个吻合（如 1.629789 vs 0.185106）⇒ 两条路径的空间与度量口径一致，可直接比较。

产物：`test-results/p4-02f-flat-exact-300k.json`；工具：`tools/diagnostics/p4_02f_stage0.py`
新增 `numpy-flat-exact` 模式（`--modes identity,numpy-flat-exact`，100 行增量，`src/**` 零改动）。
本基线只用于定位瓶颈：**不引入 NumPy 产品索引、FAISS、ANN、第二后端或 A3**。

## 8. 单点优化实现与前后对照（2026-09-27）

### 8.1 选点与「减少 60 次查询」的阻塞判定（同会话基线）

命令（同一 30 万夹具、同一查询、top_k=200、4 样本）：

```powershell
uv run --no-sync python tools/diagnostics/p4_02f_stage0.py `
  --root "W=tmp/p402f-阶段0/scale-300000-wave/data" --order W `
  --modes identity,stages,overhead --samples 4 --top-k 200 --output test-results/p4-02m-before.json
```

| 口径 | 中位（ms） |
|---|---|
| service | **5008.73**（p95 5189.56，样本 4753.24–5198.99） |
| engine 合计（60 次调用） | **4345.58** |
| ├ 表句柄 `_table()` | 356.64（每块约 5.9） |
| ├ plan | 18.35 |
| ├ **Arrow pass（扫描+过滤+距离）** | **3505.94（80.7%）** |
| ├ 行转换 | 136.30 |
| └ 物化 | 327.97 |
| sqlite 读合计 | 591.33（候选 walk 326.80 + 待补页 259.18 + 其�余 5.35） |
| 其他（融合/权威/残差） | 75.94 |
| blocks / scanned / truncated | 60 / 300000 / false |

**判定：在当前冻结契约下，把 60 次调用合并成更少次调用无法安全实现。**

1. 加大块长会让每次调用的 `asset_id IN (...)` 过滤更贵：P4-02F §13 的同族实测是块 50 000（6 次调用）
   engine 6976.0 ms、整代一次调用 10 430.5 ms，都劣于 60 次的 4345.6 ms。
2. 单次无约束 pass（346.96 ms）本质是「全库 Top-K 后再过滤」，被主架构 §9.3 第 4 条与卡面同时禁止。
3. ANN / HNSW / FAISS / A3 / 第二后端 / NumPy 持久索引都超出本卡范围。

**最小机制与预期收益（记录，不在本卡实施）**：需要一种「引擎可低成本判定的候选集成员资格」——
例如代级 bitmap/块描述符随世代物化，或 ADR-0012 的 A3 证书——把 60 次过滤 pass 压向单次 pass 量级。
按 e56c906 基线（纯距离计算 10.9 ms、单次 pass 346.96 ms）外推，service 有望从 5.0 s 量级降到约
0.8–1.0 s（含 SQLite 候选 walk、待补页与融合）。这是**机制/契约决策**，须另立卡。

### 8.2 本卡实现的唯一优化：边界并列探测 + 最少返回行

同一热点内可安全收的那一半是「limit=len(ids) 的全行物化」：

- `indexing/store.py::search_candidates_bounded`：每块只向引擎要 `top_k + 1` 行，并返回
  `resolved = len(rows) <= top_k or rows[top_k].distance > rows[top_k-1].distance`。
  证明：若第 `k+1` 近的距离严格大于第 `k` 近，则引擎给出的 `k+1` 行已覆盖该块**所有**距离 ≤ `d_k` 的行，
  该块参与全局 `(distance, asset_id)` Top-K 所需的信息完整。
- 只有边界并列组被截断（`resolved=False`）时，`search/retrieval.py::vector_channel` 才对该块回退到原
  `search_candidates`（一行/候选、按 `(distance, asset_id)` 排序后取前 k），因此合并结果与旧路径**逐项相同**。
- 引擎仍然只在权威候选集**内部**排名；没有全局 Top-K 后过滤，没有改块大小、通道上限或被截断语义。

### 8.3 前后对照与完整结果等价（同夹具、同查询、同会话）

| 观测 | 优化前 | 优化后 |
|---|---|---|
| `page`（200 行 id+距离） | — | **逐字节相同**（`page` 与 `vector_probe_identical=true`） |
| blocks / scanned / truncated | 60 / 300000 / false | 60 / 300000 / false |
| service 中位 | **5008.73 ms** | **4211.61 ms（−797.1 ms，−15.9%）** |
| service 样本区间 | 4753.24–5198.99 | 4005.03–4545.82 |
| engine 合计 | 4345.58 ms | 约 **3530 ms**（见下注） |

注：本卡**没有**改诊断工具。工具当前的阶段埋点只挂在 `LanceVectorIndex.search_candidates` 上，
新路径的引擎时间因此落入残差桶（`other_fusion_and_authority` 3606.7 ms，其中约 3530 ms 是未计时的引擎
工作量），与「行转换 136.30 + 物化 327.97 = 464.3 ms 被消除、每块返回 201 行而非 5000 行」的量级一致。
补齐工具埋点属于下一轮（会扩大审查面），本卡如实标注。

**残余（量化，未做）**：每块 `_table()` 句柄仍需 356.64 ms（约占 7%）；与 8.1 的机制卡一起处理更合适。

### 8.4 本轮验证状态

- 聚焦回归（含 A3 契约）：`test_p4_02g_a3_contracts.py` + `test_p4_01c_retrieval_blocks.py` +
  `test_p2_search_authority/topup/channels/image` → **49 passed / 0 failed**（`test-results/p4-02m-focused.xml`）。
- **B 级全量**：`pytest -q tests/unit tests/integration` → **1783 tests / 0 failed / 0 errors / 48 skipped（271.0 s）**
  （`test-results/p4-02m-full.xml`）。
- B 级抓到的第一处回归（也是本卡唯一的接口耦合）：`test_p4_02g_a3_contracts.py` 的 `_StubIndex` 只实现
  `search_candidates`。处置：**不改冻结的 A3 契约测试**，改为产品侧鸭子类型容错——`vector_channel` 只在
  替身**提供** `search_candidates_bounded` 时才走新路径，否则退回原全量调用（等价路径），因此所有既有替身
  仍产出逐字节相同的结果。49/0 与 1783/0 均在该修正后取得。
- **仍未收口**：独立 **DS-H 完整受影响面主审**（状态 = IMPLEMENTED / INDEPENDENT REVIEW PENDING，不是 PASS）；
  诊断工具补挂 `search_candidates_bounded` 埋点（本轮刻意不改工具，避免扩大审查面）。

## 9. 聚焦终审收口（2026-09-27）

[独立 DS-H 主审](P4-02M-dsh-affected-surface-review.md)产品面已通过；[聚焦终审](P4-02M-sol-focused-review.md)裁决 F1 闭合，任务 **PASS（仅本卡单点优化）**。§8.4 的待主审状态为当时记录，现由这两份审查报告更新。

F1 解释：K+1 是块内边界探测，严格间隔证完整前 K，并列回退旧穷举；最终全序截断、oracle 的资格/revision-blind/发布语义保留。ADR-0012 §8 禁止修改 oracle 来掩盖缺项，不禁止有完整等价证明的访问优化；不改 ADR/companion，不授权 A3。

§8.3 的前后中位收益仅为每臂 **4 样本观察**，不称 ≥30 样本 P95 或 §12.2 达标；新方法尚未挂阶段计时，engine 反推值仍是估算。真实模型/API/质量集、后台争用、GTX1660、打包与备份恢复没有因本卡通过而获得验收。
