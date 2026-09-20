# P3-07B — 人物 all/any × 地点 AND 组合搜索实现

- 状态：**PASS（B + D 级；未改打包输入集合，因此不触发 C 级）**
- 日期：2026-09-20
- 实际模型：DS-H（DeepSeek V4.1 Flash / 高推理），由用户在 Codex 中手动选择；未发生自动模型切换
- 起点 HEAD：`6fbe68e`（P3-07A 收尾，开始时工作树干净）
- 协议依据：[ADR-0009](../decisions/ADR-0009-person-place-combined-search-contract.md) 与
  [p3-person-search-contracts.v1.json](../decisions/contracts/p3-person-search-contracts.v1.json)（未改动）
- 上一任务：[P3-07A 报告](P3-07A-person-search-contract.md)
- 下一任务：[P3-07C 独立新会话 DS-H 全量工程主审](../tasks/P3-07C.md)

## 1. 交付范围与文件清单

本次把 ADR-0009 的冻结规则落到 `POST /api/v1/search` 的可执行面上，**没有**新增表、列、索引、migration、
queue cause 或依赖，`schema_version` 仍为 **13**，`0001`～`0013` 逐字节未改。

| 文件 | 改动性质 |
|---|---|
| `src/photo_search/catalog/errors.py` | 加性新增 `INVALID_PERSON_FILTER`（本任务唯一新码） |
| `src/photo_search/api/errors.py` | 该码映射 **422** |
| `src/photo_search/api/models.py` | `SearchRequestModel` 加性新增 `person_ids`（`list[StrictInt]`）与 `person_match` |
| `src/photo_search/search/contracts.py` | `SearchRequest` 加性新增两个可选字段、判定表、`person_ids` 形状检查 |
| `src/photo_search/search/retrieval.py` | `SearchScope` 加人物条件、`person_condition()`（复用 `MEMBER_PREDICATE`）、`scope_view` 回显、`MAX_PERSON_IDS`/`PERSON_MATCH_VALUES` |
| `src/photo_search/search/sessions.py` | `query_hash` 纳入 canonical 人物条件 |
| `src/photo_search/search/service.py` | canonical 解析、人物条件进入 scope/投影/复核、续页顺序收敛（见 §3.3） |
| `tests/p3_person_search_fixtures.py`（新） | `%TEMP%` 夹具：真实扫描库 + 真实人物权威行 |
| `tests/unit/test_p3_person_search_scope.py`（新） | 21 例：信封形状、scope 谓词、续页摘要 |
| `tests/integration/test_p3_person_search_filter.py`（新） | 37 例：十条验收证据 |
| `tests/unit/test_p3_person_search_contracts.py` | **只加不改**：新增 5 例实现↔冻结件漂移检查（原 41 例断言逐字保留） |
| `tools/diagnostics/person_search_smoke_fixture.py`（新） | `%TEMP%` 真实夹具冒烟（无模型、无网络） |
| `docs/reports/P3-07B-person-search-implementation.md`、`docs/development-status.md`、`docs/tasks/P3-07C.md` | 报告、状态、下一卡 |

禁止范围逐项未被触碰：`docs/decisions/ADR-0009-*.md`、`p3-person-search-contracts.v1.json` 与四个既有
companion、`catalog/migrations/**`、`schema_version`、`pyproject.toml`、`uv.lock`、`PhotoSearch.spec`、
前端资源、`places/**`、`geo/**`、`ocr/**`、`ingestion/**`、`workers/**`、`face/**` 的检测/embedding/阈值。
`persons/**` 只读复用（`identity.resolve_canonical_person_id`、`members.MEMBER_PREDICATE`），未做任何修改。

## 2. 实现要点

### 2.1 请求形状与判定表（ADR-0009 §2.2）

`SearchRequest`/`SearchRequestModel` 只加两个**可选**字段，`contract_version` 仍是 `p2-contracts.v1`，
既有字段、默认值与上下界一字未改；不提交这两个字段的旧调用方拿到与 P2-06A/06B 逐字相同的答案。判定表逐行落地：

| 输入 | 结果 |
|---|---|
| 缺省 / `[]`（`any` 或缺省） | 合法，**不产生**人物条件（不是「没有人物脸」）；回显 `[]`/`any` |
| 缺省或 `[]` + `person_match=all` | 422 `INVALID_PERSON_FILTER`，`reason=person_match_all_requires_ids`、`person_match=all` |
| 非数组 / 元素非整数（含 `true`/浮点/字符串/`null`）/ `<1` / 原始重复 | 422 `VALIDATION_ERROR` |
| 元素数 `>100` | 422 `INVALID_PERSON_FILTER`，`reason=person_filter_too_large`、`max_person_ids=100` |
| `person_match` 非法值 / 未知字段 | 422 `VALIDATION_ERROR` |

两处刻意的分层：HTTP 模型用 `StrictInt` 拒绝 `true`/`1.5`/`"3"`/`null`（与契约一致），但**不**在传输层设
`maxItems`——元素数超界必须由信封给出 ADR 冻结的 `INVALID_PERSON_FILTER` 并公开 `max_person_ids`，而不是
一个通用形状错误。原始重复由信封拒绝（与 ADR-0007 §5 对重复 face 的口径一致），canonical 折叠是另一件事。

### 2.2 唯一权威谓词与 AND 组合（ADR-0009 §3）

`retrieval.person_condition()` 是人物条件的唯一实现，片段里直接内联 `persons/members.py` 的
`MEMBER_PREDICATE`（`CONFIRMED` + 无 `MANUAL_UNASSIGNED` + 无 `active=1` 排除），检索层**没有**第二份口径。
`any` = 一个 `EXISTS … l.person_id IN (…)`；`all` = 每个人一个 `EXISTS … l.person_id = ?` 用 AND 连接
（`|P|=1` 时两者等价）。该片段进入 `scope_condition`，因此**每个通道**（FTS、向量）与**提交前复核**
（`_verified_ids`）、**补查页**（`pending_projection_page`）都自动带上人物条件，不存在「先全库 Top-K 再过滤」。

人物条件只收窄候选，不新增命中原因或排序通道：`hit_reason` 仍只有 `TEXT`/`SEMANTIC`/`TEXT+SEMANTIC`/
`FILTER_ONLY`，`matched_by` 仍只有 `fts`/`vector`/`filter`。纯人物/纯筛选请求走既有权威列表与
`FILTER_ONLY` 行形状，`profile_id`/`binding` 为 `null`（无激活 profile 时），`pending_semantic` 为 `null`，
查询编码器一次都不被调用。地点 `place_ids`/`place_state` 的拒绝矩阵原样保留。

### 2.3 canonical 解析、去重与同源口径（ADR-0009 §4）

每个原始 id 都用 `persons/identity.resolve_canonical_person_id` 在同一只读快照里解析，结果去重后**升序**
作为谓词、回显与摘要的唯一身份。任一 id 失败即整请求拒绝：不存在 → 404 `PERSON_NOT_FOUND`（`person_id`），
成环/悬挂 → 409 `PERSON_REDIRECT_CYCLE`（`person_id`，可含 `steps`/`missing_person_id`），不丢 id、不部分返回。
不同原始 id 折叠到同一 canonical 是合法去重（`filters.person_ids` 回显 canonical 集合），原始重复仍是形状错误。

`person_ids=[p]` 的命中集合与 `GET /api/v1/persons/{p}` 的 `asset_count` 在真实夹具上**同源同值**：
两者都用同一个 `MEMBER_PREDICATE`，检索侧是 `EXISTS` 形式的照片级谓词，读模型侧是
`COUNT(DISTINCT f.asset_id)`；`assets` 表在 schema 中只有 `ACTIVE` 且 `faces.asset_id` 是
`ON DELETE RESTRICT` 外键，所以两侧集合恒等（§4 逐人实测）。

### 2.4 会话、游标与缓存边界（ADR-0009 §6）

`sessions.query_hash` 纳入 canonical、去重、升序的 `person_ids` 与规范化 `person_match`，连同既有
`text`/`source_id`/`availability`/`place_ids`/`include_descendants`/`place_state`/`top_k`/`page_size`；
`/search/image` 继续用图片摘要占据 `text` 位。没有第二个 revision：人物改名/合并/拆分/纠错/撤销各使同一个
`catalog_filter_revision` 恰好 +1（cause `PERSON_CHANGED`），`place_state=suggested` 仍额外绑定既有的
`suggestion_revision`；续页过期**复用** `CATALOG_FILTER_REVISION_STALE`（details 仍是既有四个键，未加 `cause`）。

### 2.5 投影与补查（ADR-0009 §5）

人物事实不进入 `search_documents`：`search_projection` 的键与含义不变（`SYNCED` 当且仅当
`queued_assets == 0`，不得新增人物字段），`pending_semantic` 的 200 上界、
`reason ∈ {GENERATION_NOT_READY, ENTRY_NOT_CONFIRMED}`、`limit/truncated/count` 语义不变。人物条件本身是
权威 SQLite 谓词，所以文本/向量投影滞后不会改变判定，提交前复核也包含人物条件——旧投影不能复活一张已被
取消归类、被排除或已改判的照片（§4 有专门反例）。

## 3. 与既有冻结契约的关系

### 3.1 未改动项

`p2-contracts.v1`、`p2-place-contracts.v1`、`p3-face-correction-contracts.v1`、
`p3-person-split-contracts.v1` 的 SHA-256 与冻结时记录逐字节一致（契约测试与集成测试各断言一次）；
ADR-0009 与 `p3-person-search-contracts.v1.json` 未改；`0001`～`0013` 连续且内容未改；
`SUPPORTED_SCHEMA_VERSION == 13`；依赖仍为 95 包（`uv lock --check` exit 0）。

### 3.2 加性扩展点

唯一新错误码是 `INVALID_PERSON_FILTER`（422），details 只使用冻结字段集
（`reason`/`person_match`/`max_person_ids`）；其余失败全部复用既有码，未给任何既有码新增 details 键。
`filters` 只新增 `person_ids`（canonical 升序，无条件时 `[]`）与 `person_match`（无条件时 `"any"`），
两键始终存在，其余键集合不变；`/search/image` 不新增 multipart 字段，其回显固定为 `[]`/`"any"`。

### 3.3 一处需要 DS-H 主审确认的**行为顺序收敛**

`_continue_session` 过去先比 `query_hash`、后比 `catalog_filter_revision`。合并会把源人物的 link 改写为
目标人物（`persons/operations.py` 先 DELETE 再 INSERT），因此**被合并掉的旧 id 的 canonical 身份会变**：
同一续页请求解析出的 digest 与建会话时不同，旧顺序会答 `INVALID_CURSOR`。ADR-0005 §10
（「继续翻页时 revision 不同则返回 `CATALOG_FILTER_REVISION_STALE`」）与 ADR-0009 §4.4/§6.2
（合并后的旧会话必须返回该码、由客户端重搜）要求它答 stale，故本任务把两个 revision 检查移到 digest 比较
**之前**：

```text
解游标 → 会话存活 → offset 越界 → catalog_filter_revision → suggestion_revision
       → query_hash → profile/generation binding → 渲染本页
```

影响面：revision 已变化的会话现在统一答 422/409 中的 **409 `CATALOG_FILTER_REVISION_STALE`**（更早、
更可操作）；revision 未变时的「不同查询条件续页」仍答 422 `INVALID_CURSOR`（既有 P2-06A 断言全部原样通过，
见 §5）。这是本任务**唯一**改变既有判断顺序的地方，已在 §4 用「不同人物条件 + 同一会话」和
「五类人物编辑后旧会话」两组反例钉住，**请 P3-07C 主审重点核对这一条**。

## 4. ADR-0009 §8 十条必测反例的实测证据

1. **判定表逐行**：`{"person_ids"}` 的字符串/数字/对象/`0`/负数/布尔/浮点/数字串/`null`/原始重复、
   `person_match` 非法值与未知字段各为 422（`VALIDATION_ERROR` 或 `INVALID_PERSON_FILTER`）；`all` 无 id 为
   `person_match_all_requires_ids`；101 项为 `person_filter_too_large`+`max_person_ids=100`，100 项合法。
   每例都断言请求后 `catalog_filter_revision`/`assets`/`faces`/`links`/`persons` 计数与会话数**逐字未变**
   （零写、零会话）。HTTP 侧另有参数化用例覆盖同一张表。
2. **AND 组合矩阵**：`any(A)`=`all(A)`={日落,西湖}（`|P|=1` 等价）；`any(A,B)`={日落,西湖,狗};
   `all(A,B)`={日落}；`any(A,B)×地点`、`all(A,B)×地点`、`×source_id`、`×availability`（双向）逐项验证，
   纯筛选行仍是 `FILTER_ONLY`；`place_ids+place_state=unassigned` 仍 422 `INVALID_PLACE_FILTER`；
   文本+人物时**两个通道**的候选集都被限制（`channels.vector.candidates == |expected|`），且命中原因里
   不出现任何人物值。
3. **canonical 五种结果**：合并后**旧 id** 单独使用与 `[A,B]` 折叠都答同一结果且回显 canonical；
   不存在 id → 404 `PERSON_NOT_FOUND`（details.person_id）；成环 → 409 `PERSON_REDIRECT_CYCLE`；
   悬挂 → 409 `PERSON_REDIRECT_CYCLE`（details.missing_person_id）。后两者整请求拒绝（无部分结果）。
   schema 的 `merged_into` 外键与 CHECK 让悬挂/自环在正常写入下不可能发生，故悬挂态由**带
   `PRAGMA foreign_keys=OFF` 的原始连接**注入（D 级故障注入，不改 ACL、不改 schema）。
4. **同源同值（真实夹具）**：对 A/B/C/D 四人，`person_ids=[p]` 的命中集合 == 直接按 `MEMBER_PREDICATE`
   查询得到的照片集合 == `GET /api/v1/persons/{p}` 的 `asset_count`（2/2/0/1）。
5. **三种「不是成员」**：`SUGGESTED` 关联（C 在西湖）与 `MANUAL_UNASSIGNED` 脸（C 在第三张）都不得命中；
   第三张上被人工排除的 A 脸也不得让 A 命中该照片。同图 sibling 不受影响：取消排除/恢复 `AUTO_ALLOWED`
   后 A 恢复 {日落,西湖,第三张}、C 变为 {第三张}，而 B、D 的命中集合逐字不变。
6. **无 GPU、无 profile、`FILTER_ONLY`**：纯人物请求 `profile_id`/`binding`/`query`/`pending_semantic` 均为
   `null`，两通道 `engaged=false`（向量通道 `reason=NO_QUERY_TEXT`），行形状为 `{"filter": n}`+
   `FILTER_ONLY`，且假编码器的调用列表为空。激活 profile 后重跑同一请求：仍 `engaged=false`、
   `pending_semantic=null`、编码器零调用。
7. **五类人物编辑后的会话失效**：`rename`/`merge`/`split`/`face-correction`/`undo`（undo 含 rename+undo
   两次提交）各使 `catalog_filter_revision` 恰好 +1/+2 且 cause 为 `PERSON_CHANGED`；旧会话续页统一
   409 `CATALOG_FILTER_REVISION_STALE`（`reason=CATALOG_FILTER_REVISION_CHANGED`，details 恰为四个既有键）；
   带旧 id 的**新**请求给出正确答案（merge 后 A∪B 三张；split/取消归类后仅西湖）。
   「不同人物条件 + 同一 `search_session_id`」的续页被摘要拒绝（422 `INVALID_CURSOR`，`any`↔`all` 亦然）；
   TTL 到期 → 409 `SEARCH_SESSION_EXPIRED`（`reason=SESSION_NOT_LIVE`、`ttl_ms=600000`）。
8. **投影与补查**：绑定 profile 的文本+人物请求上，`search_projection` 的键集合 == companion 的
   `required ∪ {terms, match_expression}`，`SYNCED ⇔ queued_assets == 0`，且不含任何 `person*` 键；
   `pending_semantic` 键集合 == companion required，`limit=200`、`count == len(entries) <= 200`、
   `reason` 属闭集、每条 entry 的 `semantic_ready` 恒为 false。**不复活**反例：文本投影仍指向西湖时，
   对西湖脸加人工排除后同一「文本+人物」请求只剩日落，`total_candidates` 同步降为 1。
9. **隐私负向扫描**：人物筛选成功响应、`PERSON_NOT_FOUND`、`INVALID_PERSON_FILTER`（两种）四份响应体
   逐字扫描，均不含人名、`name_key`、`aliases`、`bbox`、`landmark`、`embedding`、`similarity`、
   `confidence`、`payload_sha256`、`before_value_json`、经纬度、data root 或 `C:\` 形态路径；
   `filters` 键集合恰为 companion 的 11 个 required 键；`/search/image` 回显 `[]`/`"any"` 且拒绝
   `person_ids` multipart 字段（422 `VALIDATION_ERROR`）。
10. **冻结件原样**：四个既有 companion 的 SHA-256 逐个核对通过，ADR-0009 companion 内容未改；
    migration 恰为 `0001`～`0013` 连续 13 个，`schema_version == SUPPORTED_SCHEMA_VERSION == 13`；
    `uv lock --check` exit 0（95 包）；`p2-contracts.v1` 的 `SearchRequest`/`SearchResult` 既有断言、
    以及 P2-06A/P2-06B/P2-07 的全部既有用例在完整套件中未改一字通过。

## 5. 实测命令与结果

| 命令 | 结果 |
|---|---|
| `uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration --basetemp=.pytest-tmp/P3-07B-full --junitxml=test-results/p3-07b-full.xml` | exit 0；JUnit `tests=1334 failures=0 errors=0 skipped=48`（237.366 s）；基线 1271 → 1334（**+63**） |
| 本任务专项（`test_p3_person_search_scope.py` 21 + `test_p3_person_search_contracts.py` 46 + `test_p3_person_search_filter.py` 37） | exit 0；JUnit `tests=104 failures=0 errors=0 skipped=0`（19.314 s）；`test-results/p3-07b-focused.xml` |
| `uv run --no-sync ruff check .` | exit 0；`All checks passed!` |
| `git diff --check` | exit 0；clean |
| `uv run --no-sync uv lock --check` | exit 0；`Resolved 95 packages`（依赖未变） |
| `uv run --no-sync python tools/diagnostics/person_search_smoke_fixture.py` | exit 0；`status=ok`、`problems=[]`（证据见下） |

48 项 skip 全部是「缺模型权重 / 未启用 GPU」的显式环境 skip（与 P3-05C/P3-06/P3-07A 同一集合）；本任务
专项批为 **0 skip**。

### 5.1 真实夹具冒烟的实测输出（`tools/diagnostics/person_search_smoke_fixture.py`）

真实扫描生成 3 张照片；7 张脸、3 个人物、1 个 `SUGGESTED`、1 个 `MANUAL_UNASSIGNED`、1 个活动排除：

```text
counts:  A hit=2 asset_count=2 | B hit=2 asset_count=2 | C hit=0 asset_count=0
any(A,B) = [1, 2, 3]            all(A,B) = [1]
merge:   filter_revision 0 → 1；续页 stale_code = CATALOG_FILTER_REVISION_STALE
         旧 id 重搜 → canonical 回显 [A]，命中 {日落,西湖,第三张}
undo:    恢复后 B = [1, 2]
restart: 重启前游标 → SEARCH_SESSION_EXPIRED；重启后同查询 = [1, 3]
problems: []                    status: ok
```

每一步的命中集合都与 `PersonService.get_person()` 的 `asset_count` 逐人相等，行形状全部是
`FILTER_ONLY` + `{"filter": rank}`，且夹具目录（`%TEMP%` 下的 `p3-07b-person-search-*`）在结束时被删除。

## 6. 开发期发现（**超出本卡范围，未修改**）

运行上述冒烟夹具时命中一处**既有**缺陷：**当合并目标的 `person_id` 大于源人物时，该合并无法撤销**。
最小复现（1 张照片、2 张脸、2 个人物，无排除、无控制行）：

```text
merge(target_person_id=2, sources=[{person_id: 1}])  →  提交成功
undo(operation_id=...)                              →  IntegrityError:
   UNIQUE constraint failed: face_person_links.face_id
merge(target_person_id=1, sources=[{person_id: 2}])  →  undo OK
```

成因定位（只读，未改代码）：`persons/operations.py` 的 `undo_person_operation` 按 `item_index`（即
`sorted(before)` 的人物 id 升序）逐人恢复 link；源人物的 `live_links` 为空、`wanted_links` 是合并前的
link 集合，于是先把这些 `CONFIRMED` link 插回源人物，而此时目标人物仍持有被搬过去的同一批 link，
触发 `face_person_links` 上的部分唯一索引 `ux_face_person_links_effective`。目标 id 较小时顺序恰好相反，
所以既有测试未覆盖到该方向。

处置：P3-07B 的允许范围不含 `persons/operations.py` 与人物语义，且本卡明确规定不得顺手改动人物权威，
因此**未修**；本任务的冒烟夹具改用受支持的顺序（目标 id 更小），并把该缺陷连同最小复现记在这里。
建议由后续人物任务（P3-08 或单开一张卡）按人物撤销语义修复，并补一条「目标 id 大于源 id」的回归用例。
该缺陷不影响 P3-07B 的验收：合并后的**搜索**语义、canonical 解析、旧会话失效与旧 id 重搜均已实测通过。

## 7. 未验证项

- **30 万容量下的查询延迟与索引使用**：人物条件是 `EXISTS` 形式的权威 SQLite 谓词，未在正式容量上做
  计划/耗时测量；若 P4-01/P4-02 发现瓶颈，需单独出卡（ADR-0009 §5.2 明确本卡不授权迁移）；
- **多进程并发**：会话仍为进程内存储，本任务未验证多进程/多实例下的续页行为（沿用 P2-06A 的边界）；
- **脸级口径**：同框共现、按 `face_id` 检索、脸级条件均**未实现**，ADR-0009 明确不在本阶段；
- **自然语言解析器与 `person_mode`**：仓库仍无解析器，协议字段只有 `person_match`，一对一映射未实现；
- **时间条件**：`SearchRequest` 仍无时间字段（P4-00A/B 范围）；
- **`GET /api/v1/assets` 的人物筛选**：按 ADR-0009 §1 未扩展；
- **C 级**：本任务未改 `PhotoSearch.spec` 或任何打包输入集合，因此未做打包冒烟；`person_condition`
  内联在既有模块中，`src/photo_search/**` 的打包输入集合没有变化。

## 8. 环境事实

- `.pytest-tmp` 对受限令牌仍不可读/不可删（`WinError 5`），因此**全部** pytest 改由已授权的正常 Windows
  权限路由执行，basetemp 仍为项目内 `.pytest-tmp/P3-07B-*`，**未修改任何 ACL**（沿用 P3-05B/P3-06/P3-07A
  的既有事实）；
- 受限沙箱不允许写系统 `%TEMP%`，因此 `tools/diagnostics/person_search_smoke_fixture.py` 与 D 级原始连接
  故障注入同样经已授权路由执行；两者都只写自己的临时目录，并在结束时删除；
- `uv` 缓存使用项目内 `.uv-cache`（`UV_CACHE_DIR`）；JUnit 报告写入 `test-results/`。

## 9. 交接

```text
任务 ID / 状态：P3-07B / PASS（B + D；未触发 C）
起点 / 本任务改动：6fbe68e 起，改 7 个 src 文件 + 1 个既有测试（只加不改）+ 4 个新文件
核心交付：person_ids/person_match 加性可选字段与判定表；唯一 MEMBER_PREDICATE 复用的人物条件进入
  scope/通道/复核/补查；canonical 解析与去重；query_hash 纳入人物条件；INVALID_PERSON_FILTER(422)；
  filters 两键回显；/search/image 空人物回显
实测：完整 JUnit tests=1334 / 0 failures / 0 errors / 48 env skips；专项 104 例 0 skip；
  ruff、diff-check、lock-check clean；真实扫描夹具 status=ok、problems=[]
未改：ADR-0009、companion、0001～0013、schema_version 13、依赖、spec、前端
新发现（超范围，未修）：合并目标 person_id 大于源人物时 undo 触发
  face_person_links 唯一索引冲突；最小复现与成因见报告 §6，建议 P3-08 或单开卡处理
下一任务：P3-07C / 独立新会话 DS-H 全量工程主审（完整 diff、测试证据、ADR-0009 一致性、跨模块影响），
  随后把 canonical / revision / 缓存失效三条边界交 C2 / GPT-5.6 Sol / high 聚焦终审
任务卡：docs/tasks/P3-07C.md
```
