# P4-02W — 容量证据专项审查 R2（独立 DS-H，窄复审）

**结论：`REVIEW PASS`**（扩容版 A/D/B/C/§6 的数字与定性均可独立复算；**总体 `PERSONAL TRIAL PERFORMANCE FAIL / PARTIAL` 保持不变**）。附 5 条非阻断发现。

- 复审性质：只读复核 + 文档一致性；未重跑任何性能测试，未改 `src/**`、`tests/**`、schema、依赖、`uv.lock`。
- 复审对象：`docs/reports/P4-02W-personal-trial-performance.md`（扩容版）、`test-results/p4-02w-{warm-text,background-overlap,upload-search,browser-first-screen,attribution,offline-smoke}.json`、`test-results/p4-02w-runtime-provider.json`、`docs/tasks/P4-02W.md`、`docs/development-status.md`。
- 与本仓库既有 `docs/reports/P4-02W-dsh-capacity-evidence-review.md`（A-only 版，18:23，`REVIEW PASS`）的关系：该文件按报告 §12 保留不改，本 R2 针对扩容版另立。
- 逐条对应你点名的 7 个问题。

---

## 1. D 真实后台是否成立 —— 成立

`test-results/p4-02w-background-overlap.json` 证据：

- 后台入口是**真实产品维护入口**：`python -m photo_search.indexing.cli build --data-root tmp/p4-02w/bg/data --model-dir …chinese_clip\vit-b-16 --manifest … --device cuda --source-id 2 --limit 12000 --skip-profile --json`。
- 扫描真实产出：`scan.payload.scan` = `files_seen/enumerated/locations 12000`、`assets 10632`（余额 1368 为同内容去重）、`state=SUCCEEDED`。
- 建库真实推进（构建进程自报）：`targets 10632`、`enqueue.queued 10632`、`run.steps 10632`、`drain.applied 10632`、`batches 54`、`confirmed_delta 10632`、`write_rejections 0`、`coverage_complete true`。
- 状态真实落地：`fixture_after` = `index_entries CONFIRMED 310632`、`search_documents 310632`、`search_projection_queue 0`、`outbox CONFIRMED 310632`（起始 300000）。
- 真实 CUDA 图像塔：构建进程 `evidence.resolved_provider = CUDAExecutionProvider`、`qualification_evidence_id = …:gpu:FP32:CUDAExecutionProvider`、`runtime_package_id = onnxruntime-gpu@1.30.0`。
- 逐样本确有后台活跃：`background_alive` 为 True 的样本段与 `background_delta.advanced=True` 重叠，`before/after` 快照里的 `jobs.SUCCEEDED`、`artifacts_published`、`search_documents`、`outbox` 计数逐样本变化（例：sample0 +364 jobs、+367 artifacts、`search_documents` 304000→304800）。

结论：D 的“后台建库”不是纯文字投影或轻量重投影冒充，**成立**。并用一次性副本 `tmp/p4-02w/bg/data`，未污染 A/C 复用的 `warm/data`。

## 2. D 26/30 overlap 如何定性 —— 定性为「测量充分性缺口」，不是产品缺陷，也不能被升级成 PASS 口径

- 事实：30 个样本里 `overlap=true` 仅 **26**（idx 0–25）；idx 26–29 的 `background_delta` 全 0，且 `background_alive` 自 idx 25 起为 False ⇒ 后台在建库过程中把 10632 资产消费完，后 4 个样本是**空后台**样本。
- 定性：这是 **backlog 定容偏小（12000 文件/10632 新资产 ≙ 约 26 个样本时长）导致的测量充分性缺口**，不是后台实现缺陷。卡面 §5 D 明确要求「至少取得 **30 个有效真 overlap** 前台搜索样本」，故 D 在本口径下**不可能判 PASS**——即便 P95 达标，也只能记「不足 30 真 overlap」。
- 关键点：D 的 FAIL **不是**只靠 26<30 撑起来的。全 30 样本 P95 = **8144.9**，仅取 26 个真 overlap 样本 P95 仍 = **8144.9**（n=26 时 `sorted[ceil(0.95*26)-1]=sorted[24]`），**两种算法都 > 8000**，因此「26<30」只是附加缺口，不改变结论。
- 可复算：26 个 overlap 样本延迟 P50 6134.4 / P95 8144.9 / max 8618.8；全 30 样本 P50 6320.2 / P95 8144.9 / max 8618.8 —— 与报告 §3 一致。
- 若后续卡需要「合规的 D 基线」，须把 backlog 定容到覆盖 ≥30 个样本时长后重测；**当前 D 已 FAIL，无需为它单独重跑**。
- 附注（`R2-F1`）：现有 `advanced` 判定把 `outbox_pending>0` 也计作“推进”。逐样本增量显示 26 个 overlap 样本里 `entries_confirmed/outbox_confirmed` 只在尾部少数样本（约 3 个）>0，主信号是 `jobs_succeeded`/`artifacts_published`/`outbox_pending`（编码+发布阶段）。所以「26/30 真 overlap」应读作「26/30 样本期间后台计数器真实推进」，**不应**读成「26/30 样本都发生了 index apply/confirmed 推进」；场景级管线确实走完（drain.applied 10632、CONFIRMED 300000→310632），故卡面「真实代表性后台建库」仍成立。

## 3. C 30 样本是否可信 —— 可信

`test-results/p4-02w-upload-search.json` 证据：

- 入口真实：`POST /api/v1/search/image` 真文件上传（同一 `_server_for` 的真实 HTTP 服务），30 样本全 200 / 全 `ok`，`index` 0–29 连续、无补跑替代。
- 上传链路真跑：每样本 `image_query` 回显 `format=JPEG, width=320, height=240, byte_size=…` 与 `limits`；6 个 `byte_size` 与 6 张上传文件逐一对应，`photo-00000000.jpg` 的 SHA256 与样本 `image_query.digest` 逐字相同 ⇒ 解码确在链路上。
- 检索真跑：`vector.engaged=true`、`engine_blocks=60`、`hits=20` ⇒ 打的是同一 300 万级候选集（30 万档）同一精确向量通道。
- 图片集合规：`tmp/p4-02w/upload-images/photo-0000000{0..5}.jpg` 与冻结母版库 `tmp/p402f-阶段0/scale-300000-wave/library/bucket-0000/photo-0000000{0..5}.jpg` **同名同字节数**，是**库内受支持 JPEG**，非合成图、非预计算向量。
- 统计：复算 P50 4526.2 / P95 4658.7 / max 5035.4 / mean 4548.8，失败 0，与报告 §5 逐字一致；冷首传 8931.1 ms（含模型加载）单列、smoke 4 次不计入。
- 口径注：1/30 样本 max 5035.4 > 5000（原样保留），但卡面门槛是 **P95 ≤ 5 s**，故 PASS 成立；另外 `fixture_before == fixture_after` ⇒ 上传搜索未写库。

## 4. B BLOCKED 是否合理 —— 结论合理；但理由措辞宜收紧（`R2-F4`）

- 卡面 §5 B 本身就把「占位」写成允许项：环境无法取得**可靠、可重复**的浏览器第一屏 timing ⇒ 记 `BROWSER_TIMING BLOCKED`，且**不得**用人工秒表 30 次制造伪精确 P95。
- `test-results/p4-02w-browser-first-screen.json`：`verdict = BROWSER_TIMING BLOCKED`，`reason` = 无可靠可重复的浏览器首屏计时能力、且卡面禁止新增浏览器自动化依赖；`missing_capability` = 不新增自动化依赖即可给「首张图可见」计时的记录式真实浏览器 harness。全程**没有**把它写成 P95 或引用为浏览器首屏时间。
- HTTP 层诊断（n=5，明确标注**非**浏览器首屏）：`search_http_ms` min 4040.2 / p50 4095.5 / p95 4201.4；`first_thumb_ms` 2.7–3.5；`first_screen_http_complete_ms` min 4079.3 / p50 4135.6 / p95 4243.3；失败 0 —— 与报告 §4 数字逐字一致。
- `R2-F4`（非阻断，措辞）：报告 §4 说「本 build 无『记录式真实浏览器』计时能力」略强——仓库确有「记录式真实浏览器（Codex 内置浏览器）」的**观察式**做法（ADR-0010 §496、ADR-0011 §411、P3-08 系列）。更准确的依据是「可得的记录式浏览器只能观察、给不出 30 个可重复计时样本，且卡面禁止人工秒表 30 次伪精确 P95」。结论（BLOCKED）不变。
- 生效边界：BLOCKED ⇒ 卡面禁止拿它当 PASS，也没扩成浏览器自动化开发任务，符合要求。

## 5. CUDA provider 新证据 —— F2 已闭合

- `test-results/p4-02w-runtime-provider.json`：`onnxruntime_providers` 含 `CUDAExecutionProvider`；`resolve_device("cuda")` = `provider CUDAExecutionProvider`、`cpu_fallback_disabled true`、`precision FP32`、`strict_fp32 true`。
- 更强的独立记录：D 的建库进程自身写出 `evidence.resolved_provider = CUDAExecutionProvider`、`qualification_evidence_id = …:gpu:FP32:CUDAExecutionProvider`、`runtime_package_id = onnxruntime-gpu@1.30.0` —— 这是**产品自己记的** provider，不是外部探针。
- 与 `runtime/device.py` 的静态保证一致（缺 CUDA provider 即 `DeviceUnavailableError`，不影响静默 CPU 回退；会话 `session.disable_cpu_ep_fallback=1`），因此「真实 CUDA」不再只靠“没报错”推断。
- 残余限度（非阻断）：`runtime-provider.json` 是机器级探针，无 `git_head`/`data_root`/时间戳，也不是从被测 server 进程内取；A/C/B 的 JSON 本身仍不记录 per-process provider。若后续要“一进程一证据”，可在诊断里落 `resolve_device().as_dict()`。

## 6. 报告与 JSON 是否一致 —— 一致（两处措辞类小瑕）

逐项核对（全部一致）：A（30 样本/P50 4508.0/P95 5076.4/max 5172.4/失败 0、逐样本串）；D（P50 6320.2/P95 8144.9/max 8618.8、26/30 overlap、前 8 样本 `LAGGING`、构建计数、收尾 310632/queue 0）；C（见 §3）；B（见 §4）；归因（6 分量从 `rounds` 逐字复算一致、vector p50 4598.8 / full 4921.1 ≈ 93%）；§7 离线冒烟（在线 5×present、缩略图 55.2 ms/1830 B、部分离线列表 2.2 ms 且 `availability=[missing,missing,missing,present,present]`、详情 2.0 ms、缓存缩略图 1.5 ms、整源缺失 `SOURCE_OFFLINE` rc 1、列表 4.7 ms）；§9 证据清单里的 6 个 JSON 均存在。

两处非阻断小瑕：

- `R2-F3`：报告 §4 写「首屏 **20** 张缩略图」，JSON 字段 `first_screen_size` = **24**（请求值），但每轮实际只取了 **20** 张（`top_k=20` 截断了 hits）。测得的三个统计量本身是对的；只是 requested 24 与 actual 20 并列时容易被读成矛盾，建议 JSON 区分 requested/actual。
- `R2-F2`：`docs/development-status.md` 仍保留 **F1 更正前**的措辞「`vector_channel` 分块引擎往返 + `search_candidates(limit=len(ids))` 整行物化」，而报告 §6/§6.1 已改为「bounded 优先、并列未证明才回退穷举」。状态行与报告不一致，本轮已在 status 内修正为与报告相同的表述。
- `R2-F5`（非阻断）：报告 §6 的“D 相对 A 的额外 ~3 s”未点明统计量；实测是 **P95** 差（8144.9 − 5076.4 = 3068.5 ms），而 **P50** 差只有 1812.2 ms（6320.2 − 4508.0）。建议写成“P95 差 ≈3.1 s”。

## 7. 最终结论是否应保持 FAIL/PARTIAL —— 应保持

卡面 §7 PASS 需 6 条全成立。当前：A FAIL（P95 5076.4 > 5000）、D FAIL（P95 8144.9 > 8000，且真 overlap 26 < 30）、B `BLOCKED`（卡面明示 BLOCKED 不得当 PASS）、C PASS、§6 PASS。⇒ **`PERSONAL TRIAL PERFORMANCE FAIL / PARTIAL` 必须保持**；C 通过不足以翻盘，B 的 BLOCKED 也不构成“故意未测”。

同时明确**不得追认**：本结论不授予 GTX 1660、正式发行、P4-03B/P4-04、P4-05A/B、P4-07、P4-08、40 万档、质量集（Recall/nDCG/AI 准确率）任何 PASS；§12.2 全部门槛仍未达标。

---

## 8. 发现清单（全部非阻断）

- `R2-F1`：D 的 `advanced` 判定含 `outbox_pending>0`；「26/30 真 overlap」应限定为“后台计数器真实推进”，apply/confirmed 级推进主要出现在尾部少数样本。
- `R2-F2`：`development-status.md` 的修复卡措辞仍是 F1 更正前版本（本轮修正）。
- `R2-F3`：B JSON `first_screen_size=24`（请求）与实际取 20 张并列易误读（测量值本身正确）。
- `R2-F4`：B 的 BLOCKED 理由宜改为“记录式浏览器只能观察、给不出可重复 30 样本计时 + 卡面禁人工秒表”；结论不变。
- `R2-F5`：§6“额外 ~3 s”应注明是 P95 差。

## 9. 判定

- **`REVIEW PASS`**（窄复审）：D 真实后台成立；C 30 样本可信；B BLOCKED 合理（理由措辞待收紧）；CUDA provider 证据已落实；报告与 JSON 一致；总体 **FAIL/PARTIAL 保持不变**。
- 无 ADR / 协议未决项 ⇒ `SOL_NOT_REQUIRED`。
- 下一阶段仍为报告 §10 的最小修复卡（精确向量通道单点优化，同时服务 A 与 D）；本复审不启动任何后置任务、未重跑性能测试、未 push。
