# P4-PHOTO-COLLECTION-A — 普通照片合集与搜索结果保存

- **状态**：READY / 任务卡已提交，尚未实施；本卡仅为实施设计，不代表功能完成
- **优先级**：P1；**推荐执行模型**：GPT-6 High（用户在 Codex 手动选择）
- **设计基线**：`szzeng/aisearch`，`master @ cb9700acc35e54d2df9c8e43f5c0803b8740563b`（2026-10-19，复核时未发现更新）；`SUPPORTED_SCHEMA_VERSION=33`。实施前必须检查最新 HEAD、工作树、连续迁移、依赖卡的实际报告和 API，不得按本卡旧编号覆盖新迁移。
- **前置依赖**：`P4-FAVORITES-CORE-A`、`P4-FAVORITES-UX-B` **实际实现且必要验收完成后才启动编码**。截至设计基线，两者仅有 READY 任务卡，不能假定收藏页、星标、批量选择 API 已存在。
- **必读**：`AGENTS.md` → `docs/development-status.md` → 本卡 → `docs/performance.md`、`docs/testing.md`、`docs/tasks/TASK-TEMPLATE.md`；V3.0 主架构 §9.2/9.3/9.5、§12；V3.1 执行策略 §2.4、§3.2/3.4、§4.2；`P4-FAVORITES-{CORE-A,UX-B}`、`P4-SEARCH-QUALITY`、`P4-SEARCH-INTENT-UNIFIED-A`、`P4-UI2-VIEWER-UNIFY-A` 及直接实现报告。实际修改前再核对所触及的消费者及独审门槛。
- **交付原则**：个人本地图库，一次完成可实际使用的普通照片合集闭环；不触碰真实 `PhotoSearchData`，不强制执行 GPU、模型、30 万照片完整压测或正式发行测试。迁移/人工长期状态需要必要故障、恢复、兼容和独立影响面审查，不因个人使用而省略。

## 1. 用户目标与产品语义

用户可以把一次搜索得到的照片保存为**新的普通照片合集**；也可以创建空合集、从已有照片列表/照片弹窗添加照片、将照片追加至已有合集、移除照片、改名和删除合集。合集有独立导航、封面、照片网格、统一查看弹窗和时间轴入口；照片的多来源路径与原识别结果均保持原状。**先打通“搜索→保存→合集浏览→人工整理”的主闭环，再接时间轴和限定搜索，避免在底层成员 API 未稳时同时重构多条入口。**

三种业务必须分开：

1. **收藏**：由 `P4-FAVORITES-*` 提供，单照片星标真值，不等于合集成员。
2. **人物合集**：当前 `person_collections` 储存人物/人脸锚点、自动派生照片；本卡**不复用、不重命名、不迁移**该业务表与 `kind=collection` 旧语义。
3. **普通照片合集**：保存稳定的、用户确认的 `asset_id` 成员集合；新增符合搜索条件的照片**不会自动加入**。创建来源文字仅作为可选显示说明，不是可执行搜索规则。

**不在本卡实现**：智能/动态合集、规则持久化与自动刷新、语义缓存/增量调度、搜索结果自动追踪、AI 自动分类、跨图库文件复制或移动、导出/共享、手工拖放排序、合集中自动匹配同一人物、修改面孔/地点/EXIF/OCR/向量、添加新前端框架或第二套检索引擎。不因为普通合集功能而改搜索算法、Top-K、RRF、OCR 投影或向量构建。

## 2. 最新仓库事实与必须复用的实现

- 搜索有 `combined`（综合）与 `text`（按文字找）模式；`src/photo_search/web/search.js::sendTextSearch()` 进入 `POST /api/v1/search/place-aware`，后者通过 `api/routes.py::_place_aware_search_worker` 区分语义、文字、纯条件及地点相册路径。**不能把按文字找重新解析成人物/地点语义查询**；保存操作必须绑定最终执行路径与 `retrieval_mode`，不是仅凭查询框文字猜测。
- 纯条件 `_filter_only_search_page()` 使用 **静态** `search_session_id="filter-only"` + SQLite Keyset，不存在可引用的持久 SearchSession；纯地点 `_place_album_search_page()` 亦使用独立的 `place-album:*` 伪会话身份。不得按 `search_session_id` 推断这两种结果已经被快照缓存。
- 排名查询由 `search/service.py::search_request()` 产生进程内 `SearchSessionStore` 候选，`MAX_SESSION_IDS=5000`、`SESSION_TTL_MS=600000`，重启会清空；`search/retrieval.py` 的文本/向量/RRF 排名及权威复核不变。`candidates_truncated` 为真时，这些只是**本次最多 5000 个候选**，不是全图库全部匹配照片。
- `search.js::renderPage()` 通过 `PhotoScrollPager.trimGrid(...,500)` 回收 DOM；当前可见/已加载 `hits` 不是全部搜索结果。搜索保存全体候选必须在服务端获得权威 ID，不能滚动到最底或提交页面 DOM。
- `P4-FAVORITES-UX-B` 规划的照片选择为 `Set<asset_id>`，最多 500 个显式已选 ID，Shift 只覆盖同一操作域实际保留的可呈现行；本卡复用**其最终真实实现/交互规则**和共享照片查看器 `AIPhotoInfo`，不再做另一套 Shift/多选状态机。若跨页面无法直接复用该页面的 Set，则复用最小公共行为与限制，保留页面独立选择域，禁止跨范围泄漏。
- `api/queries.py`、`api/timeline.py` 已有有界分页、拍摄时间语义；`catalog/image_policy.py::photo_asset_sql` 是图片资格判断；`0033_scale_read_indexes.sql` 不覆盖本卡成员索引。`Catalog.write/read_snapshot` 管事务；`library_retention.py` 对新的资产引用表需要白名单及人工引用保护。
- 已有统一页面搜索范围。照片合集如提供「搜索此照片合集」，必须与原「人物合集」scope 明确区分，且**在 FTS/向量候选形成之前**限制成员，不能检索全库 Top-K 后再按合集过滤。

## 3. SQLite：新增独立成员真值，禁止迁移老人工数据

实际实施从最新 migration 序列选择**下一个连续版本**；当前只有 v33，收藏 CORE 的计划是下一连续迁移，UX 原则上不新增迁移，因此照片合集可能是 v35，**具体以实施时真实状态为准**。不得提前占号，不得改写 `0001`～`0033` 或已经提交的迁移。

推荐最小结构（字段命名可按仓库风格收敛，约束不可弱化）：

```sql
CREATE TABLE photo_collections (
  collection_id      INTEGER PRIMARY KEY AUTOINCREMENT,
  instance_token     TEXT NOT NULL UNIQUE,
  name               TEXT NOT NULL,
  name_key           TEXT NOT NULL UNIQUE,
  revision           INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
  member_count       INTEGER NOT NULL DEFAULT 0 CHECK (member_count >= 0),
  create_request_id  TEXT NOT NULL UNIQUE,
  create_request_hash TEXT NOT NULL,
  next_position      INTEGER NOT NULL DEFAULT 1 CHECK (next_position >= 1),
  created_at_ms      INTEGER NOT NULL,
  updated_at_ms      INTEGER NOT NULL
);
CREATE INDEX idx_photo_collections_recent
  ON photo_collections(updated_at_ms DESC, collection_id DESC);

CREATE TABLE photo_collection_members (
  collection_id INTEGER NOT NULL REFERENCES photo_collections(collection_id)
    ON DELETE CASCADE,
  asset_id      INTEGER NOT NULL REFERENCES assets(asset_id)
    ON DELETE RESTRICT,
  position      INTEGER NOT NULL CHECK (position >= 1),
  added_at_ms   INTEGER NOT NULL,
  PRIMARY KEY (collection_id, asset_id),
  UNIQUE (collection_id, position)
);
-- UNIQUE(collection_id, position) 已隐式提供成员顺序索引；
-- 不再创建重复的 (collection_id, position, asset_id) 索引。
CREATE INDEX idx_photo_collection_member_reverse
  ON photo_collection_members(asset_id, collection_id);
INSERT INTO metadata(key, value) VALUES ('photo_collections_revision', '0');
```

`name_key` 采用与现有合集一致的安全规范化/大小写折叠，在照片合集命名空间内去重；允许和**人物合集**同名。名称限制以现有 UI 长度约束为准，拒绝空白/非法输入；不把用户文字拼入 SQL。`position` 单调追加，在一次批量操作中按**选择/本次搜索的实际顺序**赋值；移除不压缩，之后追加继续递增，避免大合集整体 UPDATE。默认浏览按 `position ASC`（**合集收录顺序**）；时间轴使用原拍摄时间排序，不将搜索相关度伪装成拍摄时间。**新增位置从 `photo_collections.next_position` 分配并在同一事务更新**，避免每次批写重复求 `MAX(position)`，也避免删掉末尾成员后复用历史位置；重复加入不改变旧位置，整批 no-op 不推进计数器。

`member_count`、`next_position` 与实际成功 INSERT/DELETE 数量必须在**同一 Catalog 短写事务**更新；冲突/no-op 不改变计数或版本；删除合集不留下计数行。`metadata.photo_collections_revision` 用于**目录排序、封面或数量可见变化**的分页失效，单个合集用自己的 `revision` 控制成员/改名/删除并发。**照片合集自身的增删改绝不推动 `catalog_filter_revision`、`favorites_revision`、语义 outbox 或其它现有索引修订**；但是当某次**搜索以照片合集为筛选范围**时，搜索会话和游标必须额外绑定并复核该合集的 `instance_token + revision`，不能只检查旧的全局过滤 Revision。

同一资产多个文件位置只占一个成员；离线、零位置、扫描更新及模型重建不自动移除成员。路径相同但内容变更后产生不同 `asset_id` 时不自动继承。删除合集只删除元信息和成员关系，**不删除文件、assets、收藏、人脸、地点、向量**。人工长期记录的保护要更新 `library_retention.py` 白名单和人工引用判定；备份/恢复应携带新表与 metadata，原有身份、操作历史和模型内容不得改动。目录/封面只展示仍合格的照片，遇到失效或离线资产显示明确状态，**不默默抹掉人工成员**。

## 4. API 与一致性合同

新增隔离业务模块，例如 `photo_search/photo_collections/service.py` 与只读 SQL 辅助；`routes.py` 只做校验、权限、调用分发。所有写入走现有认证/session、Origin/CSRF、`Catalog.write`，错误采用当前项目结构化 4xx/409，不引入宽泛吞错。

| 端点（建议） | 必需行为 |
| --- | --- |
| `GET /api/v1/photo-collections?limit=20&cursor=...` | 列表 Keyset，返回 ID、token、revision、名称、member_count、封面候选、时间；列表 cursor 绑定独立目录 revision |
| `POST /api/v1/photo-collections` | 建空合集；含 `name`、`create_request_id`；同一键相同请求重试返回原结果，冲突载荷拒绝 |
| `GET /api/v1/photo-collections/{id}` | 返回单合集权威身份、当前计数/版本；不存在 404，实例不符 409 |
| `PATCH /api/v1/photo-collections/{id}` | 改名需 `instance_token` + `expected_revision`；无变化 no-op；成员不变 |
| `DELETE /api/v1/photo-collections/{id}` | 实例与版本比较后删除，仅关系数据；显示二次确认 |
| `GET /api/v1/photo-collections/{id}/assets?limit=50&cursor=...` | 仅该合集成员，`position, asset_id` Keyset；结果批量读取摘要/位置/缩略图，不逐图请求 SQL |
| `POST /api/v1/photo-collections/{id}/members/batch` | `{action:add\|remove, asset_ids:[...], instance_token, expected_revision}`；1～500 张，不重复且严格正整数，先校验整批，原子提交，幂等目标态 |
| `POST /api/v1/photo-collections/from-search` | `{name, create_request_id, source_token, mode: ranked_candidates\|all_filter_matches}`；创建与导入**全成全败**；相同幂等键+相同载荷返回原结果，不重复创建 |
| `POST /api/v1/photo-collections/{id}/members/from-search` | `{instance_token, expected_revision, source_token, mode}`；将本次受信结果追加至已有合集，原子去重，重复请求结果不明时以权威状态恢复 |

`GET /assets` 等旧 response schema、`POST /search` 冻结合同以及人物合集 API 保持不变。集合 ID、实例 token、修订号和游标不能跨合集复用；成员变更后旧游标返回 409 并引导重取第一页，不混合两个版本。

批量 add/remove：未知照片、非 ACTIVE、非图片、非法 ID、越界、并发版本冲突必须整次拒绝；已存在/已移除属于 no-op，只有实际变化才增加合集 revision。**新增、删除与计算实际改变数量必须基于数据库真实变更结果，不用请求 ID 数代替**；同一事务中写成员、计数、修订与目录版本。读成员时保留已存在但暂不可读/离线的人工记录，不把“不能生成缩略图”当成删除理由。异常及未知写结果先读权威状态与成员关系，不盲目重放“切换”。`create_request_id + create_request_hash` 的幂等创建必须校验规范化载荷指纹（名称、保存模式、来源凭据身份）；**已提交过的同键同载荷重试先返回原结果**，即使原查询令牌随后过期也不再执行导入；同键不同载荷拒绝，网络重试不生成同名重复合集。

## 5. 搜索结果保存：服务端权威快照，不偷换范围

搜索结果工具栏增加「保存为合集」，分成三个**真实且明确区分**的操作。**默认优先显示“保存已选照片”（有选择时），不能默认勾选全部 5000 排名候选**，防止把低相关照片误收录；无选择时允许明确选择“保存本次候选”或“保存全部筛选结果”：

1. **保存已选照片**：复用 Favorites UX 的显式选择集合，单次上限 500，跨 DOM 回收保持已选 ID，不自动扩展到未加载页。可新建合集或追加到现有合集；服务端验证资产资格，若提供搜索来源凭据再校验属于本次来源。
2. **保存本次排名候选**：对 `combined`、`text` 等实际产生 `SearchSession` 的请求，直接复制**当前服务端冻结候选的有序 asset_id**，最多 5000；不重新解析文字、不重新排序、不偷偷执行第二次向量检索。保存范围为该会话的**完整候选排名集合**，不等于前端可见 DOM，也不等于“高度匹配”照片；前端切换“按时间查看候选”时须恢复原排名范围说明，**不得把时间排序冒充保存顺序**。若检索返回 `candidates_truncated` 或任一相关通道报告截断/未完成，只能标注「当前候选快照（不保证全库完整）」并要求显式确认；不能声称本次候选就是所有符合条件的照片。
3. **保存全部符合条件**：仅对**真正走纯 SQLite 过滤路径**的请求适用，包括纯地点/人物/日期/来源、无条件图库等；不能从 `filter-only` 或 `place-album:*` 伪 session 读取所谓“会话结果”。使用**服务器签发、短期、不透明且绑定本次本地认证会话的 `source_token`**（可通过 `/search/place-aware` 加性返回或专用只读制备接口提供）；内部绑定已确认的规范化范围、检索执行路径、人物 canonical ID、地点身份/包版本、来源、拍摄/入库时间及规则版本、现有范围 token、过滤修订、权限会话。服务端采用有界临时存储/验签机制，不引入持久全局 SearchSession 表，也不接受前端 SQL/自选规则；过期或服务重启明确 409。只在有效期内按**完全相同的权威条件**服务器端执行 `SELECT asset_id ... ORDER BY asset_id LIMIT 5001`，读快照后作事务内版本复核。**“按文字找”即使没有向量，也属于 FTS 内容搜索，不可归入“纯条件全部匹配”**。

**本卡给“全部纯条件保存”设置明确的单次上限 5000**：服务器取最多 5001 个以判断超限；超过则整次拒绝并显示实际原因，提示细化筛选或选中分批添加，**不暗中只保存前 5000 张**。这不限制一个普通合集日后通过多次批量添加超过 5000 张。超过 5000 结果的无阻塞全量导入/断点续建属于独立容量设计，不在本卡伪造支持。排名候选的“最多 5000”与纯条件“超过 5000 拒绝”在 UI 文字中必须分别写清。

**版本/原子性**：排名候选来源不能仅凭一个客户端传入的 `search_session_id` 视为授权：需要服务端短期可信 `source_token` 或等效的**会话归属校验**，同时验证 session 仍有效、当前检索模式/查询摘要/profile/generation、图库过滤 Revision 及地点/人物/照片合集 scope 身份。`SearchSessionStore` 现有的进程内会话本身未承诺绑定浏览器认证会话，**本卡应补手递凭据校验，而不是假设已有保障**。过期、重启、过滤/配置变化返回 409「请重新搜索后保存」，绝不静默重跑。纯筛选来源也要求 token 正确、期限有效、条件/目录/时间身份未失效；跨读写间变化不得部分保存。服务端使用 `read_snapshot` **有界收集候选**，关闭读快照后进入 `Catalog.write` 短事务，先复核版本/范围和照片资格、再一次提交元信息与成员；禁止长读事务升级写锁。必要时使用一条批量资格查询/有界参数分批校验，不对 5000 个资产逐个 SQL；任何版本改变、5001 超限或中途错误都必须在新合集出现前整体拒绝。未知写结果用 `create_request_id` 查询权威结果后恢复。

已有 `SearchSession.entries` 仅承载其真实排名顺序；`reranked` 若只是独立解释区块，不可以假定其覆盖实际 `hits` 顺序。保存排名候选时，服务器应对待提交的 ID 集合作**当前 SQLite 权威资格与查询范围校验**：若会话所依赖事实已失效则拒绝，而不是偷偷缩小、补入或重新排名。前端保存的范围说明要和实际显示的顺序/候选数完全一致；`strong_match_count` 是解释性计数，不是成员判据。上传图片检索本卡仅需支持**显式已选照片加入合集**；不新增上传图片永久查询或“全部匹配”机制。

### 5.1 搜索结果快照的接口边界（不能省略）

- `source_token` 是**保存动作专用的短期凭据**，不等于 `search_session_id`，也不作为长期合集规则；客户端只提交这个凭据和目标合集/保存模式。令牌应带会话绑定、防篡改和期限，不放入 URL/浏览器历史或普通日志，生成/解码/失效统一放小型服务中；若现有安全会话机制可原生支持则复用，不引入额外第三方鉴权服务或数据库持久任务表。
- 排名型：服务端由令牌指向**仍存在的** `SearchSession.entries`，校验来源与当前权威范围后按**会话原顺序**导入；不能再次调用 `search_request()` 获取“差不多的”候选。若 `candidates_truncated`、待索引或候选扫描未完成，只能宣称保存了该次有效候选快照，不能宣称完整召回。
- 纯条件型：凭据内关联**当次确认后的服务器内部筛选计划**（或其受信摘要），不能以客户端重新解释的文字/页面筛选组件现值为准。执行上限 `LIMIT 5001` **先判断是否超限，再写合集**；即使原查询展示了完整总数，也不能跳过提交前的版本校验。
- 在确认对话框打开期间若用户切换查询、结果浏览方式、搜索范围、认证会话或目标合集，应清除旧来源状态；请求提交后前端只显示服务端返回的真实 `inserted_count`/`existing_count`，避免把「候选数」误当成「新增数」。

## 6. 页面与导航：复用现有 UI 2.0

- 现有五个导航页面在「照片」「收藏」附近增加 **「照片合集」**，但与「人物 → 合集」视觉上和路由上独立；建议主页 `/?view=photo-collections`，合集详情 `/?view=photo-collection&id=...`。正确设置 `data-shell-page`，刷新/Back/详情返回不跳成“全部照片”。避免新造一个独立 HTML 相册应用或复制 `asset-grid` 全套交互。
- 合集目录：按最近修改排序，展示名称、数量、封面、改名/删除；空态说明“搜索结果可保存为合集”；封面从已有成员顺序的第一张可展示照片派生，缺图只显示占位，不访问原图去计算封面。
- 合集详情：沿用 `app.js::buildTile`、`PhotoScrollPager`、共享 `AIPhotoInfo` 及已交付的 Favorites 选择组件。默认“合集收录顺序”，用 50 张有界 Keyset、懒加载和 500 DOM 回收；「移出合集」为显式选择操作，不以点击缩略图直接进入多选。可以从弹窗「加入合集」选择已有合集或新建，原图片查看、收藏星标、人脸管理操作保持有效。**不要将人物/地点/地图既有纯浏览缩略图强行升级成全站照片多选模式**；本卡新增的批量入口限照片合集与已具备多选的普通/收藏/搜索列表。
- 合集时间轴：在核心闭环完成后复用 `api/timeline.py` 的日期/分桶/分页和原有前端时间轴，实现**独立的** `kind=photo_collection`/`photo_collection_id` 范围或等价清晰路由；`kind=collection` 仍指旧人物合集。Cursor 同时绑定照片合集 `instance_token`、成员 `revision`、相关图库/拍摄时间规则版本；成员变化后正确 409/刷新。时间轴必须**先在真实合集成员集合上全局排序，再分页**，不先选 50 个成员排序；稀疏合集以成员驱动，或在实际 `EXPLAIN QUERY PLAN` 证明访问代价可控后选择其它等价路径。需要统计年份/月计数时按当前 scope 执行，不能为目录中每个合集预先全量统计。时间轴属本卡明确范围，未完成应写 `IMPLEMENTED / VALIDATION PENDING`，不能虚报整卡 PASS。
- 搜索范围：合集详情中的「搜索此照片合集」沿用全局搜索框与已有 scoped-search 交互，仅最小加性扩展明确的 `photo_collection` scope；前端传 ID/token/revision，后端验证并把**服务端固定模板、仅绑定参数**的成员 `EXISTS` 条件并入权威候选 SQL **生成之前**。不得把原人物合集 `collection_scope` 重载成照片合集，不能先全库 Top-K 后过滤；不做“输入合集名称自动联想”解析。**首查与续页均复核照片合集实例+revision**，变更后报明确 409 并提示重搜；由于本卡不能推动 `catalog_filter_revision`，必须在原会话绑定摘要之外提供此项专属失效校验。不能只复用当前用于地点的 `album_scope_revision` 名称就假设逻辑已适配，应检查实际字段及错误回显。
- 选择域：Shift/全选/反选只作用当前操作域；保存“本次全部结果”是**独立按钮及服务端操作**，不复用“全选当前可见 500 张”来伪装。退出选择模式、切换合集/查询/排序、Esc、滚动 DOM 回收、异步请求迟到时，选择集合/版本/页面高亮不得污染其他页面。

## 7. 性能设计（按最新 `docs/performance.md`）

- **链路与风险 / PERF-BOUNDED 初始**：热点是进入合集目录、打开照片列表/续页、保存搜索及时间轴。风险为每合集 COUNT/封面 N+1、成员双重索引使 5000 导入写放大、候选重复 FTS/向量查询、稀疏合集使用全库日期索引扫描、在大范围导入时锁住 SQLite，以及因 DOM 回收而漏选。
- **做法与边界**：成员主键及 `UNIQUE(collection_id, position)` **已有排序索引**驱动 50 张页面，不重复创建相同前缀索引；`member_count` 同事务维护、目录封面仅批量取当前页；纯过滤“全部保存”最多 5000，批量手选每次 500，业务允许同合集累计超过 5000；排名直接复用存活的候选快照，零二次 GPU/FTS 检索；候选在只读快照中有界生成，短写事务批量验证和写入；只批量读本页媒体位置，不逐资产开 SQL；时间轴用成员集与时间排序正确的访问路径。
- **验证与停止**：编码前列出目录/续页/批量导入/时间轴的 N/K/P 工作量及真实索引；用合成稀疏/稠密合集、`EXPLAIN QUERY PLAN` 和 SQL 次数检查 50 张续页、500/5000 导入、目录封面与无 N+1；对 5000 导入补一次短事务锁竞争/超时/重试边界检查。若大范围查询出现明显与图库规模相关的无效扫描或写锁阻塞，先在同一卡调整路径并做小型配对对照，有充分证据才升档 `PERF-SCALE`。保留排序、范围、去重、版本和图片资格的 oracle 断言；不以小样本称“30 万性能 PASS”，不新增未经测量的毫秒保证。

在普通图库浏览、搜索、人物、地点和收藏未使用照片合集时，**不应因此增加每张图片的后台请求、全局 JOIN 或重投影成本**。合集列表默认不主动刷新或计算所有合集的缩略图。

## 8. 验收：状态安全、范围真实、UI 可实际用

### A. 数据与 API（含迁移、恢复）

1. 最新已实施 schema → 下一连续版本；旧表及人工记录字节/语义不变；重复启动不回填；旧版本读取新库仍遵守 `SCHEMA_TOO_NEW`。升级故障不得留下半张新表或损毁旧表；备份/恢复和 retention 对合集成员有保护。
2. 创建/同键重试/重名、空合集、改名、删除空/非空、收藏/人物合集同名、错误 instance token、并发 expected revision、成员 500/501/非法输入/重复增删/网络结果不明；计数、位置、成员集合与 revision 无部分提交、无重复。
3. 多路径、离线和零位置、非图片路径、同路径内容新身份、已删除/不可用资产；删除合集不删除资产/收藏/人脸/地点/向量。
4. 分页 50、成员顺序、相同毫秒、删除/追加期间深页 stale→409、封面变化、时间轴 capture/unknown/pending/source 跨页、范围和索引访问计划；另验证**合集成员变更仅使“搜索此合集”的会话失效**，不无端使全站搜索或时间轴失效。

### B. 保存搜索的关键真实性

5. 综合 `combined`、按文字 `text`、纯人物/地点/来源/时间/无条件、固定人物合集/地点范围；根据原服务端候选和规则生成独立 oracle，逐项核对**保存成员及顺序**。
6. 选中的 3/500 张与“当前 DOM 保留 500 张”无关；服务端全部快照 20/500/5000，纯过滤 5001 彻底拒绝；`text` 模式有 FTS 命中不误判为纯条件全部匹配；排名截断/索引追赶须明示并确认；空结果创建空合集的行为明确（允许用户显式建空合集，搜索保存 0 结果时提示不生成）。
7. 模型/profile/generation 改变、检索方式切换、搜索过期/重启、过滤 revision、地点包/人物合集/照片合集成员更新、相对日期计划变化、索引迟到、双标签并发及事务读写窗口；包括 `source_token` 被篡改、跨应用登录会话复用、同键异载荷、操作结果不明、5000 条批量资格检查和模拟写锁冲突；不得偷重新查询、偷扩大范围或残留半成品。
8. 上传图片仅显式选中照片；`search/image`、冻结 `/search`、`RRF/Top5000` 和搜索会话旧错误合同必须保持回归。

### C. UI 与影响面

9. `搜索 → 多选 3 张 → 新建照片合集 → 打开合集 → 查看弹窗 → 返回搜索`；`搜索 → 保存本次服务端候选 → 截断提示`；`照片/收藏 → 加入已有合集 → 移除 → 改名 → 删除`；`合集 → 时间轴/搜索此合集 → 返回`。正常查看照片、收藏、原人物合集、地点、地图返回位置不能回归。
10. 用隔离合成图库、应用绑定 `127.0.0.1`、一次性会话链接在 Codex 内置浏览器验证 2～3 条最关键闭环；若受真实工具限制，按规则记录 `BROWSER_PENDING` 及阻塞范围，不能把 Node DOM 测试冒充浏览器 PASS。截图只在真实导出可用时保存到忽略目录。

### 测试范围与交付

- 按 `docs/testing.md` 和 V3.1 §3.2/3.4 先对照可信基线，跑**完整受影响** unit/integration/API/Node UI，以及迁移、长期人工状态、事务拒绝/恢复相关的 D 类故障验证；schema 迁移语义、不可界定共享变化若触发全量回归必须执行或如实记 PENDING，不因本卡要求“轻量”而省略。受影响面需做符合 §2.4 的**独立 DS-H 工程审查**（长期人工成员/迁移/并发风险）；缺项不标 PASS。
- 检查 `node --check`、受影响 `ruff`/`py_compile`、`git diff --check`；测试输出 JUnit XML，原始 logs/JSON/截图进忽略的 `test-results/` 或 `.tmp/`，报告记录真实命令、exit、失败、未验项，绝不虚写全量通过。
- 实施交付源码、新的连续 migration、测试、`docs/reports/P4-PHOTO-COLLECTION-A.md` 与精简状态更新；完成本任务后停止。**不自动实施智能合集，也不预设同名 B/C 卡**；智能合集另行以实际普通合集与搜索实现为基准设计。

## 9. 实施顺序及停止条件

1. **依赖复核**：先确认 Favorites CORE/UX 实际代码、报告、收藏多选与静态资源，并按 `AGENTS.md` 的规定补齐本次必要性能/审查描述；未完成则停止实施并报告依赖未满足，不要抢先复制实现。
2. **先定真值**：migration、集合身份/成员位置/计数/独立 revision、retention、最小 CRUD + 核心故障测试。
3. **先做搜索保存的后端合同**：区分排名 session 与纯过滤的受信上下文，补服务端签发/校验的 `source_token`、会话归属、版本/范围绑定；候选验证、上限、事务导入及错误恢复通过后再接 UI。
4. **页面闭环**：复用导航、网格、弹窗、已有选择状态和目录；完成从搜索结果新建/追加、从照片加入/移除、详情返回。
5. **时间轴/范围集成**：照片合集独立 scope，索引路径和版本正确，不碰旧人物合集检索契约。
6. **受影响验收、浏览器、独审及报告**：先消除数据/查询不变量问题，再做必要 UI 回归。合格后收口，未覆盖的智能自动刷新明确留在本卡范围外。
