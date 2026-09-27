# P4-02P 真实图库试建库与第一轮试用（用户授权，3.2k 档）

用户 2026-09-27 授权：目录 `D:\SZ\朋友`、`D:\SZ\pic`、`D:\SZ\吉他`、`D:\SZ\vsco`（**原图只读**），
数据根 `D:\SZ\project\PhotoSearchData`。本报告是资源/试用步骤的实测记录，**不是** §12.2 验收，也不是产品契约变更。

## 1. 盘点与扫描

| 项 | 值 |
|---|---|
| 只读盘点（按扩展名） | `朋友` 907 张/2.12 GiB、`pic` 1353/4.18、`吉他` 55/0.12、`vsco` 872/5.21 ⇒ **3187 张** |
| 生产扫描 | 4 个 source 全部 `SUCCEEDED`；**assets 3658、locations 3682（PRESENT 3682 / MISSING 0）** |
| 说明 | assets < locations：同一内容出现在多个目录时按内容合并为一个资产、多个位置；扫描器同时登记了视频/音频/文档（见 §3） |

## 2. 第一轮建库（真实模型，单进程顺序）

```powershell
uv run --no-sync python -m photo_search.indexing.cli build `
  --data-root D:\SZ\project\PhotoSearchData `
  --model-dir D:\SZ\project\PhotoSearchData\models\chinese_clip\vit-b-16 `
  --manifest tools/prepare_models/manifests/chinese-clip-vit-b-16.json --device cuda --batch-size 200 --json
```

- `targets=3658`、`SUCCEEDED=3178`、**`FAILED=480`**。
- 失败原因（SQL 直查 `jobs.last_error_code/last_error_message`）：全部 `RUNTIME_RESOLUTION_MISMATCH` /
  `the image encoder failed: UnidentifiedImageError`；按扩展名统计 = **479 个非图片文件**
  （`.mp4` 330、`.mp3` 39、`.pdf` 29、`.db` 22、`.m4a` 13、`.txt` 7、`.wav` 6、`.xlsx` 5、`.3gp` 4、
  `.ogg` 4、`.rar`/`.zip` 3、`.avi` 3、`.mov`/`.pptx` 2…）+ 1 个 `.heic`（该进程未注册 HEIF 插件）。
- 结论：**不是模型问题**，而是建库入口的覆盖/解码问题（把非图片资产当图片排进队列、且未按仓库唯一注册点启用 HEIC）
  ⇒ 立 [P4-02Q](../tasks/P4-02Q.md)，修完再扩容。

## 3. P4-02Q 修复内容

1. **目标选择走媒体层判定**：`media.decode.plugin_format_decision`（Pillow 已识别的格式只接受支持集）+
   `sniff_format`/`read_header` 兜底 + `media.heif.ensure_heif_support()`；非图片资产进 `out_of_scope`
   （数量 + 格式直方图 + 抽样），**不再产生 job、不再计为失败**。
2. **`--retry-failed`**：把 FAILED 的逻辑 job 重置为 `QUEUED`（复用 face 的 `_rewind` 形状，attempts 归零），
   供修复后重跑；RUNNING 不动。
3. **每资产一个规范位置**（最早 `location_id`），与执行器读文件的规则一致，避免多位置资产被重复计数。

## 4. 补建后的真实库状态

| 观测 | 值 |
|---|---|
| 补建命令 | `build … --retry-failed` → exit 0、wall **5.29 s**；`checked=487`、`targets=1`（那个 HEIC）、`out_of_scope=486` |
| 修后 dry-run | `targets=0`；`out_of_scope=479`（`ISO-BMFF` 339 + `UNIDENTIFIED` 140） |
| 索引 | `entries CONFIRMED = 3179`、`generation = image-gen-5bcba729dfda4897a94974b7`、`state=READY`、`is_active=true` |
| 搜索 profile | `semantic-image-gen-5bcba729dfda4897a94974b7` 已创建并激活 |
| 覆盖 | 3658 资产 = **3179 图片已嵌入** + **479 非图片（不在图文索引范围）** |
| 历史残留 | 第一轮的 479 条 FAILED job 行保留（都是非图片），重跑不再处理它们；报告中显式说明，不当作图片失败 |

## 5. 缩略图（试用可见性）

`catalog thumbnail` → exit 0、**84.7 s**、`assets_considered=3658`、**published 2800**（46.3 MB）、
失败 `DECODE_FAILED` 479 + `FORMAT_UNSUPPORTED` 379。

**379 个资产有嵌入但没有就绪缩略图，全部 `FORMAT_UNSUPPORTED`**：341 个 `.jpg`（MPO 多图 JPEG）、
35 个 `.cr2`、1 个 `.dng`（另有 2 个同类）。**机制已由 [P4-02R 只读诊断](P4-02R-embedding-thumbnail-coverage-diagnosis.md)
查明并更正本节初稿的猜测**：语义探测与媒体契约判定**一致**，这 379 个嵌入是 P4-02Q 过滤生效**之前**那一轮的
历史残留（修复后它们已不再进入新目标，dry-run `targets=0`）。影响：这些资产可被搜索返回但网格无缩略图。
处置见 P4-02R §4（推荐：把 MPO 首帧纳入受支持格式；RAW 保持拒绝）。

## 6. 暖机文字查询（真实库 3.2k 档）

工具：`tools/diagnostics/p4_02p_library_queries.py`（真实应用 + 一次性凭据 + **不带显式绑定**，由激活 profile 提供绑定）。

| 观测 | 值 |
|---|---|
| 接线/能力 | `wiring available=true`、`query_encoder_available=true`、`active_profile_id` 与 `active_image_generation_id` 均已发布 |
| capabilities HTTP | 3533.9 ms（该请求付查询 worker 的 CUDA 冷启动） |
| 10 条中文查询（各 5 样本） | 中位 **63.5–104.7 ms**；每条 `vector engaged`、`engine_blocks=1`、`hit_count=20` |
| 规范查询「红色的图片」（32 样本） | **P50 76.9 / P95 88.8 / min 72.8 / max 132.9 ms** |

**这不是 §12.2 证据**：3.2k 档（不是 30 万）、RTX 4060（不是 GTX 1660）、没有首屏/上传/后台建库门槛、
没有人工标注保留集 ⇒ 不做质量/Recall 结论；命中数只说明"有候选"，不代表相关。

## 7. 未验证 / 下一步（唯一后续卡）

1. 379 个 `FORMAT_UNSUPPORTED` 的口径统一（MPO/RAW 预览）——影响试用网格可见性。
2. **质量**：需要用户授权的 20–30 条**已知目标**查询（本次查询集只用于延迟与命中存在性）。
3. **30 万档**：3.2k 档时 `vector_channel` 只有 1 个引擎块；30 万档是 60 块（P4-02K/02M 的已知瓶颈）
   ⇒ 扩容前先做 P4-02M §3 的单点优化，否则每查一次都是 5–8 s 量级。
4. 首屏（缩略图 + 页面）未单独计时；1600px 预览按需生成未测；上传搜图未在真实库上验证。
5. P4-02N / P4-02O 的**独立 DS-H 审查**仍未做（两卡都还是 IMPLEMENTED / INDEPENDENT REVIEW PENDING）。

**结论**：3.2k 档真实闭环已可用——扫描 → 真实模型建库（3179 张 CONFIRMED）→ profile 激活 →
暖机文字查询 P95 ≈ 88.8 ms 且全部有结果；但 §5 的 379 张差异、真实质量集与 30 万档都未闭合。
