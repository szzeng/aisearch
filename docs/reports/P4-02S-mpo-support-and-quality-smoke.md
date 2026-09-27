# P4-02S 实现报告 — MPO 首帧支持 + 覆盖率复核 + 无看图质量冒烟

**状态：IMPLEMENTED / INDEPENDENT REVIEW PENDING**（执行者自检；产品改动 ⇒ 需独立 DS-H 完整受影响面主审）。
基线 `549075f`；来源：[P4-02R 诊断](P4-02R-embedding-thumbnail-coverage-diagnosis.md)、[P4-02P 试用](P4-02P-real-library-pilot-build.md)。

## 1. 交付

| 文件 | 改动 |
|---|---|
| `src/photo_search/media/contract.py` | `MPO`（多图 JPEG）从 `REFUSED_INPUT_FORMATS` 移入 `BASE_SUPPORTED_INPUT_FORMATS`，并写清语义 = **容器的第一帧**、以及放行前做的客观对照 |
| `tests/unit/test_p4_02s_mpo_first_frame.py`（新增） | 4 个用例：契约/策略接受 MPO 且仍拒绝 TIFF/AVIF/RAW/JXL；`load_normalized` 返回**第一帧**；语义探测接受 MPO、拒绝 TIFF；拒绝路径带 `format` 标签 |
| `tests/unit/test_p4_heic_media.py` | 拒绝清单断言移除 `MPO`（其余家族不变） |
| `tools/diagnostics/p4_02s_self_retrieval.py`（新增） | 自检索质量冒烟（§3） |

未改：schema/迁移/依赖/`uv.lock`；检索/信封/错误码/outbox/调度语义；`indexing/**`；`§12.2`；RAW 仍拒绝。

## 2. MPO 首帧：客观验证（不看图）

MPO 的帧是 JPEG 编码，因此**不存在逐字节相等**；honest 判据是：

| 判据 | 结果 |
|---|---|
| `Image.open(mpo).format` | `MPO`；尺寸 = 第一帧尺寸 |
| `media.decode.load_normalized(mpo, long_edge=320)` | **成功**，`source_format=MPO`，尺寸/模式 = 第一帧 |
| 平均颜色 vs 第一帧 / 第二帧 | 与第一帧差 `near<12`（JPEG 有损范围），与第二帧差 `far>100` ⇒ 返回的确实是第一帧 |
| 单测证明可失败性 | 第二帧颜色刻意不同，取错帧必然触发 `far` 断言 |
| `semantic.image_eligibility(mpo)` | `(True, "MPO")`（与媒体契约同源） |
| TIFF/AVIF/RAW/JXL | 仍 `decodable=False`，`load_normalized` 仍 `FORMAT_UNSUPPORTED` |

测试：`tests/unit/test_p4_02s_mpo_first_frame.py` + `tests/unit/test_p4_heic_media.py` → **24 passed / 0 failed**
（JUnit `test-results/p4-02s-mpo.xml`）；媒体契约/解码单测 45/0（`test-results/p4-02s-media.xml`）。
**B 级全量**：`pytest -q tests/unit tests/integration` → **1773 tests / 0 failed / 0 errors / 48 skipped（301.8 s）**
（`test-results/p4-02s-full.xml`）。首轮全量曾 `FAILED 1`：`test_media_decode.py` 的拒绝清单仍含 `MPO`，
按新策略更新该断言后全绿——这是策略变更的正常连带，不是隐藏失败。

## 3. 真实库覆盖率复核（同一数据根 D）

| 步骤 | 结果 |
|---|---|
| `catalog thumbnail`（无 `--retry-failed`） | 0 新增（确定性失败被缓存跳过，符合设计） |
| `catalog thumbnail --retry-failed` | **+343 张**（MPO 全部转正），25.8 s |
| 缩略图状态 | `published 2800 → **3143**`、`failed 479`（非图片，不变）、`unsupported 379 → **36**`（只剩 RAW） |
| 建库 dry-run | `targets=0`、`out_of_scope=479`（`ISO-BMFF` 339 + `UNIDENTIFIED` 140，全是非图片） |
| 判定矩阵复跑（`p4_02r_decode_matrix.py`，per-suffix 6） | 只剩 `TIFF`×7 抽样（CR2/DNG）：媒体契约与语义探测一致拒绝 |

**最终口径（3658 资产）**：**3143 可显示且可搜** + **36 RAW 可搜但不可显示**（`.cr2`/`.dng`，RAW 仍拒绝）+
**479 非图片**（不在图文索引范围）。

## 4. 质量冒烟（不看图，已知目标）

### 4.1 自检索（image → image）

工具：`tools/diagnostics/p4_02s_self_retrieval.py`（固定种子 `20260927` 抽 30 张已入库照片，用**原图**做上传查询）。

| 观测 | 值 |
|---|---|
| 抽样 | 30（另有 55 张因超过上传字节上限被跳过，报告单列） |
| 200 且 **top1 = 自身** | **26 / 26 = 1.0** |
| 上传闸门拒绝（记录，非质量失败） | 1 × `UPLOAD_TOO_LARGE`（413）、3 × `FORMAT_UNSUPPORTED`（409）。**更正（DS-H 审查 F2）**：这 3 个资产的后缀全是 `.jpg`，即 MPO —— 上传边界 `UPLOAD_INPUT_FORMATS` 是 P2-07 冻结白名单（`JPEG/PNG/WEBP/BMP/GIF`），MPO 从拒绝清单移出后仍不在该白名单里，因此当时 **MPO 只能被索引/显示、不能作上传查询**；本库 RAW（`.cr2`/`.dng`）一个都没被拒（36 个大多落在那 55 张超限样本里）。**后续（[P4-02T](P4-02T-upload-format-policy.md)）**：该白名单已显式纳入 `MPO/HEIC/HEIF`，MPO 与 HEIC 现在既可索引、也可作上传查询（真实模型端到端 9/9） |
| 上传查询 HTTP | P50 **992.4 ms** / P95 **1496.5 ms** / max **5186.9 ms** |

结论：图像塔 + 索引 + 检索在真实照片上自洽（同内容必然距离 0 取胜），并顺带验证了**上传搜图**路径可用。

### 4.2 固定查询集（延迟/命中，不含相关性结论）

12 条（`日落/海边/猫/狗/吉他/生日蛋糕/城市夜景/花朵/食物/全家福/红色的图片/蓝天`），各 6 样本：
**中位 62.0–87.6 ms，全部 20 条命中、vector engaged、1 个引擎块**；规范查询「红色的图片」32 样本
**P50 87.5 / P95 95.1 / min 70.6 / max 136.4 ms**（capabilities 冷启动 3544.1 ms）。

**未做**：路径派生已知目标（`vsco`/`吉他` 等目录词）——它是 FTS 主导的管道检查，本卡未跑；没有人工标注保留集，
因此**不做 Recall/相关性结论**。

## 5. 未验证 / 边界

- **不是 §12.2**：3.2k 档、RTX 4060、单进程、无 GTX 1660、无 30 万档、无混合负载；上传 P95 1496 ms 只是本夹具量级。
- **36 RAW**：可搜不可显示；要显示需 RAW 预览提取的独立验证（另一张卡）。
- **升级口径（DS-H 审查 F3）**：媒体格式策略不在 `catalog-thumbnail@1` 的身份里，`catalog thumbnail` 对确定性失败是缓存跳过（本次不带 `--retry-failed` 的那一遍是 `generated=0`、`skipped_cached_failures=858`）。所以**已装库升级到本构建后必须跑一次 `catalog thumbnail --retry-failed`**，MPO 的 +343 才生效；**已裁决（2026-09-27，采纳 DS-H 推荐）：不 bump** `catalog-thumbnail@N`（避免 2800 张已发布缩略图无谓重建），升级动作 = `catalog thumbnail --retry-failed`；发行级自动失效登记为 P4-07 显式要求。
- 路径派生已知目标未跑；12 条查询只证明延迟/命中存在性。
- `indexing.cli reexecute` 仍走 adapter 的 path 直读（HEIF/方向归一未接），属遗留项。
- P4-02N / P4-02O / P4-02Q / P4-02S 的**独立 DS-H 审查**仍未做 ⇒ 都不算 PASS。

## 6. 下一步

(a) 30 万档扩容前先做 [P4-02M §3](P4-02M-exact-search-optimization.md) 单点优化（3.2k 只有 1 个引擎块，30 万是 60 块）；
(b) 若想继续做质量，需要**人工标注**的已知目标集（20–30 条，含预期目标），当前无人工标注 ⇒ 不做相关性结论；
(c) RAW 预览支持与 `reexecute` 解码路径归一化可另立一张卡。
