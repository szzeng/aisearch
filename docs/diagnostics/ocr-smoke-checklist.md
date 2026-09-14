# P3-01 OCR smoke checklist（人工/可重复）

目的：在不接触任何真实照片库的前提下，重复验证「固定权重 → 有界高分辨率识别 → 原文/区域/置信度
产物 → 检索副本与 FTS → 只读状态投影」这条闭环。

所有步骤只用 `%TEMP%` 里的自建夹具；`PhotoSearchData` 与模型副本都在本次工作目录内。

## 0. 前置

- 机器上已按 P0-04 安装固定权重（默认数据根 `%LOCALAPPDATA%\PhotoSearchData\models`）；
  缺失时脚本以 `OCR_WEIGHTS_MISSING` 退出 1，属预期失败而非「空结果」。
- 使用项目内 uv 缓存：`$env:UV_CACHE_DIR = 'D:\SZ\project\aisearch\.uv-cache'`。

## 1. 一键端到端（脚本）

```text
uv run --no-sync python tools/diagnostics/ocr_smoke_fixture.py --json
```

期望（脚本自身断言，失败即退出 1）：

- `status = ok`，`enqueue.queued = 3`，`run.jobs = {"SUCCEEDED": 3}`，`projection.state = SYNCED`；
- `images.invoice.regions >= 4` 且 `original_text` 含 `1280`、`20260914`；
- `images.dense.slice_count >= 2`（高文字密度页确实被切片，不是整图压到 320/448）；
- `images.blank.regions = 0`（空白页是**诚实的空文档**，不是缺行）；
- `fts_hits` 里 `发票`、`1280`、`20260914` 都命中发票图；
- `ocr_summary.state = SYNCED`、`stale_documents = 0`。

工作目录默认 `%TEMP%\P3-01 OCR 验收`，可用 `--work-dir` 指定。

## 2. 受控入口（产品 CLI）

```text
uv run --no-sync python -m photo_search.ocr.cli weights --json
uv run --no-sync python -m photo_search.ocr.cli status --json
uv run --no-sync python -m photo_search.ocr.cli recognize --asset-id <id> --json
uv run --no-sync python -m photo_search.ocr.cli recognize --all --force --json
```

核对点：

- `weights` 必须列出 4 个固定文件（det/cls/rec/config）且 `size_matches=true`；把其中一个文件改成
  同字节数的不同内容后必须报 `OCR_WEIGHTS_HASH_MISMATCH`（绝不回退到 wheel 自带模型）；
- `recognize --asset-id` 对**单张**强制重识别：`enqueue.rewound = 1`（复用同一逻辑任务，不新增 job 行）；
- `recognize --all --force` 后 `run.jobs` 仍只有一个 `SUCCEEDED`，`asset_ocr_text.text_revision` 递增；
- `status` 只读：`documents/stale_documents/regions/low_confidence_regions/jobs/state`。

## 3. 只读状态投影（API）

启动本地服务（见 `docs/diagnostics/frontend-smoke-checklist.md` 的第 1 步），然后：

```text
GET /api/v1/capabilities   # 看 "ocr" 段
GET /api/v1/jobs           # 看 task_type=image_ocr 的行
GET /api/v1/assets?text=发票
```

核对点：

- `ocr.state` 在未运行时是 `NOT_RUN`、排队/落后时是 `LAGGING`、有阻塞任务是 `BLOCKED`、
  全部追上才是 `SYNCED`；
- `ocr.weights_present` 反映模型根是否有固定文件（未校验哈希，只做存在性/大小检查）；
- `/capabilities`、`/jobs`、`/assets` 的响应里**不得**出现识别原文、区域坐标或单区域置信度
  （只有计数、阈值与状态）。

## 4. 已知边界（不要在冒烟中当作缺陷）

- 固定引擎会在拼行时去掉行内空格（`222 999` → `222999`），因此按单个数字段做前缀检索不一定命中；
  检索副本忠实反映引擎输出，产品不做数字/日期/编号纠正。
- 精排/语义通道与 OCR 无关；本阶段 API 进程没有编码器，OCR 文本不会进入向量通道。
- 真实复杂版面（竖排、艺术字、手写、旋转/倾斜、多语言混排）与真实截图库规模未验证。
