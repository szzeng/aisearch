# P4-02T 独立 DS-H 完整受影响面审查

**审查方式**：完整受影响面工程主审（V3.1 §2.4）。本卡改的是**冻结 P2-07 断言**与产品解码路径，属协议面，
因此不适用"仅打包/仅展示/仅测量"的专项审查，按完整主审执行：逐项覆盖全部改动文件、直接调用链、契约差异与必需证据。
**用户明确要求本次主审在本会话完成**（实现与复审同一执行者），因此按"完整受影响面 + 有界复审"记账，并在 §4 注明这一点。

**结论：`ENGINEERING REVIEW PASS`（产品面）** —— 无阻塞发现；两处文档问题（R1/R2）已在本轮修正；审查 F2 闭合、F3 已裁决（§5）。
本卡**不涉及**性能/打包/§12.2，不得当那些 PASS 使用。

## 1. 改动面（逐文件核对）

| 文件 | 改动 | 核对 |
|---|---|---|
| `src/photo_search/search/upload.py` | 白名单显式扩为 8 项；`_decode_upload` 先注册 pillow-heif 再 `Image.open`；`exif_transpose` 前调用 `restore_recorded_orientation`；`upload_refused_families()` 归一为 `refused_input_formats()`；docstring 第 3 条按 R1 更正 | 逐行读；顺序、错误码、上限分支未变 |
| `src/photo_search/media/decode.py` | `_restore_recorded_orientation` → 公开 `restore_recorded_orientation`（唯一调用点同步更新） | 2 行改动，语义不变；全仓无残留旧名 |
| `tests/unit/test_p4_02t_upload_formats.py`（新增） | 9 例：白名单字面/拒绝集关系、MPO 首帧、HEIC 方向 6/8×容器两种形状、EXIF-only 控制例、坏 HEIC→`DECODE_FAILED`、TIFF 仍拒 | 读用例；控制例确实在无归一行为下会失败 |
| `tests/unit/test_p4_heic_media.py` | 唯一字面 5 项断言改为新口径并断言 HEIC 上传可用 | 显式修订，非删除覆盖面 |
| `tests/unit/test_p2_search_upload.py` | "已识别但拒绝"的示例由 HEIF 换成 AVIF（不变量保留） | 与格式集解耦 |
| `tests/integration/test_p4_heic_pipeline.py` | "HEIC 上传必须 409"改为"现在 200 + 直立尺寸 + 向量通道 engaged" | 真实 HTTP 样本随卡修订 |
| `tools/diagnostics/p4_02t_upload_formats.py`（新增） | 合成库 + 真实模型 + 产品 CLI build + 三个上传探针 | 读驱动；本轮把内联 `__import__` 改为顶部 import |
| `docs/tasks/P4-02T.md`、`docs/reports/P4-02T-upload-format-policy.md` | 卡与报告 | 数字与产物逐一核对（见 §2.3） |

未改（经 diff 核对）：schema/迁移/依赖/`uv.lock`；API 信封/游标/错误码/`total`/RRF/BM25/距离全序/权威补查/发布资格/
outbox 写入与提交协议/调度；媒体契约的受支持集（RAW/AVIF/JXL/PSD/ICO 仍拒绝上传）；§12.2；上传仍不产资产、不写索引。

## 2. 独立复核

### 2.1 一致性反例：上传解码 vs 库内解码（逐像素）

这是本卡最关键的不变量（"上传与库内同语义"）。审查用独立探针（内联脚本，不是产品测试）对同一文件跑两条路径，
再用 `ImageChops.difference(...).getbbox()` 判等：

| 夹具 | 上传 | 库内 | 逐像素相同 |
|---|---|---|---|
| 两帧 MPO（首帧红/次帧蓝） | 96×64 | 96×64 | ✅ |
| HEIC 方向 6 容器 irot / 仅 EXIF | 64×96 / 64×96 | 同 | ✅ / ✅ |
| HEIC 方向 8 容器 irot / 仅 EXIF | 64×96 / 64×96 | 同 | ✅ / ✅ |
| 普通 HEIC（无方向） | 96×64 | 96×64 | ✅ |
| JPEG | 96×64 | 96×64 | ✅ |
| **两帧 HEIC（iPhone 连拍形状，对抗例）** | 96×64 | 96×64 | ✅（两条路径都取第 0 帧） |

⇒ "只加白名单不接方向归一"会静默错向的风险，在实现层面已被消除，且两条路径对多帧容器取帧一致。

### 2.2 跨面排查（这是专项审查最容易漏的部分）

- **P2-07 的其它断言不受影响**：`test_a_format_outside_the_frozen_whitelist_is_refused`（TIFF 例）与
  `test_the_format_whitelist_and_decode_classification_are_honest`（TIFF/broken/empty 参数化）都与具体格式集无关；
  全仓唯一的字面 5 项断言就是本卡修订的那一条。
- **前端/能力面不枚举上传格式**：`web/search.js` 的 `FORMAT_UNSUPPORTED` 文案不列格式；`/capabilities` 的
  `supported_input_formats` 来自媒体契约（P4-02S 已含 MPO/HEIC）；`docs/decisions/contracts/p4-upgrade-contracts.v1.json`
  不含格式枚举 ⇒ 无契约面遗漏。
- **旧裁定文档的溯源**：`P4-00C-A` 报告 §5/交付表与 `P4-00C` DS-H 报告第 6 条都写着旧的 5 项白名单；已各加一句
  "已被 P4-02T 修订"的指针（R2）。`docs/development-status-archive-2026-09-27.md` 是**原样归档**，
  按规矩**不修改**（本轮只做只读哈希核对）。
- **上传边界外溢副作用**：`heif.ensure_heif_support()` 现已在上传解码前调用；它是幂等且失败即 `False` 的仓库唯一注册点，
  与能力探针 / 媒体解码调用同一函数，不引入新的进程级状态差异。

### 2.3 复跑（主审自己的产物）

| 项 | 结果 |
|---|---|
| 新单测 + 被修订的 P2-07 断言 + HEIC 文件 | **86 passed / 0 failed**（`test-results/p402-t-review-focused.xml`） |
| 真实模型端到端驱动（复跑） | **9/9**、`status: pass`（`test-results/p402-t-review-upload-formats.json`） |
| **B 级** `pytest -q tests/unit tests/integration` | **1783 / 0 failed / 0 errors / 48 skipped**（237.1 s，`test-results/p402-t-review-full.xml`） |
| `ruff check .` / `uv lock --check` / `git diff --check` | exit 0 / exit 0 / exit 0 |

实现者自报的数字（新单测 9/0、四文件 86/0、B 级 1783/0/0/48、驱动 9/9）与主审复跑**逐个一致**。

## 3. 卡面不变量逐条

| 不变量 | 结论 |
|---|---|
| 白名单显式可审（不自动跟随媒体契约） | ✅ 仍是字面集合；与拒绝集无交集；媒体契约变化不会悄悄放宽 |
| 与库内解码同语义（MPO 首帧 / HEIF 方向归一） | ✅ §2.1 八例逐像素相同，含多帧 HEIC 对抗例 |
| 坏文件分类不乱 | ✅ 白名单内坏文件 → `DECODE_FAILED`（单测）；白名单外已识别家族 → `FORMAT_UNSUPPORTED` + 新 `supported_formats`（单测 + 真实驱动的 TIFF 探针） |
| 上传仍不写库、上限/信封不变 | ✅ diff 只动格式集与方向归一；`UPLOAD_TOO_LARGE`/`UPLOAD_TOO_MANY_PIXELS`/`VALIDATION_ERROR` 与 `details.field=image` 未改；集成测试的临时目录回收断言仍绿 |
| 依赖方向不变 | ✅ pillow-heif 仍只在 `media/heif.py` 注册；`search/**` 未新增重型 import |

## 4. 发现

| 编号 | 严重度 | 内容 | 处置 |
|---|---|---|---|
| R1 | 低（文档） | `search/upload.py` 模块 docstring 第 3 条仍写"本构建没有编码器"，P4-02N 之后已不成立 | **本轮已改**：改为"没有 adapter 的进程（默认打包 / 未登记模型）才结构化拒绝，P4-02N 在有模型时接真实 adapter" |
| R2 | 低（溯源） | `P4-00C-A` 报告 §5/交付表、`P4-00C` DS-H 报告第 6 条记载的旧 5 项白名单没有"已被修订"指针，顺着文档链会读到过期规则 | **本轮已改**：两处各加 P4-02T 指针；归档文件按规矩不动 |
| R3 | 提示（非缺陷） | `test-results/junit.xml`（仓库 pytest `addopts` 的默认产物）会被任何不带 `--junitxml` 的 pytest 调用覆盖 | 属仓库既有配置；本轮复核产物一律带显式 `--junitxml` 文件名 |
| R4 | 记录 | 本卡改动的是冻结 P2-07 断言，实现与复审同会话 | 已在标题与本节注明；如需严格独立，可另起一轮只读该白名单/方向归一与两个断言文件 |

无阻塞发现（无产品语义、错误码、数据安全或恢复面问题）。**DS-MAX 不适用**（V3.1 §2.2 六类条件逐条不成立：
根因明确、无事务/revision 矛盾、测试与 ADR 一致、无连锁回归、diff 极小）；**`SOL_NOT_REQUIRED`**
（未新增冻结 §11 协议/ADR/数据安全未决项；P2-07 的修订只是"白名单加三族"这一条附加变更，且已由本主审复核）。

## 5. 审查 F2 / F3 的收口记账

- **F2（上传闸门仍拒 MPO，报告归因错误）**：由本卡闭合 —— 白名单显式纳入 `MPO/HEIC/HEIF`，真实驱动证明
  "上传 MPO 命中自身首帧、上传旋转 HEIC 命中自身"；P4-02S 报告 §4.1 的归因错误已更正。
- **F3（格式策略未版本化，旧库复用过期失败缓存）**：**已裁决（2026-09-27，用户采纳 DS-H 推荐）**：
  **不 bump** `catalog-thumbnail@N`（会让全部 2800 张已发布缩略图失效重建，为 343 张 MPO 付发行级代价不划算）；
  升级动作写为 **`catalog thumbnail --retry-failed`**；**发行级自动失效**（启动时按需重试或版本化处理器标识）
  登记为 **P4-07 打包/升级卡的显式要求**。
- ⇒ **P4-02S 的 F2/F3 均已收口**，其产品面可记 `ENGINEERING REVIEW PASS`；其真实库读数仍以既有产物 + 只读库核对为准
  （本轮未重扫真实图库，无新授权）。

## 6. 未验证与边界

- 未用**真实手机 MPO/HEIC 原图**验证（无新图库授权）：证据是合成夹具 + 真实模型 + 完整 HTTP 路径。
- 未重建打包产物、未跑 `packaged_api_smoke.py`：C 级不在本卡 ⇒ GTX 1660 / §12.2 / 打包产物仍未验证。
- 未验证上传侧的**并发/长时运行**与 HEIC 解码的峰值内存（沿用既有 `UploadLimits`，未改）。
- 已知遗留：`indexing.cli reexecute` 仍走 adapter 的 path 直读（HEIF 注册与方向归一未接），属另一张卡。