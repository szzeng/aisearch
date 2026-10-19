# P4-PHOTO-SMART-COLLECTION-A — 动态智能照片合集与有界语义结果缓存

- **状态**：IMPLEMENTED / ACCEPTANCE PARTIAL（2026-10-19）。过期浏览、save-static、FILTER/地点排除排序分页已交付；联合受影响组 59/59、迁移/恢复/性能组 52/52 通过。本次修复 COMBINED active profile 缺失漏报、增加热缓存免检索和普通成员/收藏/智能排除隔离测试。真实 COMBINED 模型、活 worker 崩溃恢复、不同合集排队成本及 DS-H 全影响面审查仍未验。详见[执行报告](../reports/P4-PHOTO-SMART-COLLECTION-A.md)。
- **优先级**：P2；**建议执行模型**：GPT-6 High（用户在 Codex 手动选择）。人工状态、迁移和并发提交须按 V3.1 §2.4 做符合风险的独立 DS-H 影响面工程审查。
- **2026-10-19 收口目标（P4-PHOTO-SMART-COLLECTION-REFRESH-CLOSEOUT）**：只关闭智能刷新有界排队、运行中租约续期、已确认死亡实例的启动恢复及真实 COMBINED 模型闭环；不新增任务表/调度服务、不改变候选上限、检索语义或迁移版本。队列硬上限为 1 个运行、2 个等待；有旧有效缓存的任何失败、取消、接管或规则替换都保留旧 build 与人工排除。真实模型只用开发机既有权重和独立合成 PhotoSearchData。
- **本轮性能设计（PERF-BOUNDED）**：链路与风险：refresh API → 本地队列 → COMBINED 检索 → 缓存原子发布，主要风险是无界排队、重复模型查询、排队先占租约及模型等待期间 SQLite 写锁/租约过期。做法与边界：应用内最多 1 执行 + 2 等待，实际启动才取得 600 秒可续 lease，心跳采用独立短事务，检索期间不持写锁；候选 K≤5000，沿用现有模型与索引。验证与停止：集中请求/立即完成/提交异常/取消/关停/长任务/进程退出测试，独立真实模型闭环与隔离浏览器刷新重试；确认队列、调用和恢复不变量后停止，不新增 30 万、GPU 压测或 SLA 结论。
- **设计基准**：`szzeng/aisearch` / `master`；设计复核基准 `master @ d61e2fe3cdf60044f34132325298f4635be00dd8`（2026-10-19）；提交前远端 `master @ d5795b07a17e5a933ee0366ffe4d4acfb78e1462`；普通照片合集卡已提交为 [`d36c50d`](https://github.com/szzeng/aisearch/commit/d36c50d560bff9b1ef7e943ee0604ad72742d89b)，尚未实施。当前可见产品 Schema v33；**两张 Favorites 与普通照片合集均不能据任务卡存在而视为已实施**。实际开工必须检查最新 HEAD、工作树、已落地迁移、API、报告和活跃模型/索引合同。
- **实施前置**：`P4-FAVORITES-CORE-A` → `P4-FAVORITES-UX-B` → `P4-PHOTO-COLLECTION-A` 均达到其自身必要验收并具备可信代码。本卡可提前审查，不提前占 migration 版本，不改上一卡已冻结的人工成员真值。
- **必读**：`AGENTS.md` → `docs/development-status.md` → 本卡 → `docs/performance.md`、`docs/testing.md`、`docs/tasks/TASK-TEMPLATE.md`；V3.0 §9.2/9.3/9.5/§12、V3.1 §2.4/§3.2/§3.4/§4.2；上述三张前置卡与实测报告、`P4-SEARCH-QUALITY`、`P4-SEARCH-INTENT-UNIFIED-A`、`ADR-0005`、`ADR-0009`、`ADR-0011` 及直接受影响调用链。不可把文档假设当成最新真实接口。
- **交付策略**：一张任务卡完成**可用的智能合集闭环**；实现按「规则/纯筛选 → 排名缓存/刷新 → UI/恢复」顺序组织，同卡调试、验证、独审及报告；不自动生成 B/C。不要求真实图库、GPU 重建、30 万照片完整压测或正式发布验收，但新增数据库人工状态必须通过对应迁移、故障和恢复验证。

## 1. 用户语义与边界

用户在**已完成并确认的搜索结果**上选择「保存为智能合集」，输入名称，系统保存经过服务器确认的搜索规则。以后进入该合集时，符合当前规则且已完成相应索引/识别的新照片会按策略出现，无须再次手工保存；**纯条件型下次读取即反映权威数据，文字/AI 型需刷新排名缓存，不承诺入库后瞬时出现**。与普通合集不同，**智能合集的成员由规则计算决定**；用户可「从此智能合集排除」明显误判的照片，排除选择应持续生效，不能因为再次识别/刷新又回来。

三种搜索/执行类型必须严格区分：

| 类型 | 典型例子 | 打开时的执行路径 | 显示语义 |
| --- | --- | --- | --- |
| `FILTER` 纯条件 | 指定人物 + 深圳 + 2024 年照片 | 服务器原生 SQLite 条件/Keyset，每次按最新权威事实读取；**不建成员缓存** | 当前符合条件的完整动态合集（分页） |
| `TEXT` 按文字找 | 文件名/有效地点文本/OCR 包含“合同” | 已有 FTS 排名路径；复用有界**持久候选缓存**、索引版本失效 | 当前文字排名候选；不是无上限的全库结果 |
| `COMBINED` 综合搜索 | 海边日落、温馨合照，含来源/人物条件 | 已有 FTS + 向量 + RRF（及其现有前置权威过滤）；复用有界**持久候选缓存**、模型/发布版本失效 | 当前语义排名候选；不是永久客观标签 |

`TEXT` 即使完全不用 GPU，仍是**内容排名搜索**，不得当作纯条件任意全量分页；`FILTER` 不经 AI、不调用向量编码器。`COMBINED` 不把语义距离阈值或「高度匹配数」偷偷转换为新的分类规则。智能结果自然变化是设计意图，但**不能修改普通合集或收藏中的人工成员**。

**本卡包括**：搜索后创建智能合集、空态、目录/详情、改名/删除、查看条件、用新搜索结果**显式替换规则**、刷新状态、自动受控刷新、错误/离线降级、手动排除与**可找回的已排除列表**、复用统一 viewer 和照片浏览、在候选范围内切换时间排序、从当前快照「另存为普通合集」并明确候选上界。

**本卡不包括**：保存上传图片作为长期查询、全图库逐照片实时语义布尔分类、为每个智能合集在入库时执行 AI、每张照片/每次索引发布即触发全体合集重算、跨智能合集规则嵌套、机器学习重新训练、独立任务守护进程、自动导出文件、自动修改原图/人物/地点、任意自然语言规则编辑器、定期夜间全表扫描、额外数据库/Redis/云服务。首版不提供「搜索此智能合集」的二次语义嵌套搜索；也不提供针对智能合集的智能交集/并集规则组合。**普通照片合集既有“搜索此合集”保持不变**。这是明确产品边界，不得用全库 Top-K 后过滤冒充范围搜索。

## 2. 当前代码事实与执行边界

- 当前搜索存在 `retrieval_mode=combined|text`；综合查询会由 `src/photo_search/search/unified_intent.py`、`personal_search.py` 解析人物、地点、日期、来源等身份，`api/routes.py::_place_aware_search_worker` 选择纯条件、地点相册或排名路径。**一旦保存成功，自动刷新直接执行已确认的结构化计划，不再逐次重新解析原始自然语言**。
- `api/routes.py::_filter_only_search_page` 是无上限 SQLite Keyset（当前还执行 COUNT，需要在智能列表首屏避免重复无谓聚合）；`_place_album_search_page` 也是独立分页，不是有数据的 `SearchSession`。纯筛选规则存的是**权威筛选身份**，不是其伪 session ID 或静态搜索结果。
- `search/service.py::search_request` 当前冻结带排序证据的排名候选到**进程内** `SearchSessionStore`，`MAX_SESSION_IDS=5000`、`SESSION_TTL_MS=600000`、`MAX_SESSIONS=64`；进程重启后不存在。智能合集的持久缓存是**另一个可重建的派生层**，不能长期保存或冒充这些短期搜索会话。
- `search/retrieval.py` 已有 FTS、Lance 向量分块精确 Top-K、RRF 和后置权威复核；检索候选硬上限 5000，结果可能 `candidates_truncated`、索引扫描 `truncated`、`pending_semantic` 或 FTS `search_projection` 滞后。**增量追加新资产无法保证与全局重算的排名等价**；本卡不改变检索算法、召回、RRF、阈值、Top-K 或已冻结协议。
- `places/revision.py` 的 `catalog_filter_revision` 及事件已经涵盖大量筛选事实改动（含影响资产信息），但**不完整覆盖独立 FTS 投影发布、向量索引确认、SearchProfile 切换及可能不触发该全局修订的专项日期/包版本**；实施前沿真实生产者逐项验证。`search/documents.py::drain_queue` 处理派生文本，`indexing/store.py::process_batch` 确认向量；新增缓存失效需要正确观测这些已成功发布的变化，不能只比较 `catalog_filter_revision`，也不能靠 TTL 当作真值失效协议。
- 日期拍摄时间仅按现有 `taken_time` 规则；相对词“今年/最近30天”在创建时**明确落成固定的起止日期**，首版不暗中滚动日期。未来若实现相对日期规则须另审日历时区/日界与缓存依赖。地点保留源包和选择身份，人物保留 canonical ID/实例关系；失效、合并、删除都不能把范围默默放宽成“全部照片”。
- 普通照片合集 `P4-PHOTO-COLLECTION-A` 计划新增独立照片成员表、目录、缩略图、时间轴、viewer、搜索保存 `source_token`。**本卡只在其真实落地能力上做加性实现**：智能缓存不写入普通 `photo_collection_members`、不修改人工成员 `position/member_count/revision`，也不把既有 `person_collections` 当成照片集合。

## 3. 规则保存与身份语义

### 3.1 创建来源与规则冻结

- 搜索结果仅在用户**确认并完成的一次查询**上展示「保存为智能合集」。新建请求携带前置卡提供的服务端可信短期 `source_token` 或等价的本地认证会话绑定搜索凭据，服务端从可信执行上下文提取规范化计划；**不接受客户端任意自写 SQL、任意 `rule_json` 或仅凭查询字符串重新分类**。
- 持久化 `rule_version`、`execution_kind=FILTER|TEXT|COMBINED`、`content_query`（仅 TEXT/COMBINED）、原始搜索文字（只供展示）、确认为真的 `source_id/availability/place_ids/person_ids/person_match/capture` 等条件、固定照片合集/人物合集范围身份、已选地点的 `kind/key/source/dataset_revision` 及时间规则版本、创建时检索模式/模型绑定。所有字段由**服务器白名单结构验证**，只允许复用已有搜索能力能忠实执行的规则。
- 一次查询仍需用户挑选人物同名、GeoNames 同名地点或来源冲突时，不允许保存未定的「自动猜测」规则；存的是用户**最终确认后的身份**。人物改名后沿 canonical 关系保持原对象语义，合并/拆分后依既有重定向/修订确认；地点包退役或身份无效则标 `RULE_NEEDS_ATTENTION`，**不丢弃这条条件**。
- `FILTER` 对空内容规则允许创建（等于目前可搜索的全部图库），但 UI 必须明确提示「匹配范围为整个图库，查询按页进行，不物化 30 万资产」。`TEXT/COMBINED` 的范围与 `retrieval_mode` 一经持久化，不在刷新时自动切到不同模式；`COMBINED` 未有可用编码器/绑定时不自动回退到 TEXT。
- 创建智能合集不等于执行一次全范围分类。可以直接复用**本次仍有效**的排名会话候选为初始缓存，但必须通过新的规则签名、图库权限/Revision/索引发布状态和**该 source_token 所指实际执行结果（不能凭查询文字重演）**验证；不能从浏览器当前 500 DOM 卡片抄 ID。若原搜索只返回候选列表而无法恢复排名/计划身份，先创建规则并显示「等待首次刷新」，绝不悄悄重新搜索并宣称已复用。没有有效搜索结果或查询尚待消歧时，保存入口禁用并给出原因。
- 规则编辑首版采用「通过新搜索确认后替换当前规则」；替换须 CAS `instance_token + expected_revision`，旧缓存随即对新规则**不可见**，旧请求结果不许写回。不在卡内重写一个完整高级搜索编辑器。**替换规则不自动清除人工排除**，界面提示原有排除数并允许逐项恢复；即使该资产目前不再匹配规则，也保留用户排除意愿。

### 3.2 规则版本、模式/模型不兼容

- 规则持久化使用严格、带版本的服务器结构（规范化 JSON 或等价列+JSON），禁止持久化 SQL 片段、临时游标/会话 ID、上传图像 bytes、任意 Python 表达式。
- 每次新搜索/刷新按**现有权威身份**再次验证：被删除/禁用人物、固定来源、地点包变更、被删除的静态父合集、拍摄时间规则变更等不得被静默忽略；无法等价执行时返回 `RULE_NEEDS_ATTENTION`，显示旧条件及操作办法，不扩大筛选。
- `COMBINED` pin 创建时真实使用的 SearchProfile/space/generation/parser/tokenizer/fusion 配置版本与搜索时绑定。当前有效模型或生成代发生变化时标 `MODEL_REBIND_REQUIRED`；**首版直接引导“用当前模型重新搜索→确认替换规则”**，复用上述可信 source_token 与 CAS，不额外造一套模型重绑定业务/API。模型暂时不可用标 `RUNTIME_UNAVAILABLE`，旧缓存可带提示浏览，但不冒称最新结果。
- `TEXT` pin 分词器/FTS 代及 `retrieval_mode=text`，不要求加载 GPU；相应代不可用或检索规则改变时标 `RULE_NEEDS_ATTENTION`，不能从原文偷偷解析成人物/地点语义。

## 4. 数据库：纯条件无成员表；排名缓存与排除分开

**推荐独立、加性、最小的 `photo_smart_*` 命名空间**。原因：普通 `photo_collections` 当前任务卡将其定义为静态人工成员与 `member_count/next_position`；等它实施后再为智能合集重建该表或让智能缓存写进成员表，容易破坏人工真值。智能目录在 UI 上可并入同一「照片合集」导航，用明确 `kind=smart` 标签和专属 `smart_id/instance_token` 区分普通合集，**严禁把两种整数 ID 混用**。若前置卡实施时恰好提供稳定通用的合集根表，可在开工复核后复用，但不得改坏旧成员 API/迁移。

推荐表/索引（DDL 由执行者以实际 schema 与迁移约束落实，不预先占迁移号）：

| 持久对象 | 职责与约束 |
| --- | --- |
| `photo_smart_collections` | `smart_id PK`、`instance_token UNIQUE`、`name/name_key`、`rule_version`、`rule_json`、`rule_hash`、`execution_kind`、`revision`（用户操作 CAS）、`rule_revision`（仅规则变化）、`exclusions_revision`（仅排除变化）、创建/修改时刻；严禁以排除版本充当规则缓存失效信号，原规则持久化为权威真值 |
| `photo_smart_exclusions` | `(smart_id,asset_id) PK`，用户排除意愿为**人工持久真值**，带创建时刻；只有明确恢复才删除；依赖主键支持唯一性，另有 `(smart_id,created_at_ms DESC,asset_id DESC)` 分页索引和 `(asset_id,smart_id)` 反向引用索引；FK `asset_id ON DELETE RESTRICT`、`smart_id ON DELETE CASCADE`（仅用户确认删除智能合集时） |
| `photo_smart_cache_state` | 每个 ranked 智能合集唯一状态：已发布 `build_id`、`rule_revision`、依赖签名、刷新时刻、候选数/截断/投影状态及可解释错误；按需记录有期限的刷新 `lease_owner/lease_until`，防多进程/重启后重复提交；**仅派生数据** |
| `photo_smart_cache_entries` | `(smart_id,build_id,rank)` 的 Keyset 索引 + 唯一照片身份；每次构建最多 5000；还须 `UNIQUE(smart_id,build_id,asset_id)` 防止同资产重复排名；保持原排名 `rank`；缓存引用资产使用 `ON DELETE CASCADE` 或清理前显式删除派生缓存的策略，不得作为人工保留理由；失效、丢失、可重建 |
| `photo_smart_cache_builds`（有证据才增） | **默认优先一次有界短事务插入 ≤5000 缓存行并原子切换已发布指针**；若实际争锁/写耗时不合理才用短批 staging 与额外 build 表，最终发布仍一次原子事务。不得为“预防万一”先造任务队列和无限历史 build；失败/重启的孤儿 build 清理须有界且可恢复 |
| 索引发布版本 | 首选复用已存在、能证明完整覆盖的成功发布修订；如无则在 FTS 文档实际 `drain_queue` 写入、向量 `process_batch` 真正确认发布成功的**相同 SQLite 事务**各维护轻量单调 `fts_published_revision` / generation 级 `vector_published_revision`。按成功批次推进、no-op 不推进、失败不推进；不修改旧搜索的结果排序、权限及 `catalog_filter_revision` 语义 |

`photo_smart_collections` 不需要复制 30 万照片；`FILTER` 连 `photo_smart_cache_entries` 都不写。缓存条目是*可再生投影*，因删除资产或垃圾回收而清除不能阻止资产清理；人工 `photo_smart_exclusions` 必须加入 `library_retention.py` 的已知引用和保护条件，不应被当作可丢弃缓存。**排除可以在该资产当前不命中时继续存在；资产临时离线也不撤销排除。**普通人工合集成员仍有其原独立保护。**删除一个智能合集**删除其规则、排除与缓存，不触碰 `assets`/照片文件/收藏/人物/地点/模型索引。

人工修改需区分三类修订：`revision` 负责前端写入 CAS，替换规则另增 `rule_revision`，排除/恢复另增 `exclusions_revision`；重命名不触发排名重算，排除/恢复**只更新 SQL 排除覆盖层而不重跑 FTS/向量**。缓存依赖签名只绑定 `rule_revision` 与搜索环境依赖，**不绑定 `exclusions_revision`**；`FILTER` 分页游标绑定 `instance_token`、规则/排除修订、排序和当前筛选依赖版本；**ranked 旧缓存允许带“待更新”继续分页**，其游标应绑定 `instance_token`、规则/排除修订、排序、已发布 `build_id` 以及每次显示资格复核所依赖的当前范围修订，**不因单纯 FTS/向量发布推进就直接报 409**。当缓存指针切换、规则/排除或权威筛选事实变化才明确 409；FTS/向量变化只改变新旧状态并触发受控刷新。目录变更另按实际实现维护目录修订。所有上述操作不提高 `catalog_filter_revision`、`favorites_revision` 或图片向量 outbox；不采用 TTL 代替 409/CAS。名称可与人物合集相同；若普通/智能允许同名，在目录里必须有显著类型区分，不让误删发生。

## 5. 动态读取、刷新与失效策略（本卡核心）

### 5.1 `FILTER`：最新 SQL 即成员，不做刷新任务

- 打开纯条件合集时，复用 `SearchScope`/权威 SQL、`photo_asset_sql`、人物 canonical、地点确认/包版本、拍摄日期规则；条件**先于分页**，默认按现有“加入图库时间”倒序 Keyset，可切换受信拍摄时间排序（未知日期在尾），照片网格一次约 50，时间轴复用已支持的同义 date/time 合同。
- 对 30 万规模，不能每次目录渲染所有 `FILTER` 合集 COUNT/逐个做查询；详情首屏只做必要分页，精确总数可由用户操作或可复用的轻量受控统计按需计算，未计算显示「数量待统计」而非 0。稀疏条件检查是否存在从 `assets` 全库逆序扫描再逐行 EXISTS 的风险；按实际条件选择成员/倒排索引驱动后全局排序的等价路径，不能先 LIMIT 再过滤或丢失不含拍摄时间项。**沿用权威谓词和 Keyset 合同，不机械照搬现有 `_filter_only_search_page` 每页的全量/已跳过项 COUNT**；总数按需计算，未计算绝不填 0。
- 用户加入新图库、人物归属/地点信息改变、时间事实完成后，**下次读取自然得到最新合格照片**，无需刷新缓存和 GPU。分页游标绑定全局过滤版本、相关来源/地点专属版本、规则/排除修订、时间口径；版本变化以明确 409 + 刷新第一页，不能新旧结果混页。

### 5.2 `TEXT/COMBINED`：版本签名 + 有界排名缓存

- 缓存键至少包含：`smart instance + rule revision/hash + canonical scope 身份及其专属 revision + catalog_filter_revision + FTS 发布 revision/分词器代 + 模型绑定（COMBINED）及该 generation 向量发布 revision + 有效拍摄时间规则版本 + 必要的搜索排名规则版本`。`TEXT` 不因其它模型的向量发布而失效，`COMBINED` 不因其它无关 generation 的发布而失效；无法可靠区分时宁可保守失效，**绝不能漏掉影响结果的成功发布**。只有同一键、仍可执行且不处于待更新状态才标作 `CURRENT` 并复用缓存。**签名应采用同一数据库一致性读视图获得的权威版本组合**；不把“索引队列为空”当成“没有下一次发布”，也不把发布版本推进等同于全部索引准备完成。
- `catalog_filter_revision_events` 可以在未来用来减少无关范围失效；本卡**不要求消费为精确增量变更日志**，也不根据旧 Top-K 和新照片单独检索拼装所谓等价排名。变化只标记/推导 `STALE`，不会直接触发 N 个智能合集重跑。
- 未有缓存时首次打开显示「正在生成结果」，请求调用原来的 `search_request`/retrieval 路径运行**一次**实际搜索，不复制 FTS/Lance/RRF 逻辑；`TEXT` 无编码器，`COMBINED` 使用现有绑定编码器和权限检查。得到真实候选排序、截断、FTS 投影状态、索引扫描完成度，作为一个版本化结果构建；如果已经存在同一有效缓存，直接读取，不调用模型。
- 检索前后各取签名，并在最终 `Catalog.write` 发布时**重新核对**规则 revision、权限/身份、依赖修订及检索模式/模型；变化则丢弃新 build，保留旧发布缓存并标 `STALE`，不发布“半新半旧”排名。多个刷新并发同一 smart 应只允许一份实际运行（进程内 single-flight + 数据库短事务有期限 lease；**仅在证实产品固定单进程时才可以省略跨进程 lease**）。lease 过期的旧工作即使完成也不得发布；应用重启后恢复或安全重试。对于 5000 条写入若单事务锁持有过长，使用短批 staging + 指针原子发布，旧 build 逐步回收；长计算与 SQLite 写锁绝不重叠。
- 已有缓存但签名过时，**先显示旧排名并标识“上次结果 · 待更新 · 上次更新于...”**，而且必须基于**当前规则和当前权威范围在 SQL 侧批量复核每页候选**（包括照片资格、删除/离线政策、人物/来源/地点及固定父合集资格），不能只检查 `asset_id` 是否存在；若规则被用户替换、身份失效或权威范围无法验证，则旧缓存**不可按新规则展示**，展示明确的受限状态而不是扩大到全图库。阅读 UI 时应清楚区分“上次缓存排名”与“当前最新匹配”；不能在后台未更新时显示“全部符合条件”。必要前景操作（例如手动刷新、另存普通合集）必须复核发布版本/当前资格。
- **状态采用两条互不混淆的轴**：`freshness=NO_CACHE|CURRENT|STALE|REFRESHING|BLOCKED` 与 `coverage=BOUNDED|TRUNCATED|INDEXING|UNAVAILABLE`（或等价明确的结构化枚举）。`CURRENT` 只说明依赖版本一致，**不意味着搜到了全库所有语义匹配**；`TRUNCATED/INDEXING` 不可被 UI 隐藏。OCR/FTS 或向量索引仍在追赶时 (`search_projection` 未 SYNCED、index outbox/READY/CONFIRMED 未稳定、候选扫描 truncated)，**不把结果标 `CURRENT_COMPLETE`**。可作为「部分候选 · 索引处理中」缓存以供浏览，但在索引持续发布阶段不要每批自动重新计算；等后续查询/进入页面发现已静稳并满足重试条件后再刷新。候选达到 5000 标明「最多 5000 条候选，可能还有其他照片」，不能偷称全库总数。
- **刷新节流**：只在用户进入智能详情或点击「刷新」时检查签名，普通合集目录只读元数据；对同一智能合集 single-flight，自动刷新同一时刻全局最多 1 个（如现有运行时有更严格资源调度则以其为准），**以使用该产品进程的有界可取消执行单元运行，API/事件循环只登记任务和轮询状态，不在读取接口同步执行重型模型**，索引忙/运行时不可用/失败后保留旧缓存并退避。不要建立扫描全部智能合集的常驻定时器、重复装载模型或抢占现有导入 GPU；**GET 详情/列表必须为只读**，前端在进入过期合集时可以经鉴权的 `POST /refresh` 自动请求一次有界刷新，状态通过轮询或用户重进查看。用户点击手动刷新可跳过普通冷却时间，但不能跳过模型可用性、索引忙时的资源保护、签名校验和 single-flight。刷新执行应复用已有异步执行隔离机制，**不能同步阻塞事件循环**。
- **缓存保有范围**：每个 ranked 智能合集最多保留 5000 条**已发布**候选（构建中最多一份 5000 条，历史 build 清理有界）；不存图像、向量、embedding；不缓存无限个历史 generation。磁盘占用随 `ranked 合集数 × 5000` 增长，后台刷新全局并发有界。自动刷新并非保证导入完成即立刻同步；UI 明确「下次进入/刷新时更新」。

### 5.3 人工排除与时间轴

- 纯条件合集直接在权威范围中 `NOT EXISTS(photo_smart_exclusions ...)`；排名合集在**选出本次前 5000 排名候选之后**应用用户排除（不会自行把第 5001 名提升成命中，除非未来单独定义补位规则）。排名合集按原 `rank` 的整个已发布 build，在 SQL 层同时套当前资格与排除过滤后 `LIMIT page_size+1`，不得每次先取原始前 50 后丢弃造成空页/错误 `has_more`；因筛选变密而扫描旧缓存时上界始终为该 build 的最多 5000 条。两种模式都按唯一 `asset_id` 去重；「从智能合集排除」是人工长期偏好，不会移出普通合集/收藏，不删除原图。**必须提供“已排除”独立子标签/管理面板**，按排除时间 Keyset 浏览（含当前不再匹配规则或暂时离线的资产占位）并支持“恢复按规则匹配”；恢复只删除排除记录，照片是否立即出现仍取决于当前规则是否命中。不能让用户排除后彻底找不到它。
- 缓存候选计数、排除后显示数、页面当前可见数、全库匹配数**不能混用**；全库匹配总数对 ranked 模式是未知的。排除写入单次短事务 + CAS、幂等，分页 409；客户端重载当前可见页，不让旧排除回流。
- 默认 `FILTER` 按当前 SQL 的时间顺序，`TEXT/COMBINED` 按缓存的相关度顺序；可切换拍摄时间，只对正确的**整个有效缓存候选集合**按既有可信 capture 时间规则排序后分页，不能先取前 50 再重新排序。复用现有时间轴组件和照片查看弹窗；包含未知拍摄时间、离线、缺图占位。缓存已过时，时间轴标注为「上次候选结果」，不能把该范围当作全库时间轴。

## 6. API、UI 和兼容合同

建议在 `photo_smart_collections` 独立服务内集中 SQL/规则编译/缓存失效，`api/routes.py` 仅增加鉴权路由；所有写入走现有 `Catalog.write`、session/Origin/CSRF、结构化错误。请求入参采用服务端签发 ID、实例与 CAS，不从浏览器提交 `additional_condition_sql`。

| API（建议） | 要求 |
| --- | --- |
| `GET /api/v1/photo-smart-collections?limit=20&cursor=...` | 可用于合并照片合集目录的元数据页，返回类型、名称、规则摘要、更新时间、可用缓存数量及状态；不为每个智能合集触发检索/COUNT |
| `POST /api/v1/photo-smart-collections/from-search` | `{name,create_request_id,source_token}`，从本地认证会话的权威检索计划创建，经校验后落规则；同键+同载荷重试幂等、不同载荷冲突；不持久化 source_token |
| `GET /api/v1/photo-smart-collections/{id}` | 规则身份、解释性条件、`FILTER/TEXT/COMBINED`、规则/缓存状态、最近刷新和错误；客户端不能得到 SQL 或秘密凭据 |
| `PATCH/DELETE /api/v1/photo-smart-collections/{id}` | 仅持久业务字段，需 `instance_token,expected_revision`；删除确认只删智能规则/排除/缓存，不删照片 |
| `POST /api/v1/photo-smart-collections/{id}/replace-rule` | 用**新的一次已确认搜索**的 source_token 替换规则；旧缓存即时对新规则不可见；同键/并发 CAS 安全 |
| `GET /api/v1/photo-smart-collections/{id}/assets?limit=50&cursor=...&order=...` | `FILTER` 用当前 SQLite 权威分页；ranked 用发布缓存有界分页 + **当前完整范围 SQL 资格复核**；游标绑定 `build_id`、规则/排除与当前筛选资格修订，缓存环境更新本身标 STALE 而不直接禁止续页；候选在 SQL 中整页过滤、取得准确的 `has_more`，不先取 50 条再剔除使页内数量异常；响应含范围说明与状态 |
| `POST /api/v1/photo-smart-collections/{id}/refresh` | 有界非阻塞刷新发起，返回 `QUEUED/RUNNING/DEFERRED/ALREADY_CURRENT` 等状态 + `refresh_id`（仅短期状态凭据）；同一 smart single-flight，不生成重复 GPU 工作；由 GET 详情读取最终发布状态，进程退出可安全重试，不承诺异步通知 |
| `PUT/DELETE /api/v1/photo-smart-collections/{id}/exclusions/{asset_id}` | 显式排除/恢复目标态，需实例+期望修订，未知 ID /非图片完整拒绝，幂等 no-op 不加版本；只提升 exclusions/CAS 修订、不清除排名缓存 |
| `GET /api/v1/photo-smart-collections/{id}/exclusions?limit=50&cursor=...` | 已排除条目独立 Keyset 列表，按排除时刻+asset_id 排列；离线/当前规则不命中的记录仍可见、可恢复，不逐图 SQL |
| `POST /api/v1/photo-smart-collections/{id}/save-static` | **从当前有效可解释结果另存普通合集**：`FILTER` 限最多 5000，超过拒绝不截断；ranked 只复制该次已发布、**版本一致、经当前全部规则复核**的最多 5000 候选，明示 `truncated/STALE/PARTIAL`，只有用户明确确认上次候选快照时才允许从 stale 另存；调用普通合集可信服务端批量创建合同；独立幂等键防重复创建 |

状态建议区分**正常读取状态**（`CURRENT`、`STALE`、`REFRESHING`、`PARTIAL_INDEXING`、`REFRESH_DEFERRED`）与**真正的请求失败**（如 `RULE_NEEDS_ATTENTION`、`MODEL_REBIND_REQUIRED`、`RUNTIME_UNAVAILABLE`、`CURSOR_STALE`、`VERSION_CONFLICT`）；按现有 API 错误协议映射，不把正常的旧缓存标记一律当 HTTP 409，更不能用新增字符串覆盖冻结的 `SEARCH_SESSION_EXPIRED`/`CATALOG_FILTER_REVISION_STALE` 语义。发生创建或刷新失败绝不冒称成功；必须保留人工规则、排除及既有已发布缓存。纯 FILTER 的新页无需等刷新请求。

**UI**：沿用普通照片合集的「照片合集」目录，增加 `智能` 类型标签及可区分图标；搜索结果「保存为合集」增加清晰的**普通 / 智能**选择。**目录建议同一“照片合集”入口下提供“普通 / 智能”两个标签并各自独立分页**，避免为了混排两个异构表而引入跨表全局 COUNT、封面 JOIN 和复杂统一 cursor；不强制将两种 ID 合并成一个序列。智能详情显示搜索条件、执行模式、是否最新、索引追赶/模型不可用、最近刷新时间和更新按钮。详情照片复用现有懒加载、DOM 回收、共享 `AIPhotoInfo`/Favorites 星标与返回锚点；**已排除列表不能从主结果入口“猜出”**，须有清晰入口及恢复动作；`FILTER`/ranked 的默认排序解释必须不同。模型更换时只给出“重新搜索后替换规则”指导，不实现独立 profile-rebind 编辑器。加入普通合集通过已存在显式批量动作；智能界面不出现误导性的「手动添加到智能合集」按钮。`Esc`、Back、切换合集、滚动回收、异步刷新迟到结果必须清理/按实例与版本丢弃旧 UI 响应。无需新增 JS 框架或第六份独立相册 HTML 页面。

## 7. 性能设计（遵守新 `docs/performance.md`）

- **链路与风险，初始 PERF-BOUNDED**：图库 30 万照片时，最重路径是 `FILTER` 稀疏范围的 SQL 分页/时间排序、`TEXT/COMBINED` 缓存失效后重复全文/向量检索、目录逐合集 COUNT/封面 N+1、5000 缓存写锁与前台索引争用。检索代价仍由已有搜索引擎和其候选上限决定，不伪造新 GPU 延迟预算。
- **做法与边界**：`FILTER` 不做成员物化；默认约 50 张 Keyset + 索引驱动/有界批量投影；`TEXT/COMBINED` 缓存每合集最多 5000、版本不变直接命中、排除操作不重检索、单-flight/全局一次刷新、索引追赶节流、派生 build 原子发布；缓存查询只对结果页做**完整当前规则**的权威资格批量复核及照片/位置批量读取。智能合集目录**零自动检索**，普通照片/人物/地点/收藏页面零新增重型查询；无逐资产重复 OCR/AI/embedding。
- **验证与停止**：在编码前列当前真实 N/K/P、索引与调用次数；合成稀疏/密集筛选、同名身份、5000 缓存/刷新、图片与跨页排序跑 `EXPLAIN QUERY PLAN` 和 query count，比较结果 oracle；用假编码器/FTS/索引和可控时钟覆盖“无变化不重算、排除与改名不重算、一次真实发布会失效、发布失败不失效、投影追赶不风暴、lease 过期与重启恢复”。若发现明显 30 万扩展风险或写锁长占，先同卡做有界小型对照，证据充分才升级 PERF-SCALE。不必默认跑 30 万整库/GPU，不以小夹具宣称 30 万吞吐达标。

**禁止为追求速度而悄悄降低 `top_k`、修改模型/RRF、跳过权威规则复核、把过时结果当最新或让索引未就绪时返回“全库没有匹配”。** 统计含刷新前后 SQL/模型调用数、缓存 entry 数/占用、目录首屏与前台可交互状态（实际能观察到的范围）；只提交与本任务相关的有效性能结论。

**编码前基线（2026-10-19）**：图库 N 无授权读取，按架构 30 万支持目标设计，不将其当作本轮实测；既有排名候选硬上限 K=5000，页面 P=50；智能目录最多读取一页 20 个元数据且不触发每合集查询/COUNT；刷新预算为单合集 single-flight、全局最多 1 个实际检索 worker。合成浏览器只含 3 张照片，不用于规模推断。性能停止条件是任何读 GET 触发检索/模型、缓存超过 K、并发超过 1、或有界页面退化为全库资产扫描；命中即先修正/限缩，不进入 30 万压测。

## 8. 测试与独审（重点用例）

1. **迁移/真值**：最新 Schema → 下一连续迁移可重复启动；普通 `photo_collections` 成员、`asset_favorites`、人物/地点、旧 `index_entries`/`search_documents`/索引 generation 不变。旧程序面对新版库继续 `SCHEMA_TOO_NEW`；中断/恢复、备份/还原、人工排除保护与缓存可丢弃语义验证。不得把缓存表误判为必须阻止资产 GC 的人工引用。
2. **规则准确性**：综合、按文字找、纯人物/日期/地点/图库来源、同名消歧、固定人物合集/静态照片合集范围；用同一确认后的执行计划与原搜索 oracle 对照成员和排序。未知/损坏/自行拼装的 `rule_json`、SQL 注入、跨认证会话 source_token、过期/重放、同键异载荷、删除/重建同 ID token、模型/分词器变化都明确拒绝/提示；不会误放宽到全库。
3. **`FILTER` 动态**：插入新照片、日期抽取、人物确认/撤销/合并、地点改名/包切换、普通合集范围成员改变，下一次读取即反映最新（或规则明确失效）；无向量/FTS 调用；50 张首/续页、409、未知日期、照片多位置/离线、稀疏/稠密与实际排序等价；首页不开全量 COUNT。
4. **排名缓存完整性**：`TEXT` 不装载模型，`COMBINED` 不误绑定 generation，真实候选顺序与原服务一致；缓存冷启动/暖复用/进程重启/新索引发布、OCR 投影批处理、模型切换、编译规则替换、5000 截断、`pending_semantic`/`search_projection=LAGGING`/扫描 truncated，所有状态描述正确，**同签名不重复执行 FTS/向量**；重命名或排除/恢复也不触发重算。目录/详情 GET 零写入、零模型工作；只有明确 POST 能实际触发刷新。
5. **并发/故障**：两个页面/两个进程同时刷新同一个 smart、两个 smart 请求刷新、写锁争用、读中规则修改、**排除在检索执行中发生后仅覆盖排除不丢弃有效排名计算**、模型失效/原索引未 READY、index batch 实际确认失败、刷新后发布失败、分批 staging（如启用）中断、网络超时/写结果未知、lease 过期/旧 worker 迟到；旧发布缓存持续可读，不出现半发布候选或旧刷新覆盖新规则。按事务确认事实变化推进版本，no-op 不推进；无法保证完成的刷新不写 `CURRENT_COMPLETE`。
6. **人工排除/另存**：排除后主结果消失、**“已排除”分页列表里仍可找回（包含离线或已不命中规则者）**，恢复后依现行规则决定是否出现；排除在刷新前/后均不复活、不导致 FTS/向量重算，幂等排除/恢复、旧游标 409、排名 5000 之后不偷偷补位；转普通合集后静态成员稳定，源 smart 后续刷新与人工普通合集互不影响，当前候选截断状态完整保留。
7. **UI/浏览器**：用隔离合成图库、应用 `127.0.0.1`、一次性会话在 Codex 内置浏览器重点检验「搜索→创建 FILTER→新图出现」「搜索→创建 TEXT/COMBINED→缓存再次进入→手动刷新」「排除→**已排除列表**→重进→恢复」中的可行关键流程，以及共享 viewer/返回/照片收藏不回归；必要内容索引可用合成或假适配器，**不能以 fake UI 冒充真实浏览器**。浏览器工具不支持的要求记录 `PENDING/BLOCKED`，不冒称 PASS。

按 `docs/testing.md` 与 V3.1 §3.2/§3.4 覆盖**完整受影响** B 测试、人工状态/迁移/并发/失败恢复 D 测试、受影响 UI 与必要独立 DS-H 工程审查。schema 迁移语义或影响面无法界定若触发全量回归，按规定执行或如实标未完成；不因本卡是“个人使用”而把必要门槛豁免。`node --check`、受影响 Ruff/py_compile、`git diff --check`，pytest JUnit XML 保留实际 exit；生成材料与截图进入忽略 `test-results/`/`.tmp/`。报告只记录可信覆盖与未验项，不宣称未经验证的 GPU/30 万性能 PASS。

## 9. 实施顺序与停止条件

1. **依赖审查**：检查 Favorites 两卡与 `P4-PHOTO-COLLECTION-A` 真正实现的数据库、`source_token`、搜索范围及 UI 回归；不具备则停止编码，允许保留本设计卡。以最新索引/修订的真实发布事务为依据，不机械照抄本卡推荐表名。
2. **确定规则契约**：规范化并验证 `FILTER/TEXT/COMBINED` 持久规则、模型/身份绑定、实例/CAS、元数据迁移、保留/备份/独审测试；不破坏普通合集真值。
3. **先做 FILTER 动态闭环**：服务端源 token 创建、规则查询、分页/计数成本、条件失效、排除/恢复、与统一 UI 的基本集成；保证没有模型请求。
4. **再做 ranked 缓存**：优先复用完整既有搜索工作流；补充分别覆盖 FTS 与当前 generation 向量发布的失效信号；实现冷建、命中、排除覆盖层独立修订、single-flight/lease、节流、**默认单次有界发布（实际必要才用 staging）**、截断与投影追赶、失败恢复。
5. **收口 UI 与普通合集联动**：普通/智能两标签目录、已排除管理、显示状态、模型不兼容时引导重新搜索替换规则、另存普通合集、viewer/返回、时间排序/时间轴；不扩展智能嵌套范围搜索。
6. **验证/独审/报告**：按本卡必测范围实测；完成任务报告 `docs/reports/P4-PHOTO-SMART-COLLECTION-A.md`、必要开发状态更新。仅本卡全部必测通过才记 PASS，未验证项如实记 PENDING/BLOCKED。完成后停止，不自动扩展到准实时监控或其它任务卡。

## 10. 2026-10-19 执行结果与验收边界

- **性能三行**：主风险为排名缓存失效后检索及 FILTER 动态分页；排名缓存 K≤5000、页 P≤50、worker 单并发，FILTER 不物化成员；定向合成 JUnit 119/119 通过，未做 30 万/GPU/真实图库测试，不据此声称容量保证。
- **验证**：完整 unit/integration 3269 项为 3212 passed、6 failed、51 skipped、0 errors，6 项中 5 项是并行 UI 资源版本/既有 query digest 断言，1 项是运行途中才更新的 app.js 缓存版本断言；最终受影响组合 119/119 通过。Ruff、相关 py_compile、Node 语法与 diff-check 通过。实际命令、JUnit 路径和逐项解释见执行报告。
- **浏览器**：隔离合成图库、127.0.0.1、内置浏览器覆盖 TEXT 创建、详情、排除、已排除列表及恢复。语义模型未就绪；修复后的排除缩略图路径仅有单测验证，浏览器复核 PENDING；未导出截图。

## 11. 2026-10-19 第一轮：核心正确性与性能修复

- **PERF-BOUNDED**：FILTER 首/续页保持规则范围及 revision 身份；排除在可信筛选 SQL 中先行执行，页大小最多 50，不物化成员、不调用 FTS/向量，FILTER API 不执行全量或 offset COUNT。`capture_desc` 用既有 `idx_asset_metadata_capture` 读两个可信 EXIF 流，再按 asset_id 读取未知时间尾部；ranked 排序只查询当前有界缓存（最多 5000），统一将非可信时间放入尾部。
- **规则冻结**：来源凭据同时携带服务端确认的执行路径和结构化计划。`TEXT` 从原始查询恢复，`COMBINED` 固定确认的内容查询/intent plan，纯条件按实际 FILTER 路径保存；缺少可恢复身份时拒绝保存。刷新沿用冻结计划，不重新解析原自然语言。
- **租约**：worker factory 初始化失败后用请求已打开的共享服务尽力释放本任务 lease；发布事务同时校验 owner、有效期、rule_revision 与依赖签名。过期、规则替换和接管后的旧 worker 不发布；失败清理保留既有 build。
- **验证**：最终受影响组合 JUnit **41 passed, 0 failed, 0 skipped**：`test-results/smart-final-20261019-190731-722.xml`。覆盖 201 张隔离合成照片的 0/50/95/100% 排除、每种排除比例的 50 张首/续页无漏重、COUNT trace 为 0；EXPLAIN 检查排除主键探测和时间索引；FILTER/ranked 时间排序覆盖可信 EXIF、EXIF_ROOT/无时间尾部、同时间跨页及 revision 过期；规则分类与缺失确认拒绝；lease 过期、旧 worker 接管、规则替换、factory 失败与旧缓存保留。受影响普通合集、收藏及拍摄时间回归同组通过。Ruff、py_compile、`node --check app.js`、`git diff --check` 均 exit 0。
- **中间失败记录**：首轮 JUnit `smart-round1-20261019-190338-509.xml` 为 39 项、6 failed：规则重建漏传 Pydantic 必需 request_id、两个 API 用例因此 500、时间 fixture 违反 EXIF 来源置信度约束，以及两个 LanceDB 测试因 `$TEMP` 目录未预创建而 panic。修正实现/fixture 并创建项目临时目录后重跑；`smart-round1-20261019-190409-288.xml` 的两个默认排序接线失败也已修复。之后时间桶测试发现并修复布尔排序方向；最终 JUnit 41 项全过。所有中间 XML 留在忽略的 `test-results/`。
- **未关闭项**：本轮没有实现 stale ranked 快照资格浏览和 `save-static`；不声称浏览器 refresh/进程重启恢复、真实模型向量路径、完整并发规则替换及 5000 截断另存通过。上述持久状态/并发高风险改动还需按 V3.1 §2.4 完成独立 DS-H 影响面审查后，才可重新评估整卡验收状态。未访问真实图库。
- **Luna High 独立复核**：检索前后依赖签名与发布事务重核、实际 search session coverage、缓存发布失败时持久 mutation 的显式成功响应均已补；没有发现本轮修复引入的新问题。旧 ranked 快照当前不可浏览、`save-static` 未实现是两项 P1；worker factory 异常时租约会等到期为 P2。刷新进程重启/模型真实路径、并发替换及候选另存仍 PENDING。
- 本任务未提交/推送，未读取真实图库；并行 UI 改动原样保留。

## 12. 2026-10-19 地点纯筛选范围续修复

- **确认的问题**：智能 FILTER 详情重放地点纯条件时仍走 `place_album`。该路径未接收智能排除、排序和 `filter_statistics=False`，故排除可被忽略、`capture_desc` 退回 asset_id 分页、每次请求仍运行全量/offset COUNT。普通 `filter_only` 已由第 11 节修复；本续修复只补地点范围分支。
- **性能三行**：地点智能合集详情/续页的风险是重复 COUNT、先分页后排序或范围外查询；复用现有 `selected_members_sql` 作为权威地点范围，将 `NOT EXISTS(photo_smart_exclusions)` 加入同一 SQL scope，并复用 `_filter_capture_desc_rows` 的可信 EXIF 双流及未知尾部 Keyset，智能读取关闭统计；验证跨页 oracle、调用次数及 EXPLAIN，不增加规模性能结论。
- **实现边界**：不改地点身份/修订、照片资格、普通地点搜索默认统计/asset_id 排序，也不将范围改为全库搜索；capture 游标同时绑定原地点 revision/fingerprint 和外层 smart rule/exclusion revision。FILTER 仍不物化、不触发 FTS/向量。
- **验证**：新增 `test_place_filter_smart_exclusion_capture_desc_continuation`，53 张隔离合成照片中排除排序第一张，按 50 张首/续页与含重复时间/未知时间 oracle 对照；断言两个分页调用均保留地点成员与排除 SQL、首续页 position、智能详情零资产 COUNT；EXPLAIN 命中拍摄时间索引和排除主键。完整受影响组合为 51 passed；Ruff、py_compile、diff-check 通过。JUnit 与命令见执行报告。
- **范围外仍未验**：原卡的 stale ranked 快照浏览、`save-static`、浏览器刷新/进程重启、真实向量模型、完整并发替换和 DS-H 独立影响面审查仍未关闭；整卡继续 `IMPLEMENTED / ACCEPTANCE PARTIAL`。未访问真实图库。

## 13. 2026-10-19 stale 浏览、save-static 与刷新恢复续交付

- **PERF-BOUNDED 三行**：风险是旧 ranked 候选经规则或范围变更后越权展示、另存超出权威结果或部分创建；候选复用最多 5000，50 张页，按当前冻结规则 SQL 复核完整范围/照片资格/人工排除，FILTER 在原范围内最多探测 5001 并拒绝超限；验证以隔离合成 API/UI、事务重试及受影响 JUnit 为界，不推断真实模型或图库容量。
- **过期浏览**：TEXT/COMBINED 的 STALE/REFRESHING 继续显示上次已发布候选，并在 SQL 中按当前规则、权威范围、照片资格和排除复核；返回“上次结果·待更新”，规则修订不一致时闭锁旧结果。游标绑定缓存 build，刷新发布新 build 后旧游标 409。
- **另存普通合集**：新增 save-static。FILTER 使用完整当前服务端范围并在第 5001 个成员时拒绝；ranked 只复制已发布、身份/版本及资格核对后的候选。STale、TRUNCATED、INDEXING 返回必要确认项；使用普通合集事务、幂等请求键和事务内 smart/scope revision 检查，失败不留下半成品或修改源规则/排除。
- **恢复与 UI**：详情显示 freshness/coverage、旧结果标签及刷新错误重试入口；另存按钮复用普通合集创建，原有 viewer、返回、排除及恢复入口保留。新增实际 Catalog 重新打开/租约过期后接管测试；过期旧 worker 和被接管 owner 的迟到发布拒绝。
- **验证**：最新受影响 JUnit **56 passed, 0 failed, 0 skipped**：`test-results/smart-followup-final-20261019.xml`，覆盖智能合集单元/集成、普通合集、存储 UI 与 viewer UI；包括 stale SQL 资格复核（断言不调用 FTS/vector）、STALE/TRUNCATED/INDEXING 另存确认、FILTER >5000 明确拒绝、另存幂等及候选稳定、缓存 build 游标失效和进程重开/lease 接管。Ruff、py_compile、`node --check app.js`、`git diff --check` 均通过。
- **隔离浏览器**：127.0.0.1 合成图库完成 3 条核心流程：缓存过期→旧候选显示“上次结果·待更新”→手动刷新→新候选；另存为普通合集并确认静态成员；排除→已排除列表→恢复，并在刷新及真实应用进程重启后核对人工状态。另检查排除照片查看器关闭后回到排除列表。TEXT 流程未调用模型；没有访问真实图库，浏览器未导出截图。
- **未验项**：真实 COMBINED 向量模型刷新不可在当前合成环境验证；完整跨进程规则替换压力和 DS-H 独立全受影响面审查待做。整卡维持 **IMPLEMENTED / ACCEPTANCE PARTIAL**，不据合成测试声称 30 万容量或模型可用性 PASS。

## 14. 2026-10-19 普通与智能合集联合审查

- **PERF-BOUNDED 三行**：风险为 FILTER 高排除比例分页、候选缓存热打开/发布、最多 5000 候选的 rank/capture_desc 浏览和多合集刷新资源占用；FILTER 继续 SQL 原范围 Keyset、页 50、无成员物化/检索，ranked 缓存有界 5000、应用检索 worker 单并发；以受影响 JUnit、SQL trace 和隔离 100/1000/5000 候选小型计时验证，停止于功能正确且单页/事务无明显异常，不外推 GPU/30 万性能。
- **明确修复**：COMBINED 规则绑定的 active profile 消失时，详情原先没有标记 `MODEL_REBIND_REQUIRED`，刷新也会多走到 worker 才失败。现对空 profile 和不匹配 profile 同样明确延期刷新、不取得新租约，保留旧 build；真实空 active profile 单测覆盖 freshness、DEFERRED、无 lease、旧 build 保留。有效排名缓存打开增加 API 回归，若进入 `search_request`/检索即失败。
- **人工数据隔离**：隔离集成测试同时创建普通成员、收藏及智能排名候选，再排除其中一张；断言普通成员、收藏、智能规则/候选和排除各自保持原状态。普通成员与智能规则/排除/收藏互不干扰。
- **验证**：最终联合智能/普通合集与共享 UI 组 `test-results/joint-smart-combined-20261019.xml` 59/59；迁移、备份恢复、普通保存 COUNT、范围搜索及时间轴组 `test-results/joint-data-20261019-rerun.xml` 52/52；最后针对新增智能状态和隔离边界组 `test-results/joint-smart-final3-20261019.xml` 23/23。Ruff、相关 py_compile、Node `app.js` 语法、diff-check 通过。并行初跑只遇到未预建临时根目录的 SQLite/Lance I/O 环境失败；串行重跑通过。
- **隔离合成计时**：100/1000/5000 ranked 候选发布分别约 1.35/5.56/30.30 ms；所有 rank 页顺序浏览分别约 1.46/11.93/80.46 ms，capture_desc 分别约 1.26/30.92/517.35 ms（100 页全量串行浏览在 5000 样本，约 5.17 ms/页）。照片含 10% 可信 EXIF、重复时间，其余未知；每种排序都验证全量分页无漏重。是本机小型合成配对，不是容量/延迟保证。FILTER 的 0/50/95/100% 排除、50 张首续页正确性与 SQL COUNT=0 使用既有受影响 JUnit 证据；普通合集重复 COUNT 和时间轴/封面复用已有 300k EXPLAIN/计数/计时报告，本轮定向组重跑通过。
- **并发及恢复边界**：代码将实际 runner worker 限为 1、同一 smart_id 由 lease 单飞；不同 smart_id 的 ThreadPoolExecutor 待处理队列没有应用层上限，也无排队时长/锁竞争对照，记 **PENDING / 风险**。进程在有效 lease 中途退出后，Catalog 重开会在最长 600 秒 lease 到期前继续报 RUNNING；本轮未做活进程崩溃恢复验证，记 **PENDING**。多智能合集集中刷新与长时间 SQLite 锁占用没有压测证据。
- **浏览器及验收**：智能 stale→上次结果浏览→刷新、另存静态合集、排除→恢复及 viewer 返回沿用此前实际隔离浏览器记录；本轮补完成普通搜索→保存→详情/viewer→时间轴→移出→返回浏览器流程。真实 COMBINED 模型路径、活 worker 崩溃恢复、不同合集排队、完整跨进程规则替换和 DS-H 全受影响面审查均 **PENDING**。普通 FILTER/TEXT 和两类合集人工状态在已验证边界内可用，但整张智能合集卡保持 **IMPLEMENTED / ACCEPTANCE PARTIAL**，不标日常无条件可用。未访问真实 PhotoSearchData。

## 15. 2026-10-19 save-static 完整候选修复

- **PERF-BOUNDED 三行**：风险是另存 ranked 缓存被 UI 页大小静默限制，导致完整候选丢失；服务端另存最多读取当前已发布 5000 候选，UI 浏览仍固定每页最多 50；以 51/120/5000 合成候选的 rank/capture_desc、排除/资格/重试集成测试验证，不新增规模延迟结论。
- **复现**：隔离 API 集成中发布 51 个 TEXT 候选并确认索引未完成后调用 save-static，普通合集创建成功但仅含 50 张；失败证据 `test-results/smart-save-static-repro.xml`。
- **实施**：将公共 UI `ranked_assets_page` 与 save-static 专用 `ranked_assets_for_static_save` 分开，共用一套资格/排除/build/排序 SQL；另存读取上限由缓存容量限定为 5000，capture_desc 对整个候选集排序。原确认和事务 CAS/幂等路径不变，UI API 的 `limit=5000` 仍返回 422。
- **验证**：新集成测试覆盖 TEXT/COMBINED × rank/capture_desc × 51/120/5000，共 12 项；逐页读回普通合集并按 oracle 比较顺序，覆盖重复/未知时间、人工排除、当前照片资格变化、50 页边界和同请求键重试。另测另存期间 smart revision 变化返回 409 且无部分合集。受影响 smart/ordinary 单元与集成组合 61/61 通过，JUnit 为 `test-results/smart-save-static-affected-final2.xml`；参数矩阵 JUnit 为 `test-results/smart-save-static-matrix-final.xml`。Ruff、py_compile、diff-check 通过。
- **未验范围**：仅隔离合成 Catalog/API；没有真实图库、COMBINED 模型/GPU、全量容量或浏览器测试。既有整卡 PENDING 边界不因本次收口而关闭。

## 16. 2026-10-19 智能刷新可靠性与真实模型收口

- **收口状态**：`P4-PHOTO-SMART-COLLECTION-REFRESH-CLOSEOUT` 的验收范围 PASS。原卡仍保留历史整卡 `IMPLEMENTED / ACCEPTANCE PARTIAL` 标记；本轮关闭真实 COMBINED、活 worker 异常退出恢复及应用层有界队列缺口。完整证据见同一[执行报告](../reports/P4-PHOTO-SMART-COLLECTION-A.md)。
- **实现**：runner 限制 1 active + 2 queued，提交前校验 instance token；重复合集返回现有状态，满队列返回 retryable BUSY；实际执行开始时才领取 lease。活动 lease 每 60 秒通过独立短事务续租 600 秒，按 owner/request/state/rule revision 核对。取得数据根 OS 独占权后恢复上一实例残留任务为可重试 FAILED，不启动 GPU 重算。异常、取消、规则替换和迟到 worker 保护旧 build、规则及人工排除。无数据库迁移。
- **验证**：最终受影响组 171 passed / 0 failed / 0 skipped，JUnit `test-results/smart-refresh-closeout-final5.xml`；Ruff、相关 py_compile、Node 语法、diff-check 通过。子进程异常退出/重启恢复、长任务续租、旧 owner 拒绝续租/发布、Future 立即完成、提交失败、取消/关闭、重复与 BUSY、非法 instance token 和失败重试均有定向回归。
- **真实模型**：使用开发机既有 Chinese-CLIP ViT-B-16 权重，CUDA / `CUDAExecutionProvider`，隔离合成图片建立真实 READY generation；真实 COMBINED 查询实际调用 query encoder 和向量检索。从真实结果创建并 pin 智能合集，刷新发布 3 候选，缓存重开无额外模型查询；人工排除、模型变化拒绝并保留缓存、检索异常保留缓存及重试、同合集并发请求 single-flight 均通过。详见报告和 `test-results/p4-smart-real-20261019-r6.json`。合成 FTS projection capability 显示 LAGGING，仅据此显示索引处理中，不声称 FTS 已追赶。
- **浏览器**：隔离 loopback 内置浏览器完成智能合集打开→真实模型刷新→候选查看→排除/刷新；另注入刷新失败验证旧缓存可读、错误码提示及重试成功。当前 CUA 工具未提供截图文件导出路径，未保存截图。
- **独立复核**：按用户指定由 Luna High 子代理复核。规则替换 stale lease 清理及快速返回前 instance token 授权检查均已修复并重跑最终受影响套件；最终意见记录在执行对话审查中。
- **范围边界**：没有访问真实照片库、生产数据库或生产向量索引；未下载/训练模型。PENDING 仅为本轮明确排除的 30 万认证、GTX 1660 压测、正式 SLA、真实用户图库及截图导出。无后续优化任务卡。

## 17. 2026-10-19 COMBINED 刷新中模型切换边界补测

- **PERF-BOUNDED 三行**：风险仅限模型绑定失配时 cache 状态、worker lease 与重试状态不一致；在 `request_refresh()` 中按当前状态及 owner/request ID CAS 释放已失效的 RUNNING lease，QUEUED 保持原 request id，不改 1+2 队列或模型执行；以隔离 SQLite 的运行中切换、迟到 worker、新 owner、排队切换及立即重试定向测试后停止，不新增模型向量和容量测试。
- **实施结果**：Chinese-CLIP pinned rule 在 worker 搜索等待时切换到模拟 Qwen，request 返回 `DEFERRED/MODEL_REBIND_REQUIRED`，原缓存保留，精确的旧 owner lease 被清理；切回原 profile 后立即建立新排队请求。旧 worker 在之后返回时不能发布或清理新 owner。模型切换发生在 QUEUED 时保留原排队身份，无 lease 被错误领取/清理；模型恢复后可继续领取并发布。
- **验证**：smart collection unit + API integration **49/49**，JUnit `test-results/smart-model-switch-final2.xml`；Ruff、相关 py_compile、diff-check 通过。Luna High 独立复核 PASS。未访问真实数据根或修改用户活动 profile；没有 Qwen 编码/向量生成。
