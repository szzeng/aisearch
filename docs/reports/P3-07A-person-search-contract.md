# P3-07A — 人物 all/any 与地点 AND 组合搜索契约冻结

- 状态：**PASS（A 级契约/文档闭环；零生产/schema/依赖变化）**
- 日期：2026-09-20
- 实际模型：DS-H（DeepSeek V4.1 Flash / 高推理），由用户在 Codex 中手动选择；未发生自动模型切换
- 起点 HEAD：`87ad5c7`（P3-06 收尾，开始时工作树干净）
- 本任务提交：`e6b1f7e`（6 个文件的文档/契约闭环，其中 3 个新增；无生产代码改动）
- 权威决策：[ADR-0009](../decisions/ADR-0009-person-place-combined-search-contract.md)
- 机器伴随物：[p3-person-search-contracts.v1.json](../decisions/contracts/p3-person-search-contracts.v1.json)
- 下一任务：[P3-07B](../tasks/P3-07B.md)

## 1. 交付与阶段边界

本任务只冻结 P3-07 的**筛选协议**：新增 ADR-0009、draft-2020-12 companion、41 个只读契约用例、本报告与
P3-07B 执行卡，并更新状态。**未修改**任何 `src/photo_search/**`、`catalog/migrations/**`、
`schema_version`、依赖、`PhotoSearch.spec`、前端资源、既有 ADR/companion 或既有测试文件。

`schema_version` 保持 **13**，migration 连续 `0001`～`0013`（13 个文件，`SUPPORTED_SCHEMA_VERSION = 13`
逐字未改）。因此 PASS 只说明协议与机器伴随物自洽，**不**声称 `POST /api/v1/search` 已接受
`person_ids`、数据库/服务已执行这些拒绝、或人物筛选已经可用——这些全部留给 P3-07B。

生效范围限定为 `POST /api/v1/search` 及其会话/游标；`GET /api/v1/assets` 的地点/来源/可用性筛选面在
本阶段不扩展（ADR-0009 §1）。

## 2. 核心冻结结论

### 2.1 只增两个可选字段与一张判定表

请求新增 `person_ids: [int]`（每个 `>= 1`，去重前元素数 `<= 100`）与 `person_match: "all" | "any"`
（缺省 `any`）；`contract_version` 仍是 `p2-contracts.v1`，旧调用方不提交这两个字段时行为逐字不变。
判定表（ADR-0009 §2.2）把七类输入分开：

| 输入 | 结果 |
|---|---|
| 缺省 / `[]` | 不产生人物条件（不是「没有人物脸」），合法 |
| 缺省或 `[]` + `person_match=all` | 422 `INVALID_PERSON_FILTER`（`reason=person_match_all_requires_ids`） |
| 非数组 / 非整数 / `<1` / 原始重复 | 422 `VALIDATION_ERROR` |
| 元素数 `>100` | 422 `INVALID_PERSON_FILTER`（`reason=person_filter_too_large`、`max_person_ids=100`） |
| 不同原始 id 折叠到同一 canonical 人物 | 合法去重，命中集不变 |
| id 不存在 | 404 `PERSON_NOT_FOUND` |
| 链成环/悬挂 | 409 `PERSON_REDIRECT_CYCLE` |

「原始重复是形状错误」与「canonical 折叠是合法去重」被显式分成两件事，沿用 ADR-0007 §5 对重复 face
的口径（不靠去重静默接受矛盾输入）。

### 2.2 组合语义：照片级 all/any × 其他条件 AND

- 唯一谓词是「有效成员脸」：`MEMBER_PREDICATE`（`CONFIRMED` + 无 `MANUAL_UNASSIGNED` 锁 + 无 `active=1`
  排除），**只有一份定义**（`persons/members.py`），检索层必须复用而不是复制；
- `any` = 至少一个人物命中，`all` = 每个人都命中，`|P|=1` 时等价；照片按 `asset_id` 去重；
- 人物条件与来源、可用性、地点、文本/语义条件**一律 AND**，精排只重排不新增；
- 人物不是命中原因、也不是排序通道：`hit_reason` 词表仍只有 `TEXT`/`SEMANTIC`/`TEXT+SEMANTIC`/
  `FILTER_ONLY`，`matched_by` 仍只有 `fts`/`vector`/`filter`；
- `place_ids` 与 `place_state` 的拒绝矩阵照抄 ADR-0005 §10（只有 `any`/`assigned`），未被人物字段改动；
- 只冻结**照片级**口径，脸级口径不在本阶段；
- 「同一人的 `face_count`/`asset_count` 与 `person_ids=[p]` 命中集必须同源同值」被写成可测要求。

### 2.3 canonical、旧 ID 与会话失效

每个 id 都用 `resolve_canonical_person_id` 在同一只读快照内解析；任一失败即整请求拒绝（不丢 id、不部分
返回）；canonical 集合去重后升序用于摘要与回显。「旧筛选可继续使用」被写清：旧 id 永远可解析，所以
**重新发起**的同一查询照常正确；但合并已使 `catalog_filter_revision +1`，合并前的会话/游标必须返回
`CATALOG_FILTER_REVISION_STALE` 并让客户端重搜，禁止静默重排。

### 2.4 权威判定、不新增 migration 与补查上界

- 人物条件是**权威 SQLite 谓词**，与来源/可用性/地点一起进入同一个 `scope_condition`，被每个通道与提交前
  复核使用；不得只作为派生投影存在；
- 现有表列足够，因此 P3-07B **不新增**表/列/索引/migration/queue cause，`schema_version` 仍为 13，
  `0001`～`0013` 逐字节不变，`search_projection_queue` 已有的 `PERSON_CHANGED` 足够；
- 若实测（例如 30 万容量）确需派生列或人物候选索引，本卡即失败：停止、交 DS-H 全量主审、另开决策/迁移卡，
  **不得在 P3-07B 顺手加迁移**；
- `search_projection` 被字段级冻结（不新增任何字段，`SYNCED` 当且仅当 `queued_assets == 0`），并写明
  `LAGGING` 只说明派生文本索引在追赶、永远不代表人物条件结果不完整；
- 补查上界固定为权威候选页 1000、`pending_semantic` 200 条（`truncated` 如实报告，
  `reason ∈ {GENERATION_NOT_READY, ENTRY_NOT_CONFIRMED}`），schema 不能安全求值时返回既有
  `SCHEMA_TOO_NEW`/`SCHEMA_INCONSISTENT` 且不发布分页。

### 2.5 失效与缓存边界

- 只有**一套** revision：人物改名/合并/拆分/纠错/撤销各自 `catalog_filter_revision +1`，cause 仍是
  `PERSON_CHANGED`（ADR-0006～0008 已冻结），地点走 ADR-0005 §11 的 cause；同时带地点与人物条件时只绑定
  一个 `catalog_filter_revision`，仅 `place_state=suggested` 额外绑定既有 `suggestion_revision`；
- 续页过期**复用** `CATALOG_FILTER_REVISION_STALE`（409）而不是另立新码，理由写进 ADR §6.2：该版本是人物/
  地点共享的单一全局计数器，人物另立代码会暗示两套各自为政的 revision；details 不加 `cause` 等新字段；
- 游标摘要必须纳入 canonical、去重、升序的 `person_ids` 与规范化 `person_match`，否则两个不同人物条件的
  续页可能被错误接受；
- 必须立即一致：`GET /persons`、`/persons/{id}`、`/faces`、`/person-operations`、`face_summary` 计数与人物
  谓词本身；允许滞后：`search_documents`+FTS、缩略图/预览/`cache/face-crops`、人脸候选索引与进程内会话；
- `cache/face-crops` 的键**不得**含 person_id（否则合并/拆分后可能把某人的头像当另一人返回），
  `avatar_face_id` 一律读 SQLite 当前值。

### 2.6 隐私与错误面

companion 全部公共 object `additionalProperties: false`；请求只增两个字段，响应 `filters` 只增
`person_ids`（canonical 升序，无条件时为 `[]`）与 `person_match`（无条件时为 `"any"`），且两键始终存在；
`/search/image` 不新增 multipart 字段，其回显固定为空人物条件。

错误闭集（ADR-0009 §7.3）：`VALIDATION_ERROR` 422、`INVALID_PERSON_FILTER` 422（本卡唯一新增码）、
`PERSON_NOT_FOUND` 404、`PERSON_REDIRECT_CYCLE` 409、`CATALOG_FILTER_REVISION_STALE` 409、
`SEARCH_SESSION_EXPIRED` 409、`INVALID_CURSOR` 422、`SCHEMA_TOO_NEW`/`SCHEMA_INCONSISTENT` 409、
`INVALID_PLACE_FILTER` 422（未改动）。响应/错误/日志不得含路径、姓名/`name_key`/别名、bbox、landmark、
向量、相似度/confidence、幂等 payload/hash 或 before/after 快照。

## 3. 与既有冻结契约和任务卡的差异说明

| 面向 | ADR-0009 的结论 | 依据 |
|---|---|---|
| `p2-contracts.v1` | 既有字段/默认值/上下界/`contract_version` 逐字不变，只加性增加两个可选字段 | ADR-0003 §8/§9；本卡禁止改 `p2-contracts.v1` |
| 地点筛选与 revision | `place_ids`/`place_state` 矩阵、`catalog_filter_revision`/`suggestion_revision` 语义原样复用 | ADR-0005 §10/§11 |
| 人物权威 | canonical 解析、有效成员、revision、幂等、撤销全部复用；人物条件只是这些权威事实的消费者 | ADR-0006 §4/§5/§8、ADR-0007 §2～§8、ADR-0008 |
| 命中原因词表 | **不新增**人物原因值，人物只作筛选 | 本卡「默认不新增」项 |
| `person_mode` vs `person_match` | 架构 §9.1 的 `person_mode` 是尚未实现的解析器输出字段；协议字段固定为 `person_match`，将来解析器输出一对一映射到它 | 本卡建议名；仓库无解析器实现 |
| 时间条件 | 任务卡把「时间」列为既有条件，但冻结的 `SearchRequest` **没有**时间字段（P2 明确留到 P4-00A/B，`order=time_desc` 是加入时间浏览）；ADR 按实际字段集合冻结 AND，并规定时间条件将来进入同一 AND 集合与同一 revision 绑定 | 主架构 §9.5、§14 P2/P4 |
| 生效端点 | 只冻结 `POST /api/v1/search`；`GET /api/v1/assets` 的人物筛选留作后续独立任务 | 本卡范围限定为组合搜索 |

以上差异都在 ADR-0009 §2.3、§3.5、§1 显式记录，不靠聊天历史解释。

## 4. 契约反例覆盖

`tests/unit/test_p3_person_search_contracts.py` 共 **41 例**（0 跳过），实际执行 companion 的
`$ref`/`const`/`enum`/`oneOf`/`allOf`/`if-then`/`type`/`required`/closed-object/bounds/`uniqueItems`
子集，并额外验证 JSON Schema 不能表达的规则：

- **请求形状**：缺省/空/`any` 合法；`all` 无 id 拒绝；字符串/数字/对象/0/负数/布尔/浮点/数字串/`null`/
  原始重复/101 项逐项拒绝；100 项恰好合法；
- **响应闭集**：`filters` 11 个键固定，缺 `person_match`、`person_ids` 乱序/重复/`0`、任何私密字段
  （`name`/`name_key`/`aliases`/`bbox`/`landmarks`/`similarity`）一律非法；binding 缺字段非法；
- **投影与补查**：`search_projection` 键集合固定，`SYNCED`⇔`queued_assets==0` 为交叉规则，未知 state、
  负数、以及任何 `person_*` 键非法；`pending_semantic` 的 200 上界、`count==len(entries)`、
  `truncated` 只允许整页、`semantic_ready` 恒为 false、reason 闭集；
- **错误面**：10 个 code 闭集；`INVALID_PERSON_FILTER` 只允许两个 reason 且
  `person_filter_too_large` 必须带 `max_person_ids=100`；`PERSON_*` 必须带安全 person_id；
  私密字段与未知 code 被拒；companion 的所有键名不含 bbox/landmark/similarity/confidence/`name_key`/
  alias/embedding/payload_sha256；
- **参考语义**：canonical 去重与成环/自环拒绝；`all`/`any` 命中集；与地点条件的 AND；`place_state` 矩阵；
  单 revision 续页与摘要（顺序无关、`[3,7]`→`[3]` 等价、`all`≠`any`、有条件≠无条件）；
- **同源口径**：用真实 `MEMBER_PREDICATE` 对合成 SQLite 夹具求人物命中集，与真实 `member_counts()` 的
  `asset_count` 相等（`MANUAL_UNASSIGNED` 脸、活动排除脸、`SUGGESTED` 都不计入），并断言 ADR 要求检索层
  复用同一份定义；
- **冻结件未被改动**：四个既有 companion 的 SHA-256 与冻结时记录逐字节一致；
- **ADR 文本**：唯一端点、两个加性字段、判定表、照片级/脸级边界、单一 revision 与复用 stale 码的理由、
  不新增 migration 的越界升级规则、投影/补查上界、只冻结不实现的阶段声明逐条断言。

测试直接读取 ADR 与 companion 并断言冻结值，只读引用 production 的 `MEMBER_PREDICATE`/`member_counts`
作为同源证据，没有复制常量自证，也没有断言「现状已实现」。

## 5. 实测命令与结果

| 命令 | 结果 |
|---|---|
| `uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit/test_p3_person_search_contracts.py --junitxml=test-results/p3-07a-focused.xml` | exit 0；JUnit `tests=41 failures=0 errors=0 skipped=0`（0.059 s） |
| `uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration --basetemp=.pytest-tmp/P3-07A-full --junitxml=test-results/p3-07a-full.xml` | exit 0；JUnit `tests=1271 failures=0 errors=0 skipped=48`（173.5 s）；基线 1230 → 1271（+41）；48 项为缺模型权重/未启用 GPU 的显式环境 skip |
| `uv run --no-sync ruff check .` | exit 0；`All checks passed!` |
| `git diff --check` | exit 0；clean |
| `uv run --no-sync uv lock --check` | exit 0；`Resolved 95 packages`（依赖未变） |
| `git status --porcelain` | 只有 3 个新增未跟踪文件（ADR、companion、契约测试）；`git diff --stat` 为空，即无既有文件被改动 |
| `rg SUPPORTED_SCHEMA_VERSION` / migration 计数 | `13`；`src/photo_search/catalog/migrations/*.sql` 共 13 个 |

环境事实：uv cache 使用项目内 `.uv-cache`；`.pytest-tmp` 对受限令牌仍不可写（`WinError 5`，P3-05B/P3-06
已记录）。完整套件按既有方式改用已授权的正常 Windows 权限路由执行，basetemp 仍是项目内
`.pytest-tmp/P3-07A-full`，**未修改任何 ACL**。

## 6. 未实现与未验证

- `person_ids`/`person_match` 尚未被 `SearchRequest`、API model 或服务接受；`INVALID_PERSON_FILTER`
  尚未存在于错误表；会话摘要尚未含人物条件；这些都属于 P3-07B；
- 人物条件在真实 SQLite 上的组合谓词、权重、索引使用与 30 万容量延迟未测（P3-07B 的 B/D 级）；
- `GET /api/v1/assets` 的人物筛选、脸级口径、自然语言解析器与 `person_mode` 映射、时间条件（P4-00A/B）
  均未实现；
- 本任务不涉及打包，因此未做 C 级；P3-07B 若改动打包输入集合再补 C。

以上不阻塞 P3-07A 的 A 级契约 PASS，但在 P3-07B 真实完成前不得声称人物组合筛选可用。

## 7. 交接

```text
任务 ID / 状态：P3-07A / PASS（A，契约与文档；零生产/schema/依赖变化）
冻结产物：ADR-0009 + p3-person-search-contracts.v1.json + 41 个契约用例
核心结论：person_ids/person_match 加性可选；原始重复=形状错误、canonical 折叠=合法去重；
  all/any 判定对象是 MEMBER_PREDICATE 有效成员；与其他条件一律 AND；人物不作命中原因；
  人物条件=权威 SQLite 谓词、不新增 migration；单一 catalog_filter_revision + 复用
  CATALOG_FILTER_REVISION_STALE；续页摘要必须含人物条件；投影/补查上界字段级冻结
未改动：src/**、migrations、schema_version 13、依赖、spec、前端、既有 ADR/companion/测试
下一任务：P3-07B / DS-H 实现 + 独立 DS-H 全量工程主审（max 仅按 V3.1 §2.2；Sol high 只终审
  canonical/revision/缓存失效边界）
任务卡：docs/tasks/P3-07B.md
```

**下一任务建议使用 DS-H（DeepSeek V4.1 Flash / 高推理），请在 Codex 中手动切换模型/推理档后继续。**
