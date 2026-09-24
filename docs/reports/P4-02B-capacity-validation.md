# P4-02B 正式容量验证报告（1 万 / 10 万 / 30 万）

本报告是 **P4-02B** 的交付证据：在**真实产品代码路径**上完成 1 万 / 10 万 / 30 万三档容量测量，
按主架构 §12.2 的口径分阶段报告时间与资源，并给出查询侧延迟、D 级故障注入、未达标项与决策。

执行模型：**DS-L / DeepSeek V4.1 Flash / 低推理**（由用户在 Codex 中手动选择）。独立审查按
[任务卡](../tasks/P4-02B.md) 与 V3.1 §2.4 走 **DS-H 容量证据专项审查**；本卡只新增测量工具、
夹具、测试与文档，未改产品行为，因此默认记录 `SOL_NOT_REQUIRED`。**但本报告 §9 的 F2/F4 触及
§12.2/§12.3 承诺口径（查询 P95、ANN/A3），若主审把其中一条升级为协议或 ADR 裁决，必须改记
「Sol high 聚焦终审必选」并把状态停在 `ENGINEERING REVIEW PASS / SOL FOCUSED REVIEW PENDING`。**

## 1. 结论摘要

1. **三档全部真实跑完**（合成夹具、项目内临时 data root），30 万档达到
   `artifacts=PUBLISHED 300 000`、`index_outbox=CONFIRMED 300 000`、
   `index_entries=CONFIRMED 300 000`、`semantic_ready=300 000`、活动 generation `READY`、
   `schema_version=15`、`pending=0`、`pending_repair=0`。
2. 语义阶段完全走产品入口：真实 `jobs` 行 → 真实 `Scheduler` + `RuntimeCapabilities`/资格门 →
   `ArtifactStore.prepare/persist_result` 真实产物文件与 `index_outbox` →
   `python -m photo_search.indexing.cli drain` 确认与 `asset_id` 标量索引。
3. **发现两条必须记录的能力事实**（不是放宽门槛）：
   - **F2 查询延迟未达标**：暖机文字搜索 P95 在 10 万档约 8.3 s、30 万档约 125 s，
     而 §12.2 目标是 ≤ 1 s；后台维护同时前台搜索 30 万档已无法在 1.5 s 内完成（10 万档实测
     12.6 s）。根因是 `vector_channel` 的候选块路径（每 5 000 候选一块、每块一次引擎 pass），
     30 万档共约 60 块。
   - **F1 语义发布的一次性突发是二次成本**：一次性投递整个积压时，
     `jobs.claimable_candidates` 每次领取都会物化并排序**整个可运行积压**，于是
     1 万档 96.2 件/秒 → 10 万档 47.3 件/秒 → 30 万档（截断样本）**11.3 件/秒**；
     改为有界波次（发布 5 000 → 生产 drain → 再发布）后回到 10 万 92.3 件/秒、30 万约 79 件/秒。
4. **真实模型与最低配置条目 BLOCKED**：本机没有 pinned 中文 CLIP / 人脸 / OCR 权重，也没有
   GTX 1660。嵌入由**确定性替身执行器**产生（协议、文件、事务、outbox 完全真实，只有模型计算被
   替换）。因此本报告的数字是**产品管线成本**，不是模型吞吐，也不能替代最低配置验收。
5. 禁止面零改动：`0001`～`0015`、`SUPPORTED_SCHEMA_VERSION=15`、ADR-0001～0011 与 companion、
   `taken_time`/`capture_desc`/`SearchFiltersEchoV2`（11 + 2）、`search_projection` 键集、
   `POST /api/v1/search` 信封/`query_hash`/游标/错误码、RRF/BM25/距离公式、P1～P3 人工状态、
   `PhotoSearch.spec`、`tools/packaging/**`、`packaged_api_smoke.py`、`pyproject.toml`、`uv.lock`
   全部未触碰；改动面只有新增工具、测试与文档。

## 2. 机器与运行环境

| 项 | 值（`test-results/p4-02b-capacity-300k.json` 的 `machine`） |
|---|---|
| 平台 | Windows-11-10.0.26200-SP0 |
| Python | 3.12.10（`uv`，项目本地 `.uv-cache`） |
| CPU | Intel64 Family 6 Model 183 Stepping 1，32 逻辑核 |
| 物理内存 | 34 038 341 632 B（31.7 GiB） |
| LanceDB / PyArrow | 0.38.0 / 25.0.1 |
| GPU | NVIDIA GeForce RTX 4060 Laptop GPU，8 188 MiB，驱动 616.92（**不是 GTX 1660**） |
| 模型权重 | **未安装**（`%LOCALAPPDATA%\PhotoSearchData\models`、`D:\PhotoSearchData` 实测不存在） |

## 3. 方法

### 3.1 夹具

- 每档都在 `--work-dir`（本次 `tmp/p4-02b 容量 验证`，**含中文与空格**）下新建独立合成照片库与
  独立 data root；**不读取、不扫描任何真实照片库**。
- 夹具由种子 `--seed 20260924` 驱动：64 张预编码 JPEG 变体 + 每文件唯一 ASCII 尾标记，因此文件
  内容哈希不同（资产唯一）、仍是可解码 JPEG、创建成本与文件数近线性；mtime 压到 1 小时前避免稳定性等待。
- 可复现性由 `tests/integration/test_p4_02_capacity_validation.py::test_the_fixture_is_seeded_and_reproducible`
  固定（同 seed 两次构建逐字节相同）。

### 3.2 阶段与产品入口

| 阶段 | 产品入口 |
|---|---|
| 发现（scan） | `CatalogService.run_scan`（流式枚举、内容确认、稳定性策略、拍摄时间提取） |
| 目录可查 | `search.documents.catch_up`（API 同一条投影追平路径）+ FTS5 行数核对 |
| 缩略图（浏览） | `CatalogService.generate_thumbnail`（P1-03 媒体缓存） |
| 语义 | `jobs.enqueue` → `scheduler.engine.Scheduler` + 诊断 `RuntimeCapabilities`/资格 → `ArtifactStore.prepare/persist_result` → `photo_search.indexing.cli drain` |
| 查询 | `search.service.search_assets` 与真实 ASGI `POST /api/v1/search`（会话 + CSRF + 中间件 + 路由） |
| 故障注入 | 同批产品函数：`maintenance.drain_semantic_outbox`、`IndexCoordinator.process_batch(failpoint="after_lance")`、`photo_search.indexing.cli` |

### 3.3 有界波次与突发样本

语义阶段默认按 **`--publish-wave 5000`** 有界波次推进：发布一批 → 用生产 drain 确认 → 再发布
下一批。这就是 AI worker 持续发布 + 维护入口的稳态用法；同时每档开头保留一个
**`--burst-sample 20000`** 的无界突发，用来把「一次性积压」的成本也测出来（§9 F1）。

### 3.4 诚实限制

1. **没有真实模型权重**：space/dimension/processor 身份沿用 pinned
   `chinese-clip-vit-b-16`（`space_id=chinese-clip-vit-b-16:c8ac42d3907c57b0`，
   `processor_id=chinese-clip-vit-b-16@36e679e65c2a`，512 维，cosine），但向量由确定性替身产生。
   真实模型吞吐、显存峰值、完整 API P95（含文本塔）与 GTX 1660 最低配置一律 **BLOCKED**。
2. 查询延迟中的「文本编码」为替身成本；报告的 P50/P95 是**检索/融合/SQLite 校验**成本。
3. 产品**不建 ANN 索引**（P4-01B 裁决 B1），30 万档只能精确扫描；§12.3 的 ANN Recall@20 ≥ 0.95
   **未验证**。A3（无过滤时一次全局查询）仍**未授权、未实现**。
4. 人脸与 OCR 阶段没有可用权重，本报告不给出这两阶段数字（不用替身冒充模型吞吐）。
5. 30 万档的**语义执行/逐波 drain 结构化明细没有落盘**：该档的结构化 JSON 来自「复用已建成
   data root 的查询测量」（§3.5），阶段时间来自运行日志与 SQLite 直读，报告内逐处标注来源。

### 3.5 实际执行的命令

```powershell
# 1) 主运行：1 万 + 10 万（结构化）＋ 30 万（阶段），工作目录含中文与空格
uv run --no-sync python tools/diagnostics/capacity_validation.py `
  --scales 10000,100000,300000 --work-dir "tmp/p402b 容量 验证" --queries 40 `
  --output test-results/p4-02b-capacity.json
# 30 万档在该命令中已完成 fixture/scan/catalog/thumbnails + 60 波语义（300 000 确认），
# 40+40 次查询采样按实测 ~2 分钟/次需约 3 小时，遂按下面两条有界测量收口。

# 2) 30 万查询（复用已建成 generation，3 次暖机采样，无 HTTP）
uv run --no-sync python tools/diagnostics/capacity_validation.py `
  --reuse-data-root "tmp/p402b 容量 验证/scale-300000/data" --scales 300000 `
  --queries 3 --no-http --skip-injections --output test-results/p4-02b-capacity-300k.json

# 3) D 级注入（1 500 资产小夹具）
uv run --no-sync python tools/diagnostics/capacity_validation.py --injections-only `
  --work-dir "tmp/p402b 容量 验证" --injection-scale 1500 `
  --output test-results/p4-02b-injections.json

# 4) B 级全量 + 本卡新增用例（受限令牌下 WinError 5，按正常 Windows 权限路径执行）
uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration `
  --basetemp=.pytest-basetemp-p402b/full --junitxml=test-results/p4-02b-full.xml
uv run --no-sync python -m pytest -q -p no:cacheprovider tests/integration/test_p4_02_capacity_validation.py `
  --basetemp=.pytest-basetemp-p402b/scale2 --junitxml=test-results/p4-02b-scale.xml
uv run --no-sync ruff check .   # exit 0
git diff --check                # exit 0
uv lock --check                 # exit 0
```

证据文件：

| 文件 | 内容 |
|---|---|
| `test-results/p4-02b-capacity-10k-100k.json` | 1 万与 10 万档完整结构化结果（主运行的第一份落盘快照） |
| `test-results/p4-02b-capacity.json` | 主运行最终落盘（含 1 万/10 万；30 万档被收口模式覆盖前的内容见上一行） |
| `test-results/p4-02b-capacity-300k.json` | 30 万档查询/存储/索引状态（复用 data root） |
| `test-results/p4-02b-capacity-300k-log.txt` | 30 万档阶段时间线 |
| `test-results/p4-02b-burst-partial.json` | **一次性突发**对照（1 万 96.2 件/秒、10 万 47.3 件/秒完整；30 万在 16 567 件处截断） |
| `test-results/p4-02b-burst-log.txt` | 突发运行时间线 |
| `test-results/p4-02b-injections.json` | 五个 D 级注入结果 |
| `test-results/p4-02b-full.xml` | B 级全量：**1682 / 0 failures / 0 errors / 48 skipped**（259.2 s） |
| `test-results/p4-02b-scale.xml` | 本卡新增：**11 / 0 / 0 / 0**（12.5 s） |

## 4. 三档容量结果

### 4.1 各阶段时间、吞吐与资源峰值

| 档位 | 夹具 | 发现 scan | 目录可查（投影+FTS） | 缩略图 | 语义（execute + 逐波 drain） | 人脸 | OCR |
|---|---:|---:|---:|---:|---:|---|---|
| 1 万 | 8.4 s | 28.6 s（349 件/秒；枚举 8.1 s，处理 20.5 s） | 0.57 s（17 667 文档/秒） | 45.1 s（222 件/秒） | 102.9 s（97.2 件/秒，1 波）+ drain 11.6 s（864 事件/秒） | BLOCKED | BLOCKED |
| 10 万 | 92.4 s | 287.8 s（348 件/秒；枚举 84.4 s，处理 203.2 s） | 5.6 s（17 857 文档/秒） | 419.1 s（239 件/秒） | 1 083.1 s（92.3 件/秒，17 波）+ drain 134.4 s（744 事件/秒，1.344 ms/事件） | BLOCKED | BLOCKED |
| 30 万 | 293.6 s | 874.0 s（343 件/秒） | 20.1 s（14 925 文档/秒） | 1 379.7 s（218 件/秒） | 约 3 810 s（≈79 件/秒，60 波，含逐波 drain；见 §3.4 第 5 条） | BLOCKED | BLOCKED |

工作集峰值（`MemorySampler`，50 ms 采样）：

| 档位 | scan | 缩略图 | 查询阶段 |
|---|---:|---:|---:|
| 1 万 | 148.0 MB | 156.0 MB | 未单独采样 |
| 10 万 | 291.3 MB | 372.4 MB | 未单独采样 |
| 30 万 | 未单独采样 | 未单独采样 | **2 118.1 MB 峰值（+1 937.4 MB 增量）** |

语义阶段（发布/scheduler/drain）未单独包采样器，只有进程级峰值：该阶段内存条目**未记录**，
列入 §10 未验证项（补采样器是纯测量改动）。

### 4.2 数据形状、库大小与 generation 状态

| 档位 | 资产/位置 | catalog.db | artifacts | thumbnails | vectors | data root 合计 |
|---|---:|---:|---:|---:|---:|---:|
| 1 万 | 10 000 / 10 000（0 MISSING） | 31.4 MB | 107.7 MB | 33.1 MB | 21.4 MB | 193.5 MB |
| 10 万 | 100 000 / 100 000 | 306.4 MB | 1 076.6 MB | 330.7 MB | 224.5 MB | 1 938.1 MB |
| 30 万 | 300 000 / 300 000 | 916.0 MB | 3 229.7 MB | 992.0 MB | 746.9 MB | 5 884.5 MB |

每档语义结束后的只读状态（`maintenance.semantic_index_status`）：

| 档位 | artifacts | outbox | entries | semantic_ready | pending | pending_repair | generation |
|---|---|---|---|---:|---:|---:|---|
| 1 万 | PUBLISHED 10 000 | CONFIRMED 10 000 | CONFIRMED 10 000 | 10 000 | 0 | 0 | `READY`（active） |
| 10 万 | PUBLISHED 100 000 | CONFIRMED 100 000 | CONFIRMED 100 000 | 100 000 | 0 | 0 | `READY`（active） |
| 30 万 | PUBLISHED 300 000 | CONFIRMED 300 000 | CONFIRMED 300 000 | 300 000 | 0 | 0 | `READY`（active） |

`asset_id` 标量索引：1 万档 drain 报告 `created`，10 万档报告 `present`（两者都进入候选块
查询路径）。30 万档沿用同一 generation 的索引状态（前两档已建立、`ensure_scalar_indexes` 幂等），
但本档结构化记录来自复用模式的查询测量，**没有单独记录其 `state`**（列入 §10 未验证项）。
三档 `schema_version` 均为 15。

**长时稳定性**：没有单独的 soak 测试；最长单阶段为 30 万档语义阶段（约 63.5 分钟连续运行，
0 失败、0 事件丢失）。更长时长的稳定性仍未验证（§10）。

## 5. 查询侧结果

查询文本固定为 `西湖 sunset 全家福`，`top_k=20`，查询向量由同一替身编码器给出。

| 档位 | 冷启动（service） | 暖机 service P95（样本） | 暖机 HTTP P95（样本） | 后台维护同时前台 P95（样本） |
|---|---:|---:|---:|---:|
| 1 万 | 165.0 ms | **200.3 ms**（40） | 195.5 ms（40） | 454.3 ms（36） |
| 10 万 | 8 380.5 ms | **8 317.8 ms**（40） | 8 719.1 ms（40） | **12 622.7 ms**（1） |
| 30 万 | 89 129.4 ms | **125 050.5 ms（P50）/ 125 174.8 ms（P95）**（3） | 未测（见下） | 未测（见下） |

- 10 万档 HTTP 与 service 的 P95 相差 < 5 %，说明 HTTP/会话/CSRF 层不是瓶颈；
  30 万档据此只测 service 层（单次约 2 分钟，40 + 40 采样约 3 小时，已按 §3.5 收口）。
- 30 万档「后台维护同时前台」没有可用样本：最后一次波次 drain 在首次搜索完成前就退出，
  前台搜索自身也需约 2 分钟，无法形成有意义的重叠窗口（上限仍由 §9 F2 决定）。
- 冷启动仅表示「本进程第一次查询」，不是冷 OS 缓存；三档都在同一进程内测。

## 6. 检索质量与承诺对照（§12.3）

| 能力 | 本次测量 | 结论 |
|---|---|---|
| 精确路径 Recall@20/@50 = 1.000 | 未重测（P4-01 已在 30 万档固定；本卡未改检索路径） | 沿用 P4-01 证据 |
| ANN Recall@20 ≥ 0.95 | 不适用：产品不建 ANN（P4-01B B1） | **未验证**，不得记为通过 |
| 全局无过滤查询（P4-01 的 A3，约 0.37 s@30 万） | 仍未授权、未实现 | **未验证**；见 §9 F4 |
| 中文自然语言搜索质量（nDCG/Recall 标注集） | 本卡不测（需真实模型与标注集） | 未验证（P4-04 及后续） |
| 新向量维护前可搜索 | §7 `pending_vector` 证明「确认后立即可检索」（产品无 ANN 维护窗口） | 通过（exact/部分结果口径见 §7） |

## 7. D 级注入与恢复

夹具：1 500 资产（同一种子与夹具构造），五个注入全部走产品函数
（`test-results/p4-02b-injections.json`）。

| 注入 | 关键前后计数 | 结论 |
|---|---|---|
| 1. 维护中途杀进程后重启 | 900 事件：中途 `CONFIRMED 80 / IN_FLIGHT 10 / PENDING 810`；恢复后 `applied 820`、`outbox CONFIRMED 900`、`index_entries 900`、Lance 行 900 | 零逻辑重复、无静默丢行 |
| 2. 「Lance 已写、outbox 未确认」重放 | 注入崩溃 `after_lance` 后 `outbox {CONFIRMED 900, IN_FLIGHT 1}`、Lance 行 901；恢复后 `applied 1`、`CONFIRMED 901`、Lance 行仍 901 | 重放幂等（行数不变） |
| 3. 同 `asset_id` 内容变更 | 变更前距旧向量 `0.0`；变更后 `entry_artifact=p4-02b-change-new-751`、`confirmation_revision=1`、Lance 距新向量 `-0.0`、距旧向量 `1.0387`、旧向量不再是首位 | 旧向量被取代，不回滚 |
| 4. 位置离线 | `availability=present` 前后：目标 4 在「前」为命中首位，「后」与维护后均不再出现；Lance 行仍存在（`visible=true`，等待下一次索引事件） | 产品检索面**无幽灵命中**；派生行不会自行消失这一点如实记录 |
| 5. 新增向量维护窗口 | 未确认时按精确向量检索**不命中**；`drain` 确认 1 个事件后**立即**命中首位；文本/融合通道前后均有结果 | 产品无 ANN 维护等待：差异只来自 P2-03 §7.2 的确认协议 |

`availability=any`（默认）下离线资产仍会返回，这是产品「离线仍可浏览并给提示」的既有语义，
不是幽灵向量；本卡按 `present` 过滤口径验证。

## 8. §12.2 / §12.4 对照

| 条目 | 本次结论 |
|---|---|
| §12.2 30 万暖机文字搜索 API P95 ≤ 1 s | **未达标**：service P95 约 125 s（3 样本）；含真实文本塔的完整 API 目标另记 BLOCKED |
| §12.2 第一屏图片可见 ≤ 2 s（浏览器侧可选） | 未测（本卡无浏览器步骤） |
| §12.2 后台建库同时前台搜索 P95 ≤ 1.5 s | **未达标**：10 万档实测 12 622.7 ms；30 万档无法形成有意义窗口 |
| §12.2 冷启动/冷缓存单列 | 已单列（§5）；「冷 OS 缓存」未测 |
| §12.2 新增 1 万各阶段完成时间 | 见 §4.1（发现 28.6 s / 目录 0.57 s / 语义 114.5 s / 人脸·OCR BLOCKED） |
| §12.4 第 3 条（Lance 已写、outbox 未确认） | 见 §7 第 2 行 |
| §12.4 第 4 条（旧租约迟到不覆盖新结果） | 本卡未注入（P2-03/P4-01C 已覆盖），保留为未重测 |
| §12.4 第 5 条（处理期间内容改变） | 见 §7 第 3 行 |
| §12.4 第 8 条（投影滞后仍遵循权威状态） | 见 §7 第 4 行与 §5 的 pending 说明 |
| §12.4 第 9 条（新增向量维护前仍可搜索） | 见 §7 第 5 行（无 ANN ⇒ 无维护窗口；确认协议仍生效） |
| §12.4 第 32 条组合条件 | 本卡未注入（P4-01/P3-07 已覆盖），保留为未重测 |

## 9. 未达标项与决策

### F1（发现，需另开卡）：语义发布的一次性突发是二次成本

- 测量（同一夹具、同一替身执行器、同一产品入口，只差「积压形状」）：
  - 一次性投递：1 万 **96.2 件/秒**（10 000 件 / 103.9 s）；10 万 **47.3 件/秒**
    （100 000 件 / 2 113.7 s）；30 万截断样本 **11.3 件/秒**（16 567 件 / 1 464 s，
    当时 `jobs{QUEUED 283 433}`）。
  - 有界波次（5 000 件/波 + 逐波生产 drain）：10 万 **92.3 件/秒**（1 083.1 s，17 波）；
    30 万约 **79 件/秒**（含逐波 drain）。
- 原因：`src/photo_search/jobs/queue.py::claimable_candidates` 把 `state='QUEUED'`（以及过期
  `RUNNING`）行全部取出并按计算出的 `effective_priority` 排序，每次领取物化**整个可运行积压**
  ——成本随积压线性增长，一次性 N 件就是 O(N²)。
- 最小反例：一次性 `enqueue` 30 万件后反复 `run_once`，领取成本随 `jobs.QUEUED` 行数增长；
  `QUEUED 283 433` 时实测 11.3 件/秒，`QUEUED ≈ 4 000` 时 79–92 件/秒。
- 决策：**另开卡**（不改本卡范围）。修法方向（需独立契约/语义审查，不能在本卡顺手改）：
  在保持 ADR-0003 §10 aging 公平语义的前提下给两侧 `UNION` 加索引友好的有界取数，
  或把「领取窗口」显式化；同时补一条 30 万积压回归。
- 影响：AI worker 持续发布（小积压）时成本正常；**首次全库导入/大积压追赶**会明显变慢。

### F2（未达标，需决策）：查询延迟远超 §12.2

- 测量：1 万 P95 200 ms（达标）→ 10 万 P95 8 318 ms → 30 万 P95 125 175 ms（3 样本，
  P50 125 050 ms，冷启动 89 129 ms）。目标 ≤ 1 s，30 万档差约 **125 倍**。
- 原因：`search/retrieval.py::vector_channel` 的候选块路径按 `SCOPE_BLOCK_SIZE = 5000`
  对权威候选集分块，每块一次引擎 pass；30 万档约 60 块，且查询阶段工作集峰值 2.1 GB
  （候选块被真实物化）。P4-01 已测「无过滤时一次全局查询 ≈ 0.37 s@30 万」，但该路径（A3）
  **未授权、未实现**。
- 决策：**停止交回并建议另开卡**，不自动放宽 §12.2：
  1. 本卡不改检索语义（A3 属未授权路径，超出本卡「最小改动」边界）；
  2. 建议新卡在保持 §9 语义与错误码不变的前提下，把「无过滤/大候选」落到一次全局查询或更大块，
     并用同一工具复测 10 万/30 万 P95；
  3. §12.3 的 ANN 承诺继续维持 P4-01B B1 的「不引入、不宣称」。
- 影响：30 万档「已启动、模型已暖机的普通文字搜索 P95 ≤ 1 s」**当前实现不成立**；
  在真实模型下只会更差（还要加文本塔）。

### F3（测量口径，需复核）：同一 10 万规模的两次真实测量相差 2.4 倍

- 突发模式（单进程一次性写 100 000 行）：service P95 3 484 ms；波次模式（17 个 drain 进程）：
  service P95 8 318 ms。两次都远超目标，但差异说明查询成本对 Lance 数据集布局/版本很敏感。
- 决策：本卡**取较差值**（8 318 ms）作为容量结论，并把差异列为独立审查必查项
  （修 F2 时一并定位，避免「改完了但只是缓存/布局变了」）。

### F4（未验证，需保持口径）：ANN 0.95 与 A3

- 产品不建 ANN（P4-01B B1），本卡不引入、不宣称；§12.3 的 ANN Recall@20 ≥ 0.95 **未验证**。
- A3（无过滤一次全局查询）仍未授权；F2 的修法若采用它，属于协议/策略层面裁决
  ⇒ 任务卡规定的 Sol 升级条件成立时应改记 **Sol high 聚焦终审必选**。

## 10. 未验证 / BLOCKED

| 条目 | 状态 | 原因 |
|---|---|---|
| 真实模型吞吐（图文/人脸/OCR）、显存峰值 | **BLOCKED** | 本机未安装 pinned 权重 |
| 完整 API P95（含真实文本塔） | **BLOCKED** | 同上；本报告 P95 只含检索/融合/SQLite |
| GTX 1660 6 GB 最低配置 | **BLOCKED** | 本机为 RTX 4060 Laptop |
| 人脸、OCR 阶段时间 | **BLOCKED** | 无权重；不用替身冒充 |
| 30 万档 HTTP 层查询 | 未测（有界收口） | 10 万档 HTTP≈service（<5 %），单次约 2 分钟 |
| 30 万档「后台维护同时前台」 | 未形成窗口 | 前台单次约 2 分钟，波次 drain 更短 |
| 30 万档语义阶段内存峰值 | 未记录 | 该档结构化记录来自复用模式；补采样器即可 |
| 30 万档 `asset_id` 标量索引 `state` | 未记录 | 同上；前两档已实测 `created`/`present` |
| 长时稳定性（>1 小时连续或多轮） | 未验证 | 本卡没有 soak 用例 |
| 冷 OS 缓存查询 | 未验证 | 只测了「进程内首次查询」 |
| §12.4 第 4、32 条 | 未重测 | 已由 P2-03/P4-01/P3-07 覆盖 |
| 浏览器首屏 ≤ 2 s | 未测 | 本卡无浏览器步骤 |

## 11. 真实 Git 状态

- 观测基线：`0d13023`（P4-02B 起点，`git status --short` 当时干净）。
- 本卡改动面：`tools/diagnostics/capacity_validation.py`（新增）、
  `tests/integration/test_p4_02_capacity_validation.py`（新增）、
  本报告（新增）、`docs/tasks/P4-03.md`（下一卡新增）、`docs/development-status.md`（状态更新）。
- 禁止面（migration、`catalog/schema.py`、ADR/companion、`api/**`、`web/**`、`src/**` 业务代码、
  `PhotoSearch.spec`、`tools/packaging/**`、`pyproject.toml`、`uv.lock`）**零改动**。
- 临时树（`tmp/p4-02b 容量 验证`，含 30 万档 data root 约 5.9 GB）已在收口后删除；复核时用
  §3.5 的命令按同一 seed 重建，`test-results/p4-02b-*.json` 保留全部机器可读结果。
- 未提交：本卡结束时改动仍在工作树（按仓库惯例由用户决定何时入库）。
