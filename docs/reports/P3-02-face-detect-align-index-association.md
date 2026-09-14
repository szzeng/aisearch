# P3-02 — 人脸检测对齐、特征、候选索引、保守自动关联

**状态：PASS（B + D + C）**，执行于 2026-09-14。任务卡 [docs/tasks/P3-02.md](../tasks/P3-02.md)。

执行模型：**DS-H（DeepSeek V4.1 Flash / 高推理）**。本次会话未发生模型切换，也未声称发生过；客户端
未暴露 token/费用明细。本任务没有触发 C1/C2 升级：ONNX 运行时的 CUDA 分支在**本机 venv 内**不可用
（见 §14「环境事实」），但固定权重在 CPU 上稳定加载、真实夹具端到端全部通过，且冻结契约、五字段身份、
artifact/outbox 顺序、既有 index/地点/OCR 语义均未被改动，因此无需转 C1/C2。

起始基线：HEAD `34db5e3`（P3-01 报告提交），工作树干净。

## 1. 交付物与边界

新增：

| 文件 | 行数 | 作用 |
|---|---|---|
| `src/photo_search/face/contract.py` | 295 | 冻结身份、P0-04 权重镜像、**校准阈值**与推导公式；不做重依赖导入 |
| `src/photo_search/face/weights.py` | 196 | 固定 `det_10g.onnx` / `w600k_r50.onnx` 的大小 + SHA-256 校验（无任何回退） |
| `src/photo_search/face/engine.py` | 713 | SCRFD 解码、NMS、ArcFace 5 点相似变换、512 维特征、质量口径、设备/会话解析与缓存 |
| `src/photo_search/face/processor.py` | 1053 | `ProcessorSpec`、`face-document.v1` 产物、`face_id`、执行器、同事务持久化钩子 |
| `src/photo_search/face/index.py` | 741 | 独立候选索引（世代/outbox/entry/candidate）与纯函数式保守关联策略 |
| `src/photo_search/face/service.py` | 394 | 设备探测、能力快照/资格记录、入队/运行/索引/关联/端到端编排（无常驻线程） |
| `src/photo_search/face/cli.py` | 276 | `python -m photo_search.face.cli`（weights/status/enqueue/run/index/group/detect） |
| `src/photo_search/catalog/migrations/0010_face_core.sql` | — | 连续增量迁移，`schema_version 9 → 10` |
| `tools/diagnostics/face_smoke_fixture.py` | 586 | 真实夹具端到端 smoke + 阈值校准复测（`%TEMP%` 内，从不接触真实照片库） |
| `tests/{p3_face_fixtures.py, unit/test_p3_face_*.py, integration/test_p3_face_*.py}` | 2276 | 87 个新用例（contract 27、geometry 17、association 12、migration 6、cli 9、lifecycle 16） |

加性修改：`catalog/errors.py`（6 个 face 错误码）、`catalog/schema.py`（`SUPPORTED_SCHEMA_VERSION = 10`）、
`scheduler/engine.py`（同事务持久化钩子）、`scheduler/projection.py`（`face_summary`）、
`api/{models,routes}.py`（`/capabilities` 的 `faces` 段）、`tools/diagnostics/packaged_api_smoke.py`
（`coverage_faces_block`）、以及 4 处版本耦合的机械断言（见 §12）。

**未修改**：`0001`～`0009`、冻结 ADR 与 `docs/decisions/contracts/*.json`、ADR-0002/0003/0005 语义、
五字段 job 身份、lease/attempts/迟到提交、artifact/outbox 提交顺序、P2-03A/B 地点状态机与幂等、
P2-04 tokenizer 世代与 `place_text` 来源、P2-05 RRF/`hit_reason`、P2-06A/B 会话/profile/反馈、
P2-07 上传限制、P3-01 OCR 契约与产物形态、`geo/**`、`places/**`、`ingestion/**`、前端资源、打包入口
（`PhotoSearch.spec` 一字未改）、依赖（`uv.lock` 仍 95 包）。

## 2. 处理器契约（沿用 ADR-0003，不新造协议）

`face_processor_spec()` 是冻结外壳的实例化，`ProcessorSpec.from_envelope` 双向无损：

| 字段 | 值 | 理由 |
|---|---|---|
| `processor_id` / `spec_revision` | `face-model@1` / `face-buffalo-l-scrfd-arcface-v1` | 世代身份同时覆盖检测、对齐、特征与全部预处理 |
| `processor_kind` / `task_type` | `embedding_model` / `image_face` | 输出是 512 维人脸特征，不是文本或标签 |
| `device_policy` | `CPU_ALLOWED` | ADR-0001 §2 把检测/识别列为 CUDA 默认模型：默认走 GPU，显式 CPU 被服务并记作降级，GPU 故障不得变成隐式 CPU |
| `supported_devices` / `precisions` | `(gpu, cpu)` / `FP32` | 与 P0-04/P0-06 实测一致 |
| `space_id` | `insightface-buffalo-l:<w600k_r50 sha[:12]>` | 与图片空间 `chinese-clip-vit-b-16:<digest>` 不可能相等；换权重=换空间 |
| `dimension` / `metric` / `normalization` | `512` / `cosine` / `L2` | P0-04 manifest |
| `config_hash` | `sha256(canonical_json(ENGINE_CONFIG))` | 任何预处理/判定参数变化都会改变处理器身份，而不是悄悄改变语义 |
| `max_pixels` | `640×640` | 检测画布固定，与 P0-04 实测输入一致 |

**产物形态的一个明确决定**：冻结契约要求 `embedding_model` 的 `output_type = "embedding"`，但本任务发布
的是 **structured** 产物（`face-document.v1`），原因是 ADR-0003 的 `embedding` 产物类型是**资产级**的：它会
写入 `index_entries`/`index_outbox`（主键 `(space_id, asset_id, generation_id)`），即把人脸向量挤进图片
索引的资产身份里 —— 正是任务卡第 5 条禁止的复用。因此：一张资产一个结构化人脸文档（含每张脸的源坐标系
bbox、5 关键点、检测分、质量度量、质量判定与 512 维向量），规范向量在同事务写入 `face_embeddings`，独立
候选索引由 `face_index_*` 自己的一套 outbox/entry 驱动。产物确定性（无时间戳、无路径、无 job id），重放
会复用同一 artifact 行。

## 3. 权重校验

`contract.PINNED_WEIGHTS` 是 `tools/prepare_models/manifests/face-ocr.json` 的镜像，由
`tests/unit/test_p3_face_contract.py::test_the_pinned_table_mirrors_the_p0_04_manifest_exactly` 强制一致：

| role | 相对路径（`<data_root>/models` 下） | bytes | SHA-256 |
|---|---|---|---|
| detection | `face/buffalo_l/det_10g.onnx` | 16 923 827 | `5838f7fe…b5b91` |
| recognition | `face/buffalo_l/w600k_r50.onnx` | 174 383 860 | `4c06341c…19e43` |

缺件 → `FACE_WEIGHTS_MISSING`；同尺寸异字节 → `FACE_WEIGHTS_HASH_MISMATCH`；**没有任何** InsightFace 自动
下载或包内默认模型回退。`FaceEngineCache` 用「root + 两文件的 size/mtime」做键缓存已验证权重与会话，新
进程一律重新校验。受控入口在任何入队之前先证明权重（CLI `weights`/`enqueue`/`detect` 都先
`resolve_and_verify`），因此坏模型根是一次结构化失败而不是一队 BLOCKED 作业；已入队后权重才损坏的情况
会被设备探测记录成能力事实并把作业阻塞（§8）。

## 4. 检测、对齐、特征与质量口径

* **检测**：固定 640×640 画布（P0-04 实测输入）。大于画布的工作栅格等比缩小并居中；**小于画布时按原始
  像素居中粘贴**而不是放大 —— 实测证明把 112 px 紧裁人像放大到 640 会让 SCRFD 完全漏检（训练分布里人脸
  占画布比例有限），而保留原始像素 + 中性填充能稳定检出。九个输出按 stride 8/16/32 解出 12800/3200/800
  个锚点（每格 2 锚），距离→框/关键点乘 stride，再减填充偏移、除缩放比回到工作栅格坐标。
  阈值 `0.5`、NMS IoU `0.4`，排序用 `lexsort` 保证确定性。
* **对齐**：ArcFace 5 点模板 + 最小二乘 4 参数相似变换（无反射），112×112 双线性重采样；工作栅格长边上限
  1280（`WORKING_LONG_EDGE`），因此识别拿到的不是 320/448 px 网格缩略图。
* **特征**：`(RGB − 127.5) × 1/127.5` → `[1,3,112,112]` → `[1,512]`，L2 归一化；维度不符或零向量是结构化
  `RUNTIME_CAPABILITY_UNAVAILABLE`。
* **质量口径（三项，按优先级）**：`face_pixels`（识别帧内的可用短边，画布封顶后不再假装原始分辨率）、
  `blur_variance`（对齐 112×112 灰度图的 Laplacian 方差）、`det_score`。低于 `MIN_FACE_PIXELS=48` /
  `MIN_BLUR_VARIANCE=24` / `LOW_DETECTION_SCORE=0.65` 之一即 `LOW` 并记录 `quality_reason`
  （`SMALL`/`BLURRY`/`LOW_DETECTION_SCORE`）。真实夹具实测下界：`det_score 0.805`、`face_pixels 72.55`、
  `blur 92.56`。
* **有界**：声明像素 > `MAX_DECODE_PIXELS`（60 MP）先拒绝；人脸数 > `MAX_FACES`（200）是
  `FACE_RESULT_TOO_LARGE`；单图墙钟预算 `DEFAULT_DEADLINE_MS=120 s` 超时是 `RETRY_WAIT`（可重试、耗一次
  attempt），取消是 `CANCELLED`。
* **来源与内容守卫**：复用 P1-04 的受控读取 + P1-03 解码边界（格式门、EXIF 方向、JPEG 降采样），读取前后
  各做一次 stat 对照、并复核 SHA-256；`input_revision` 与 `assets.sha256` 不符即 `STALE_INPUT_REVISION`。

## 5. `face_id` 确定性

`face_id = sha256(canonical_json({spec_revision, input_revision, ordinal, bbox(2 位小数)}))`。

* 同一内容 + 同一检测/特征世代 ⇒ 同一批 id（重跑不产生第二套脸，也让关联天然幂等）；
* 不依赖数据库状态、job id、时间或路径；
* 产物校验会**重新推导**每个 `face_id` 并拒绝不匹配的文档（防止产物被篡改后写入"另一张脸")。

## 6. 迁移 0010 与表命名

连续增量 `0010_face_core.sql`（`schema_version 9 → 10`，`0001`～`0009` 逐字节不变）。表与职责：

| 表 | 职责 |
|---|---|
| `faces` | 当前检测事实（`face_id` PK、`asset_id`、`spec_revision`、`detector_id`、`ordinal`、`input_revision`、`location_id`、bbox/landmarks JSON、`det_score`、`face_pixels`、`blur_variance`、`quality_state/reason`、`artifact_id`、`face_revision`）；`UNIQUE(asset_id, spec_revision, ordinal)` = "重跑不产生第二套脸"的数据库事实 |
| `face_embeddings` | 规范 512 维向量（`PRIMARY KEY(face_id, processor_id)`，含 `space_id`/`dimension`/`metric`/`normalization`/blob/摘要） |
| `persons` | P3-02 的**未命名分组**（`name`/`name_key` 恒为 NULL）；`merged_into` 与 `name_key` 唯一索引已按架构 §4.2/§8.3.1 预置，P3-03 才写 |
| `face_person_links` | 每脸**至多一个有效归属**（`decision='CONFIRMED'` 上的偏唯一索引）+ 任意条 `SUGGESTED` 候选建议（**建议不是有效归属**，由 CHECK/索引保证而非约定）；`assignment_kind ∈ {AUTO, MANUAL}` 但本卡只写 `AUTO` |
| `face_association_queue` | 持久的"这批事实还需要一次保守关联"集合，与脸的事实**同事务**写入，由关联步骤消费 |
| `face_index_generations` / `face_index_entries` / `face_index_outbox` / `face_candidates` | **独立**人脸候选索引：自己的世代、自己的 outbox/entry、自己的候选行；与 `index_generations`/`index_entries`/`index_outbox` 无任何共享身份 |

数据库能表达的不变量全部下沉到 SQL 并逐条测试：sha256 形态的 `face_id`、`quality_state='LOW' ⇔ reason 非
NULL`、`SUGGESTED ⇔ reason 非空`、`CONFIRMED ⇔ confirmed_at 非空`、每 face 空间仅一个活动世代、
`merged_into <> person_id`、`(name IS NULL) = (name_key IS NULL)`、FK RESTRICT（脸被引用时不能删除）。

**重跑语义**：同一 `(asset_id, spec_revision)` 的行在每次提交中被替换（不再出现的脸被删除），因此"同一
asset 重跑不产生第二套脸"；`face_id` 不变的行在原位更新（先把待重写的行挪到临时 ordinal 区间再分配最终
ordinal，避免自碰 `UNIQUE`）。**人工优先的读取顺序已经成立**：`human_controlled_face_ids()` 每次提交都
读取 `face_assignment_controls.mode ∈ {MANUAL_ASSIGNED, MANUAL_UNASSIGNED}` 与 `face_exclusions.active=1`
（这两张表由 P3-03/P3-04 创建），命中的人脸既不会被自动改写，也不会在重跑中被删除。

## 7. 独立候选索引的身份与世代

* 索引键是 **`face_id`**，不是 `asset_id`：`face_index_entries(PK generation_id, face_id)`、
  `face_candidates(PK generation_id, face_id)`，事件 id 亦由 `face_id` 派生。图片索引的资产级主键语义
  完全未被复用，也没有把多张脸塞进一行。
* 世代 id `face-gen-<sha256(space+processor)[:24]>`，与 `image-gen-*` 分属不同命名空间；人脸世代切换不
  重算图文向量，反之亦然（`tests/integration/test_p3_face_lifecycle.py::test_the_face_index_never_touches_the_image_index`
  断言整轮人脸处理后 `index_generations/index_entries/index_outbox` 仍为 0 行）。
* 提交协议照抄 P2-03 的纪律：`Scheduler.submit` 在同一事务写事实 + `PENDING` outbox + `PENDING_REPAIR`
  entry；协调器在**事务外**发布候选行，再回到 SQLite 确认 entry/outbox；只有 `CONFIRMED` entry 才可能被
  当作候选。崩溃窗口具名注入（`failpoint="after_publish"`），重放会重发同样的字节并确认，候选行不会重复。
* 代表样本：`is_representative=1 ⇔ 质量 OK ∧ 已有有效归属 ∧ 位于该人物按 (face_pixels DESC, face_id) 排序
  的前 `MAX_REPRESENTATIVES_PER_PERSON=8` 名`。因此候选匹配是"对代表做增量线性扫描"，不是全量两两比较；
  归属变化会把受影响脸的 entry 降级为 `PENDING_REPAIR` 并重新发布（沿用 P2-03A「人工优先 → 机器产物追赶」
  的同一语义）。

## 8. 阈值校准证据

夹具（全部 `%TEMP%`，无任何真实私人照片）：`skimage` 自带 NASA 宇航员（公共领域）1 张、InsightFace 自带
六人合影 1 张（6 张脸）、InsightFace 自带 112 px 紧裁人像 1 张，共 **8 个真实身份**；同人变体由脚本合成，
异人即为 8 身份两两组合。

`uv run --no-sync python tools/diagnostics/face_smoke_fixture.py --device cpu` 会用**产品引擎本身**重测
下表，并断言 `contract.CALIBRATION` 与之相符（`recorded_calibration_matches=true`）：

| 分布 | n | min | p05 | p50 | p95 | max |
|---|---|---|---|---|---|---|
| 异人（28 对，含同图两人） | 28 | −0.0831 | −0.0608 | 0.0099 | 0.1479 | **0.2061** |
| 同人 · 整图光度变化后重新检测 | 40 | **0.8527** | 0.8720 | 0.9740 | 0.9927 | 0.9936 |
| 同人 · 对齐裁剪几何变化（旋转 12°／20% shear／镜像／28 px 低清） | 32 | **0.6433** | 0.6952 | 0.9216 | 0.9653 | 0.9740 |

推导（写进 `contract` 文档字符串，由单元测试复核）：

```text
gap     = min_genuine - max_impostor = 0.6433 - 0.2061 = 0.4372
MERGE   = max_impostor + 0.8 * gap   = 0.2061 + 0.3498 = 0.5559 -> 0.56
SUGGEST = max_impostor + 0.2 * gap   = 0.2061 + 0.0874 = 0.2936 -> 0.29
```

`MERGE_THRESHOLD = 0.56` 位于实测间隙的**上四分位侧**（不是通用常数 0.45，也不是区间中点），仍低于最弱
实测同人样本 0.6433；`[0.29, 0.56)` 一律只产出待确认建议。`NEAR_TIE_MARGIN = 0.08` 是阈值守卫而非校准值
（没有夹具产生近似并列），`tests/unit/test_p3_face_association.py::test_a_near_tie_between_two_people_is_not_merged`
用构造样本覆盖它。**年龄变化没有真实夹具**，因此它是明确的未验证项（§14），没有被任何数字假装覆盖。

## 9. 保守关联与冲突检查

`decide_for_face()` 是纯函数（12 个单元用例逐分支覆盖），`reconcile()` 只做编排：

| 输入情形 | 决定 |
|---|---|
| 与某人物相似度 ≥ MERGE 且领先第二名人物 ≥ MARGIN | `MERGE`（唯一有效归属） |
| 相似度 ∈ [SUGGEST, MERGE) | `SUGGESTED`（`BORDERLINE`） |
| 领先不足 `NEAR_TIE_MARGIN` | `SUGGESTED`（`NEAR_TIE`） |
| 质量 LOW（尺寸/清晰度/检测分） | 只可能 `SUGGESTED`（`LOW_QUALITY`）或"保持未归类"，**永不**成为有效成员 |
| 目标人物已出现在同一张照片（含拼图/镜子：同图两张近乎相同的脸） | `SUGGESTED`（`SAME_IMAGE_CONFLICT`），不自动合并 |
| 无候选或全部低于 SUGGEST | `NEW_GROUP`（新建 `name=NULL` 分组，confidence=0.0 表示"无相似度证据"） |
| 人脸已被人工控制（P3-03/P3-04 的表存在且有行） | `HUMAN_CONTROLLED`：不做任何写入 |
| 人脸已有有效归属（同内容重跑） | 跳过：**不会**产生第二个分组 |

同一张照片内先建立的归属会进入 `in_asset` 比较集，因此"把同一张脸贴两次"的拼图会被记录成
"1 个成员 + 1 条同图冲突建议"，而不是悄悄合并或凭空多出一个从未出现的分组
（真实夹具 `composite.png` 实测：`CONFIRMED 0.9944` + `SUGGESTED(SAME_IMAGE_CONFLICT) 0.9995`）。

## 10. 只读状态投影与隐私

`face_summary()`（`/api/v1/capabilities.faces` 与 `face.cli status` 共用）只输出计数/状态/阈值：
`state ∈ {NOT_RUN, LAGGING, BLOCKED, SYNCED}`、`library_assets`、`analyzed_assets`、`pending_assets`、
`stale_assets`、`assets_with_faces`、`faces`、`low_quality_faces`、`persons`、`confirmed_links`、
`suggested_links`、`manual_links`、`same_image_conflicts`、`associations_queued`、`degraded_jobs`、
`jobs`、`index{...}`、`thresholds{...}`。**没有** bbox、关键点、向量、单对相似度或任何路径；未分析 ≠
"照片里没有人"（`NOT_RUN`/`LAGGING` 与 `analyzed_assets` 分开表达）。单元/集成测试对投影序列化结果做
了路径与坐标字段的双重断言。

## 11. 实测命令与退出码

全部必需命令都在**沙箱外（提权）**执行：本会话受限令牌无法创建/删除 pytest basetemp
（`.pytest-tmp` 的 DACL 拒绝访问，即使先手工建目录也一样）。这是本机既有环境事实，不是本任务引入的
行为，前几个任务同样如此；uv 缓存始终使用项目内 `.uv-cache`。

| 命令 | 退出码 | 结果 |
|---|---|---|
| `uv run --no-sync pytest -q -p no:cacheprovider tests/unit tests/integration` | 0 | JUnit `tests=964 failures=0 errors=0 skipped=1`，297.6 s |
| 本任务新增用例 | 0 | 87 例（contract 27、geometry 17、association 12、migration 6、cli 9、lifecycle 16） |
| `uv run --no-sync ruff check .` | 0 | `All checks passed!` |
| `git diff --check` | 0 | clean |
| `uv run --no-sync uv lock --check` | 0 | `Resolved 95 packages`（依赖未变） |
| `uv run --no-sync python tools/diagnostics/face_smoke_fixture.py --device cpu` | 0 | `status=ok`、`problems=[]`；见 §8 与下表 |
| C：`uv run --no-sync pyinstaller --noconfirm PhotoSearch.spec` | 0 | 真实 onedir 重建，bundle 3.30 GB |
| C：`uv run --no-sync python tools/diagnostics/packaged_api_smoke.py --bundle dist/PhotoSearch --work-dir D:\PhotoSearchSmoke\P3-02-final --output D:\PhotoSearchSmoke\P3-02-report.json` | 0 | `status=ok`、`problems=[]`、`startup_seconds=1`、`coverage_capabilities_status=200`、`coverage_has_no_local_path=true` |

真实夹具 smoke（`%TEMP%`，6 张资产 / 12 张脸 / 8 个未命名分组）：

| 观察 | 值 |
|---|---|
| `astronaut.png` + `astronaut_bright.jpg` | 同一分组，`CONFIRMED 0.9723`（同人异光照） |
| `astronaut_small.jpg`（42% 缩放，38.6 px） | `LOW/SMALL`，`SUGGESTED(LOW_QUALITY) 0.7826`，**不**成为成员 |
| `composite.png`（同图两张同人） | `CONFIRMED 0.9944` + `SUGGESTED(SAME_IMAGE_CONFLICT) 0.9995` |
| `group.jpg`（六人合影） | 6 张脸 → 6 个不同分组（无一人并入宇航员） |
| `portrait.png`（112 px 紧裁） | 独立分组（验证"不放大"画布规则的正面证据） |
| 汇总 | `analyzed=6`、`faces=12`、`persons=8`、`suggested=2`、`same_image_conflicts=1`、`degraded_jobs=6`、`state=SYNCED` |
| 索引 | `state=READY`、`is_active=true`、`entries={CONFIRMED:12}`、`representatives=10`、`candidates=12` |
| 强制重跑同一内容 | 脸数/分组数/链数/artifact 行数与 id 集合**逐项不变**，作业行仍是 6（rewind 而非新增） |

C 级打包审计：`dist/PhotoSearch/_internal/photo_search/catalog/migrations/` 含 `0001`～`0010`（10 个文件），
且 `0010_face_core.sql` 的 SHA-256 与源码**逐字节一致**（`77E22325E80CE2AE0A09B41663D8C7CD110E73994CC84761D00FABCE9ED358D0`）；
`/api/v1/capabilities.faces` 在冻结包内返回真值（`state=NOT_RUN`、`weights_present=false`、
`thresholds.merge=0.56`），这是 `face.contract`/`face.weights` 与迁移 0010 真的在包内的行为证据。

## 12. D 级故障注入（具名场景）

| 场景 | 注入方式 | 观察结果 |
|---|---|---|
| 权重缺件 | 只复制 detection 图 | `FACE_WEIGHTS_MISSING`（受控入口 exit 1）；队列路径把 CPU/GPU 都记为不可用，作业 BLOCKED、`attempts=0`、零事实 |
| 权重哈希不符 | 同尺寸改 1 字节（recognition / detection 各一次） | `FACE_WEIGHTS_HASH_MISMATCH`；零事实、零 artifact |
| 租约到期 + 迟到结果 | 真实执行后跨过 60 s 租约再提交 | `STALE_LEASE`，零 artifact/零事实/零分组；随后接管 `attempts+1` 并正确落库 |
| 内容变化（读取前） | 作业绑定后改写文件字节 | `INPUT_UNSTABLE`/`FILE_READ_ERROR` → BLOCKED，该资产零事实（其余资产不受影响） |
| 内容变化（绑定后） | 重新扫描并提交旧 `input_revision` 的作业 | `STALE_INPUT_REVISION` → FAILED，零事实 |
| 来源不可用 | 位置被目录册标记 `MISSING`（字节仍在盘上） | `SOURCE_FILE_MISSING` + `blocking_reason=SOURCE_UNAVAILABLE` → BLOCKED，零事实；同源其他资产不受影响 |
| 重跑同一内容 | `detect --force` | 脸/分组/链/artifact 与 id 集合逐项不变，作业行不增加 |
| 候选索引写而未确认 | `failpoint="after_publish"` | entry 仍 `PENDING_REPAIR`、候选行已发布；关联步骤 `deferred=6, processed=0`；重放 12 个事件后 entry 全 `CONFIRMED`、候选行数仍 12（无重复） |
| 人工优先 | 手工创建 P3-03/P3-04 的两张人工表 + MANUAL 归属 | 自动路径报告 `HUMAN_CONTROLLED`，MANUAL 链保持；再次强制重跑也不删除该脸 |
| 显式 GPU 请求 | `--device gpu`（本机 CUDA 无法初始化） | 作业 BLOCKED、`blocking_reason=RUNTIME_CAPABILITY_UNAVAILABLE`、**零事实**，绝不隐式转 CPU |

## 13. 修复轮次（3 个产品缺陷 + 若干测试夹具修正）

1. **画布填充偏移未回减**（真实缺陷）。首版把小于 640 的栅格按原始像素居中粘贴，却在解码时只除缩放比、
   没减填充偏移，导致对齐裁剪采到图像外的黑区：`blur_variance=0`、所有这类脸的 512 维向量退化成同一个
   常量（异人相似度出现 1.0），于是六个陌生人和宇航员被并成一组。修复：`build_canvas` 返回
   `(blob, scale, offset)`，`decode_detections` 用 `(坐标 − offset)/scale` 回到栅格坐标系。
2. **锚点网格顺序错误**（真实缺陷）。`np.mgrid[:h,:w][::-1].reshape(-1,2)` 得到的是"先所有 x 再所有 y"，
   不是交错的 `(x,y)` 对；首轮探针因此检出满图错框（宇航员 bbox 越过图像下边界）。修复为
   `np.stack(np.mgrid[...][::-1], axis=-1)` 后再 reshape；修复后 bbox 与 P0-04 记录
   `[180.91, 59.41, 270.49, 179.11]` 完全一致（外部证据对齐）。
3. **设备探测会被权重错误打断**（真实缺陷）。`FaceEngineCache.attempt()` 用 `verified_weights()` 计算缓存
   键，权重缺件时异常直接逃出探测。修复：缓存键改用未校验的 `resolve()`，坏权重集合成为**被记录的能力
   事实**（provider unhealthy）而不是异常。
4. 测试夹具修正：`%TEMP%` 写文件后打时间戳的顺序、`jobs` 查询需带 `task_type`（否则会读到 `catalog_scan`
   作业）、跨身份样本的 IoU 匹配在镜像下失效（改为"整图光度变化"与"对齐裁剪几何变化"两族分开测量）、
   `build_canvas` 居中偏移的期望值、`availability` 只有 `PRESENT/MISSING` 两种取值。

## 14. 环境事实与未验证项

**环境事实（不是本任务引入）**：本机 venv 内 `onnxruntime.get_available_providers()` 列出
`CUDAExecutionProvider`，但创建 CUDA 会话失败（`cublasLt64_13.dll` 不在 DLL 搜索路径上，
`session.disable_cpu_ep_fallback=1` 因此直接报错，**不会**退化到 CPU）。打包入口
`PhotoSearch.spec` 已把 `cublas/cudnn/...` 复制成 `nvidia_dlls/`，P0-06 也是在打包产物上验收了
RTX 4060 的 CUDA 路径；`face/engine.py` 已按同一机制在 CUDA 分支调用 `preload_runtime_dlls()`。**但本任务
没有在 GPU 上跑过人脸推理**，因此 GPU 执行仍是未验证项（见下）。

未验证项（不阻塞本任务，但必须如实记录）：

* **真实 GPU 推理**：人脸检测/识别的 CUDA 会话在本机 venv 不可用、在打包产物上也未触发（打包 smoke 的
  data root 没有固定权重，`weights_present=false`）。若要现在就拿到 GPU 证据（含真实 GTX 1660 6 GB），
  建议手动切 **C1 / GPT-5.6 Terra / medium** 做一次 GPU/DLL bring-up 验证。
* **年龄变化、遮挡/口罩/眼镜、强侧脸、低照度**：没有真实夹具。侧脸只用了对齐裁剪的合成 yaw/roll 代理
  （12° 旋转 + 20% shear），低清只用 28 px 上采样代理；这些样本的最差相似度 0.6433 已计入阈值下界，但
  不能当作真人年龄/遮挡覆盖。
* **聚类纯度与真实家庭照片库规模**：夹具只有 8 个身份、12 张脸；候选匹配是代表线性扫描（≤8/人），
  **大库（万级脸）的延迟与代表上限调参未测**；没有做纯度/召回指标评估。
* **多人同图上限**：`MAX_FACES=200` 只在小夹具验证；未在整个画面都是小脸（如合影墙）的真实照片上测。
* **世代切换**：`faces.spec_revision` 已按世代隔离，但"模型升级后新旧脸映射/待确认锚点"（架构 §8.3.4 末段）
  属于 P3-04/P4，本卡未实现，也未注入过真实换权重场景。
* **质量度量口径**：只有尺寸、Laplacian 方差与检测分；没有姿态角/模糊模型的真实标定，阈值仅由 8 个真实
  脸的下界（72.55 px / 92.56 / 0.805）推出安全余量。
* **候选索引不是向量库**：本构建的人脸候选索引是 SQLite 派生表（可重算、可确认），不是 LanceDB/ANN；
  若未来要求 ANN 或跨进程并发写，需要新的架构决策。
* **跨进程并发、取消抢占式强杀、`face_revision` 单调性的规模效应**未测；取消是"下一个检查点停下"而非抢占。
* **数据集样例窗口**：为保持产品自带的地点样例可用，`tools/places/examples/mini-cities.{spec,manifest}.json`
  的 `compatible_catalog_schema.maximum` 由 9 机械更新为 10（用项目自带 `tools/places/manage_dataset.py build`
  重新生成 manifest，语义未变）。

## 15. 交接记录（策略 §8.4）

```text
任务 ID / 状态：P3-02 / PASS（B + D + C；3 个产品缺陷修复轮次）
实际模型 / reasoning / 速度：DS-H（DeepSeek V4.1 Flash / 高推理）按任务卡推荐执行；客户端未暴露 token/费用明细，未做任何切换声明
基线提交号：34db5e3（P3-01 报告提交），工作树干净
已完成行为：固定 buffalo_l 检测/识别权重校验；SCRFD 检测（640 画布、固定锚点/阈值/NMS、小于画布不放大）、ArcFace 5 点相似对齐 112×112、512 维 L2 特征与三项质量口径；确定性 face_id；迁移 0010（faces/face_embeddings/persons/face_person_links/face_association_queue + 独立 face_index_* / face_candidates）；同事务事实持久化钩子；独立人脸候选索引（自己的世代/outbox/entry，候选只认 CONFIRMED）；按真实夹具校准的保守关联（MERGE 0.56 / SUGGEST 0.29 / 冲突与低质量只出建议）；只读状态投影接入 /api/v1/capabilities 与 CLI；受控 python -m photo_search.face.cli
修改文件与接口/迁移变化：新增 src/photo_search/face/**（8 文件）、catalog/migrations/0010_face_core.sql、5 个测试文件 + 共享夹具、tools/diagnostics/face_smoke_fixture.py；加性改 errors/schema/scheduler.engine/scheduler.projection/api.models/api.routes/packaged_api_smoke；schema_version 9→10；未改 0001～0009、冻结 ADR/JSON schema、geo/places/ingestion/前端/打包入口/依赖（95 包）
实际命令和结果：pytest 964 例 0 失败 1 跳过（exit 0，JUnit 297.6s）；本任务新增 87 例；ruff/diff-check/lock 全 clean；真实夹具 smoke status=ok problems=[]（6 资产/12 脸/8 组/2 建议/1 同图冲突/索引 READY+active）；真实 onedir 重建 3.30 GB + packaged_api_smoke status=ok problems=[] startup_seconds=1，/capabilities 200 且 faces 段真值，bundle 含 0001～0010 且 0010 与源码 SHA-256 一致
报告/详细日志路径：docs/reports/P3-02-face-detect-align-index-association.md；JUnit test-results/junit.xml；打包 smoke D:\PhotoSearchSmoke\P3-02-report.json
未验证项和具体阻塞：真实 GPU 人脸推理（本机 venv CUDA 会话不可创建；打包产物已带 nvidia_dlls 但本次未触发）；真实年龄变化/遮挡/眼镜/强侧脸/低照度；真实家庭照片库规模、聚类纯度与代表上限调参；候选索引 ANN/跨进程并发；世代切换后的新旧脸映射；无阻塞
失败修复轮次：3 个产品缺陷（画布偏移、锚点网格顺序、探测被权重错误打断）+ 若干测试夹具修正
下一任务及必须读取的文件：P3-03（人物名称、NFC name_key、同名合并、旧 ID 重定向），任务卡 docs/tasks/P3-03.md，推荐 C2 先冻结规则 → DS-H 实现 → C2 审查；需读 AGENTS.md、docs/development-status.md、本卡、主架构 §4.2/§8.3.1～§8.3.5/§11.2/§12.4、策略 §3/§7/§8/§11/§12
独立审查者与结果：本任务不需要独立审查（B+D+C 自证；GPU 项建议后续 C1）
```

## 16. 下一任务

**P3-03 — 人物名称、NFC `name_key`、同名合并、旧 ID 重定向**，任务卡
[docs/tasks/P3-03.md](../tasks/P3-03.md)。推荐 **C2 / GPT-5.6 Sol / high** 先冻结命名与合并规则，
再由 **DS-H** 实现，最后交 **C2** 审查；验收 **B + D**。模型切换必须由用户在 Codex 中手动完成。
