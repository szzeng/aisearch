# ADR-0009: 人物 all/any 与地点 AND 组合搜索契约

- 状态：Accepted（P3-07A C2 规则冻结）
- 日期：2026-09-20
- 适用实现：P3-07B
- 上游：ADR-0003 §8/§9、ADR-0005 §9～§11、ADR-0006 §2/§4/§5/§8、ADR-0007 §2～§8、
  ADR-0008 §3/§8；主架构 §4.3～§4.5.4、§8.3.3～§8.3.5、§9.1～§9.5、§10.2、§12.4 第 14～22 与第 28～32 条、
  §14 P3
- 机器可读伴随物：`contracts/p3-person-search-contracts.v1.json`
- 冻结基线：`schema_version = 13`，migration 连续 `0001`～`0013`

## 1. 决策范围与阶段边界

本 ADR 冻结把**人物 `all`/`any` 条件**与地点、来源、可用性、语义条件组合成一条可判定、可失效、可补查的
检索协议的**唯一规则**，并冻结它在 `POST /api/v1/search` 的请求、会话/游标、响应回显、投影状态与错误面
上的精确形状。机器可读伴随物是 `contracts/p3-person-search-contracts.v1.json`；两者冲突时以本 ADR 为准。

P3-07A 只冻结规则。它**不修改任何生产代码、migration、`schema_version`、依赖或既有测试**，也不声称
数据库、API 或检索实现已经执行这些拒绝；实现留给 P3-07B。本 ADR 冻结的字段与拒绝必须由 P3-07B 的
B + D 级测试证明，而不是由本卡的文字证明。

冻结边界（不得被本 ADR 或 P3-07B 改动）：

- `p2-contracts.v1` 的 `SearchRequest`/`SearchResult` 既有字段、默认值、上下界、`contract_version`
  字符串与闭集规则逐字不变；本 ADR 只**加性**增加两个可选人物字段（§2）；
- ADR-0005 的地点筛选、`place_state` 组合拒绝矩阵、`catalog_filter_revision`/`suggestion_revision` 语义；
- ADR-0006～ADR-0008 的人物权威、canonical 解析、有效成员口径、revision、幂等与撤销语义；
- `0001`～`0013` 与既有四个 companion 的字节内容。

本 ADR 冻结的可执行面只有 `POST /api/v1/search` 及其会话/游标。`GET /api/v1/assets` 的地点/来源/可用性
筛选面在本阶段**不扩展**：它保持现有 `place_ids`/`place_state`/`text`/`order` 语义与游标空间，任何把人物
条件加到该列表的后续任务都必须复用本 ADR §2～§6 的语义并单独出卡。

## 2. 条件形状与闭集字段

### 2.1 只增两个可选字段

`POST /api/v1/search` 的请求体新增两个**可选**字段，仍拒绝任何未冻结字段：

```text
person_ids:   [int]              # 每个 >= 1，去重前元素数 <= 100
person_match: "all" | "any"      # 缺省 "any"
```

两个字段都不改变既有字段的含义、默认值、上下界或位置；`contract_version` 仍是 `p2-contracts.v1`，
不新增版本号，也不新开一套 `SearchRequest`。旧调用方不提交这两个字段时，答案与 P2-06A/P2-06B 逐字相同。

### 2.2 判定表（本卡的唯一形状与拒绝口径）

| 输入 | 判定 | 结果 |
|---|---|---|
| `person_ids` 缺省 | 不产生人物条件 | 合法；**不得**解释为「没有人物脸」 |
| `person_ids: []`（`person_match` 缺省或 `any`） | 不产生人物条件 | 合法；回显 `person_ids: []`、`person_match: "any"` |
| `person_ids` 缺省或 `[]`，且 `person_match: "all"` | `all` 需要至少一个 id | 422 `INVALID_PERSON_FILTER`，`reason=person_match_all_requires_ids` |
| `person_ids` 不是数组（字符串/对象/数字） | 形状错误 | 422 `VALIDATION_ERROR` |
| 元素非整数（含 `true`/`false`、浮点、字符串、`null`） | 形状错误 | 422 `VALIDATION_ERROR` |
| 元素 `< 1` | 形状错误 | 422 `VALIDATION_ERROR` |
| 原始元素重复（如 `[3, 3]`） | 形状错误，**禁止**静默去重 | 422 `VALIDATION_ERROR` |
| 元素数 `> 100` | 超界 | 422 `INVALID_PERSON_FILTER`，`reason=person_filter_too_large`，`details.max_person_ids = 100` |
| 两个不同原始 id 解析到同一 canonical 人物（`[3, 7]`，7 已合并进 3） | 合法重复条件 | 静默折叠为 `[3]`；`all`/`any` 命中集不变（§4） |
| 任一 id 不存在 | canonical 解析失败 | 404 `PERSON_NOT_FOUND`，`details.person_id` |
| 任一 id 的 `merged_into` 链成环或指向不存在的人物 | canonical 解析失败 | 409 `PERSON_REDIRECT_CYCLE`，`details.person_id`（+ `steps` 或 `missing_person_id`） |
| `person_match` 不是 `all`/`any` | 枚举错误 | 422 `VALIDATION_ERROR` |
| 出现任何未在 §2.1 冻结的字段 | 闭集拒绝 | 422 `VALIDATION_ERROR` |

原始重复是形状错误、canonical 折叠是合法去重，这两件事**必须分开**：前者是调用方提交了自相矛盾的
选择（与 ADR-0007 §5「500 内重复 face 是 `VALIDATION_ERROR`，不能靠去重静默接受」同一口径），
后者是权威事实在两次提交之间发生了合并。任何一项被拒绝时请求零写、零会话、零排序副作用。

### 2.3 与主架构 §9.1 `person_mode` 的关系

主架构 §9.1 的「查询结构」示例用 `person_mode` 表达同一条件。仓库当前**没有**自然语言查询解析器：
P2-06A 把 `text` 原样交给 `build_match`，`person_mode` 只存在于文档中。本 ADR 把 HTTP 协议字段名固定为
`person_match`，并要求解析器落地时其输出字段 `person_mode` 与 `person_match` **一对一映射**（同一值域、
同一缺省），请求、回显、companion 只承认 `person_match`。

## 3. 组合语义：照片级 all/any × 其他条件 AND

### 3.1 唯一谓词：有效成员脸

一张照片属于人物 `p` 当且仅当它至少有一张 `p` 的**有效成员脸**，即满足：

```sql
EXISTS (SELECT 1 FROM face_person_links AS l
        JOIN faces AS f ON f.face_id = l.face_id
        WHERE f.asset_id = a.asset_id AND l.person_id = ?
          AND <MEMBER_PREDICATE>)
```

其中 `<MEMBER_PREDICATE>` 就是 `persons/members.py` 的唯一 `MEMBER_PREDICATE`：链路 `decision='CONFIRMED'`、
该脸没有 `MANUAL_UNASSIGNED` 人工锁、该脸对该人物没有 `active=1` 的排除。**只有一份定义**：实现必须复用
`persons/members.py`（`MEMBER_PREDICATE`/`MEMBER_SQL`），不得在检索层复制第二份成员口径。

由此固定：

- 照片按 `asset_id` 计一次，多个文件位置不重复计数；
- `SUGGESTED` 关联、被人工取消（`MANUAL_UNASSIGNED`）的脸、活动排除的脸一律**不算**有效归属；
- `person_ids=[p]` 的命中集合与 `GET /api/v1/persons/{p}` 的 `asset_count` 必须**同源同值**：同一个只读
  快照下二者必须相等。P3-07B 必须以真实夹具证明这一条，而不是各自实现再对齐；
- 本阶段只冻结**照片级**口径。脸级口径（同框共现、按 `face_id` 的检索、脸级条件）**不在本阶段**，
  不得由本 ADR 推导为已冻结或已实现。

### 3.2 all 与 any

设 `P` 为去重后的 canonical 人物集合（1..N）：

- `person_match="all"`：照片对每个 `p ∈ P` 都满足 §3.1；
- `person_match="any"`：照片对至少一个 `p ∈ P` 满足 §3.1；
- `|P| = 1` 时两种模式等价；
- 空 `P` 不产生条件（§2.2）；`person_match` 只在 `P` 非空时才有判定作用。

`all`/`any` 的判定对象**只能是有效成员**：取消归类、人工排除、建议、被合并的旧分组都不能让一张照片
「属于」某人。放宽 `AND` 为 `OR`、把 `all` 降级为 `any`、用建议补足缺失人物都是禁止的静默语义变更；
空结果时客户端可以显式放宽并重新提交，服务端不猜。

### 3.3 与其他条件的关系：一律 AND

人物条件只**收窄**候选集，与以下条件全部 `AND`，互不覆盖：

- `source_id`、`availability`；
- `place_ids` / `include_descendants` / `place_state`（ADR-0005 §10 的 OR 与后代展开仍在 `place_ids` 内部）；
- 文本/语义通道（`text` 经 `build_match` 之后的 FTS 与向量召回）与 `top_k`/`page_size`；
- `rerank`：精排只重排既有候选，永远不新增命中，也不能把被人物条件排除的资产加回。

人物条件**不是**命中原因，也**不是**排序通道：`hit_reason` 词表仍然只有 `TEXT`、`SEMANTIC`、
`TEXT+SEMANTIC`、`FILTER_ONLY`，`matched_by` 的键仍只有 `fts`、`vector`、`filter`。不新增
`PERSON`/`person` 原因或通道值。纯筛选请求（无 `text`、无查询向量）继续由权威 SQLite 列表直接作答，
并保持既有 `FILTER_ONLY` 行形状。

### 3.4 与地点条件的组合拒绝矩阵（照抄 ADR-0005 §10）

`place_ids` 非空时 `place_state` 只允许 `any` 或 `assigned`；`unassigned`/`manual_unassigned`/`suggested`
与 `place_ids` 的组合仍是 422 `INVALID_PLACE_FILTER`。人物字段不改变、不绕过、不新增这条矩阵：两组条件
各自按自己的规则校验，任何一组非法都整请求拒绝。

### 3.5 时间条件的现状（与任务卡措辞的差异）

当前 `SearchRequest` **没有**时间字段：P2 明确把拍摄时间留到 P4-00A/B，`order=time_desc` 只是
`GET /api/v1/assets` 上的**加入时间**浏览键。因此本 ADR 冻结的 AND 集合是 §3.3 列出的实际字段集合，
并规定：将来加入时间条件时，它进入同一个 AND 集合、同一个 `catalog_filter_revision` 绑定与同一个游标
摘要，不新增第二套 revision。P3-07B 不得顺手加时间字段。

## 4. canonical 解析与旧 ID

1. 每个 `person_ids` 元素都用 `persons/identity.py` 的 `resolve_canonical_person_id` 解析；
   不存在 → `PERSON_NOT_FOUND`（404），成环/悬挂 → `PERSON_REDIRECT_CYCLE`（409）。这是唯一解析器，
   检索层不得自己走 `merged_into`。
2. 解析在**同一个只读快照**里完成，随后用于谓词、回显与摘要。任一 id 解析失败即整请求拒绝：
   不允许「丢掉坏 id 继续查」，也不允许部分结果。
3. 原始重复是形状错误（§2.2）；不同原始 id 折叠到同一 canonical 人物是合法去重，`all` 与 `any`
   都不得因重复计数改变命中集。canonical 集合按升序输出并参与摘要与回显；调用方给出的顺序无语义。
4. 「旧筛选可继续使用」的精确含义：被合并的旧 id 永远可解析，所以**重新发起**的同一查询（仍然带旧 id）
   照常得到正确答案；但合并本身使 `catalog_filter_revision +1`（cause `PERSON_CHANGED`），
   因此**合并前建立的会话/游标不得继续翻页**——续页返回 `CATALOG_FILTER_REVISION_STALE`（§6），
   客户端必须重搜。静默换一套排序或悄悄改查另一个 id 都是禁止的。

## 5. 权威判定、投影滞后与补查上界

### 5.1 人物条件是权威 SQLite 判定

人物条件必须作为**权威 SQLite 谓词**与 `source_id`/`availability`/地点谓词一起进入同一个
`scope_condition`，被每个通道与提交前的复核使用。它不得只作为派生投影存在。

### 5.2 本阶段不新增 migration：可行路径与判定条件

已有表列足够：`face_person_links`、`face_assignment_controls`、`face_exclusions`、`faces.asset_id`、
`persons.merged_into`，配合 `persons/members.py` 的 `MEMBER_PREDICATE`。因此：

- P3-07B **不新增**表、列、索引、migration 或 queue cause；`schema_version` 仍为 13，
  `0001`～`0013` 逐字节不变；`search_projection_queue` 已存在的 `PERSON_CHANGED` 足够表达人物事实变化；
- 若 P3-07B 实测发现当前表结构无法在 30 万正式容量下满足本 ADR §3～§6 的语义或上界（例如确实需要
  派生的「按人物预筛」列或按资产的人物候选索引），**本卡即失败**：必须停止实现、把差异与最小反例交
  DS-H 全量工程主审并出一张新的决策/迁移卡，**不得在 P3-07B 顺手加迁移**。本 ADR 不授权任何迁移。

### 5.3 投影滞后的结论

人物事实当前**不进入** `search_documents` 的六个文本列，所以分词/文本投影滞后不会改变人物条件的
判定，也不会漏掉刚归类或继续显示已移除的人物命中。仍然成立的是：

1. 文本/语义通道可能滞后，因此提交前必须按当前权威谓词复核每个候选（复核谓词**包含**人物条件），
   不能让旧投影复活一张已不满足条件的照片；
2. 有界补查必须继续存在：候选的语义投影尚未确认时，用 `pending_semantic` 把它们点名，而不是丢掉；
3. 若将来的实现把人物文本塞进 `search_documents`（例如复用 `user_text` 或新增列），那只是加速器：
   权威仍是 §5.1，且新增列需要 migration，超出 P3-07B 的授权（§5.2）。

### 5.4 `search_projection` 的字段级冻结

响应的 `search_projection` 对象**不新增任何字段**，键与含义固定为：

| 键 | 类型 | 含义 |
|---|---|---|
| `documents` | int ≥ 0 | `search_documents` 行数 |
| `queued_assets` | int ≥ 0 | 补查后的待投影队列深度（含 `PERSON_CHANGED` 等全部 cause） |
| `stale_documents` | int ≥ 0 | tokenizer/generation 与当前不一致的文档数 |
| `tokenizer_version` | int ≥ 1 | 当前分词版本 |
| `fts_generation_id` | string | 当前 FTS 世代 |
| `state` | `SYNCED` \| `LAGGING` | `SYNCED` **当且仅当**本次有界补查后 `queued_assets == 0` |
| `processed_assets` | int ≥ 0 | 本次请求内有界重导出的资产数 |
| `terms` | 仅在有文本查询时出现 | 规范化查询词 |
| `match_expression` | 仅在有文本查询时出现 | 绑定参数的 MATCH 表达式 |

`state=LAGGING` 只说明**派生文本索引**还在追赶，永远不代表人物条件的结果不完整；`state=SYNCED`
也只说明队列已空，不代表从未入队的派生事实已经被覆盖。人物条件没有对应的投影字段，也不得为此新增。

### 5.5 补查上界与失败语义

- 权威候选页上界 1000（`candidates_truncated` 如实报告是否截断）；
- 补查上界 200 条，`pending_semantic` 的形状固定为
  `{entries, count, limit, truncated, generation_id, generation_state, reason}`，
  每条 entry 为 `{asset_id, entry_state, pending_events, generation_state, semantic_ready}`，
  `reason ∈ {GENERATION_NOT_READY, ENTRY_NOT_CONFIRMED}`；
- 无绑定 profile/generation 的纯筛选请求（含纯人物条件）`pending_semantic` 为 `null`，且**不需要 GPU**；
- 权威复核无法安全执行（schema 过新/不一致）时返回既有 `SCHEMA_TOO_NEW` / `SCHEMA_INCONSISTENT` 并
  **不发布任何分页**：不得返回只复核了一部分的页，也不得把未复核的行当命中；
- 达到上界时如实截断并置 `truncated`，不得悄悄返回不完整集合当作完整结果。

## 6. 失效、会话绑定与缓存边界

### 6.1 单一 revision 集合

人物改名、同名合并、拆分、单脸纠错与撤销一律使 `catalog_filter_revision` **恰好 +1**，cause 仍是
`PERSON_CHANGED`（ADR-0006 §8、ADR-0007 §8、ADR-0008 §8 已冻结）；地点事实按 ADR-0005 §11 各自的 cause
递增同一个计数器。**不存在第二套 revision**：一个同时带地点条件与人物条件的请求只绑定一个
`catalog_filter_revision`，并且只在 `place_state=suggested` 时额外绑定既有的 `suggestion_revision`。

### 6.2 会话/游标绑定与过期码

- 会话在创建时记录当时的 `catalog_filter_revision`（现有行为），续页时比较：不同即 409
  `CATALOG_FILTER_REVISION_STALE`，`reason=CATALOG_FILTER_REVISION_CHANGED`，details 仍是既有的四个键
  （`reason`、`search_session_id`、`session_catalog_filter_revision`、`current_catalog_filter_revision`），
  **不新增** `cause` 等字段；
- **选择复用而不是新码的理由**：该版本是人物与地点人工事实**共享的单一全局计数器**，人物条件只是它的
  一个使用者；为人物另立代码会暗示存在两套各自为政的 revision，与 ADR-0003 §9、ADR-0005 §10 及本节
  §6.1 冲突。过期时客户端必须重搜，服务端不在旧会话里静默重排；
- 游标摘要（`query_hash`）**必须**把 canonical、去重、升序的 `person_ids` 与规范化的 `person_match`
  纳入，连同既有 `text`/`source_id`/`availability`/`place_ids`/`include_descendants`/`place_state`/
  `top_k`/`page_size`；否则两个不同人物条件的续页可能被错误接受；
- 进程重启或 TTL（600000 ms）到期 → 409 `SEARCH_SESSION_EXPIRED`（`reason=SESSION_NOT_LIVE`）；
  损坏、跨会话或越界游标 → 422 `INVALID_CURSOR`。

### 6.3 缓存边界

必须**立即一致**（直接读 SQLite 权威，不得用缓存代替）：`GET /persons`、`GET /persons/{id}`、
`GET /faces`、`GET /person-operations`、`/capabilities` 的 `face_summary` 计数，以及 §5.1 的人物谓词本身。

允许短暂滞后（可重建、可丢弃）：`search_documents` 与 FTS、`cache/thumbnails`、`cache/previews`、
`cache/face-crops`、人脸候选索引 entry/generation，以及进程内检索会话。

头像/计数补充规则：

- `cache/face-crops` 的缓存键仍是（源内容、几何、face_id）；**不得**把 person_id 当作缓存键的一部分，
  否则合并/拆分/撤销后可能把某人的头像当另一人的头像返回。页面与 `GET /persons` 的 `avatar_face_id`
  一律取 SQLite 当前值，无头像就是 `null`，不用旧裁剪「补」出一个头像；
- 若实现存在人物计数缓存，它是派生缓存：合并/拆分/纠错后允许短暂滞后，但 `GET /persons` 的
  `face_count`/`asset_count` 必须来自 SQLite 权威，并与 §3.1 的命中集合同源。

## 7. 隐私闭集与错误面

### 7.1 请求/响应闭集

- 请求新增字段只有 `person_ids`、`person_match`；响应 `filters` 回显新增字段只有
  `filters.person_ids`（canonical、去重、升序，无人物条件时为 `[]`）与 `filters.person_match`
  （规范化后回显，无人物条件时为 `"any"`）。两个键**始终存在**，其余 `filters` 键集合不变；
- companion 中所有公共 object 都是 `additionalProperties: false`；未冻结字段仍被拒绝；
- `POST /api/v1/search` 与 `POST /api/v1/search/image` 共用同一个 `SearchResult` 信封：图像检索面
  不新增人物 multipart 字段，其 `filters` 回显固定为 `person_ids: []`、`person_match: "any"`；
- `person_ids` 只回显整数 ID（`GET /persons` 已公开同一 ID），不含姓名、别名或 `name_key`。

### 7.2 不得出现在任何响应、错误与日志中的内容

本地路径、`name`/`name_key`/别名文本、面对面框 bbox、landmark、embedding/向量、距离或相似度、
confidence、排除关系内部快照、幂等 payload 或其 hash（`payload_sha256`）、操作 before/after 快照。
错误 details 只允许安全 ID、expected/current revision、固定 `reason` 与上界常量；不新增几何、姓名、
向量或相似度字段。

### 7.3 冻结错误闭集

| code | HTTP | 语义与安全 details |
|---|---:|---|
| `VALIDATION_ERROR` | 422 | `contract_version`、未知字段、`person_match` 非法值、`person_ids` 非数组/非整数/`<1`/原始重复 |
| `INVALID_PERSON_FILTER` | 422 | `all` 且无人物 id（`reason=person_match_all_requires_ids`）；人物 id 数超过 100（`reason=person_filter_too_large`，`max_person_ids=100`） |
| `PERSON_NOT_FOUND` | 404 | 任一人物 id 不存在（`person_id`） |
| `PERSON_REDIRECT_CYCLE` | 409 | 人物重定向链成环或悬挂（`person_id`，可含 `steps`/`missing_person_id`） |
| `CATALOG_FILTER_REVISION_STALE` | 409 | 续页的权威筛选 revision 已过期，必须重搜（复用 ADR-0005 §9 的码与既有 details） |
| `SEARCH_SESSION_EXPIRED` | 409 | 会话不存在或过期（`reason=SESSION_NOT_LIVE`、`ttl_ms`） |
| `INVALID_CURSOR` | 422 | 游标损坏、不属于该会话或越界 |
| `SCHEMA_TOO_NEW` / `SCHEMA_INCONSISTENT` | 409 | catalog 不能安全求值，不发布分页 |
| `INVALID_PLACE_FILTER` | 422 | `place_ids` 与 `place_state` 的既有组合拒绝（ADR-0005 §10，未改动） |

`INVALID_PERSON_FILTER` 是本 ADR 新增的唯一错误码，语义等价于地点面的 `INVALID_PLACE_FILTER`；
其余码全部复用，且**不得**借本次扩展给它们新增 details 字段。

## 8. P3-07B 实现边界与必测反例

P3-07B 可改 `src/photo_search/search/**`（谓词、契约、服务、会话摘要）、复用
`persons/identity.py` 与 `persons/members.py`、加性修改 `api/models.py`、`api/routes.py`、`api/errors.py`、
`catalog/errors.py`（新增 `INVALID_PERSON_FILTER`），并新增测试与一个 `%TEMP%` 诊断夹具。
不得修改本 ADR、companion、`0001`～`0013`、依赖、`PhotoSearch.spec`、前端资源、`places/**`、`geo/**`、
`ocr/**`、`ingestion/**`、`workers/**`、人脸检测/embedding/阈值或既有测试断言。

至少以 B + D（涉及打包时再补 C）验证：

1. `person_ids` 缺省/空/重复/非正数/非整数/101 项/`person_match` 非法值/未知字段，逐项命中 §2.2 的
   判定表，且全部零写、零会话；
2. `all`/`any` 与地点、来源、可用性、语义条件的 AND 组合矩阵（含 `place_ids` 与 `place_state` 的既有
   拒绝组合），以及 `|P|=1` 时两模式等价；
3. canonical 解析：旧 id、折叠重复、不存在 id、成环与悬挂 id 四种结果，且请求不丢 id、不部分返回；
4. `person_ids=[p]` 的命中集合与 `GET /api/v1/persons/{p}` 的 `asset_count` 同源同值（真实夹具）；
5. `MANUAL_UNASSIGNED` 的脸、活动排除的脸、`SUGGESTED` 关联都不让照片命中；同图 sibling 不受影响；
6. 纯人物/纯筛选请求无需 GPU、无需 profile，`hit_reason` 仍是 `FILTER_ONLY` 且不出现任何人物原因值；
7. 改名/合并/拆分/纠错/撤销后旧会话续页返回 `CATALOG_FILTER_REVISION_STALE`，重搜（同一旧 id）得到
   正确结果；人物条件不同但同 `search_session_id` 的续页被摘要拒绝；
8. `search_projection` 与 `pending_semantic` 的字段与上界逐字符合 §5.4/§5.5，投影滞后时不漏召回、
   不复活已移除命中；
9. 响应/错误隐私负向扫描：无路径、无姓名/别名/`name_key`、无 bbox/landmark/向量/相似度/confidence、
   无幂等 payload/hash 与内部快照；
10. 未改动的 `p2-contracts.v1`、既有四个 companion、`0001`～`0013`、依赖与既有测试断言全部原样通过。

实现完成后必须由独立新会话做 **DS-H 全量工程主审**；只有 V3.1 §2.2 六类具名条件成立时才升
**DeepSeek max**；**canonical / revision / 缓存失效**这三条边界在 DS-H 收敛后交 **C2 / GPT-5.6 Sol / high
聚焦终审**（只审这三条，不重复全量 diff）。

## 9. 后果与未验证项

本冻结以「显式形状拒绝 + 单一 revision + 权威谓词复用 + 明确上界」换取可判定与可恢复：代价是
`person_ids` 的上下界被写死（100）、原始重复被拒绝、以及将来把人物放进文本投影时必须走新决策与迁移。

未验证（不得当已实现）：检索实现是否真的按 §5.1 组合谓词、`MEMBER_PREDICATE` 复用是否真实发生、
会话摘要是否已含人物条件、`INVALID_PERSON_FILTER` 是否已在 API 生效、30 万规模下人物条件的查询延迟、
自然语言解析器与 `person_mode` 的映射、脸级口径、时间条件。这些全部留给 P3-07B 及后续任务实测。
