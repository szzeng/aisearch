# P4-02N / O / Q / S 独立 DS-H 完整受影响面审查

**审查方式**：**完整受影响面工程主审**（V3.1 §2.4）。四卡都改了 `src/**` 的真实启动接线、语义建库入口
与媒体格式契约，不满足容量/展示/打包三类"专项审查"的仅测量/仅展示/仅打包前提，因此按完整主审执行：
逐项覆盖全部改动文件、直接调用链、契约差异与必需测试证据。

**结论：不通过（NOT PASS）。**

- P4-02O（含 P4-02Q 面）**不通过**：交付的 `build --source-id` 在产品 CLI 上必然抛
  `sqlite3.OperationalError: near "WHERE": syntax error`（**F1**，必修）。
- P4-02S **不通过**：上传闸门仍拒 MPO 而库内 MPO 已受支持（**F2**，需裁决），且格式策略变更没有进入任何
  版本化身份，已装库仍复用过期失败缓存（**F3**，需裁决/至少写明升级口径）。
- P4-02N **产品面通过**：接线、能力诚实、无权重分支逐字不变三条都独立复现；但它的改动使人工冒烟清单
  `docs/diagnostics/search-smoke-checklist.md` 的"本进程没有查询/图像编码器"变成过时断言（**F4**，文档必修）。

本阶段**不需要 DS-MAX**（V3.1 §2.2 六类条件逐条不成立，见 §5），也**不需要 Sol 聚焦终审**
（未新增冻结协议/ADR/数据安全未决项；若 F2 选择放宽 P2-07 上传白名单，那是一次新的契约决定，另行路由）。

**审查基线**：工作树 `549075f` + 未提交在制品（`docs/development-status.md`、6 个产品文件、4 个测试文件、
7 个诊断工具、7 个报告/卡）。本次审查**未修改任何产品代码、契约、依赖或 schema**。

## 1. 改动面与实际核对内容

| 文件 | 归属 | 本次核对 |
|---|---|---|
| `src/photo_search/workers/query_encoder.py`（新增） | P4-02N | 逐行读；spec 推导、worker 握手/空间校验、单请求锁、上传暂存、关闭路径 |
| `src/photo_search/__main__.py` | P4-02N | diff 逐行；`_encoder_wiring`/`_default_model_dir`/新 CLI+环境变量/启动行 |
| `src/photo_search/api/context.py` | P4-02N | diff 逐行；`query_encoder()` 惰性解析与 `close()` 的鸭子类型停机 |
| `src/photo_search/indexing/semantic.py`（新增） | P4-02O/Q | 逐行读；目标谓词、幂等 job、执行器、runtime 证据、drain、profile 引导 |
| `src/photo_search/indexing/cli.py` | P4-02O/Q | diff + 新增 `build` 子命令全部选项与校验分支 |
| `src/photo_search/media/contract.py` | P4-02S | diff；`MPO` 从拒绝集移入受支持集的实际影响面 |
| `tests/unit/test_p4_02*.py`、`test_media_decode.py`、`test_p4_heic_media.py` | 三卡 | 读用例并独立重跑；另核对 JUnit 计数 |
| `tools/diagnostics/p4_02n_*.py`、`p4_02o_semantic_build.py`、`p4_02s_self_retrieval.py` | 三卡 | 读驱动实现并独立重跑其中三条 |

## 2. 独立复现（本审查自己跑的命令）

| 命令 | 结果 |
|---|---|
| `pytest -q tests/unit/test_p4_02n_query_encoder_wiring.py tests/unit/test_p4_02o_semantic_build.py tests/unit/test_p4_02s_mpo_first_frame.py tests/unit/test_p4_heic_media.py tests/unit/test_media_decode.py` | **66 passed / 0 failed**（`test-results/p402-review-unit.xml`） |
| **B 级** `pytest -q tests/unit tests/integration` | **1773 / 0 failed / 0 errors / 48 skipped**（338.9 s，`test-results/p402-review-full.xml`） |
| `ruff check .` / `uv lock --check` / `git diff --check` | exit 0 / exit 0（Resolved 96 packages）/ exit 0 |
| `p4_02n_real_search.py --model-dir <登记权重> --device cuda --keep` | 7/7、`status: pass`（`test-results/p402-review-n-real-search.json`） |
| `p4_02n_no_model_startup.py --data-root tmp/p4-02n-real/PhotoSearchData --port 8129` | 7/7、`MODEL_DIR_MISSING`、文本/上传两处 409 逐字一致（`test-results/p402-review-n-no-model.json`） |
| `p4_02o_semantic_build.py --per-family 6 --device cuda --keep` | 13/13、`passed: true`（`test-results/p402-review-o-semantic-build.json`） |
| 自建 4 帧 MPO 探针（本审查自写，与单测夹具不同形状） | 解码结果 = 第 0 帧（均值 240/10/10），≠ 第 1–3 帧；`image_eligibility=(True,'MPO')` |
| `python -m photo_search.indexing.cli build --dry-run`（无 / 有 `--source-id 1`） | 无：exit 0；有：**exit 1 + `sqlite3.OperationalError: near "WHERE": syntax error`** |
| 只读核对登记库（`file:…?mode=ro&immutable=1`，不打开任何照片文件） | assets 3658、PRESENT locations 3682、CONFIRMED 3179、pending 479、jobs 479 FAILED + 3179 SUCCEEDED、重复 sha256 组 **0** |
| 全部相关 JUnit 计数复核 | N 13/17/1761、O 5/59/1766、Q 21/8/1769、S 24/45/1773 —— 与四份报告逐个一致 |

命令细节：`UV_CACHE_DIR=.uv-cache`、`uv run --no-sync`、项目内 `.pytest-basetemp-p402-review/*`
（受限令牌下 `--basetemp` 会 WinError 5，按仓库既有正常权限路由，未改 ACL）。
本轮**没有**重跑真实图库步骤（缩略图/自检索/12 条查询/真实库建库），理由见 §6。

## 3. 卡面不变量逐项核对

### 3.1 P4-02N

| 不变量 | 核对结论 |
|---|---|
| 能力诚实（有/无编码器） | 有权重 `query_encoder_available=true` + 真 200；无权重 `false` + 两条既有 409，逐字与 P2-07/P2-08 记录一致（§2 复现） |
| 依赖方向（ADR-0003 §7） | `workers/query_encoder.py` 不 import ONNX/PyTorch，只经 `AiWorkerClient`；`__main__.py` 原本已 import 该 client，新增 import 只是协议与 `ProcessorSpec`，**没有把重框架拉进业务进程** |
| 一次编码、续页复用 | `search/service.py`、`search/sessions.py` 零 diff；`encode_query` 仍逐次校验 binding，编码点未新增 |
| 无权重分支逐字不变 | `models/` 未创建、无 traceback、两条 409 逐字一致（§2 复现） |
| 资源与代码分开 | 两条诊断都设 `HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE`；本卡不下载任何东西 |
| 空间不被发明 | `pinned_embedding_spec()` 与建库侧同一函数；worker 报告的 space/dimension 不一致即 `SPACE_MISMATCH` 且不泄漏 worker 进程（单测覆盖） |

### 3.2 P4-02O（含 P4-02Q 面）

| 不变量 | 核对结论 |
|---|---|
| 空间来自 manifest | ✅ 与查询侧同函数；真实闭环 `processor_id=…@36e679e65c2a`、`space_id=…c8ac42d3907c57b0`、512 |
| 设备如实记录 | ✅ `adapter_runtime_facts` 读的是真实加载后的 adapter（provider/precision/包版本）；profile 激活门复读同一批事实 |
| 增量按内容修订、重复运行幂等 | ✅ 目标谓词 = 无"当前 `sha256` 对应的 CONFIRMED 条目"；`jobs.enqueue` 是 `ON CONFLICT DO NOTHING` 五字段逻辑键；真实第二次 pass `targets=0` |
| 单点错误不中断 | ✅ 执行器把缺失/失败收敛为 `SOURCE_FILE_MISSING`/`RUNTIME_RESOLUTION_MISMATCH` 并让其余 job 继续（单测覆盖），真实库 479 非图片不阻塞 3179 图片 |
| 零契约改动 | ✅ artifact 仍是 `embedding.v1` + `payload={"vector":…}`（与 `capacity_validation.embedding_artifact` 同形）；`search_profiles` INSERT 与 `0007` DDL 列名列序逐字段对齐；outbox/generation/profile 门与错误码未改 |
| 不越界改查询侧 | ✅ 只 import `pinned_embedding_spec`（只读），未改 `workers/query_encoder.py`、`search/**`、`indexing/{store,maintenance}.py` |
| `build --source-id`（卡面允许面） | ❌ **F1：恒失败** |

### 3.3 P4-02Q

| 不变量 | 核对结论 |
|---|---|
| 只挑媒体层判定可解码的图像 | ✅ `image_eligibility` 走 `plugin_format_decision` + `sniff_format/read_header` 兜底 + `ensure_heif_support` |
| 非图片不计为失败 | ✅ 进 `out_of_scope`（数量 + 格式直方图 + 抽样），不建 job；真实库 479 非图片 |
| `--retry-failed` 把 FAILED 重置为 QUEUED 且幂等 | ✅ `_rewind` 与 face 处理器同形（同 REWINDABLE_STATES 集合）；真实库 HEIC 1 条回退后转 CONFIRMED，第二次 `rewound=0` |
| 只读边界 | ✅ 未改 `media/**`、schema、依赖；`indexing.cli reexecute` 的遗留未修项如实列出 |

### 3.4 P4-02S

| 不变量 | 核对结论 |
|---|---|
| MPO 按首帧放行（缩略图/预览与语义探测同时） | ✅ 契约/SPEC/语义探测三处一致；本审查的 4 帧独立探针确认取第 0 帧而非平均值/末帧 |
| 客观判据可失败 | ✅ 单测故意让第 2 帧颜色不同，取错帧必然触发 `far` 断言；本审查探针同样可区分 |
| RAW 保持拒绝 | ✅ 只读核对：库内 36 个"可搜不可显示"恰为 35×`.cr2` + 1×`.dng`；`p4_02s-decode-matrix.json` 中 7 个抽样全部 `TIFF/decodable=False`、`FORMAT_UNSUPPORTED`，`adapter_decode=ok`（如实"拒绝而非不能解"） |
| 覆盖率口径自洽 | ✅ 3658 = 3143 可显示可搜 + 36 RAW 可搜不可显示 + 479 非图片；缩略图产物 2800 published + 858 cached failures(479 DECODE_FAILED + 379 FORMAT_UNSUPPORTED) 与之独立吻合 |
| 质量冒烟口径 | ✅ 自检索 26/26 自身命中，摘要与产物一致；12 条查询只作延迟/命中存在性；无人工标注 ⇒ 未做相关性结论（如实） |
| 不引入删除/屏蔽已发布向量 | ✅ 检索契约零 diff |
| 上传查询口径 | ❌ **F2：MPO 仍被上传白名单拒绝，且报告把被拒样本归因给 RAW** |
| 失败缓存失效 | ❌ **F3：格式策略变更没有版本化，旧库靠人工 `--retry-failed`** |

## 4. 发现

### F1（P4-02O，必修，阻塞 PASS）`build --source-id` 恒抛 SQL 语法错误

- 证据：`semantic._TARGET_SQL` 把 `{source_filter}` 放在 `JOIN source_roots …` 与 `WHERE NOT EXISTS (…)`
  之间，而 `semantic_selection` 传入的文案本身就是 `"WHERE f.source_id = ? "` ⇒ 生成
  `… f.source_id WHERE f.source_id = ? WHERE NOT EXISTS (…)` 两个 WHERE。
  产品 CLI 复现：`build --dry-run --source-id 1` → `sqlite3.OperationalError: near "WHERE": syntax error`，
  exit 1（无结构化错误）；不带该选项同一命令 exit 0。
- 影响：卡面允许且在 `build --help` 里宣传的"按来源限定建库"**完全不可用**（4 个来源根的库无法分批建库）。
  失败发生在写库之前，**无数据风险**，但属交付面缺陷；现有单测/驱动都没覆盖该选项。
- 闭合口径：把来源谓词并入同一个 WHERE（例如 `WHERE f.source_id = ? AND NOT EXISTS (…)`）；补单测
  （该 source 的目标被保留；不存在的 source ⇒ 0 目标且不报错）与一条真 CLI dry-run 产物；
  修后**只做有界复审**（该 SQL、新增测试、点名集成回归），不重推其余已核对面。

### F2（P4-02S，需裁决）上传闸门仍拒 MPO，且报告归因错误

- 证据：`search/upload.py` 的冻结白名单 `UPLOAD_INPUT_FORMATS = {JPEG,PNG,WEBP,BMP,GIF}`；
  本审查探针 `stage_upload(<4 帧 MPO>)` → `FORMAT_UNSUPPORTED {"format":"MPO"}`。
  只读核对自检索产物：3 个 `FORMAT_UNSUPPORTED` 的资产后缀**全是 `.jpg`**（即 MPO），
  不是 `.cr2/.dng`；该轮抽样的 RAW 一个都没被拒（`.cr2`/`.dng` 全库仅 36 个，且大多在 55 张超限样本里）。
- 影响：(a) P4-02S 报告 §4.1 的"3 × FORMAT_UNSUPPORTED（RAW 不能作上传查询）"**归因错误**；
  (b) 用户用手机 MPO（本库约 343 张，占 `.jpg` 约 11%）做"上传搜图"会被拒，而同一构建的网格显示与语义索引
  都支持它 ⇒ 三种口径不一致。
- 闭合口径（二选一，写进报告/状态）：①保持 P2-07 白名单不变，明确"MPO/HEIC 只能被索引，不能作上传查询"，
  并修正 §4.1 归因；②按 P2-07 附加协议变更放宽白名单（需更新 P2-07 静态/集成断言 + 契约流程，另立卡）。

### F3（P4-02S，需裁决/至少写明升级口径）格式策略变更没有版本化，旧库复用过期失败缓存

- 证据：`THUMBNAIL_PROCESSOR_ID` 仍为 `catalog-thumbnail@1`；`media/pipeline.py` 用
  `cache.read_failure(spec=spec, sha256=…)` 命中缓存即 `CACHED_FAILURE`。P4-02S 的**不带** `--retry-failed`
  那次 `catalog thumbnail` 报告 `generated=0`、`skipped_cached_failures=858`（其中 379 条是
  `FORMAT_UNSUPPORTED`），**没有任何一条被重新判定**；`--retry-failed` 后才 +343、unsupported 379→36。
- 影响：MPO 显示能力只在"人工跑过 `--retry-failed`"的库里生效；`/capabilities` 已经宣告 MPO，
  但已装库的派生状态不会自动失效。报告把它记成一次性操作，没有写成升级动作，也没有决定是否 bump 处理器标识。
- 闭合口径：明确"媒体格式策略属于处理输入"的失效口径 —— 要么在打包/升级卡里决定 bump
  `catalog-thumbnail@N`/`catalog-preview@N`（自动失效旧失败缓存），要么把
  `catalog thumbnail --retry-failed` 写进升级步骤并在报告/状态提示。

### F4（P4-02N，文档必修）人工冒烟清单仍断言"本进程没有查询/图像编码器"

- 证据：`docs/diagnostics/search-smoke-checklist.md` 第 21、42、60 行仍写
  "本进程**没有**查询编码器（与打包应用一致）"/"本构建没有图像编码器"/"本构建的 API 进程没有查询/图像编码器"；
  而真实启动在有登记权重时 `query_encoder_available=true`（§2 已复现）。
  `tools/diagnostics/packaged_api_smoke.py` 的 `search_text_without_encoder` 与
  `coverage_search_block.query_encoder_available is False` 断言只在"夹具数据根没有模型"时成立。
- 影响：人工冒烟与打包验收文档会把"有权重时应能搜"误判成异常，或反过来漏测有权重分支。
- 闭合口径：清单改成条件式两分支（有权重/无权重）；打包卡的断言写明前提"数据根无模型"。

### F5（P4-02P/Q，低，证据可追溯性）"修后 dry-run 479（339+140）"没有落盘产物

- 证据：`test-results/` 内唯一的该步骤产物是 `p4-02q-build2.json`：`checked=487`、
  `out_of_scope=486`（`ISO-BMFF` 343 + `UNIDENTIFIED` 143）、`targets=1`（那次重跑不是 dry-run）。
  本审查只读复核当前库：pending 479、`jobs` 恰好 479 FAILED + 3179 SUCCEEDED（没有第 7 组多余 job 行），
  缩略图产物 479 DECODE_FAILED + 36 unsupported 独立吻合 ⇒ **479 是对的**，但引用的那条 dry-run 产物不存在。
- 影响：结论数字正确却无法从产物复算；`486 → 479` 这 7 行差异也没有解释（最可能是活跃图库在两次测量之间
  的文件增删/重写，未留证据）。
- 闭合口径：把 `build --dry-run` 的 JSON 落 `test-results/`（只读现有库即可，不需要新扫描），
  或在该报告写明差异来源。

### F6–F9（低，报告/措辞与边界，无产品风险）

- **F6**：`enqueue_targets` 的 `queued` 统计"尝试数"而非实际插入数，`live` 恒为 0
  （face 处理器有 `_is_live` 检查，语义侧没有）。P4-02P 首次 `queued=3658` 恰好等于真实插入，故未暴露。
- **F7**：`--retry-failed` 帮助与卡面写"rewind FAILED jobs"，实现按 face `_rewind` 的形状同时回退
  SUCCEEDED/CANCELLED/BLOCKED/RETRY_WAIT。有先例、可接受，但措辞应与行为一致。
- **F8**：`build_index` 的调度循环上界 `steps >= len(targets)` 与 drain 上界 `len(targets)+16`：
  若存在上轮遗留的可运行 job 或大量未确认 outbox 事件，单次 build 可能只完成一部分，需要再跑一次或
  `indexing.cli drain`。不产生错误状态，但属应写明的边界。
- **F9**：P4-02N §4 的"建库"用的是冻结 P2 夹具 `p2_search_fixtures.submit_vector`（伪 `rev-<asset_id>`
  输入修订），不是产品建库入口；只有 P4-02O §6 披露了该交互，N 报告措辞应点明。

## 5. 路由判断

- **DS-MAX（V3.1 §2.2）**：六类条件逐条不成立 —— 无"跨模块异常且无法定根"（F1 根因是单行 SQL 拼接）；
  无 transaction/revision/undo 矛盾；测试与 ADR 不冲突（F1 是普通缺陷，测试未覆盖而非"测试对不上 ADR"）；
  无二次/三次回归；diff 规模与状态机耦合远低于"需要一次很长整体推演"；Sol 未提出问题。⇒ **不升 max**。
- **Sol 聚焦终审（§2.3）**：这四卡不在 Sol 必选清单（P4-04/05A/05/08 等），本次也没有形成冻结 §11 协议/
  ADR/数据安全未决项 ⇒ **`SOL_NOT_REQUIRED`**。注意：F2 若选择放宽 P2-07 上传白名单，那是**新的契约变更**，
  必须另走契约与审查流程，不在本审查结论内。
- **逐卡判定**：P4-02N 产品面 `ENGINEERING REVIEW PASS`（条件：F4 文档修正 + F9 措辞）；
  P4-02O 与 P4-02Q `REVIEW FAIL`（F1）；P4-02S `REVIEW FAIL`（F2/F3 需裁决）。
  四卡**都不得记为 PASS**，也不得据此生成后续实现卡。

## 6. 未验证与边界

- **未重跑真实图库步骤**（缩略图/自检索/12 条查询/真实库建库）：没有新授权，且 §2.4 允许沿用可核对产物。
  本审查改用**只读库核对**（不含打开照片文件）替代，结论与产物口径一致。
- **未重建打包产物、未跑 `packaged_api_smoke.py`**：C 级验收不在本审查范围 ⇒ GTX 1660、§12.2 门槛、
  打包产物仍未验证（与四卡报告一致）。
- **上传/HEIC/MPO 的完整 HTTP 端到端**未跑：F2 只在 `stage_upload` 边界层取证（该层即白名单判定点）。
- 本审查复现的产物：`test-results/p402-review-unit.xml`、`p402-review-full.xml`、
  `p402-review-n-real-search.json`、`p402-review-n-no-model.json`、`p402-review-o-semantic-build.json`
  （按 `.gitignore` 不入库）。

## 7. 建议的唯一下一步

先做 **F1 的有界修复**（一张最小卡：来源谓词并入同一 WHERE + 单测正/负例 + 一条真实 CLI `--dry-run`
产物），修完只复审该 SQL 与点名集成回归；F2/F3/F4/F5 是裁决与文档项，建议与 F1 同批落文档
（F2 的"放宽白名单"分支则必须单独走 P2-07 契约流程）。**不接管实现修复**，也不预生成后续卡。
