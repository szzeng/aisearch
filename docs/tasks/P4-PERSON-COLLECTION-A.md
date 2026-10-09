# P4-PERSON-COLLECTION-A — 人物合集与自动照片集合

日期：2026-10-09。状态：**READY / 未实施（仅任务卡）**。设计核对基线：`master / ad86d389a173069d5e82e9117a6bc56b448ddce9`；开工前重新核对实际 HEAD。**个人相册最小可用功能**，不另建一套人脸识别或照片库。

## 1. 现状与目标

已核对仓库事实：

- `P4-PERSON-HIDE-A` **已实现**：schema v22 的 `person_hidden`、`GET /persons/browse?visibility=all|visible|hidden`、`POST /persons/visibility`、已命名/未命名/已忽略人物的批量操作（最多 500 ID）。`P4-PERSON-HIDE-UI-FIX-A` 已修复多页选择时不必要的列表重载；这些能力直接复用，**不重新做忽略模块**。
- `src/photo_search/persons/albums.py::people_page/photo_page` 已实现人物列表和照片分页，成员资格权威为 `persons/members.py::MEMBER_PREDICATE`，照片按唯一 `asset_id` 统计。前端 `web/persons-albums.js` 已有卡片、多页选择、照片网格与预览。
- `api/queries.py::asset_page` 支持 `capture_desc`、`person_ids`；但 `search/retrieval.py::SearchScope.build` 将普通请求的 `person_ids` **上限固定为 100**。人物合集可能有超过 100 人，**不能通过把集合成员拼进旧搜索请求、解除该上限或每人请求一次再前端拼接实现**。
- 当前自动匹配仍按**每个 canonical 人物最多 8 张代表脸**执行；“合集”只组织人，不是合并为同一个人物。已忽略人物仍参与识别，隐藏不等于 `MANUAL_UNASSIGNED`。

### 用户故事与明确效果

1. 人物页新增「人物合集」入口，可建立「同事」「同学」「家人」「路人」等集合；**无须先给每个人命名**。
2. 从已命名、未命名、已忽略人物卡片上复用现有多页批量选择，一次选 1～500 人加入已有合集或先新建合集。可选「**加入后同时忽略所选**」，适合 100 个只有 1 张照片的路人；原图、身份、脸、向量不删除。
3. **点击合集默认直接平铺所有相关照片**（拍摄时间倒序、同 `asset_id` 只出现一次、分页）；另可切到「人物列表」看成员头像、名字及单人相册。合照在合集只显示一张；同一人允许属于多个合集。
4. 将来正常识别的新照片只要确认归属于集合内任一人物，就会在下一次读取/刷新该合集时出现；**不增加识别任务、不自动猜测陌生人属于“路人”**。从合集移除人后其照片也自然退出集合（除非仍匹配合集其他成员）。
5. 可以改合集名称、移除成员、删除合集（仅删除集合和成员关系，绝不删照片/人物）；不存在集合自动重命名、自动成员聚类或照片物理复制。

### 范围边界

- **本卡只收纳已经存在且有活动 canonical `person_id` 的人物**，包括只有一张有效已确认人脸的未命名人物；无人物归属的 `faces/inbox` 单脸不作为虚构人物加入合集。待归类/低质量/建议脸需先走现有人脸确认/纠错；不增加「按单脸保存到合集」或新的 face_id 持久映射。
- 本卡不更改 `face/index.py`、每人 8 张代表脸、识别阈值、模型、向量索引、归属、合并/拆分/撤销协议、照片扫描或原始文件；不让“人物合集”参与人脸身份判断。
- 首版不做自由标签规则、智能猜路人、自然语言「同事照片」搜索集成、合集封面手工裁剪、合集内全文/语义检索、合集分享或照片手工增删；后续单独评估。原有自然语言搜索和普通人物过滤不变。

## 2. 最小数据设计：schema v23

如果开工时仍为 v22，则新增连续 migration `0023_person_collections.sql` 并升 `SUPPORTED_SCHEMA_VERSION` 至 23；如已有更高版本则顺延。**仅增量迁移，不改旧 migration、不重建图库/GPU 数据**。

建议两张独立表（具体 SQL 命名可随仓库风格微调）：

```sql
CREATE TABLE person_collections (
  collection_id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  name_key TEXT NOT NULL UNIQUE,
  revision INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE TABLE person_collection_members (
  collection_id INTEGER NOT NULL REFERENCES person_collections(collection_id) ON DELETE CASCADE,
  person_id INTEGER NOT NULL REFERENCES persons(person_id) ON DELETE RESTRICT,
  PRIMARY KEY (collection_id, person_id)
);
CREATE INDEX idx_person_collection_members_person
  ON person_collection_members(person_id, collection_id);
```

- 名称非空，裁剪空白、合理最大长度（例如 80 字符）；使用已有项目的名称规范化习惯保证大小写/空白等价，拒绝重名，不能靠客户端校验。
- `(collection_id,person_id)` 保证加入幂等；一个人可在多个集合。合集成员上限**不设 100 的全局限制**，仅沿用单次批量提交最多 500 人，允许多轮累积。
- `person_hidden` 仍单独拥有可见性事实；集合成员关系**不受隐藏标记过滤**。不复制照片、`face_id`、512 维 embedding、`asset_id` 列表或预聚合相册内容。
- **合并/撤销时不可丢集合成员**：成员引用原始稳定人物 ID，不因合并删除成员记录；读取时按 `persons.merged_into` **递归解析最终活动 canonical ID**，全局去重。不能只处理单级 `merged_into`，也不能对每个人做 N+1 次查询。合并源→目标后合集看到目标的一份照片；撤销后按仍存在的原始成员关系自然恢复。对已合并人物的成员编辑按当前 canonical 解释；移除当前人物时移除该合集内所有解析到此 canonical 的锚点，避免残留幽灵成员；加入只记当前 canonical ID。无需扩展现有人物合并事务。
- 集合增删成员、重命名和删除在同一事务中更新集合 `revision`（删除不续页）；人脸/人物归属变动继续沿用已有 `catalog_filter_revision`。

## 3. 后端与性能约束

新增 `src/photo_search/persons/collections.py`（或同级小模块），复用现有 `Catalog`、`resolve_canonical_person_id` / `MEMBER_PREDICATE`、读写真正权限与照片页投影，不复制第二套成员有效性规则。

建议 API（均沿用 `/api/v1`、既有会话认证与 Origin/CSRF，写请求使用受控 body 模型）：

| 方法 | 路径 | 作用 |
|---|---|---|
| GET / POST | `/persons/collections` | 集合分页列表（名称、人数，不在列表对每集合全量数照片）/ 创建 |
| GET / PATCH / DELETE | `/persons/collections/{id}` | 集合详情、改名、删除（需前端确认） |
| POST | `/persons/collections/{id}/members` | `{action:"add"或"remove",person_ids:[...],hide_after_add?:boolean}`，1～500 个 |
| GET | `/persons/collections/{id}/people` | 当前 canonical 成员列表，40 人/页、含隐藏成员，复用人物摘要/头像 |
| GET | `/persons/collections/{id}/photos` | 默认照片流，建议 60 张/页，时间倒序、照片去重 |

具体响应字段随旧页风格保持小而清晰；集合列表的 `member_count` 是**按 canonical 去重的当前人数**，不是成员关系表原始行数。集合为空时照片页为空，不得退化成全图库。

**照片查询的正确方法**：

1. 权威 SQL 一次取得集合的**当前活动 canonical 成员集合**（或以内联 CTE/subquery 直接限定 `l.person_id`），再从有效 `CONFIRMED` 人脸关联到照片；复用 `MEMBER_PREDICATE`、活动照片约束和拍摄时间排序。
2. 使用 `asset_id IN (SELECT f.asset_id ...)` / 等价的**先求集合成员照片集合**谓词，然后走现有有界 `capture_desc` 翻页与缓存媒体投影；**去重必须发生在 LIMIT/游标切分之前**，合照多个成员不能占多张卡/多次翻页。不要为每位人物循环调用 `photo_page` 并在 JS 合并，也不要使用逐资产相关子查询重扫成员脸。
3. 尽量提取复用 `api/queries.py::asset_page` 的公共、**固定参数化**集合过滤入口与媒体页元数据（不允许将客户端 SQL 字符串透传）；只在集合专属路径触发，既有 `person_ids <= 100` 普通查询、搜索契约及索引行为不变。集合 >100 / >500 成员仍走单个分页查询，不能按成员数量线性增 HTTP 请求或在内存展开全部照片 ID。
4. 集合详情/照片/人物各用独立的 opaque cursor 前缀，绑定 `collection_id`、集合 `revision`、必要筛选及现有 `catalog_filter_revision`；非法/跨合集/成员变化续页返回现有风格 409/422 并提示刷新。既有自动识别/图库扫描期间若 revision 不提供完整快照保证，报告如实说明，不虚构并发稳定性。
5. 「加入并忽略」要**一笔 SQLite 写事务**完成：先全部验证 person ID 和集合 ID，再插入成员与 `person_hidden`；任一无效整批回滚，已有忽略状态保持，重复提交是安全 no-op。不要在前端串行调用「加入」+「忽略」两个 API 造成半成功。仅添加合集（选项关闭）则不改变 `person_hidden`；从合集移除也**不自动恢复**隐藏。
6. 校验 ID 数量、存在性、规范化、命名冲突和删除后 stale cursor；读端不可返回本地绝对路径、向量 blob 或未授权媒体。集合更改不重新跑人脸/语义索引，不人为增加 `catalog_filter_revision`；照片游标仅引用现有版本事实与本集合 revision。

## 4. 前端最小交互

主要复用 `web/persons-albums.js`、`web/persons.html`、现有 CSS/媒体卡片、`renderPhotos` 和照片预览，不新造第二个复杂管理后台。

- 在人物页加「人物合集」入口，列表创建/改名/删除，点击即进入**照片**视图；提供「照片 | 人物」轻量切换。照片仍按时间倒序，支持下一页、大图预览、前后翻页；合集页只展示与合集相关的脸，不误显示“这是同一个人”，**不把合集当作 `state.person` 触发改名/合并/换人物封面/单人搜索**。
- 已命名人物、未命名人物、已忽略视图沿用已有 `batchMode` 和 `selectedPeople`（已修复的跨页不重载行为），批量模式增加「加入合集」动作：选择现有集合或新建，勾选「加入后忽略」；提交成功刷新对应列表和集合，取消确认/提交失败**保留选择与滚动/已展开的页**。
- 集合「人物」视图可按头像逐个查看单人相册，支持批量移出；返回合集时尽量保留原模式、页和滚动。不需要为100个路人取名字；已隐藏者在合集内正常出现、可以再次打开详情和恢复（恢复仍沿用既有忽略入口）。
- 页面状态/异步序列要隔离「人物列表」「合集列表」「合集照片」；切换/翻页/失败不展示另一合集的旧照片。加入集合不会暗中执行“人物合并”；文案明确「集合只是放在一起看照片」。

## 5. 最简验收（个人使用，不做全仓基线）

使用隔离 SQLite/合成人脸关系，不碰真实 `PhotoSearchData`、GPU、照片文件或模型。仅相关定向 pytest、少量现成 DOM/Node、Ruff、`node --check`、`git diff --check`；不跑全仓/30万图库/打包/浏览器矩阵。至少覆盖：

1. **100 个单照片未命名人物批量加入**，其中 20 个原已忽略；另一次「加入并忽略」原子生效，重启后仍在合集；原人物/脸/照片/向量不变；无效 ID 整批失败，无部分隐身。多页选择 120 人不重载已展开人物卡片。
2. **平铺、去重、分页**：A、B 均出现在共同合照 X，各有单照 A/B；合集只显示 X+A+B=3 个不同 `asset_id`，`limit=1` 连续翻完不丢、不重复，时间排序正确；空合集不显示全库；没有已确认身份的待整理脸不会混入。
3. **自动更新和独立识别**：向已有合集成员新增一条正式 `CONFIRMED` 新脸（合成 fixture，不跑模型），刷新后照片出现；未知新人物不自动加入；隐藏/恢复不影响合集成员照片或候选数。已有每人 8 代表脸规则未动。
4. **合并/撤销、维护与 >100 上限**：合并两个成员→目标只显示一次、撤销可恢复；移出已合并 canonical 不残留源锚点；删除集合不删照片/人物；不同集合游标拒绝；合成 150～300 人确认不触发 `MAX_PERSON_IDS=100` 限制，SQL `EXPLAIN QUERY PLAN` 使用现有成员索引，无逐人/逐照片 N+1。

前端由用户自行简测：「未命名多页勾选 → 新建路人合集并忽略 → 日常人物列表消失 → 合集照片直接平铺 → 切人物视图 → 已忽略入口恢复」。实现报告仅写真实运行的测试和未验证内容，**PASS 不代表真实图库大规模性能已验证**。

## 6. 实施顺序、推荐模型与停止点

推荐 **GPT-6 High** 一轮完成迁移/查询/API/前端，按上述四类定向验证；仅审查新增迁移、集合事务、canonical 解析、权威 SQL 分页和前端状态，不做全仓工程主审。可先后端数据/照片分页，再接最小 UI，但**仍是一张功能卡**。

优先复用当前 v22 隐藏功能与已有照片加载路径，避免重新实现忽略/重建搜索引擎。若执行中发现公共 `asset_page` 需要破坏旧 API/ADR、必须把照片预先物化成全量 ID、或须改人脸身份协议，**停止扩大范围、写出差异另行讨论**，不要强行一次做完。

交付：改动文件、可用 UI、四类定向测试、`EXPLAIN` 简述、必要的开发状态更新以及用户手测提示。**完成判据**：100 个未命名路人一键入合集并可选忽略；平铺照片按唯一资产与日期分页；隐藏/合并/撤销保持正确；新匹配照片动态进入集合；不改变任何人脸识别算法或普通搜索上限。
