# P3-07C 阶段 A — P3-07 人物 all/any × 地点 AND 组合搜索全量工程主审（DS-H）

- 状态：**ENGINEERING REVIEW PASS / SOL FOCUSED REVIEW PENDING**（阶段 A 完结，阶段 B 未开始）
- 日期：2026-09-20
- 实际模型：**DS-H（DeepSeek V4.1 Flash / 高推理）**，新会话，由用户在 Codex 中手动选择；未发生自动切换
- 审查对象：[P3-07B 实现提交](P3-07B-person-search-implementation.md) `733f1a3` + 报告收尾 `033e3ca`
- 起点基线：`6fbe68e`（P3-07A 收尾）；阶段 A 结束时 HEAD 仍为 `033e3ca`，本次审查只新增本报告 + 状态更新，
  **未修改任何生产代码、测试、冻结件或迁移**
- 任务卡：[docs/tasks/P3-07C.md](../tasks/P3-07C.md)
- 上游冻结：[ADR-0009](../decisions/ADR-0009-person-place-combined-search-contract.md) 与
  [p3-person-search-contracts.v1.json](../decisions/contracts/p3-person-search-contracts.v1.json)（本次逐字节未改）

**是否升级 DeepSeek max：否。** V3.1 §2.2 六类具名条件逐条核对：①无协议形状/错误码被本次实现改写（唯一新码
`INVALID_PERSON_FILTER` 是 ADR-0009 §7.3 预先冻结的）；②无 migration/schema/数据模型变化（`schema_version`
仍 13）；③无并发/事务/恢复语义变化（未新增写路径，续页只读）；④无跨模块协议变更（`persons/**` 未改，
只读复用 `identity`/`members`）；⑤未出现与 ADR 冲突的未决矛盾（唯一顺序收敛见 §3，判定为**一致**）；
⑥无性能/容量回归证据（未测量 30 万，但也无性能结论被推翻）。故本次主审按 DS-H 收敛，不升 max。

## 1. 审查范围与方法

按任务卡“起始基线与先读”执行：`AGENTS.md` → `docs/development-status.md` → `docs/tasks/P3-07B.md` →
`docs/reports/P3-07B-person-search-implementation.md` → ADR-0009 全文 + companion 全文 + ADR-0005 §9～§11、
ADR-0003 §8/§9、ADR-0006 §4/§5/§8、ADR-0007 §8、ADR-0008 §8。

然后走查 `6fbe68e → 733f1a3` 的完整实现 diff（7 个 src 文件 + 4 个新文件 + 1 个既有测试的加性更新），
并用一次**审查期临时探针**（只写 `%TEMP%`，运行后删除，`git status` 已确认无残留）独立复现三类反例：
合成库上的谓词语义、续页判定顺序矩阵、报告 §6 的 merge/undo 缺陷。全量套件、Ruff、diff-check、
lock-check 均按任务卡命令原样执行（§5）。

## 2. 逐条结论（任务卡 11 项）

### 2.1 完整 diff 走查：每一处落在 ADR-0009 的哪一条

| 文件 | 改动 | 落地的冻结条款 | 是否引入未冻结形状/错误码/字段 |
|---|---|---|---|
| `catalog/errors.py` | +8 | §7.3 新码 `INVALID_PERSON_FILTER`（ADR 明示的**唯一**新码） | 否 |
| `api/errors.py` | +5 | §7.3 该码 → **422** | 否（未给既有码新增状态或 details 键） |
| `api/models.py` | +11/-1 | §2.1 两个可选字段、`StrictInt`、**故意不设 `maxItems`**（超界必须答 `INVALID_PERSON_FILTER` 并公开 `max_person_ids`，而不是通用形状错误） | 否 |
| `search/contracts.py` | +63 | §2.1 字段与默认值；§2.2 判定表逐行（`_person_id_list` 把**原始重复**判为形状错误）；§7.1 `to_envelope` 回显两键 | 否（既有字段、默认值、上下界、`contract_version` 未动） |
| `search/retrieval.py` | +87 | §2.2 常量 `MAX_PERSON_IDS=100`/`PERSON_MATCH_VALUES`；§3.1/§3.2 `person_condition()`；§7.1 `scope_view` 两键 | 否 |
| `search/sessions.py` | +8 | §6.2 `query_hash` 纳入 canonical/升序人物条件 | 否（摘要只在末尾追加两键，既有键含义不变） |
| `search/service.py` | +90 | §4 canonical 解析；§3.3/§5.5 纯人物 = 无 profile 无 GPU 的权威列表；§6.2 续页判定顺序 | 否 |
| `tests/p3_person_search_fixtures.py`、`tests/unit/test_p3_person_search_scope.py`、`tests/integration/test_p3_person_search_filter.py`、`tools/diagnostics/person_search_smoke_fixture.py` | 新增 | §8 十条必测反例（B + D） | 否（全为 `%TEMP%` 夹具与只读诊断） |
| `tests/unit/test_p3_person_search_contracts.py` | 只加不改 | §8 第 10 条 | 原 41 例断言逐字保留（`git show` 逐行核对：只有新增段落与文档字符串改动） |

未改动确认（`git diff 6fbe68e 033e3ca --stat -- docs/decisions src/photo_search/catalog/migrations
src/photo_search/persons pyproject.toml uv.lock PhotoSearch.spec src/photo_search/web` **输出为空**）：
ADR-0009 与 companion、四个既有 companion、`0001`～`0013`、`persons/**`、依赖锁定、打包 spec、前端资源。
migration 文件数实测 **13**（`0001`～`0013` 连续），`schema_version` 仍为 13。

### 2.2 判定表一致性（§2.2 每一行）

实现、ADR、测试三方对照，**逐行一致**：

| ADR §2.2 输入 | 实现位置 | 实测 |
|---|---|---|
| 缺省 / `[]`（`any`） | 信封默认 `()`；`person_condition` 返回 `"1", []` | 200，回显 `[]`/`"any"`，等同无人物条件（合成库反例：5 张全命中） |
| 缺省 / `[]` + `all` | `contracts.py` 与 `SearchScope.build` 双处 | 422 `INVALID_PERSON_FILTER`，`reason=person_match_all_requires_ids` |
| 非数组 / 非整数 / `true` / 浮点 / 字符串 / `null` / `<1` | 传输层 `list[StrictInt]` + 信封 `_integer(reject bool)` | 422 `VALIDATION_ERROR` |
| **原始重复** | `_person_id_list` 显式拒绝（`_integer_list` 那种静默去重**没有**被复用） | 422 `VALIDATION_ERROR` |
| 元素数 > 100 | 信封先于传输层给出 | 422 `INVALID_PERSON_FILTER` + `details={reason, max_person_ids:100}` |
| 两个不同原始 id 折叠到同一 canonical | `_canonical_person_ids` 集合去重升序 | **合法**，`filters.person_ids` 回显 canonical 集合 |
| `person_match` 非法值 / 未知字段 | `_enum` / `_check_shape` | 422 `VALIDATION_ERROR` |

「原始重复 = 形状错误」与「canonical 折叠 = 合法去重」确实是**两条独立路径**：前者在信封层、发生在解析
canonical 之前；后者在 service 层、只作用于已解析的 ID。传输层**故意不设** `maxItems`——实测 101 项在
`SearchRequestModel` 被接受（`len == 101`），随后由信封答冻结码并公开 `max_person_ids`，与 §2.2 一致。

### 2.3 唯一谓词复用

`search/retrieval.py` 只 `from photo_search.persons.members import MEMBER_PREDICATE` 并把它**内联**进
`EXISTS` 子查询；检索层不存在第二份「有效成员」口径（全仓 grep：`MEMBER_PREDICATE` 的定义只在
`persons/members.py:19`，使用者为 `persons/members.py`、`persons/read.py:205`、`search/retrieval.py:291`）。
`any` 是一个 `EXISTS … l.person_id IN (…)`，`all` 是每人一个 `EXISTS … = ?` 以 `AND` 连接，`|P|=1` 时两者
等价。合成库独立反例（与仓库夹具无关）实测 `ok=true`：`any[1]=[1,2]`、`all[1]=[1,2]`、
`any[1,2]=[1,2,3]`、`all[1,2]=[2]`、`any[2]=[2,3]`、空条件=全部；`MANUAL_UNASSIGNED` 脸、活动排除脸、
`SUGGESTED` 关联都不产生命中，同图的他人脸不受影响，解除人工事实后逐项恢复。

### 2.4 AND 组合与通道边界

`person_condition` 进入 `scope_condition`，而 `scope_condition` 被 `authoritative_page`、`fts_channel`、
`vector_channel`（经 `_CONFIRMED_BLOCK_SQL`）、`pending_projection_page`、`_verified_ids` 五处共用，
因此 FTS/向量候选、提交前复核、补查页同时受约束，**不存在「先全库 Top-K 再过滤」**。
`parameters` 顺序与 SQL 占位符顺序一致（person 段在 place 段之前，`person_condition` 的 `all` 分支按
升序逐个绑定，实测 `parameters == [3, 9]`）。人物条件不参加 `hit_reason`/`matched_by`/RRF：词表仍为
`TEXT`/`SEMANTIC`/`TEXT+SEMANTIC`/`FILTER_ONLY` 与 `fts`/`vector`/`filter`。精排取的是 session 的冻结候选
（已受人物条件约束），只重排不新增。纯人物/纯筛选请求 `profile_id`/`binding`/`query`/`pending_semantic`
为 `null`、两通道 `engaged=false`、编码器零调用，行形状为 `FILTER_ONLY`。地点 `place_ids`+`place_state`
的既有拒绝矩阵原样保留（`INVALID_PLACE_FILTER` 实测仍 422）。

### 2.5 高风险边界 ①：canonical 解析

唯一解析器复用 `persons/identity.resolve_canonical_person_id`（检索层不自走 `merged_into`）；
解析、谓词、回显、摘要都读同一个 `Catalog.connection`（`catalog/db.py:32` 为单一长连接，`read_one`/
`read_all` 均走它）。**本项随后被阶段 B 更正（见本节末的更正注）**。任一 ID 失败即整请求拒绝：
不存在 → 404 `PERSON_NOT_FOUND`(`details.person_id`)；
成环 → 409 `PERSON_REDIRECT_CYCLE`(`details.person_id`,`steps`)；悬挂 → 409 `PERSON_REDIRECT_CYCLE`
(`details.person_id`,`missing_person_id`)；无部分结果、无丢 ID（测试 `test_old_ids_fold_ids_and_
unresolvable_ids`、`test_a_cyclic_redirect_chain_is_refused_whole`、`test_a_dangling_redirect_target_is_
refused_whole`）。两种损坏态的注入都**只改测试数据**：成环用 `catalog.write()` 的直写 UPDATE，悬挂用带
`PRAGMA foreign_keys=OFF` 的原始连接；生产语义未改（`persons/**` 在本次 diff 中逐字节未改）。
回显/摘要/谓词同源：`scope.person_ids` 同时喂给 `person_condition`、`scope_view` 与 `query_hash`，
`_canonical_person_ids` 只在 `SearchScope.build` 之前算一次。

> **更正注（阶段 B 之后）**：上面把「同一个 `Catalog.connection`」当成「同一个只读快照」的推论**不成立**。
> 该连接由 `sqlite3.connect(..., isolation_level=None)` 打开，autocommit 模式下**每条 SELECT 各自是一个
> 隐式快照**，第二个连接的合法 merge 可以落在两个 id 的解析之间，产出任何单一状态都不存在的混合集合
> （实测 `filters.person_ids=[A,B]` 与 `hits=[]` 同时出现）。阶段 B（Sol）据此判 canonical 边界不通过；
> DS-H 已做最小修复并把该反例转为持久回归测试，依据、修复面与实测见
> [P3-07C 阶段 A 补轮报告](P3-07C-dsh-canonical-fix.md) §2～§5。本报告其余结论（判定表、唯一谓词、AND
> 组合、revision 顺序收敛、缓存边界、隐私闭集、冻结件未改）不受影响。

### 2.6 高风险边界 ②：revision 与续页（本卡最需回答的问题）

单一 revision：全仓只有 `catalog_filter_revision`（+ events）与独立的 `place_suggestion_revision`
（`0004_place_core.sql:235/245/255`），**没有第二套人物 revision**，也没有人物专属缓存版本。
人物五类编辑各自在**同一个** `publish_derived()` 里恰好 +1，cause 复用 `PERSON_CHANGED`
（`persons/operations.py:561` 是唯一 `bump_filter_revision` 的调用点，被 `operations.py` 的
rename/merge/undo、`face_correction.py:817`、`split.py:717` 共用）。实测：rename/merge/split/face-correction
各 +1，rename+undo +2，末尾 cause 恒为 `PERSON_CHANGED`。

**顺序收敛的明确结论：一致（不是回归）。** 见 §3。`CATALOG_FILTER_REVISION_STALE` 的 details 仍是既有
四键，未新增 `cause`（实测 `set(details) == {reason, search_session_id,
session_catalog_filter_revision, current_catalog_filter_revision}`）；`place_state=suggested` 仍额外绑定
`suggestion_revision`；TTL 到期仍 409 `SEARCH_SESSION_EXPIRED`(`reason=SESSION_NOT_LIVE`, `ttl_ms=600000`)。

### 2.7 高风险边界 ③：缓存失效

ADR-0009 §6.3「必须立即一致」的读面全部仍是 SQLite 直读：`GET /persons`/`/persons/{id}`/`/faces`/
`/person-operations` 由 `persons/read.py` 用传入连接 + 共享 `MEMBER_PREDICATE` 计算（未新增任何人物计数
缓存），`face_summary` 由既有 SQLite 口径计算，检索谓词本身即权威 SQL。
`cache/face-crops` 的键仍是 `crop_key = processor_id + long_edge + quality + margin + face_id + bbox +
sha256`（`media/face_crop.py:234`），**不含 `person_id`**；头像仍取 `persons.avatar_face_id` 的当前值。
「允许滞后」的派生面未变化：`search_documents`/FTS 的六个文本列不含任何人物列（实测 `TEXT_COLUMNS ==
[filename_text, user_text, ocr_text, caption_text, tag_text, place_text]`），因此投影滞后不可能改变人物判定。

### 2.8 投影与补查

`search_projection` 键集合实测 `== companion.required ∪ {terms, match_expression}`，`SYNCED ⇔ queued_assets
== 0`，且无任何 `person*` 键；`pending_semantic` 键集合 `== companion.required`、`limit=200`、
`count == len(entries) ≤ 200`、`reason ∈ {GENERATION_NOT_READY, ENTRY_NOT_CONFIRMED}`、每条 `semantic_ready`
恒为 false。人物事实不进入 `search_documents`。「投影滞后不复活已移除命中」有真实反例：文本投影仍指向
西湖时对西湖脸加人工排除，同一「文本+人物」请求只剩日落且 `total_candidates` 同步降为 1（提交前
`_verified_ids` 用当前权威谓词复核）。`search_assets` 末尾重读 revision 并如实报告
`FILTER_REVISION_CHANGED`，未返回混合版本页。

### 2.9 隐私闭集与错误面

四份响应体（成功、`PERSON_NOT_FOUND`、两种 `INVALID_PERSON_FILTER`）逐字扫描，均不含姓名/`name_key`/
别名/bbox/landmark/embedding/similarity/confidence/`payload_sha256`/`before_value_json`/经纬度/data root/
`C:\` 形态路径；`filters` 键集合恰为 companion 的 11 个 required 键（person 两键**始终存在**）。
`/search/image` 的 multipart 字段集合是闭合的（`_IMAGE_FORM_FIELDS`），不新增人物字段，回显固定
`[]`/`"any"`，显式提交 `person_ids` 表单字段答 422 `VALIDATION_ERROR`。

关于「既有码未新增 details 键」：唯一一处需要说明的是**原始重复**的 `VALIDATION_ERROR` 带
`details={where, person_id}`。这不构成冻结件偏离——companion 的 `PersonSearchErrorDetails` 是**所有**
person-search 错误码共用的**单一闭合词表**（`additionalProperties: false`），其中 `where`（第 218 行）与
`person_id`（第 223 行）都已冻结在列，`person_id` 也只是调用方自己提交的安全 ID（ADR-0009 §7.2 允许
安全 ID）。**判定：无需修复**；已在交接包中作为已决项列给 Sol 复核。

### 2.10 冻结件与依赖

四个既有 companion 的 SHA-256 在测试内被逐个钉住并通过（`p2-contracts.v1`,
`p2-place-contracts.v1`, `p3-face-correction-contracts.v1`, `p3-person-split-contracts.v1`），ADR-0009 与
其 companion 内容未改；migration 恰为 13 个且 `0001`～`0013` 连续；`schema_version ==
SUPPORTED_SCHEMA_VERSION == 13`；`uv lock --check` exit 0（95 包）。既有测试只**新增**：唯一的编辑是
`tests/unit/test_p3_person_search_contracts.py` 的文档字符串 + 5 条实现↔冻结件漂移检查，`git diff` 逐行
核对确认原断言未删改；P2-06A/06B/07 全部既有用例在完整套件中原样通过。

### 2.11 报告 §6 越界缺陷的独立核实

**独立复现成功，缺陷真实存在，且与本任务改动无关：**

```text
目标 person_id 较大：merge(target=2, sources=[1]) → undo → IntegrityError:
    UNIQUE constraint failed: face_person_links.face_id
目标 person_id 较小：merge(target=1, sources=[2]) → undo → ok
```

成因确认与报告一致：`persons/operations.py` 的 `undo_person_operation` 按 `item_index`（人物 id 升序）
逐人恢复 link，先把迁移前的 `CONFIRMED` link 插回源人物，此时目标人物仍持有同一批 link，触发
`ux_face_person_links_effective` 部分唯一索引。该路径**不在** P3-07B 的 diff 内
（`persons/**` 逐字节未改），也未被本次改动掩盖或误修。
**结论：不需要为它立即开卡；随 P3-08（人物故障全集）一并处理**，并在 P3-08 卡内注明「补一条目标 id
大于源 id 的合并撤销回归用例」。它不影响 P3-07B 的搜索语义：合并后的 canonical 解析、旧会话失效与
旧 id 重搜均已实测通过。

## 3. 顺序收敛的判定与反例（任务卡第 6 项明确结论）

实现把 `_continue_session` 的判定顺序改为
`解游标 → 会话存活 → offset 越界 → catalog_filter_revision → suggestion_revision → query_hash →
profile/generation → 渲染`。

与冻结件的核对：

- ADR-0005 §10「继续翻页时不同则返回 `CATALOG_FILTER_REVISION_STALE`，保留原查询供客户端重搜」、
  ADR-0003 §9「分页/继续会话时 revision 不同，返回结构化 `CATALOG_FILTER_REVISION_STALE`」、
  ADR-0009 §6.2（同一码、同一四键 details）都把 **revision 差异列为续页必须给出的答案**，未要求
  digest 优先；
- ADR-0009 §4.4 明确「合并前建立的会话/游标不得继续翻页——续页返回 `CATALOG_FILTER_REVISION_STALE`；
  被合并的旧 id 永远可解析，所以**重新发起**的同一查询照常得到正确答案」。旧顺序恰好违反这一条：
  合并会改写旧 id 的 canonical 身份，使续页 digest 与建会话时不同，于是旧顺序答 `INVALID_CURSOR`。

审查期独立探针（真实扫描夹具，`%TEMP%`，运行后已删除）实测矩阵：

| 场景 | digest 是否一致 | 实测答案 | 旧顺序会答 |
|---|---|---|---|
| revision 未变 + 条件不同 | 不一致（探针 hash 复算确认） | 422 `INVALID_CURSOR` | `INVALID_CURSOR`（无变化） |
| revision 未变 + 同条件续页 | 一致 | 200，`page_offset=1` | 200 |
| revision 未变 + 仅 profile 切换 | 一致 | 409 `PROFILE_GENERATION_MISMATCH` | `PROFILE_GENERATION_MISMATCH`（无变化） |
| rename 后 revision+1 + 同条件 | 一致 | 409 `CATALOG_FILTER_REVISION_STALE` | `STALE`（新顺序不再被 digest 短路，结论相同） |
| rename 后 revision+1 + 改了人物条件 | 不一致 | 409 `CATALOG_FILTER_REVISION_STALE` | `INVALID_CURSOR`（**已收敛**） |
| revision 变化 + profile 也切换 | 不一致 | 409 `CATALOG_FILTER_REVISION_STALE` | `PROFILE_GENERATION_MISMATCH`（**已收敛**） |
| **merge 使旧 id 的 canonical 改变**（报告 §3.3 引用的场景） | 不一致（session 存的是 raw `b` 的摘要；续页请求算的是 canonical `a` 的摘要，实测
`merge_session_digest_differs_from_the_canonical_digest = true`） | 409 `CATALOG_FILTER_REVISION_STALE` | `INVALID_CURSOR`（**已收敛，且正是 ADR-0009 §4.4 要求的行为**） |
| merge 后带旧 id **重搜** | — | 200，`filters.person_ids == [1]`（canonical 回显） | 同 |

**明确结论：一致。** 没有出现「本应答 `INVALID_CURSOR` 却答 stale」的错误方向：revision 未变时 digest
仍独立生效（实测 422），revision 已变时按 ADR-0005 §10/ADR-0009 §4.4/§6.2 必须答 stale。
反向的「本应答 stale 却答 `INVALID_CURSOR`」在结构上不可能：revision 检查现在无条件先于 digest，
且它读的是全局单例计数器，与 scope/条件无关。

**唯一可观察的行为外延**（不是错误，但需记录给 Sol）：当 **revision 已变且 profile/binding 同时变化**时，
现在统一答 stale，而旧顺序会答 `PROFILE_GENERATION_MISMATCH`。两者都是 409 拒绝、都要求客户端重搜，
且 ADR-0003 §8 只冻结「旧会话不能中途切换 profile」与两个码的存在，未规定二者优先级；ADR-0005 §10/
ADR-0009 §6.2 则明确要求续页在 revision 差异时给出 stale。审查判定为**符合冻结件**，建议 Sol 在
revision 边界上确认这一优先级即可（本次未改任何代码）。

## 4. 发现清单与处置

| # | 发现 | 性质 | 处置 |
|---|---|---|---|
| F1 | `_continue_session` 顺序收敛（§3） | **一致**，且是 ADR-0009 §4.4 要求的修正 | 无需修复；列为 Sol 必审边界②，附实测矩阵 |
| F2 | 原始重复 ID 的 `VALIDATION_ERROR` 带 `details.person_id` | companion 共用闭合词表已冻结该键，ADR §7.2 允许安全 ID | 无需修复；已决项列给 Sol |
| F3 | 合并目标 id 大于源人物时 undo 触发唯一索引冲突（P3-07B 报告 §6） | 既有缺陷，属 `persons/operations.py`，**不在**本次 diff 与允许范围内 | 本卡不修（卡明确禁止改 `persons/**` 语义）；建议随 P3-08 处理 |
| F4 | `SearchScope.build` 会静默去重并排序传入的 id，而信封对原始重复是 422 | 分层设计（信封先把形状错误拦掉；service 只传 canonical 集合） | 无需修复；已在 §2.3 说明：HTTP 两条入口都必经 `SearchRequest.from_envelope`，`SearchScope.build` 无绕过路径 |
| F5 | 无任何未冻结形状、未冻结错误码、未冻结字段、未授权迁移 | 通过 | — |

本次审查**未做任何最小正确性修复**：没有发现 ADR-0009 已冻结语义的实现偏差。

## 5. 独立复现与命令结果

| 命令 | 结果 |
|---|---|
| `uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration --basetemp=.pytest-tmp/P3-07C-full --junitxml=test-results/p3-07c-full.xml` | exit 0；JUnit `tests=1334 failures=0 errors=0 skipped=48`（207.613 s）；与 P3-07B 基线 1334 相同 |
| 专项批 `test_p3_person_search_scope.py` + `test_p3_person_search_contracts.py` + `test_p3_person_search_filter.py` | exit 0；`test-results/p3-07c-focused.xml`，`tests=104 failures=0 errors=0 skipped=0`（15.708 s） |
| `uv run --no-sync ruff check .` | exit 0；`All checks passed!` |
| `git diff --check` | exit 0（clean） |
| `uv run --no-sync uv lock --check` | exit 0；`Resolved 95 packages` |
| 审查期探针（临时文件，已删除）：合成库谓词语义 + 续页顺序矩阵 + merge/undo 复现 | 全部按预期：谓词 `ok=true`；顺序矩阵见 §3；merge/undo 复现 `IntegrityError`（见 §2.11） |

48 项 skip 仍是「缺模型权重 / 未启用 GPU」的显式环境 skip（与 P3-05C/P3-06/P3-07A/P3-07B 同一集合）；
本任务专项批 0 skip。审查期探针与全量套件都只写 `%TEMP%` 与项目内 `.pytest-tmp`，未触碰真实图库。

## 6. 未验证项（本阶段）

- **30 万容量下的查询延迟与索引使用**：人物条件是 `EXISTS` 形式的权威 SQLite 谓词，本次沿用 P3-07B 的
  未验证结论；未做执行计划/耗时测量（属 P4-01/P4-02）。
- **多进程/多实例并发**：进程内检索会话的边界沿用 P2-06A；本次未验证跨进程续页。
- **脸级口径、自然语言解析器与 `person_mode` 映射、时间条件、`GET /api/v1/assets` 的人物筛选**：
  按 ADR-0009 §1/§3.5/§9 明确不在本阶段。
- **C 级打包冒烟**：P3-07B 未改 `PhotoSearch.spec` 或打包输入集合，本次审查亦未改任何文件，故未重跑
  onedir 打包；如需 C 证据应另开 C1 环境任务。
- **真实浏览器**：本次为只读代码/契约审，未做浏览器 UI 验证（P3-07B 也未声称 UI 变化）。
- **F3 缺陷的修复验证**：本卡未修，未验证。

## 7. 环境事实

- `.pytest-tmp` 对受限令牌仍不可写：在普通沙箱路由下 `mkdir .pytest-tmp/P3-07C-*` 直接
  `PermissionError: [WinError 5]`（实测）。按 P3-05B/P3-06/P3-07A/P3-07B 的方式改用**已授权的正常 Windows
  权限路由**执行同一命令，basetemp 仍为项目内 `.pytest-tmp/P3-07C-*`，**未修改任何 ACL**。
- 审查期探针同样经已授权路由执行（只写系统 `%TEMP%` 下的临时目录，退出即删除），并在删除后确认
  `git status --short` 无新增文件。
- `UV_CACHE_DIR` 指向项目内 `.uv-cache`；JUnit 写入 `test-results/`。
- 本次只新增 `docs/reports/P3-07C-dsh-engineering-review.md` 并更新 `docs/development-status.md`；工作树中
  另有用户先前准备的 **strategy revision 6 审查路由对齐**文档改动（`docs/development-status.md`、
  `docs/reports/2026-09-20-review-routing-alignment.md`、`docs/task-launcher.md`、`docs/tasks/P3-07C.md`、
  V3.1 策略文档），本阶段**未提交、未回改**任何用户改动。

## 8. 给 Sol 的压缩交接包（阶段 B 输入）

```text
基线：HEAD 033e3ca（P3-07B 收尾），审查对象 733f1a3；阶段 A 未改任何生产/测试/冻结件
改动文件（仅 P3-07B）：src/photo_search/{catalog/errors.py, api/errors.py, api/models.py,
  search/contracts.py, search/retrieval.py, search/sessions.py, search/service.py}
  + 新增 tests/p3_person_search_fixtures.py, tests/unit/test_p3_person_search_scope.py,
  tests/integration/test_p3_person_search_filter.py, tools/diagnostics/person_search_smoke_fixture.py
  加性编辑 tests/unit/test_p3_person_search_contracts.py（原 41 例断言未删改）
阶段 A 结论表：11 项范围全部给出结论，无“未检查”；无最小修复；F1 判定一致，F3 为越界既有缺陷
全量证据：test-results/p3-07c-full.xml（tests=1334 / 0 failures / 0 errors / 48 env skips）；
  专项 test-results/p3-07c-focused.xml（104 / 0 / 0 / 0）；ruff、diff-check、lock-check 全 clean

三条必审边界：
① canonical —— 唯一解析器 persons/identity.resolve_canonical_person_id；解析/谓词/回显/摘要同用
   catalog.connection（单连接）；任一失败整请求拒绝（404 PERSON_NOT_FOUND / 409 PERSON_REDIRECT_CYCLE，
   person_id|steps|missing_person_id）；旧 id、折叠、成环、悬挂四条路径实测见
   tests/integration/test_p3_person_search_filter.py::test_old_ids_fold_ids_and_unresolvable_ids /
   ::test_a_cyclic_redirect_chain_is_refused_whole / ::test_a_dangling_redirect_target_is_refused_whole
   （损坏态只由测试注入：catalog.write() 直写 / PRAGMA foreign_keys=OFF 原始连接）
② revision —— 全仓只有 catalog_filter_revision（0004_place_core.sql:235/245）与独立
   place_suggestion_revision（:255），无第二套；人物五类编辑各自恰好 +1，cause=PERSON_CHANGED，
   唯一 bump 点 persons/operations.py:561（operations/face_correction:817/split:717 共用）；
   续页顺序收敛：revision → suggestion → digest → binding，**明确结论=一致**，实测矩阵见本报告 §3
   （revision 未变+条件不同仍 422 INVALID_CURSOR；merge 改 canonical 后旧会话 409 STALE、旧 id 重搜 200
   且回显 canonical；唯一外延=revision 已变且 profile 同时切换时答 STALE，报告 §3 已论证符合 ADR）
   details 闭集实测 {reason, search_session_id, session_catalog_filter_revision,
   current_catalog_filter_revision}
③ 缓存失效 —— cache/face-crops 键 = processor+long_edge+quality+margin+face_id+bbox+sha256
   （media/face_crop.py:234，不含 person_id）；GET /persons、/persons/{id}、/faces、/person-operations、
   /capabilities 的 face_summary 全为 SQLite 直读（persons/read.py + 共享 MEMBER_PREDICATE，无人物计数缓存）；
   命中集与 GET /persons/{id}.asset_count 同源同值（真实夹具 + 合成库双重证据）

关键 diff 定位：
  api/models.py:968-969（两字段，故意无 maxItems）｜search/contracts.py:203-232,277-299（判定表）
  search/retrieval.py:255-300（person_condition 内联 MEMBER_PREDICATE；31 行常量）
  search/service.py:538-556（_canonical_person_ids）、:677-709（顺序收敛点）、:815-846,895-899（scope/digest 接线）
  search/sessions.py:57-80（digest 追加两键）

最小反例与受影响测试：
  a) all 无 id / 101 项 / 原始重复 / 非整数 / 未知字段 → 422 且零写零会话（shape 参数化批 12 例 + 天花板 1 例）
  b) any/all × 地点/来源/可用性/语义 AND（matrix 1 例 + availability 1 例 + 通道候选数断言）
  c) 三种“不是成员”（SUGGESTED / MANUAL_UNASSIGNED / 活动排除）与同图 sibling（2 例）
  d) 五类人物编辑后旧会话 stale + 旧 id 重搜（参数化 5 例）、不同人物条件续页被摘要拒绝（1 例）、TTL（1 例）
  e) 投影滞后不复活已移除命中（1 例，total_candidates 1）
  f) 隐私闭集（1 例 4 份响应体）+ filters 闭键（1 例）+ /search/image 空回显（1 例）

未验证项：30 万容量延迟/索引使用、多进程并发、脸级口径、person_mode 映射、时间条件、
  GET /api/v1/assets 人物筛选、C 级打包、真实浏览器 UI
禁止扩大范围：不得改 ADR/companion/0001～0013/schema_version/依赖/spec/前端/places|geo|ocr|ingestion|
  workers|face；不得改 persons/** 语义（含 F3 缺陷）；不得为通过审查修改既有测试断言（只允许新增）
阶段 B 若发现普通实现缺陷：写最小反例 + 允许修复面退回 DS-H，不自行接管全量修复
```

## 9. 阶段交接

```text
任务 ID / 状态：P3-07C / ENGINEERING REVIEW PASS / SOL FOCUSED REVIEW PENDING（阶段 A 完结）
阶段 A 结论：11 项范围逐条通过；顺序收敛判定“一致”；未做最小修复；F3 为越界既有缺陷（建议随 P3-08）
DeepSeek max：不升级（V3.1 §2.2 六类条件均未命中，理由见文首）
本次改动：新增本报告 + 更新 docs/development-status.md（工作树另有用户既有的 revision 6 路由对齐文档改动）
证据：test-results/p3-07c-full.xml、test-results/p3-07c-focused.xml；报告 §3 顺序矩阵；报告 §2.11 复现
下一步：阶段 B —— 独立新会话 C2 / GPT-5.6 Sol / high，只读本报告压缩交接包，只审
  canonical / revision / 缓存失效三条边界；阶段 A 不生成 P3-08 卡、不标 PASS
报告（阶段 B 应写）：docs/reports/P3-07C-sol-focused-review.md
```
