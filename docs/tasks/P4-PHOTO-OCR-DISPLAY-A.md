# P4-PHOTO-OCR-DISPLAY-A — 照片查看与完整详情显示已识别 OCR 原文

- **日期**：2026-10-20。
- **状态**：IMPLEMENTED / ACCEPTANCE PARTIAL（本地工作区实现；不提交或推送）。
- **实际实施基线**：`master`，`4690bc8`（工作区另有地点性能/UI并行改动；逐项保留，不覆盖）。
- **优先级/规模**：P2，单项展示能力、少量 API 和公共前端修改。
- **推荐执行模型**：**GPT-6 High**；因 OCR Artifact 历史身份、一致性与并发读取需要审慎处理。用户指定需要复核时使用 Luna High 子代理；本次已执行独立只读复核。
- **前置与非目标**：使用**已经成功保存**的 OCR 结果；不要求重新 OCR、重新建库、模型切换、GPU、原始文件在线。

## 1. 用户可见结果（冻结本卡范围）

1. **共享照片查看弹窗**：所有通过 `AIPhotoInfo.open()` 打开的照片（首页/搜索/收藏/人物/地点/地图/合集等）统一在右侧「照片信息」内显示一个轻量「识别文字」区。
2. **完整照片详情** `/#/asset/{id}/details`：使用已经存在的 `AIPhotoInfo.render()`；与弹窗看到**同一份当前资产的 OCR 原文**，不在 `app.js` 另造第二套组件。
3. **仅有非空有效文字时**显示标题、文字、**复制文字**按钮。没有 OCR 记录、OCR 成功但空结果、OCR 结果属于旧照片内容，均静默隐藏模块；不要把这些情况说成“失败”。
4. **原文而非检索副本**：完整保留 OCR `original_text` 的换行、中文、空格、标点、数字与原始阅读顺序；禁止用 `search_documents.ocr_text` 或 `asset_ocr_text.search_text` 冒充原文。可选中、可复制；OCR 可能有误，但首版不做人工编辑。
5. **紧凑且不抢主视图**：短文字自然显示；长文字区域 `max-height`、内部滚动（允许收起）。不把原文铺满屏幕、不让弹窗外框、图像舞台随 OCR 长度变化。文字不能叠在图片上。
6. **局部容错**：当前 OCR 指向的 Artifact 丢失、损坏、身份不匹配时，原图/缩略图/人物/地点/路径仍正常工作。只在信息栏显示「识别文字暂时无法读取」与可点击「重试」。不自动进入识别队列。
7. **首版不做**：OCR 区域描框/定位/高亮、置信度列表、翻译、纠错、按文字搜图、下载 TXT、OCR 手动触发入口、全局 OCR 缓存与批量 API。这些不是当前显示需求的前置条件。

## 2. 仓库事实与复用落点

按 `AGENTS.md` → `docs/development-status.md` → 本卡 → `docs/tasks/TASK-TEMPLATE.md` / `docs/performance.md` 的相关小节 → 直接受影响代码阅读，无需全文重审架构。

- `src/photo_search/catalog/migrations/0009_ocr_documents.sql`：`asset_ocr_text` 是 OCR 当前指针/元数据；`asset_ocr_regions` 不存文字。`asset_ocr_text.search_text` 是**规范化检索副本**。
- `src/photo_search/ocr/processor.py`：不可变 OCR Artifact 包含 `original_text`、`regions`，`validate_payload()` 有历史 `_payload_profile()`；复用既有读契约，不自行拼接文字区域、不重造转换算法。
- `src/photo_search/ocr/contract.py`：`OCR_READABLE_PROCESSOR_IDS` 包括历史 v1/v2 和当前 CPU/GPU v3；已退役的 processor **禁止执行新识别，但不等于其有效历史 Artifact 不可展示**。
- `src/photo_search/artifacts/store.py::ArtifactStore.read()`：已执行受控路径、字节数、SHA-256 校验；单个保存的产物受现有 **16 MiB** 上限约束。不要直接按数据库 URI `open()`。
- `src/photo_search/api/catalog_reads.py::run_catalog_read()`：已有**仅两个 worker** 的独立 SQLite 只读查询池。这是本卡性能关键：**不可把读取/哈希/JSON 校验大 Artifact 的全过程塞进该查询池**。
- `GET /api/v1/assets/{asset_id}` / `AssetDetail` 已被许多页面复用；不要给其响应添加庞大 OCR 正文。
- `src/photo_search/web/photo-info.js::render()` 既已承担共享 viewer 信息与完整详情的信息区域；`web/app.js::drawDetail()` 已挂载 `AIPhotoInfo.render()`。现有 `current()`/`dispose()`/序号机制可阻止异步串图；留意 `render()` 在 `/assets/{id}` 完成后会执行 `content.replaceChildren()`，**早到的 OCR DOM 不能被随后基础信息渲染冲掉**。

## 3. 后端最小接口与状态契约

优先新增：**`GET /api/v1/assets/{asset_id}/ocr-text`**。若最新 HEAD 已有等价的安全单资产 OCR 原文接口，直接复用；不能重复造 API。

使用既有本机应用同源会话鉴权与统一错误封装。正常的业务读取仅返回以下三字段，不增加前端没用的模型配置或置信度字段：

```json
{"status":"available","text":"发票编号：12345\n金额：1280 元","truncated":false}
```

| `status` | 触发条件 | `text` / UI |
| --- | --- | --- |
| `available` | 当前 OCR 文档有效且原文非空 | 返回识别原文，UI 显示/复制 |
| `none` | 无 OCR；OCR 有效但无文字；存储的 OCR 对应旧 `assets.sha256`；读取期间当前指针变化 | `text=""`，不显示模块 |
| `unavailable` | 当前有效 OCR 指针存在，但对应 Artifact 缺失/未发布/损坏/校验或身份失败 | `text=""`，仅显示局部重试提示 |

**存在性、鉴权和内部故障**：不存在的 asset 依既有规范 404；未授权由现有会话层拒绝；不可将所有非 OCR 相关的数据库/服务端错误吞成 `none`。前端遇到网络/服务端临时失败亦只局部显示失败并允许重试，不影响图片。三种业务状态必须同一套 JSON 字段：`status`、`text`、`truncated`；非 `available` 的 `truncated=false`。不向响应/日志输出 SHA、文件路径、Artifact URI、区域坐标、原文字节或堆栈。

**文本长度**：单次 `text` 最多 **128 KiB UTF-8**（只限制原文字段，不是 Artifact 原件）；在 Unicode 字符边界截断并设置 `truncated=true`，不静默截断或错误声称“复制全文”。客户端显示「识别文字较长，仅显示部分」；按钮明确写「复制已返回文字」。正文不因为 UI 的内部滚动再进行第二次截断。此边界不影响数据库或 OCR Artifact 的完整性。若发现项目已有更合适的统一文本响应限额，可沿用它并在报告说明。

## 4. 读取流程：正确、只读、不阻塞

1. **短 SQL 阶段**：使用现有 `run_catalog_read`，仅按 `asset_id`（主键）取资产存在/`sha256` 与 `asset_ocr_text` 当前指针，并按当前指针查询唯一 `artifacts`。将必要字段复制成普通 dict 后结束读工作，不持有 SQLite 连接跨线程/磁盘 I/O，更不启写事务。`asset_ocr_text.input_revision != assets.sha256` → `none`。
2. **无文字快速路径**：若当前有效 OCR 指针的 `region_count=0`，按已有 OCR 存储合同视为识别为空，直接 `none`，不打开大 Artifact；`region_count>0` 不代表 `original_text` 一定有可见字符，最终仍由原文判定。
3. **先验证元数据**：`artifact_id` 精确匹配唯一 Artifact，状态 `PUBLISHED`、类型 `structured`、版本 `ocr-document.v1`、处理器在 `OCR_READABLE_PROCESSOR_IDS` 中；`processor_id`、`input_revision`、`artifact_id` 与当前 OCR 指针/资产身份一致。拒绝未知/跨资产/旧内容 Artifact；不依赖“当前 OCR 可执行的 processor 集合”来判断历史可读性。
4. **有界文件阶段**：将 `ArtifactStore.read()`、JSON 解析及既有 `validate_payload()` 放在与 SQL 查询池分离的阻塞 I/O 工作路径（例如受控 `asyncio.to_thread`），并限定同时执行此类 Artifact 读取的数量（建议 **最多 2 个**；实现时避免每请求新建线程池/信号量）。保证不会使 Catalog 两个 worker 长时间忙于哈希和 JSON 解析。不要在 ASGI event loop 上同步读 16 MiB 文件。若现有通用线程/并发约束已满足，优先复用。
5. **内容验证**：校验 payload 的 processor、输入版本、Artifact 合同、`region_count` 与当前指针一致，提取 `original_text`；原文经过合同验证，不接受无类型或恶意字段。只投影文本，不返回 `regions` 等附加信息。
6. **读后再次短 SQL 核对**：至少比较 `assets.sha256` 和 `asset_ocr_text` 的 `ocr_document_id / text_revision / processor_id / input_revision / artifact_id`（以及该 Artifact 是否仍可发布读取）。期间任何身份变化均不返回旧原文，安全回 `none`；**无须自动循环重试**，避免因后台 OCR 改写制造读放大。文件读取期间不得长持数据库读/写事务。
7. **非破坏性**：绝不加载 OCR 模型、排任务、重新解码照片、改索引、触发 FTS catch-up、写数据库或磁盘持久缓存。原图离线但已保存 OCR Artifact 有效时，仍应显示原文。

在 `api/routes.py` 增加薄 API，读取逻辑落到 `api/queries.py` 或一个小型 OCR 只读 helper；`api/models.py` 如需声明紧凑响应模型才修改。勿因本功能重构 OCR 处理器/调度器。

## 5. 公共前端：位置、生命周期和可用性

**唯一主落点：** `src/photo_search/web/photo-info.js` + `photo-info.css`；除非最新实现证明必要，不碰 `web/app.js`。

- **挂载顺序**：`AIPhotoInfo.render()` 的基础 `/assets/{id}` 请求成功，`content.replaceChildren()` 完成且「照片信息」布局已建好后，才创建 OCR 占位宿主并发出独立 `/assets/{id}/ocr-text` 请求。这里等待的是**基础信息渲染，不是图片预览**：图片加载仍按原路径并行进行；基础信息出错时不孤立展示 OCR 数据。
- **结构**：标题「识别文字」、保留换行的 `pre-wrap` 正文、一个「复制文字」按钮。只在 `available && text.trim().length>0` 时出现。正文 `overflow-wrap:anywhere`，为长 URL/无空格 OCR 字符提供换行，设适度 `max-height`（如 160–240 CSS px）和内部滚动；不要影响共享 viewer 的固定宽高规则。可复用既有简单折叠控件，但**不开发复杂展开状态机**。
- **失败**：`none` 不显示空占位、不显示“正在识别”；`unavailable`、HTTP/网络错误只在 OCR 区出现「识别文字暂时无法读取」+ 手动「重试」。初次后台查询无需一直显示加载 Spinner；禁止自动重试循环。
- **复制**：使用原始返回字符串（含 `\n`），不是 DOM 的 `innerText`；正常「复制文字」，`truncated=true` 改「复制已返回文字」，并有截断说明。剪贴板不可用/拒绝时按既有纯文本选择降级，不引入依赖。复制反馈只更新当前照片信息面板。
- **身份防串图**：把 OCR 请求纳入现有 `active/seq/current()` 生命周期；同一挂载的手动重试再用局部 request 序号，防止旧的重试响应覆盖新的。关闭/切图/跳详情/返回或挂载销毁后，迟到响应不得回填 DOM、影响复制内容或暴露旧照片文字。若增加 `AbortController`，应补上真实序号检查，不能把 abort 当作唯一正确性保障。
- **渲染安全**：OCR 内容始终走 DOM `textContent`（及复制接口），不使用 `innerHTML`；HTML 标签、脚本、路径文本仅被当作普通文字。不修改原有缩放、人物管理、收藏、地图、检索结果会话、浏览器 history 和返回按钮。

## 6. 必测场景（小型合成测试，不跑真实图库）

| 组 | 最小验证 |
| --- | --- |
| A. 文字 | 中文、英文、数字、空格、原始换行、特殊符号完整显示并可复制；相同资产在共享弹窗/详情页一致 |
| B. 空态 | 无 OCR、有效 OCR 但 `region_count=0`、有区域但原文仅不可见空白、SHA 不匹配 → `none`，无空模块；原图离线但 Artifact 完整 → 仍显示 |
| C. 历史 | 有效 v1/v2、当前 CPU/GPU v3 的真实结构 fixture 可展示；历史 retired processor 不重新执行；未知处理器/不匹配身份拒绝 |
| D. 完整性 | Artifact metadata 不存在/`RECLAIMED`/checksum 错/路径越界/JSON 不合法/payload 异常 → `unavailable` 且不泄露细节；资产不存在 404，未授权拒绝 |
| E. 并发 | SQL 初读和读后复核间资产 SHA / OCR 指针改变，旧文本绝不出站；前端 A→B→A、连续切图、关闭/重开、旧重试先后返回不串图 |
| F. 文字上限 | 超过 128 KiB UTF-8 的可生成合成 OCR 原文不切断字符，`truncated=true`，复制明确为返回部分；长无空格字符串与多行不撑大弹窗或图像舞台 |
| G. 性能边界 | 文件哈希/解析不占 Catalog 查询池、阻塞 I/O 不跑 event loop；图库列表滚动不发 OCR 请求；单次查看只按当前 asset 做有界查询；不启动 OCR/GPU、无数据库写入 |
| H. 回归 | 既有图片缩放、双击 100%/适窗、Esc/关闭、详情返回、人物/地图/路径按钮正常；OCR 局部失败时图片仍可显示 |

优先复用 `tests/unit/test_photo_view_info_ui.py`、`tests/unit/fixtures/photo_view_info_probe.js`、现有 `tests/integration/test_p3_ocr_end_to_end.py` 与 OCR 历史 fixture，并新增必要的独立 API 定向测试。避免过度依赖“源码包含字符串”的断言；测试应验证 API 状态与 DOM 行为。

测试只要求：影响范围内 `pytest -q`（隔离 `--basetemp`、`--junitxml=test-results/P4-PHOTO-OCR-DISPLAY-A.xml`）、修改的 Python 运行 Ruff 与编译、修改的 JS 运行 `node --check`，再执行 `git diff --check`。建议用隔离浏览器/合成资产目测两个入口，若无法运行则报告 `BROWSER_PENDING`；不能把模拟测试说成真实浏览器验收。**不跑**全仓、真实 OCR 权重/GPU、打包、30 万照片压力认证。

## 7. 性能设计三行（遵循项目模板）

- **链路与风险 — PERF-BOUNDED**：当前照片信息加载后再增加一条定向 OCR HTTP 请求；最大风险是文件读/哈希挤占两条 Catalog 查询线程、长文字改变 viewer 几何、切图多次旧请求并发。无列表 N+1，也无整库数据范围。
- **做法与边界**：短主键 SQL → 最多一份 ≤16 MiB Artifact 在独立有界文件工作路径读/验 → 短版本复核 → 最多 128 KiB 原文返回；不持长事务、不创建全局缓存或新索引、不预取。若现有代码已提供等效机制直接复用。
- **验证与停止**：用定向合成测试检查工作线程分离、零写入/零 OCR 推理、快切不串图、固定 viewer 尺寸；观察到明显卡顿才补小型对照测量，不自定无证据的毫秒 SLA。没有本任务的 30 万/GPU 门槛。

## 8. 修改边界、交付、停止

**允许**：`src/photo_search/api/routes.py`、`api/queries.py` 或小型 OCR 只读 helper、确需时 `api/models.py`、`web/photo-info.js`、`web/photo-info.css`、直接相关测试；共享资源发生冲突时先核对最新 HEAD。

**默认不允许**：`web/app.js` 的大规模详情重做，`catalog/migrations/**`、schema、OCR 调度/模型、`search_documents` / FTS、图片预览/解码、索引/世代、收藏/人物/地图业务、依赖升级。若必须突破边界，记录阻断理由并停止扩大修改，不擅自顺手改造。

**实施后交付**：`docs/reports/P4-PHOTO-OCR-DISPLAY-A.md`（简短说明改动与真实测试数量、失败/遗留情况）及按惯例更新 `docs/development-status.md`。定向检查通过标为 **IMPLEMENTED / 定向 PASS**；未做浏览器和实图库验证仍需单列，不冒称全面 PASS。不要自动生成 B/C 卡。

**当前执行边界**：按本卡冻结的产品范围和验收标准实施。附带草稿原文末尾“仅优化 Markdown”属于立卡时的草稿编辑指令，已由用户本轮明确的“执行这个任务卡”取代。本次不访问真实图库，不运行真实 OCR/GPU、打包或大规模压力测试，不提交、推送或创建 PR/Issue。

## 本次执行记录（2026-10-20）

- 结果：**IMPLEMENTED / ACCEPTANCE PARTIAL**。实现范围和未完成验收列于[执行报告](../reports/P4-PHOTO-OCR-DISPLAY-A.md)。
- 定向验证：`tests/integration/test_p4_photo_ocr_display.py` 与 `tests/unit/test_photo_view_info_ui.py`，JUnit 13/13；Ruff、Python 编译、JS 语法、diff-check 通过。JUnit：`test-results/P4-PHOTO-OCR-DISPLAY-A-r8.xml`。
- 浏览器：loopback 内置浏览器 + 隔离合成库，首页共享查看器及完整详情均显示同一保存文本。其余任务卡矩阵和回归待续验。
- Luna High 首轮 P2（未检查 Artifact byte_size 上限）：已按 `MAX_ARTIFACT_BYTES` 在读取前拒绝并新增超限元数据测试；跟进复核确认关闭。API 空/异常矩阵、真实读取竞态、长文本显示/复制 UI、并发读取边界及其余回归仍 PENDING，因此保持 ACCEPTANCE PARTIAL；详情见报告。
