# P3-07C 阶段 B — canonical / revision / 缓存失效聚焦终审

- 状态：**PASS（C2 FINAL SHORT RE-REVIEW）**
- 日期：2026-09-21
- 路由：按任务卡的 C2 / GPT-5.6 Sol / high 聚焦终审边界执行；模型选择由用户在 Codex 中手动控制，本报告不声称自动切换
- 输入：`docs/reports/P3-07C-dsh-engineering-review.md` §8 与 `docs/reports/P3-07C-dsh-canonical-fix.md` §11.5 压缩交接包
- 审查对象：P3-07B 实现提交 `733f1a3`（P3-07B 报告收尾 `033e3ca`）
- 最新结论：canonical、revision 与缓存失效三条边界全部通过；第三轮修复正确区分 filter 与 suggestion 重试耗尽错误面，P3-07C 可以标记 PASS。§1～§8 保留历次发现与修复轨迹，最终裁决见 §9

## 1. 三条边界结论

| 边界 | 结论 | 依据 | 未决项 |
|---|---|---|---|
| canonical | **不通过** | `_canonical_person_ids()` 对每个原始 id 分别执行查询，但没有显式只读事务、revision 前置绑定或跨整个检索的一致快照；`Catalog.read_one/read_all` 也是 autocommit 单语句读取。第二个 Catalog 连接可在两个 id 的解析之间提交合法 merge，产生任一一致快照都不可能给出的混合答案（§2） | 退回 DS-H 在 `src/photo_search/{search,catalog,api}` 与对应测试内做最小修复 |
| revision | **通过** | 单一 `catalog_filter_revision`；人物五类编辑共用 `PERSON_CHANGED`；续页顺序为 revision → suggestion → digest → binding。revision 未变而条件改变仍为 `INVALID_CURSOR`，merge 等权威变化后旧会话为 `CATALOG_FILTER_REVISION_STALE`，details 保持冻结四键 | canonical 修复不得改变该优先级或另建人物 revision |
| 缓存失效 | **通过** | 人物读面与命中谓词仍直接读 SQLite 和共享 `MEMBER_PREDICATE`；`crop_key` 只含 processor/尺寸/质量/边距/face_id/bbox/sha256，不含 `person_id`；命中集、`asset_count` 与旧投影移除反例通过 | canonical 修复不得把人物事实塞入派生缓存或 `search_documents` |

由于 canonical 是任务卡点名的三个必过边界之一，本阶段总裁决为 **FAIL**。阶段 A 的全量成功证据仍有效，但不能覆盖本次新增的并发一致性反例。

## 2. canonical 失败：同连接不是同一只读快照

### 2.1 冻结依据

ADR-0009 §4.1～§4.3 要求：每个 id 复用唯一 canonical 解析器；解析在**同一个只读快照**内完成；随后谓词、回显与摘要使用同一 canonical 集合；任一失败整请求拒绝。§4.4 又要求 merge 后旧 id 的新请求解析到当前 canonical，而 merge 前会话只允许以 revision stale 结束。

### 2.2 实现偏差

`src/photo_search/search/service.py::_canonical_person_ids()` 在同一个 `Catalog.connection` 上逐个调用
`resolve_canonical_person_id()`，随后才构造 scope、摘要并运行权威谓词。该连接使用 `isolation_level=None`；
`Catalog.read_one/read_all` 没有读事务或读锁。因而“同一个连接”只能证明连接对象相同，不能证明多条 SELECT
属于同一个 SQLite 快照。阶段 A 报告 §2.5 把这两件事等同，结论不成立。

### 2.3 最小反例

临时审查测试在既有三照片夹具上执行，未修改生产语义：

1. 初态 A 与 B 都是活动人物；照片 1 同时有 A、B，A 命中 `{1,2}`，B 命中 `{1,3}`；
2. 请求 `person_ids=[A,B], person_match=all`；
3. 第一个原始 id 已解析后，由**第二个正常 `Catalog` 连接**通过现有 `PersonService.merge()` 提交
   `merge(target=B, sources=[A])`，该提交同时搬移 link 并递增 `catalog_filter_revision`；
4. 原请求继续解析第二个 id、构造 scope 并完成检索。

实测得到：

```text
filters.person_ids = [A, B]
hits = []
```

这不是任何一致快照的合法结果：

- 合并前快照应回显 `[A,B]`，`all` 命中两人共现的照片 `{1}`；
- 合并后快照应 canonical 折叠为 `[B]`，命中 B 接收 A 后的并集 `{1,2,3}`。

实际的 `([A,B], ∅)` 是“合并前 canonical 集合 + 合并后成员事实”的混合版本，也会把已合并源 id 回显为
当前 canonical，违反 ADR-0009 §4.2/§4.3。现有 `search_assets()` 的 revision 起点是在 canonical 解析之后读取，
所以这次 merge 已经发生在它的 `revision_at_start` 之前，末尾 revision 相等，无法检测该窗口。

失败证据：`test-results/p3-07c-sol-probe.xml`，`tests=1 failures=1 errors=0 skipped=0`；失败值为
`((1, 2), frozenset())`，而允许的两个一致结果分别为 `((1, 2), {1})` 与 `((2,), {1,2,3})`。
临时测试代码已从仓库删除，`tests/integration/test_p3_person_search_filter.py` 无残留 diff。

## 3. revision 边界：通过

代码核对确认：

- `catalog_filter_revision` 仍是人物与地点共享的唯一权威筛选 revision；独立的
  `place_suggestion_revision` 只用于 `place_state=suggested`，不是第二套人物 revision；
- `_continue_session()` 在 live/offset 校验后先比较 `catalog_filter_revision`，再比较 suggestion revision、
  `query_hash` 和 profile/generation binding；
- 该顺序满足 ADR-0009 §4.4/§6.2：merge 会同时改变 canonical 与 revision，旧会话必须优先返回
  `CATALOG_FILTER_REVISION_STALE`；revision 未变时不同条件仍由摘要返回 `INVALID_CURSOR`；
- revision 已变且 profile 同时切换时优先 stale，符合 ADR-0009 §6.2 的明确要求；ADR-0003 只冻结 profile
  mismatch 的拒绝语义，没有给它规定高于权威 revision 的优先级；
- stale details 仍闭合为 `reason/search_session_id/session_catalog_filter_revision/current_catalog_filter_revision`。

受影响测试中 rename/merge/split/face-correction/undo 五类编辑、不同人物条件摘要拒绝均通过。该边界没有发现
需要新卡的问题；但 canonical 修复后必须短审 revision-before-digest 与 merge 旧 id 两条路径。

## 4. 缓存失效边界：通过

- `GET /persons`、`GET /persons/{id}`、`GET /faces`、`GET /person-operations` 与 `face_summary` 的人物事实仍由
  SQLite 读取；`persons/read.py` 与搜索谓词复用 `persons/members.py::MEMBER_PREDICATE`，未发现人物计数缓存；
- `media/face_crop.py::crop_key()` 的实际 payload 为 processor、long edge、quality、margin、`face_id`、bbox、
  源内容 sha256，**不含 `person_id`**；`avatar_face_id` 仍从当前 persons 行读取；
- 人物事实没有进入 `search_documents` 六个文本列；提交前 `_verified_ids()` 仍用当前权威人物谓词，旧投影
  不复活已移除命中的既有反例通过；
- `person_ids=[p]` 的命中集合与 `GET /persons/{p}.asset_count` 同源同值的既有反例通过。

该边界没有发现需要新卡的问题。

## 5. 实测与环境事实

| 证据 | 结果 |
|---|---|
| 阶段 A 全量 JUnit `test-results/p3-07c-full.xml` | 引用阶段 A：1334 tests / 0 failures / 0 errors / 48 environment skips |
| 阶段 B 最小受影响批 `test-results/p3-07c-sol-focused.xml` | exit 0；12 tests / 0 failures / 0 errors / 0 skipped（canonical 常规路径 + revision + 缓存反例） |
| 阶段 B 并发快照探针 `test-results/p3-07c-sol-probe.xml` | **预期暴露缺陷而失败**；1 test / 1 failure / 0 errors / 0 skipped；混合结果见 §2.3 |

受限令牌仍不能创建项目内 `.pytest-tmp/P3-07C-*`（`PermissionError [WinError 5]`）。两次有效 pytest 均经
已授权的正常 Windows 权限路由运行，仍使用项目内 `.pytest-tmp` 与 `.uv-cache`，未修改 ACL；夹具只使用测试
临时目录，未触碰真实照片库。

## 6. 退回 DS-H 的最小修复范围

仍使用 **P3-07C**，不生成 P3-08 卡。DS-H 只处理本报告的 canonical 失败：

1. 在 `src/photo_search/{search,catalog,api}` 与对应测试内建立可证明的一致性边界，使 canonical 解析、
   权威人物谓词、回显与 continuation digest 不会跨越一次已提交的人物/地点权威 revision；不得只把“同连接”
   重新描述成“同快照”。
2. 可选方案需结合现有搜索内的投影 catch-up 写入评估：专用只读快照，或把 canonical 解析前的
   `catalog_filter_revision` 作为整个新搜索的前置绑定并在会话发布前检测/有界重试。不得返回
   `FILTER_REVISION_CHANGED` 的混合人物页，也不得另建人物 revision。
3. 把 §2.3 的第二 Catalog 连接 + 合法 merge 反例转为持久回归测试。测试必须接受一致的合并前或合并后结果，
   但拒绝 `([A,B], ∅)` 之类混合结果；同时覆盖 canonical 回显、`all` 谓词和摘要使用同一集合。
4. 重跑直接受影响测试和完整 unit/integration JUnit；确保 revision-before-digest、stale details、
   `MEMBER_PREDICATE` 同源和 face-crop cache key 边界不变。

修复不得修改 ADR/companion、migration/schema、依赖、`persons/**` 语义、前端或 P3-07B 报告 §6 的 merge/undo
顺序缺陷。修复完成后，重新交独立 C2 仅短审本次失败的 canonical 边界及其 revision/cache 直接回归面；三条
全部通过后才可标记 P3-07C PASS 并生成 P3-08 卡。

## 7. DS-H 第一轮修复后的 Sol 短审补轮：仍不通过

输入：[P3-07C DS-H canonical 修复报告](P3-07C-dsh-canonical-fix.md) §9。短审只检查修复后的 canonical
边界及 revision/cache 直接回归面，没有重复全量工程主审。

### 7.1 已修好的部分

- `Catalog.read_snapshot()` 确实用 `BEGIN DEFERRED` 固定第一条读建立的 WAL 快照，并阻止同一个 Catalog 的
  写事务与嵌套快照；原先“两个 id 的解析之间提交 merge”这一窗口被关闭；
- `_person_authority_base()` 在同一快照内读取 `catalog_filter_revision` 并解析全部原始 id，canonical 集合
  内部不再混合两个状态；
- pre-binding 与 `search_assets()` 末尾 revision 不同会丢弃该尝试、重建 canonical/scope/digest；三次用尽
  返回结构化 stale 且不建会话；
- continue 路径的 revision → suggestion → digest → binding 顺序未改，缓存键、SQLite 直读和
  `MEMBER_PREDICATE` 同源边界也未改。DS-H 的完整 JUnit 证据为 1337 tests / 0 failures / 0 errors /
  48 environment skips。

### 7.2 仍存在的发布窗口

修复只把 revision 比较做到 `run_retrieval()` / `search_assets()` 返回时：

```text
candidate = run_retrieval()
candidate.catalog_filter_revision == pre_binding     ← 最后一次 revision 比较
entries = _entries_of(payload)
rerank
sessions.create(... old canonical/filters/revision ...)
_render_page(...)                                    ← 再次读取当前权威谓词
return response
```

因此第二个 Catalog 连接仍可在“最后一次 revision 比较之后、`_render_page()` 的权威复核之前”提交 merge。
这次提交不会触发重锚：会话已经使用旧 canonical、旧摘要和旧 revision 创建，而 `_render_page()` 用合并后的
成员事实过滤旧候选，仍会发布混合首页。

短审临时反例把合法 `PersonService.merge(target=B, sources=[A])` 放在唯一的 `_entries_of(payload)` 调用内，
即恰好位于上述窗口。实测：

```text
HTTP 200
filters.person_ids = [1, 2]
hits = []
```

一致的合并前结果只能是 `([1,2], {1})`，一致的合并后结果只能是 `([2], {1,2,3})`；实测值与最初 Sol
反例相同，仍违反 ADR-0009 §4.2/§4.3 及 ADR-0005 §10 的“不发布混合版本页”。失败 JUnit：
`test-results/p3-07c-sol-rereview-probe.xml`，`tests=1 failures=1 errors=0 skipped=0`。临时测试代码已删除，
生产/持久测试 diff 未被本轮修改。

### 7.3 最新裁决与第二轮最小修复边界

canonical **仍不通过**；revision/cache 的既有语义仍通过，但不足以关闭 P3-07C。当前状态改为
**SOL SHORT RE-REVIEW FAIL / DS-H SECOND FIX REQUIRED**，继续使用 P3-07C，不生成 P3-08。

DS-H 第二轮只需把一致性边界延长到首页最后一次权威读取与会话发布：

1. 在确认 pre-binding 后，保证 `_entries_of`、rerank、会话内容构造、`_render_page` 与最终响应使用一个不会
   跨 authority revision 的发布边界；或在首页完成后执行最终 revision 校验并安全丢弃该次会话/结果后重锚。
2. 不得只在 `search_assets()` 末尾重复现有检查；必须覆盖本反例的 retrieval-return → first-page-render 窗口，
   并保证失败/重试不会泄漏临时 search session。
3. 把“第二连接在 `_entries_of`/最后 revision 检查之后提交合法 merge”的反例转为持久回归；仍只接受一致的
   合并前或合并后结果，拒绝 `([A,B], ∅)`。
4. 保持第一轮修好的 read snapshot、三次有界重锚、continuation revision-before-digest、stale details、
   cache key 与 SQLite 同源读面；只重跑直接受影响批和完整 unit/integration JUnit。

允许修改范围仍限 `src/photo_search/{search,catalog,api}` 与对应测试；不得修改 ADR/companion、migration、
schema、依赖、`persons/**` 语义、前端或 P3-07B 报告 §6 的 merge/undo 缺陷。修复后再交 C2 短审本次发布窗口。

## 8. DS-H 第二轮修复后的 Sol 第二次短审补轮：错误面仍不通过

输入：[P3-07C DS-H canonical 修复报告](P3-07C-dsh-canonical-fix.md) §10.6。本轮没有重复全量工程主审，
只检查 §7 退回的首页发布窗口，以及第一轮成果的 revision/cache 直接回归面。

### 8.1 发布闸门：通过

- `_AuthorityBase` 在同一个 `Catalog.read_snapshot()` 中固定 canonical id、`catalog_filter_revision` 与独立的
  `place_suggestion_revision`；第一轮的同快照解析/pre-binding 没有被削弱；
- 新首页按 `run_retrieval → _entries_of → rerank → build_session → _render_page → _authority_holds →
  register` 执行。最后一次权威读取完成后计数器若已变化，本次结果和未注册 session 都会被丢弃；
- `SearchSessionStore.create()` 保留原有一调用语义，内部组合 `build_session()` 与 `register()`；发布路径只有闸门
  通过才注册，不会让丢弃尝试泄漏 session 或挤掉 LRU 既有项；
- 两条 DS-H 持久回归独立复跑通过：late merge 与单次 suggestion bump 均得到一致的新状态，JUnit
  `test-results/p3-07c-sol-rereview2-focused.xml` 为 2 tests / 0 failures / 0 errors / 0 skipped。

因此 §7.2 的 canonical 晚窗口已经关闭；缓存键、SQLite 权威复核和 continuation 的 revision-before-digest
路径也未见直接退化。

### 8.2 重试耗尽时错误码错分

`_authority_holds()` 同时检查 filter revision 与（仅对 `place_state=suggested`）suggestion revision，但它只返回
布尔值。三次尝试耗尽后的唯一出口却固定抛出：

```text
CATALOG_FILTER_REVISION_STALE
reason = CATALOG_FILTER_REVISION_CHANGED
session_catalog_filter_revision = base.filter_revision
current_catalog_filter_revision = read_filter_revision(...)
```

临时反例在每一次 `_entries_of(payload)` 后仅调用既有 `bump_suggestion_revision()`，不改
`catalog_filter_revision`。实测三次尝试后 HTTP 409，但返回 `CATALOG_FILTER_REVISION_STALE`，且
`session_catalog_filter_revision=2`、`current_catalog_filter_revision=2` 完全相等。该结果既声称目录 revision
改变，又用 details 证明它没有改变。

冻结 ADR-0005 的错误表明确把 suggested 列表 revision 过期归为 `PLACE_SUGGESTION_REVISION_STALE`；现有
continuation 路径也已使用该码、`reason=SUGGESTION_REVISION_CHANGED` 与 suggestion revision details。因此这不是
新增协议，而是新搜索发布重试出口没有保留既有错误分类。

失败证据：`test-results/p3-07c-sol-rereview2-probe.xml`，1 test / 1 failure / 0 errors / 0 skipped；失败点正是
期望 `PLACE_SUGGESTION_REVISION_STALE` 而实际得到 `CATALOG_FILTER_REVISION_STALE`。临时测试代码已删除，
本轮未改生产代码或持久测试。

### 8.3 最新裁决与第三次最小修复边界

当前裁决为 **SOL SECOND SHORT RE-REVIEW FAIL / DS-H ERROR-SURFACE FIX REQUIRED**。继续使用 P3-07C，
不生成 P3-08。DS-H 只需：

1. 让发布闸门失败保留具体移动的权威计数器，或在重试耗尽时按既有优先级重新读取并分类；filter revision
   确实变化时仍返回 `CATALOG_FILTER_REVISION_STALE`，suggestion-only 变化时返回
   `PLACE_SUGGESTION_REVISION_STALE` / `SUGGESTION_REVISION_CHANGED` 与对应的
   `session_suggestion_revision`、`current_suggestion_revision`；
2. 把“每次尝试都只递增 suggestion revision”的反例转为持久回归，断言三次有界尝试、正确 409 错误面、
   filter revision 未变、零 session；同时保留既有“每次人物 rename”用尽重试仍为 catalog stale 的测试，
   以锁定两类错误及 filter-first 优先级；
3. 复跑本轮 2 条发布窗口回归、两类重试耗尽回归及直接受影响批；生产修改应只在
   `src/photo_search/search/service.py` 与对应测试，不改快照、session store、ADR/companion、schema、依赖、
   `persons/**`、缓存或前端。

修复后 C2 只短审这个错误分类及 4 条直接回归；全部通过后才可关闭 P3-07C 并生成 P3-08。

## 9. DS-H 第三轮修复后的 Sol 最终短审：通过

输入：[P3-07C DS-H canonical 修复报告](P3-07C-dsh-canonical-fix.md) §11.5。本轮严格限于重试耗尽的错误面
分类及其直接回归，没有重复阶段 A 全量工程主审，也没有修改生产代码或持久测试。

### 9.1 错误分类与不变量

- `_authority_drift()` 按 continuation 已有顺序先检查 `catalog_filter_revision`，再仅为
  `place_state=suggested` 检查独立 `place_suggestion_revision`；
- `_publication_refusal()` 使用最后一次尝试的 `_AuthorityBase` 重新读取当前计数器：filter 变化返回
  `CATALOG_FILTER_REVISION_STALE / CATALOG_FILTER_REVISION_CHANGED`，否则 suggested 请求返回
  `PLACE_SUGGESTION_REVISION_STALE / SUGGESTION_REVISION_CHANGED`；两者同时变化时 filter 优先；
- 两类 details 都沿用现有字段；新搜索尚未注册 session，因此不虚构 `search_session_id`；没有新增错误码、
  revision、表、迁移或依赖；
- canonical 同快照解析/pre-binding、首页渲染后的发布闸门、通过后才注册 session、continuation 的
  revision-before-digest、缓存键与 SQLite 权威同源均未改变。

### 9.2 独立复跑证据

Sol 独立运行 5 个直接案例：suggestion-only 重试耗尽、filter+suggestion 同时变化、连续人物 rename、
late merge、单次 suggestion bump。结果：

| 证据 | 结果 |
|---|---|
| `test-results/p3-07c-sol-final-rereview.xml` | 5 tests / 0 failures / 0 errors / 0 skipped |
| DS-H 完整 JUnit `test-results/p3-07c-fix3-full.xml` | 1341 tests / 0 failures / 0 errors / 48 environment skips（引用阶段 A 证据，未重复运行） |
| Ruff（本次修复文件） | clean |
| `uv lock --check` | exit 0，95 packages |
| `git diff --check` | clean |

测试断言覆盖正确 code/reason/details 闭集、`current > session`、suggestion-only 时 filter revision 未变、
三次有界尝试、失败零 session，以及 filter-first 优先级。项目临时目录与 uv 缓存仍在项目内，未修改 ACL，
未触碰真实照片库。

### 9.3 最终裁决

三条必审边界最终均通过：

| 边界 | 最终结论 |
|---|---|
| canonical | **通过**：同快照解析与 pre-binding；检索期和首页渲染后的两个窗口均有有界重锚与持久反例 |
| revision | **通过**：单一 filter revision、独立 suggestion revision、filter-first、续页顺序与两类 stale 错误面一致 |
| 缓存失效 | **通过**：人物事实仍直读 SQLite，`MEMBER_PREDICATE` 同源，face-crop key 不含 `person_id` |

因此 **P3-07C PASS**。P3-07B 报告 §6 的“合并目标 id 大于源 id 时 undo 唯一索引冲突”没有在本任务
越界修复，必须进入 P3-08 实现验收。P3-08 同时包含尚未冻结的新旧 `face_id` 可审查映射、人物/地点交叉故障
矩阵和真实浏览器闭环，不能在一张卡内同时完成协议、实现、D 级验收与独立两阶段终审；按任务粒度拆为：

1. **P3-08A**：新旧 `face_id` 映射契约与 P3 故障验收矩阵冻结（DS-H 草拟/工程预审 → Sol high 冻结）；
2. **P3-08B**：按冻结件实现映射，修复 merge/undo 顺序缺陷，完成 D 级故障全集与真实浏览器闭环；
3. **P3-08C**：独立 DS-H 全量工程主审 → 命中具名条件才 DS-MAX → Sol high 聚焦终审。

下一任务为 P3-08A，任务卡 `docs/tasks/P3-08A.md`。
