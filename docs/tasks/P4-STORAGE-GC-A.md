# P4-STORAGE-GC-A — 个人相册存储体检与安全文件 GC

日期：2026-10-10。状态：**PLANNED / 未实施**。立卡基线：`6210c4df3a80560b6acb22aae97356802af2ea15`；开工前核对最新 `master`、Schema、文件和运行时接口。推荐实施模型：**GPT-6 High**。**个人相册、轻量实现、仅四组定向测试**。

## 1. 目的和实施边界

减少长期建库、图库来源删除、处理器版本迭代造成的**无引用物理文件、过期临时文件与无用媒体缓存**，并提供可信的空间体检。清理前后保证当前搜索、人物/合集、地点、OCR、撤销历史及索引恢复语义不变。

- 本卡只完成**统计、清理资格、文件级 GC、最小 UI/CLI**；LanceDB 压缩、索引优化和 SQLite VACUUM 交给 [P4-STORAGE-GC-B](P4-STORAGE-GC-B.md)。
- 不删除或改写用户原图，不删除 `assets`、`faces`、`persons`、`person_collections`、地点/OCR 事实、人工归属、操作回执及任何业务权威行。
- 不把“来源已删除 / 某位置 MISSING / 某个硬盘暂时离线”当作删除资产的充分条件。
- **不得访问或清理真实 `PhotoSearchData`**；开发验收只用隔离合成数据。任务完成后由用户自行选择现场执行。
- 优先**不新增 migration / 常驻服务 / 独立索引数据库 / 新依赖**；不能安全证明的类别列为“仅统计/需人工处理”，不为了多释放空间扩大删除规则。

## 2. 开工核对：可直接复用的已有代码

- `src/photo_search/config.py`：`catalog/`、`vectors/`、`artifacts/`、`thumbnails/`、`previews/`、`cache/` 和数据根路径。
- `src/photo_search/artifacts/store.py`：`ArtifactStore.resolve_uri/read/prune_temporary/prune_orphans/reclaim`。注意 `artifacts/pending` **名字不代表文件未发布**，不得整目录删。
- `src/photo_search/media/cache.py`、`media/status.py`：内容哈希与处理器版本对应的缩略图/预览、失败记录、人脸头像裁剪、库存及过期临时文件清理。
- `src/photo_search/persons/retention.py`：已实施的 P4-PERSON-OPLOG-GC-A，**30 天并且不在最近 200 条**才能清理快照，保留历史摘要与幂等回执；本卡只复用入口。
- `src/photo_search/catalog/store.py::delete_source`：删除来源仅删除 registration/location/scan，孤儿资产、产物、向量及人工事实故意保留。
- `src/photo_search/runtime/data_root_lease.py` 与 `backup.py`：既有停机独占租约、WAL/SQLite 一致性保护；不要自行另造第二套跨进程锁。
- `src/photo_search/web/index.html` 已有“图库管理与分析明细”；首版将入口嵌入该折叠区，避免新增顶级页面。后续 UI 总改版可搬迁。
- 注意服务中的扫描、图库刷新、媒体生成及人脸详情任务可能各自写数据；仅检查“此刻没有任务”不足以保证清理期间不会又有任务开始。

## 3. 存储体检（只读为默认）

提供两档结果：

1. **快速概览**：读取 `catalog.db`、WAL/SHM 的现存磁盘字节；SQLite `page_count/page_size/freelist_count`；可低成本读到的索引/历史计数；上一次深度扫描的结果与时间。普通打开页面**不得触发全目录递归**。
2. **手动深度扫描**：有界遍历 `vectors/artifacts/thumbnails/previews/cache/places/backups`（按实际存在目录）。返回每类磁盘字节、文件数、受保护/可安全清理/待核实量、跳过原因、进度、是否 partial。每个文件只计入一个物理分类，目录总量不得对 catalog 内的 BLOB 再次重复累计。
3. SQLite `face_embeddings`、`face_candidates` 的 `length(vector_blob)`、记录数只作为**catalog 内部逻辑分解**；不加到物理总量，不当作可清理字节。
4. SQLite freelist 仅表示**内部可复用页**，不是立即释放给操作系统的文件大小；人物历史预览里的 `estimated_net_savings_bytes` 是逻辑字节估计，不等于瞬时物理释放量。
5. 将“**占用、可安全清理、需离线维护、仅统计、未知**”分列；不能准确归因/文件读取失败时标记 `partial`，不能用粗估伪装确定的释放量。

深度扫描采用 `os.scandir` 等有界遍历、批量分页、取消检查；不一次加载所有文件路径/全部嵌入向量到内存。结果只保留数量、大小和必要摘要，不将几十万条本机绝对路径返给 UI。扫描只允许一项活动任务，重复请求复用状态或明确忙碌拒绝；取消不得损坏文件。

## 4. Artifact GC — 第一版严格只删“物理孤儿”

`ArtifactStore.prune_orphans()` 当前会把引用路径读成 Python `set`，并以部分生命周期/索引保护来判断。本卡**不能直接把它当成全局安全 GC**：应实现有界的资格计算（分页、SQL 临时索引或等价方法），避免 O(全库路径数) Python 内存集合。

删除资格必须**全部**成立：

- 路径在本程序控制的 `artifacts/` 内、是合法普通文件，后缀/分片结构符合管理范围，拒绝符号链接、junction/reparse point、路径逃逸及异常文件。
- 文件超过保留期：正式无引用孤儿默认 **7 天**；遗留 `*.tmp` 默认 **24 小时**；阈值是保守默认，未到期仅统计。
- 不存在**任何** `artifacts.storage_uri` 指向它（不只 `PUBLISHED`，也包括 `RECLAIMED` 等元数据状态）；若引用判断失败，保护而非删除。
- 不属于任何正在 prepare/publish/repair 的文件或仍可能写出的临时文件；所有未确认 outbox、当前/候选 generation 的恢复路径得到保护。
- 在执行阶段重新核对引用资格以及文件身份（大小、mtime、可能的可靠 file identity）；扫描快照失效则跳过，不依赖旧的预览结果直接删除。

**禁止**把 `reclaim()` 当作“垃圾删除入口”；禁止删除仍保留权威 Artifact 元数据的 embedding、结构化 OCR/GPS/人脸产物；禁止修改 checksum、replay、Artifact 生命周期以凑释放量。正式清理执行采用有界批次，报告成功、跳过、失败和实际释放字节；中断后安全重试、幂等。

## 5. 图片缓存与头像缓存

- 使用现有 `processor_id / SHA-256` 结构判断当前版本和引用，允许清理**确认没有有效用途**的旧版本缓存/过期临时文件/孤儿媒体缓存。
- 当前有效的 `thumbnails`、`previews` 默认保留；**不根据文件 mtime 推断上次访问**，首版不做 LRU、自动容量上限、每次启动清空或统一年龄淘汰。
- “手动清空预览缓存”是单独动作，预览中须提示：原始照片仍安全，但再次进入详情可能重新生成；图库离线时可能暂时没有预览。不得默默顺带删除缩略图/头像。
- `cache/face-crops` 作为可重建的展示缓存清理，不可清理任何 SQLite 人物或人脸归属数据。清理后能在原图可读时重新生成。
- 不因来源 `MISSING` 或离线就宣布缓存“过期无效”；按事实引用与具体缓存语义核对。
- 不在 GC 后主动批量重建预览，不启动模型或重新建库。

## 6. 互斥与可操作入口

**核心原则：共享一个清理资格/执行实现，区分入口和允许范围；不维护两套删除器。**

- 有浏览器服务运行时，只允许只读统计/预览；若要开放极少数在线缓存清理，必须真正与全部可能的发布者（包括另一个请求、图库刷新和人脸任务）实现同一个写入阻挡/互斥，再开放。**如现有机制不足，首版全部破坏性动作只走停机 CLI**，UI 显示停机维护说明；不能只检查队列为空便宣称安全。
- 离线 CLI 建议使用 `photo-search storage inspect`、`photo-search storage clean --stopped --apply --scope ...`（实际命令按现有 `__main__.py` 规范落地）；默认 dry-run/预览；没有明确 `--stopped --apply` 必须零写。
- 真正执行前取得 `acquire_data_root_lease`，检查无活动数据库/文件写者，参照备份对 WAL 与跨存储一致性保护；占用时结构化拒绝，不强杀进程、不删除 `service.lease`。
- 如 UI 使用 `POST` 开始只读扫描，沿用已有 session / Origin / CSRF；仅给出短状态/分类统计，不泄露原图路径。
- 现有页面“图库管理与分析明细”内新增“存储空间”折叠区：一键快速概览、手动深度扫描、占用/可清理统计、离线维护操作提示及人物撤销历史已有入口。无需新增独立导航、复杂仪表盘或定时器。

## 7. 最小测试（四组，限定合成 data_root）

- **A1 引用安全**：正式发布 Artifact、逻辑 `RECLAIMED`、未确认 outbox、真正无任何 metadata 引用的旧文件、过期/未过期 tmp 混合；只删确实合格者，索引/Artifact 元数据与修复语义不变。
- **A2 来源共享与离线**：两来源共享同一 asset，删其一仍保留另一；单来源 MISSING 和离线状态不触发权威数据删除；人物/地点/OCR/合集不变。
- **A3 媒体与空间统计**：新旧版本缓存、预览、缩略图、头像与 SQLite face 向量 BLOB；总空间无重复相加，按需预览清理后的再生成正常，深度扫描支持 partial。
- **A4 锁/并发/恢复**：模拟服务占用、扫描/发布竞态、GC 中断、二次重试、目录 symlink/junction/越界和删除权限错误；不得误删、漏报失败或触碰源照片。

仅执行相关 pytest/最小 UI DOM 或 API 用例、Ruff/语法检查/diff-check；不跑全仓、真实模型、GPU、30 万规模及浏览器矩阵。可额外做小型大量文件有界内存观察，但不要扩成性能研究。

## 8. 收口与交付

交付实现、简短测试证据与开发状态更新。PASS 需证明：体检数字与口径准确；有证据的孤儿/过期文件可释放；预览与执行一致性有保护；中断可重试；搜索、索引恢复及人工事实不受影响；原始照片零修改。清理后再次开启相册的缓存首访可能变慢，须在界面诚实提示。**本卡完成并不代表旧 Asset/向量已被删除，也不授予全项目性能 PASS**。
