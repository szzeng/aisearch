# 搜索页浏览器 smoke 核对清单（P2-07）

本清单用于人工/浏览器复核 `src/photo_search/web/search.*` 的真实行为。它只使用合成夹具，
位于 `%TEMP%`，不会读取、移动或删除任何真实图库。

## 1. 准备夹具

```text
uv run --no-sync python tools/diagnostics/search_smoke_fixture.py --photos 6 --reset
```

夹具会输出：工作目录、图库目录、数据目录、资产与 `asset_id`、已确认向量 / 待确认向量的
资产、地点 `place_id`、激活的 `SearchProfile`，以及下面这份预期观察清单。

它建出的状态（每次相同）：

- 6 张合成 JPEG，经过生产扫描器入库；
- 一个 `READY` 图像世代，其中 **2** 个资产有 `CONFIRMED` 向量条目，其余 4 个没有
  （用于观察“语义索引仍在追赶”）；
- 一个人工归属的地点（`杭州西湖` → 其中一张照片），因此“已归类”筛选只返回 1 行；
- 一个已激活的 `SearchProfile`，指向该世代；该夹具的数据目录在 `%TEMP%` 下且**没有**登记权重（模型默认位置 `<数据根>/models/chinese_clip/vit-b-16`），所以启动时 `query_encoder_available=false`。P4-02N 之后，同一构建只要把权重放进数据根（或传 `--model-dir`）就会接线真实编码器，下面的“无编码器”预期随之改变。

## 2. 启动真实端口的本地服务

```text
uv run --no-sync python -m photo_search --port <free-port> --data-root "<夹具输出的数据目录>"
```

在 Codex 内置浏览器里打开启动器打印的一次性链接（`next=/`），再进入 `/search`。

## 3. 观察步骤与预期

| # | 操作 | 预期观察 |
|---|---|---|
| 1 | 打开启动器链接，再点导航「搜索图片」 | 303 落到 `/`；`/search` 标题「搜索图片」；导航含「返回「本机图片搜索」」「地点整理」「上传搜图」「分项覆盖率」 |
| 2 | 看 `/search` 顶部提示 | 显示当前 `profile_id` 与图像世代，并说明 `query_encoder_available=false` 时文本查询会被服务端明确拒绝 |
| 3 | 输入中文查询文本并点「搜索」 | 无权重夹具下是结构化 `409 RUNTIME_CAPABILITY_UNAVAILABLE`（`QUERY_ENCODER_UNAVAILABLE`）提示，文案说明「语义通道未运行」，查询文本保留，出现「按当前条件重搜」 |
| 4 | 清空查询文本后再点「搜索」 | 出网格：每行有可点开的缩略图、总体名次、资产编号和命中原因「仅筛选…」；不显示详情按钮、语义就绪或通道名次；状态行给出「已显示 N 行 · 候选共 M 个 · 还有下一页/已到末尾」 |
| 5 | 看结果区提示 | 出现「文本投影仍在追赶：还有 N 个资产…」；若本次通道能绑定世代，还会出现「语义索引仍在追赶…」与待确认向量条目计数 |
| 6 | 把「每页数量」改成 2 后搜索，再点「加载下一页」 | 第二页追加新行，无重复；状态行「已显示 4 行 · 候选共 6 个」 |
| 7 | 「地点状态」选「已归类」后搜索 | 结果收窄到被人工归属的那 1 个资产（夹具输出里有它的 `asset_id`） |
| 8 | 选一张本地图片，点「用这张图搜索」 | 本夹具无权重 ⇒ `query_encoder_available=false` → 结构化 `409 RUNTIME_CAPABILITY_UNAVAILABLE`（`IMAGE_ENCODER_UNAVAILABLE`），提示明确说明没有用文本通道或「零命中」冒充结果；服务端不新增资产 |
| 9 | 看「分项覆盖率（分类别，不合成一个准确率）」 | 文本投影覆盖 / 语义索引覆盖 / 地点覆盖 / 来源可用性四项各自列出，各有「口径」说明，最后一句声明不合成「整体 AI 准确率」 |
| 10 | 窄视口约 640px + Tab | 无横向滚动（网格折列）；Tab 依次经过导航链接 → 查询文本 → 来源等控件，焦点可见 |

## 4. 自动化已覆盖、不必逐项手工重做的部分

- 页面静态契约（无外链、无本地路径、无 `innerHTML`/`console`/存储、只用 `/api/v1`、
  命中原因词汇、非成功状态文案、上传限制文案、四项覆盖率标题）：
  `tests/unit/test_api_frontend.py` 的 P2-07 段。
- 上传端点逐项硬限制（字节上限 413、像素上限 422、解码时间预算、格式白名单、解码失败分类、
  临时目录回收、无 session 401、无 CSRF 403、未知字段 422、半绑定 422）：
  `tests/integration/test_p2_search_image.py` 与 `tests/unit/test_p2_search_upload.py`。
- 上传图复用同一 RRF/权威路径、会话绑定到图片字节、不写库：
  同上（`test_an_uploaded_image_reuses_the_frozen_ranking_and_writes_nothing` 等）。
- 冻结包内的页面与新端点：`tools/diagnostics/packaged_api_smoke.py` 的 P2-07 检查。

## 5. 已知边界

- **数据根没有登记权重时**（本夹具即如此）API 进程没有查询/图像编码器，因此**在浏览器里**无法看到语义通道命中的行
  （`hit_reason=SEMANTIC`/`TEXT+SEMANTIC`）或「语义索引仍在追赶」提示。该提示的数据路径由
  `tests/integration/test_p2_search_image.py::test_a_lagging_semantic_entry_is_named_instead_of_hidden`
  覆盖；**P4-02N 之后真实 adapter 已接入真实启动**：把权重放进数据根（或传 `--model-dir`）就应在浏览器看到语义命中行，这项真实浏览器观察仍待补做。
- 内置浏览器的文件选择器在自动化里偶发超时，重试后可用；上传端点的结构性证据以 C 级
  `packaged_api_smoke.py`（真实 multipart 请求）为准。
