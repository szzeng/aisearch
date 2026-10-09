# P4-PERSON-ALBUM-PERF-A — 人物相册快速加载（个人试用最小优化）

日期：2026-10-09。状态：**PASS — 个人相册基础功能与小样本验证**。用户本轮明确要求个人相册只做基础测试：本卡采用下面的限定验收，不再把全仓B级转绿作为个人使用前置；既有全量失败保留，不追认全量PASS。实现与证据见[报告](../reports/P4-PERSON-ALBUM-PERF-A-implementation.md)。本卡是人物相册**浏览**性能优化，不包含“全部人物按照片数排序”。

## 当前个人使用验收范围（2026-10-09用户修订，优先于本卡旧全量收口要求）

- 复用已完成的小型合成对照和直接定向JUnit；代码/环境有变化或证据不足时，仅复跑 `test_p4_person_albums.py`、`test_p4_person_album_perf.py`、`test_person_detail_batch_ui.py`、`test_person_source_detail_ui.py`，保存JUnit XML。不重复三次性能测量，不跑全仓、30万、模型/GPU或打包。
- 一次基础交互：人物列表/名字搜索 → 点头像进入相册 → 点照片缩略图打开详情 → 上一张/下一张 → 返回相册；确认下一页无漏图/重复。复用小型合成样本，真实图库由用户自己操作，本任务不读取PhotoSearchData。
- 直接定向集保留计数/分页、命名冲突零写入、更正单脸不影响邻脸及撤销检查。关键保护不因简化而删除。
- 可行时记录图片是否实际显示；不要求冷/热缓存计时和真实体感测速。浏览器交互尚未完成就保留“基础交互待确认”，API/DOM通过只标对应范围。
- 受影响Ruff、Node语法和diff-check；既有独立高风险审查状态保留，本次只读性能卡不增加审查轮次。
- 日常闭环出现实际错误时，仅修该错误及直接回归；全量93项作为后置清单，停止自动生成/执行七组基线卡。

上述基础项完成后可标 **PASS — 个人相册基础功能与小样本验证**。不等于全仓测试转绿、真实图库提速、模型准确率或发行PASS。下面已有实现/测量内容供追溯，不要求重新实施或复测全部历史实验。

**模型：DS-H（DeepSeek V4.1 Flash／高推理）首选，GPT-6 Luna／High备选**；用户手动选择，同会话完成实现、调试和简测。只读局部优化通常无需Sol；遇到schema、持久化索引、身份、人工状态、事务或分页协议实质改变时停止扩大范围，按AGENTS/V3.1单独裁决。

## 1. 目标与代码定位

用户目标：打开人物页尽快看到可点击的人物及首屏头像；点人物后尽快看到首张照片。以30万张个人图库为长期规模，**尚无真实图库测速结论**。不能用SQL次数减少、后台识别吞吐改善代替人物相册的可见速度。

现有待核疑点：

1. `persons/albums.py::people_page` 每页40人逐人调用 `_album_summary`；经 `person_summary→member_counts` 两次COUNT，再逐人取头像，约120次附加SQL。
2. `photo_page` 每页60张经 `asset_page(... person_ids, order="capture_desc")` 选61张前瞻，再逐张 `asset_view`、`_photo_person_faces`；**主查询**本身的成员过滤与时间排序也可能慢，不能只优化逐张投影。
3. `web/persons-albums.js::boot` 无条件触发 `loadDetailSources()→GET /sources`（后端逐来源 `get_source→catalog_counts` 及progress）和 `loadDetailBatch()→/faces/source-detail-batches/active`；无活动批次时进一步请求 `/faces/source-detail-preview` 全库候选计数。它们与普通浏览无关，**属于并行启动请求，不应误写为被await的串行前置**；可能争用SQLite/事件循环，须用时序证实。
4. 首屏40张头像请求、相册最多60张照片缩略图及对应人物脸图请求；缓存未命中时媒体端可能生成预览/人脸裁剪。图片可见速度与API JSON速度是两回事。

## 2. 实施顺序（优先解决等待时间的主因）

### A. 小样本先定位

复用现有合成图库，同环境修复前后各测3次（不做耗时矩阵或30万基准）。至少记录：

- `/persons/browse`、`/persons/{id}/photos`：HTTP耗时与主要SQL次数；
- `photo_page→asset_page→_asset_chunk` 的**主SQL耗时**与逐张附加SQL耗时；
- `/sources`、`/faces/source-detail-batches/active`、`/faces/source-detail-preview` 的启动触发与耗时；
- 前端“人物名称/卡片可点击”“首屏可见头像”“人物相册首张照片可见”的时间，**区分API响应与img的load/可见时间**；冷/热缓存分开标注。

慢SQL才使用 `EXPLAIN QUERY PLAN`，尤其核对人物过滤和 `capture_desc` 是否触发大量扫描/临时排序；不因SQL次数下降就宣称加速。若无真实浏览器环境，可用最小DOM/合成HTTP证实请求和渲染时序，真实可见时间标明 **UNVERIFIED**，不编造耗时或“体验PASS”。

### B. 取消普通浏览启动的额外统计（优先小修）

- 日常人物列表/直达某人物时，不主动执行 `loadDetailSources()`（完整 `GET /sources`）或 `loadDetailBatch()`、`source-detail-preview`。进入确实需要“按来源补识别”的可见整理控件时再读取，并尽量复用已有数据；不用新API。
- 保留后台持久批次运行、暂停/继续/失败重试，不因页面不轮询就停止后台任务。重复打开、切换图库及关闭高级整理时防重复请求与过期响应覆盖。
- 只移除与浏览无关的请求，不能让人物列表、人物直达和整理入口因懒加载而失效。

### C. 人物列表限页批量SQL

- 仍先按当前 `kind/q/person_id` 游标选最多40人（加一条前瞻），只对本页人物ID批量读取有效脸数、照片去重数及封面；严禁先全库统计再 `LIMIT`。
- 与原 `MEMBER_PREDICATE`、`member_counts`、`_album_summary` 的手动头像优先、ACTIVE头像候选、排序及回退语义逐项等价。注意COUNT(DISTINCT asset_id)、同图多脸、合照多人物、排除/人工锁/旧脸替代、空未命名分组。
- 优先在 `persons/albums.py` 增加本页面批量投影，不修改共享函数的外部调用语义。检查JOIN放大/执行计划；防止批量化比原SQL更慢。

### D. 人物照片主查询 + 附加查询

- 先实测 `photo_page→asset_page→_asset_chunk` 的成员过滤、`asset_metadata` JOIN、时间排序及前61条取数。若主SQL占主耗时，应优先选当前成员范围可用的有界查询方式并证明与原权威scope、排序/游标完全等价；不能只批量优化两项逐张投影就收工。
- 针对当前60张照片ID批量读取时间事实及对应人物的人脸，去除逐张 `asset_view`、`_photo_person_faces`；保留每图最多8张及 `has_more`、面孔顺序、缺事实回退。
- 保留 `capture_desc`、`catalog_filter_revision` 游标、61张前瞻、每页60张、下一页、相册搜索/预览/更正/封面更换；不物化全相册、不提前加载后续页。
- 如果必须改变共享 `api/queries.py`，限定人物路径并补直接回归；需要新增索引或schema时暂停报告，不直接扩展。

### E. 图片加载只做有证据的小修

保留既有缩略图、人脸裁剪缓存和媒体受控路径。若首屏受图片拖累，先检查缓存命中、冷裁剪时 `_ensure_preview` 与解码、图片请求并发。允许仅对可见首屏的少数头像/照片调整 `loading="lazy"` 或加载优先级，其余保持懒加载；不改媒体生成身份、缓存键、权限、全库预生成或重新识别人脸。若确证同步媒体/SQLite阻塞事件循环且需要更改线程/连接所有权，先报告证据，不在本卡直接改并发模型。

## 3. 不可扩大范围

不改schema、迁移、持久化计数表、索引、模型/阈值/GPU/向量库、人物身份、人工分配/锁/排除/撤销、权限、API DTO、筛选revision、全球照片排序或按照片数全局人物排序。不访问/扫描/修改真实 `PhotoSearchData` 或私人照片；不自动下载模型。主要范围 `persons/albums.py`、`web/persons-albums.js` 与直接测试；需要共用模块改动须说明并验证其他调用方。

开工只读 `AGENTS.md → docs/development-status.md → 本卡`，以及主架构§8.3/10.3、V3.1策略§2.2～2.4/3.2/3.4与直接相关的 `persons/{albums,read,members}.py`、`api/{queries,routes}.py`、`web/persons-albums.js`、相关测试；不全仓重读。

## 4. 最小验收

1. **列表等价**：40人以上，命名/未命名、空未命名组、手动/失效封面、同图多脸、合照、人工排除/锁及旧脸替代。计数、头像、分页和名字搜索与原实现一致，额外SQL不再随本页人数线性增长。
2. **相册等价**：至少61张，验证首批60/续页、拍摄时间含缺失回退、对应脸、每图8+1 `has_more`、上一张/下一张及去重；主SQL无明显退化，附加SQL不再逐张线性增长。
3. **页面无关工作延迟**：普通浏览不请求完整 `/sources`、原图补识别active/preview；进入对应整理控件后来源和任务信息可用，旧响应不污染当前界面。
4. **既有耗时证据**：复用已有合成API三次对照，不新增性能测量。首屏可见时间与冷/热图片时间保持 **NOT MEASURED / UNVERIFIED**，不能把“请求少了”当“体感改善”。基础交互按卡首用户修订范围确认。
5. 仅相关定向pytest（JUnit XML）、Ruff、Node `--check`、`git diff --check` 和可行的最小DOM/合成HTTP时序。无需全量、真实图库、30万、GPU或打包测试。

## 5. 收口与报告

实现已改为人物页限页批量统计/头像、60张照片页的批量时间与人物脸投影、整理来源统计按需加载，以及少数首屏图片优先级。合成 API 对照、执行计划、SQL计数见[报告](../reports/P4-PERSON-ALBUM-PERF-A-implementation.md)与 `test-results/p4-person-album-perf-measure.json`。定向pytest、Ruff、Node、diff-check通过。历史B级全量结果 **2152 tests / 50 failures / 43 errors / 49 skipped** 仍为未通过；用户当前限定验收不要求先修绿全仓。当前收口仅余基础交互确认，不扩大到无关地点/冻结合约/迁移/ONNX修复。低风险只读变更记录 `SOL_NOT_REQUIRED`。

**判据：** SQL次数减少但可见速度没改善，不得声称“人物相册变快”；记录剩余主瓶颈与最小后续动作并停止扩张。本卡允许合成验证通过但明确保留“真实用户体感待验”。无需重新建库。
