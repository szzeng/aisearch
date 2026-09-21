# P3-07C 阶段 A 补轮 — canonical 一致性边界最小修复（DS-H）

- 状态：**SOL CANONICAL FAIL → 第一轮修复（快照 + 前置绑定 + 有界重锚）→ 短审 FAIL（发布窗口）→
  第二轮修复（发布闸门）→ 第二次短审 FAIL（错误面分类）→ 第三轮修复（重试耗尽按计数器分类）已应用 /
  SOL 短审补轮 PENDING**
- 日期：2026-09-20
- 实际模型：**DS-H（DeepSeek V4.1 Flash / 高推理）**，由用户在 Codex 中手动选择；未发生自动切换
- 触发输入：[P3-07C 阶段 B（Sol 聚焦终审）](P3-07C-sol-focused-review.md) §2「canonical 不通过」与 §6「退回 DS-H 的最小修复范围」
- 起点 HEAD：`033e3ca`（阶段 A 结束时的工作树 + 阶段 A/阶段 B 两份报告）
- 任务卡：[docs/tasks/P3-07C.md](../tasks/P3-07C.md)；仍属 **P3-07C**，不生成 P3-08 卡

## 1. 失败的边界与冻结依据

Sol 的结论：revision 与缓存失效两条边界通过；**canonical 的「解析在同一个只读快照里完成」不通过**。

冻结依据（逐条对齐，未改冻结件）：

- **ADR-0009 §4.1**：每个 `person_ids` 元素都用唯一解析器 `persons/identity.resolve_canonical_person_id`；
- **ADR-0009 §4.2**：解析在**同一个只读快照**里完成，随后用于谓词、回显与摘要；任一 id 失败即整请求拒绝；
- **ADR-0009 §4.3**：canonical 集合升序输出并参与摘要与回显（回显里不得出现已被合并掉的旧 id）；
- **ADR-0009 §4.4 / §6.2**：合并使 revision +1，旧会话必须答 `CATALOG_FILTER_REVISION_STALE`；
- **ADR-0005 §10 / ADR-0003 §9**：查询计算途中 revision 变化不得发布混合版本页；会话绑定创建时的 revision。

## 2. 偏差（已独立复现）

`src/photo_search/search/service.py` 的 `_canonical_person_ids()` 在同一个 `Catalog.connection` 上**逐条**
执行 `resolve_canonical_person_id()`，而该连接由 `sqlite3.connect(..., isolation_level=None)`
（`catalog/sqlite_io.py:63`）打开：**autocommit 下每条 SELECT 各自是一个隐式事务/快照**。
`Catalog.read_one/read_all` 同样没有读事务。因此「同一个连接」只证明连接对象相同，不证明多条 SELECT
属于同一快照；第二个进程的合法 merge 可以落在两个 id 的解析之间，产出任何单一状态都不存在的混合集合。

更糟的是 `revision_at_start` 原来是在 `search_assets()` 内部、**canonical 解析之后**才读的
（`service.py:243-244`），所以这次 merge 已经落在它的起点之前，末尾 revision 相等，末尾复核也发现不了。

本阶段开始时用临时探针（真实扫描夹具 + 第二个 `Catalog` 连接 + 既有 `PersonService.merge()`，运行后删除）
独立复现，与 Sol 的观察逐字一致：

```text
旧实现：person_ids=[A,B], person_match=all，第一次解析后由第二连接提交 merge(target=B, sources=[A])
  → HTTP 200，filters.person_ids=[1,2]，hits=[]        ← 混合结果（合并前集合 + 合并后成员事实）
  一致快照只可能给出 ([1,2], {1})（合并前）或 ([2], {1,2,3})（合并后）
```

反例使用完全正常的业务写入（`PersonService.merge()`，经 `Catalog.write()` 的 `BEGIN IMMEDIATE` 提交），
没有 `PRAGMA`、没有直写、没有改 schema。

## 3. 最小修复（三处，均在允许面内）

### 3.1 `Catalog.read_snapshot()` — 一个可证明的只读快照

新增 `src/photo_search/catalog/db.py::Catalog.read_snapshot()`：`BEGIN DEFERRED` 把快照固定在第一条读语句
并保持到块结束，块内只读；同时新增两条防护，`write()` 在快照内直接 `RuntimeError`、快照不可嵌套——
因为搜索期唯一的写者是 `search_documents.catch_up()`，它必须在快照之外运行（本修复把它留在块外）。
块内只做解析与 revision 读取，因此不会与自身写入互锁；其他进程在 WAL 下继续正常写入。

### 3.2 解析与 pre-binding 同源于那个快照

`_canonical_person_ids()` 改为
`_person_authority_base(catalog, request) -> (canonical_ids, catalog_filter_revision)`：在**同一个**
`read_snapshot()` 里先读 `catalog_filter_revision`、再用**同一个**唯一解析器解析全部 id 并去重升序。
于是 canonical 集合本身不可能混合两个状态，且它自带的 revision 就是这次新搜索的**前置绑定**。
谓词、`filters` 回显与 continuation digest 继续只使用该集合（未新增第二套口径或 revision）。

### 3.3 发布前检测 + 有界重锚（不发布混合页）

`search_request()` 的**新搜索**分支改为有界循环（`SEARCH_PUBLISH_ATTEMPTS = 3`）：

```text
第 N 次尝试：[前置绑定 revision] → 检索（search_assets 末尾重读 revision）
  两者相等 → 发布这一页（创建会话、渲染首页）
  两者不等 → 丢弃本次尝试（不建会话、不渲染），重新解析 canonical 并重建 scope/digest，再试
  三次都失败 → 409 CATALOG_FILTER_REVISION_STALE，reason=CATALOG_FILTER_REVISION_CHANGED，
                details={reason, session_catalog_filter_revision, current_catalog_filter_revision}
```

「修订已移动」的判据是权威 revision 在**同一事务内至多 +1** 的事实（ADR-0005 §11 触发矩阵），因此
pre-binding 相等即证明这段时间窗口内没有提交过权威人物/地点事实变化。继续页（cursor）路径逻辑不变：
它仍先比 revision、再比 digest，并保持既有四键 details。

`CATALOG_FILTER_REVISION_STALE` 是复用既有码（ADR-0009 §7.3 未授权新码），details 只使用 companion
`PersonSearchErrorDetails` 已冻结的键；新搜索没有会话，因此不带 `search_session_id`。

### 3.4 修复没有做的事（明确非目标）

- 未改 ADR-0009 / companion / 任何冻结件 / migration / `schema_version`(13) / 依赖(95) / `PhotoSearch.spec` /
  前端 / `places/**` / `geo/**` / `ocr/**` / `ingestion/**` / `workers/**` / `face/**`；
- 未改 `persons/**` 语义（Sol 提到的 P3-07B 报告 §6 merge/undo 顺序缺陷仍未修，仍建议随 P3-08 处理）；
- 未新增人物 revision，未把人物事实写入 `search_documents` 或任何派生缓存；
- 未改 `search_assets()` 自身语义：直接调用它的下层调用者仍按 P2-05 的方式得到
  `state=FILTER_REVISION_CHANGED` 的如实报告；本次一致性边界加在**协议面**（会话、回显、摘要由此产生）；
- 未处理 `place_state=suggested` 的 `suggestion_revision` 同类窗口（属地点边界，P2-03A/P2-06A 既有行为，
  不在本卡授权范围；列为未验证/未处理项）。

## 4. 反例转持久回归测试

| 测试 | 位置 | 断言 |
|---|---|---|
| `test_a_read_snapshot_holds_one_state_across_a_concurrent_commit` | `tests/unit/test_catalog_constraints.py` | 快照内两次读值相同；第二连接在块内提交的真实 `bump_filter_revision` 只在块后可见；块内 `catalog.write()` 直接 `RuntimeError` |
| `test_a_merge_during_the_canonical_resolution_is_never_a_mixed_answer` | `tests/integration/test_p3_person_search_filter.py` | 第二 `Catalog` 连接在第一次解析后提交合法 merge：结果必须属于 `([A,B], {1})`（合并前）或 `([B], {1,2,3})`（合并后），**排除** `([A,B], ∅)` |
| `test_a_catalog_that_keeps_moving_the_revision_refuses_instead_of_mixing` | `tests/integration/test_p3_person_search_filter.py` | 每次解析都提交一次真实 rename：409 `CATALOG_FILTER_REVISION_STALE`、details 闭集、`moves == SEARCH_PUBLISH_ATTEMPTS`、**零会话发布** |

### 4.1 这两条回归测试确实有牙（修复前/后对照实测）

在同一探针里用一个替换的 `_person_authority_base`（裸语句解析 + revision 只在检索末尾读，即修复前的
锚点语义）模拟旧实现，并记录每次 `search_assets()` 的返回：

```text
旧语义（模拟）：1 次尝试 → ([1,2], []) @rev3 → 直接发布 → HTTP 200
                 ↑ 混合结果；新回归测试的断言会拒绝它（红）
修复后：        尝试 1 → ([1,2], []) @rev3   ← 与旧实现发布的混合页逐字相同
                前置绑定 rev2 ≠ 检索末尾 rev3 → 丢弃，不建会话
                尝试 2 → ([2], [1,2,3]) @rev3 → 发布（绿）
                解析调用 4 次（1+1 丢弃尝试、1+1 重锚尝试）
```

这同时证明两件事：**快照**保证 canonical 集合内部一致（旧语义下集合会混合），**pre-binding 门**保证被
丢弃的正是那次混合尝试（否则快照本身也无法阻止「合并前集合 + 合并后成员」被发布）。

## 5. 实测命令与结果

| 命令 | 结果 |
|---|---|
| `uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration --basetemp=.pytest-tmp/P3-07C-fix-full --junitxml=test-results/p3-07c-fix-full.xml` | exit 0；JUnit `tests=1337 failures=0 errors=0 skipped=48`（229.073 s）；阶段 A 基线 1334 → 1337（**+3**） |
| 修复专项批 `tests/unit/test_catalog_constraints.py` + `test_p3_person_search_scope.py` + `test_p3_person_search_contracts.py` + `tests/integration/test_p3_person_search_filter.py` + `tests/integration/test_p2_search_endpoint.py` | exit 0；`test-results/p3-07c-fix-focused.xml`，`tests=134 failures=0 errors=0 skipped=0`（25.234 s） |
| `uv run --no-sync ruff check .` | exit 0；`All checks passed!` |
| `git diff --check` | exit 0（clean） |
| `uv run --no-sync uv lock --check` | exit 0；`Resolved 95 packages`（依赖未变） |
| 修复前/后对照探针（临时文件，已删除） | 见 §2 与 §4.1；修复前 `consistent=false`（混合页发布），修复后 `consistent=true` 且混合尝试被丢弃 |

48 项 skip 仍是「缺模型权重 / 未启用 GPU」的显式环境 skip（同一集合）；本次专项批 0 skip。

## 6. 与三条高风险边界的关系（供 Sol 短审）

- **canonical（本次修复面）**：解析、pre-binding、谓词、回显、摘要现在都锚定在一次快照 + 一个 revision 上；
  任一 id 失败仍整请求拒绝（404 `PERSON_NOT_FOUND` / 409 `PERSON_REDIRECT_CYCLE`），去重升序不变；
  旧 id 重搜仍解析到当前 canonical（阶段 A 实测 200 + 回显 canonical）。
- **revision（未改语义）**：仍只有单一 `catalog_filter_revision`；人物五类编辑各 +1、cause `PERSON_CHANGED`；
  continue 路径顺序仍是 revision → suggestion → digest → binding，stale details 仍为既有四键。
  新增的只是**新搜索**在 revision 中途移动时的有界重试与结构化拒绝。
- **缓存失效（未改语义）**：`cache/face-crops` 键仍不含 `person_id`；人物读面与命中谓词仍直读 SQLite +
  共享 `MEMBER_PREDICATE`；未把人物事实放入 `search_documents` 或任何缓存。

## 7. 未验证项

- **多进程真实并发**：反例用第二个 `Catalog` 连接模拟另一进程（同一台机器、同一文件），未做多进程压测；
  未测量高并发下 3 次尝试用尽（`CATALOG_FILTER_REVISION_STALE`）的实际发生率。
- **30 万容量下的重试成本**：重试只重复一次有界检索；未测正式容量下的延迟开销（P4-01/P4-02 范围）。
- **`place_state=suggested` 的 suggestion_revision 同类窗口**：第一轮未处理；第二轮已纳入同一个发布闸门
  （见 §10），并有反例与对照实测。
- **`search_assets()` 直接调用者**：其 `state=FILTER_REVISION_CHANGED` 语义未改，也未新增快照保证；
  它现在仍可能在自身内部跨越一次 revision（P2-05 既有行为），本次未测。
- **C 级打包**：修复只动 `src/photo_search/{catalog,search}` 与测试，未改打包输入集合，未重跑 onedir 冒烟。
- **浏览器/UI**：无 UI 变化，未验证。

## 8. 环境事实

- `.pytest-tmp` 对受限令牌仍不可写（`WinError 5`）；两次有效 pytest 与对照探针都经**已授权的正常 Windows
  权限路由**执行，basetemp 仍是项目内 `.pytest-tmp/P3-07C-fix-*`，**未修改任何 ACL**。
- 探针只写系统 `%TEMP%` 下的临时目录，运行后删除；`git status --short` 确认无残留。
- `UV_CACHE_DIR` 指向项目内 `.uv-cache`；JUnit 写入 `test-results/`。
- 本次改动文件：`src/photo_search/catalog/db.py`、`src/photo_search/search/service.py`、
  `tests/unit/test_catalog_constraints.py`、`tests/integration/test_p3_person_search_filter.py`，外加本报告与
  `docs/development-status.md` 的状态更新（工作树另有用户先前准备的 revision 6 路由对齐文档改动，未回改）。

## 9. 交接（给 Sol 短审）

```text
任务 ID / 状态：P3-07C / DS-H FIX APPLIED — SOL SHORT RE-REVIEW PENDING（不生成 P3-08，不标 PASS）
修复面（仅此 4 个文件）：
  src/photo_search/catalog/db.py            新增 Catalog.read_snapshot() + write 冲突防护
  src/photo_search/search/service.py        _person_authority_base()（快照内解析+pre-binding）、
                                           SEARCH_PUBLISH_ATTEMPTS=3 的有界重锚、结构化拒绝
  tests/unit/test_catalog_constraints.py    快照原语回归（+1 例）
  tests/integration/test_p3_person_search_filter.py  第二连接 merge 反例（+2 例）
短审边界（只审这三条，不重复全量 diff）：
  ① canonical：解析/pre-binding/谓词/回显/摘要同源；ADR-0009 §4.1～§4.3；反例测试见上表
  ② revision：revision-before-digest 顺序、单一 revision、stale 四键 details 未变（新搜索路径新增有界重试）
  ③ 缓存失效：crop_key 不含 person_id、人物读面直读 SQLite、search_documents 无人物列（均未改）
最小反例与命令：见 §2 / §4.1 / §5；关键 JUnit：test-results/p3-07c-fix-full.xml（1337/0/0/48 env skips）、
  test-results/p3-07c-fix-focused.xml（134/0/0/0）
关键 diff 定位：catalog/db.py 的 read_snapshot 与 write 防护；service.py 的 _person_authority_base、
  digest_of/build_scope、SEARCH_PUBLISH_ATTEMPTS 循环与 payload is None 拒绝
未验证项：§7（多进程压测、30 万重试成本、suggested 窗口、search_assets 直接调用者、C 级、UI）
禁止扩大范围：ADR/companion/冻结件、migration/schema_version、依赖、spec、前端、places|geo|ocr|ingestion|
  workers|face、persons/** 语义（含 §6 merge-undo 缺陷）一律不动
若短审发现普通实现缺陷：写最小反例与允许修复面退回 DS-H；修复后只短审原失败边界及直接回归
```

## 10. 第二轮修复：把一致性边界延长到首页发布（Sol 短审 §7.2 的窗口）

Sol 的短审补轮确认第一轮关闭了「两次 id 解析之间提交 merge」的窗口，但指出发布窗口仍开着：revision 比较
原**止于 `search_assets()` 返回时**，而 `_entries_of()` → 精排 → `sessions.create()` → `_render_page()`
（后者会再次读取当前权威谓词复核每一行）都发生在那之后。第二连接在该间隙提交 merge，就会发布
「旧 canonical 回显 + 旧 revision 绑定 + 合并后成员事实过滤后的命中」（实测 HTTP 200、`[1,2]` / `[]`）。

### 10.1 修复

1. **解析锚点带上了独立 suggestion revision**：`_person_authority_base()` 改为返回
   `_AuthorityBase(canonical_person_ids, filter_revision, suggestion_revision)`——三者仍取自**同一个**
   `read_snapshot()`。
2. **新增发布闸门 `_authority_holds()`**：在**页面渲染完成之后**重读冻结计数器——`catalog_filter_revision`
   对每个请求，另加 `place_suggestion_revision` 仅当 `place_state=suggested`（与 `_continue_session`
   的绑定口径一致）。相等即证明这一页的每一部分（canonical 集合、回显、摘要、revision 绑定、以及
   `_render_page` 复核过的行）都来自同一个 catalog 版本。
3. **会话只在可发布时才注册**：`SearchSessionStore.create()` 拆成
   `build_session()`（构造、生成 id、不注册、不影响 LRU）+ `register()`（注册并维持上界），`create()` 保留为
   两者的组合，语义与 LRU/TTL 行为不变。`search_request()` 先 `build_session` → `_render_page` → 发布闸门 →
   通过才 `register` 并返回；不通过则**这次尝试什么也没有留下**（既不泄漏临时会话，也不会把 LRU 里的既有
   会话挤掉），随后重锚重跑（仍是有界三次），用尽后 409 `CATALOG_FILTER_REVISION_STALE`。

第一轮的 `read_snapshot()`、解析/前置绑定同源、三次有界重锚、continuation 的
revision → suggestion → digest → binding 顺序、stale details 四键、缓存键与 SQLite 同源读面全部保留未改。

### 10.2 反例转持久回归（+2 例）

| 测试 | 注入点 | 断言 |
|---|---|---|
| `test_a_merge_after_the_last_retrieval_read_is_never_a_mixed_page` | 第二连接在唯一一次 `_entries_of()` 调用内提交合法 merge（正是 Sol §7.2 的窗口） | 结果必须是一致的前/后态之一（排除 `([A,B], ∅)`）；尝试数 ≥ 2（说明那一页被真的构建后丢弃）；会话数**恰好 +1**（丢弃的尝试没有留下会话） |
| `test_a_suggested_candidate_change_after_the_last_read_is_never_a_mixed_page` | 第二连接在同一注入点把 `place_suggestion_revision` +1 | 200；回显的 `filters.suggestion_revision` 与 `suggestion_revision` **等于变更后的数据库值**（即这一页锚定在它实际复核过的状态） |

### 10.3 对照实测（有闸门 / 无闸门）

用临时探针只把 `_authority_holds` 替换为恒真（即修复前的“发布不再检验”行为），其余代码路径不变：

```text
注入 merge（发布窗口）：闸门关闭 → 200，person_ids=[1,2]，hits=[]        ← 混合页（Sol 的反例）
                        闸门开启 → 200，person_ids=[2]，hits=[1,2,3]      ← 一致（合并后）
注入 suggestion +1：    闸门关闭 → 200，回显 suggestion_revision=0，数据库=1 ← 混合页
                        闸门开启 → 200，回显 suggestion_revision=1，数据库=1 ← 一致
```

两条新回归的断言在「闸门关闭」那一列必然失败，因此测试确实钉住了本次的发布窗口。

### 10.4 实测命令与结果（第二轮）

| 命令 | 结果 |
|---|---|
| `uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration --basetemp=.pytest-tmp/P3-07C-fix2-full --junitxml=test-results/p3-07c-fix2-full.xml` | exit 0；JUnit `tests=1339 failures=0 errors=0 skipped=48`（221.787 s）；第一轮基线 1337 → +2 |
| 第二轮专项批 `test_p3_person_search_filter.py` + `tests/unit/test_p2_search_sessions.py` + `tests/integration/test_p2_search_endpoint.py` | exit 0；`test-results/p3-07c-fix2-focused.xml`，`tests=64 failures=0 errors=0 skipped=0`（31.230 s） |
| `uv run --no-sync ruff check .` / `git diff --check` / `uv run --no-sync uv lock --check` | clean / clean / exit 0（95 包） |
| 第二窗口对照探针（临时文件，已删除） | 见 §10.3 |

### 10.5 第二轮改动文件

- `src/photo_search/search/service.py`：`_AuthorityBase`、`_person_authority_base()` 返回三件套、
  `_authority_holds()`、`publish_once()` 与「渲染后闸门 + 通过才注册会话」的循环；
- `src/photo_search/search/sessions.py`：`build_session()` + `register()`（`create()` 组合两者，行为不变）；
- `tests/integration/test_p3_person_search_filter.py`：两条新回归（+2）。

未改：ADR/companion、migration、`schema_version` 13、依赖 95 包、`PhotoSearch.spec`、前端、`places/**`、
`geo/**`、`ocr/**`、`ingestion/**`、`workers/**`、`face/**`、`persons/**` 语义（P3-07B 报告 §6 的
merge/undo 缺陷仍未修）。

### 10.6 交接（给 Sol 的第二次短审）

```text
任务 ID / 状态：P3-07C / DS-H 第二轮修复已应用 — SOL 短审补轮 PENDING（不生成 P3-08，不标 PASS）
本次修复面（3 个文件）：
  src/photo_search/search/service.py    _AuthorityBase / _authority_holds() / publish_once()
                                        （渲染后闸门；通过才 register；否则丢弃并重锚）
  src/photo_search/search/sessions.py   build_session() + register()（create() = 二者组合，行为不变）
  tests/integration/test_p3_person_search_filter.py  发布窗口两条回归（+2）
只审这两点（不重复全量 diff / 不重跑全量成功套件）：
  ① 发布窗口：retrieval-return → _entries_of → rerank → build_session → _render_page → 最终闸门 → register；
     反例注入点在 _entries_of（Sol §7.2 的窗口），对照实测见 §10.3；断言含「会话数恰好 +1」的无泄漏证据
  ② 第一轮成果未被削弱：read_snapshot、解析/pre-binding 同源、三次有界重锚、continuation 顺序与 stale
     details 四键、suggestion 仅对 place_state=suggested 参与闸门、cache key/SQLite 同源读面
关键 JUnit：test-results/p3-07c-fix2-full.xml（1339/0/0/48 env skips）、
  test-results/p3-07c-fix2-focused.xml（64/0/0/0）；第一轮证据仍有效：p3-07c-fix-full.xml（1337/0/0/48）
未验证项：多进程压测、30 万下重试成本、search_assets() 直接调用者仍只如实报告 state=FILTER_REVISION_CHANGED、
  C 级打包、UI（同 §7）
禁止扩大范围：ADR/companion/冻结件、migration/schema_version、依赖、spec、前端、places|geo|ocr|ingestion|
  workers|face、persons/** 语义（含 §6 merge-undo 缺陷）
```

## 11. 第三轮修复：重试耗尽时按真正移动的计数器分类（Sol 第二次短审 §8.2）

Sol 的第二次短审确认发布闸门本身通过（late merge 与单次 suggestion bump 两条回归复跑通过），但指出**重试
耗尽的唯一出口**固定抛 `CATALOG_FILTER_REVISION_STALE`：`_authority_holds()` 只返回布尔值，丢掉了「到底是
哪个冻结计数器移动」这一信息。当三次尝试都只因 suggestion revision 变化而失败时，返回的 409 会声称目录
filter revision 改变，却在 details 里给出两个**相等**的 filter revision——用自身证据否定自己的断言；而
ADR-0005 §9 的冻结错误表把 suggested 列表 revision 过期归为 `PLACE_SUGGESTION_REVISION_STALE`（continuation
路径早已如此作答）。这不是新增协议，而是新搜索的发布出口没有保留既有错误分类。

### 11.1 修复（只动 `src/photo_search/search/service.py` 与对应测试）

- `_authority_holds()` 改为 `_authority_drift() -> "filter" | "suggestion" | None`：先看
  `catalog_filter_revision`，再看（仅 `place_state=suggested`）`place_suggestion_revision`，与
  `_continue_session` 的比较顺序一致；发布闸门只关心「是否有漂移」。
- 新增 `_publication_refusal()` 作为**重试耗尽**的唯一出口：重新读取两个计数器后按既有优先级分类——
  filter revision 不同（任何请求），或请求不是 `place_state=suggested`，返回
  `CATALOG_FILTER_REVISION_STALE` / `CATALOG_FILTER_REVISION_CHANGED` +
  `session_catalog_filter_revision`/`current_catalog_filter_revision`；否则返回
  `PLACE_SUGGESTION_REVISION_STALE` / `SUGGESTION_REVISION_CHANGED` +
  `session_suggestion_revision`/`current_suggestion_revision`。两种码、reason 与 details 键都与续页路径
  既有形态一致；新搜索尚无会话，因此两者都不带 `search_session_id`。
- 快照、`build_session()`/`register()`、三次有界重锚、发布闸门位置与顺序均未改；未新增错误码。

### 11.2 反例转持久回归（+2 例，参数化）

`test_an_exhausted_retry_names_the_counter_that_moved[mutation=suggestion|both]`：在每次 `_entries_of()` 之后
提交一次真实 `bump_suggestion_revision()`（`both` 时再叠一次真实人物 rename），使每一次有界尝试都撞上变化：

| 参数 | 断言 |
|---|---|
| `suggestion` | 409 `PLACE_SUGGESTION_REVISION_STALE`，`reason=SUGGESTION_REVISION_CHANGED`，details 恰为 `{reason, session_suggestion_revision, current_suggestion_revision}`，`current > session`，**`catalog_filter_revision` 未变**，`moves == SEARCH_PUBLISH_ATTEMPTS`，**零会话** |
| `both` | 409 `CATALOG_FILTER_REVISION_STALE`，details 恰为既有四键之三，filter revision 确实增加——锁定 **filter-first 优先级** |

既有的「每次人物 rename 用尽重试」用例保留，因此两个错误类与优先级各有一条持久证据。

### 11.3 对照实测（修复前出口 / 修复后）

临时探针只把 `_publication_refusal` 换回「固定 filter stale」的旧出口，其余路径不变：

```text
pre-fix：409 CATALOG_FILTER_REVISION_STALE / CATALOG_FILTER_REVISION_CHANGED
         session_catalog_filter_revision=2, current_catalog_filter_revision=2  ← 自相矛盾（相等）
fix：    409 PLACE_SUGGESTION_REVISION_STALE / SUGGESTION_REVISION_CHANGED
         session_suggestion_revision=2, current_suggestion_revision=3          ← 正确分类
两次都恰好 3 次有界尝试；filter revision 全程未变
```

参数化回归的 `suggestion` 分支在修复前必然失败（旧出口答 filter 码），因此测试钉住了本次错误面。

### 11.4 实测命令与结果（第三轮）

| 命令 | 结果 |
|---|---|
| `uv run --no-sync python -m pytest -q -p no:cacheprovider tests/unit tests/integration --basetemp=.pytest-tmp/P3-07C-fix3-full --junitxml=test-results/p3-07c-fix3-full.xml` | exit 0；JUnit `tests=1341 failures=0 errors=0 skipped=48`（220.733 s）；第二轮基线 1339 → +2 |
| 第三轮专项批 `test_p3_person_search_filter.py` + `tests/unit/test_p2_search_sessions.py` + `tests/integration/test_p2_search_endpoint.py` + `tests/integration/test_p2_acceptance_search.py` | exit 0；`test-results/p3-07c-fix3-focused.xml`，`tests=71 failures=0 errors=0 skipped=0`（24.945 s） |
| 仅本轮新增/发布窗口回归（`-k "exhausted_retry or publication"`） | exit 0；`test-results/p3-07c-fix3-named.xml` |
| `uv run --no-sync ruff check .` / `git diff --check` / `uv run --no-sync uv lock --check` | clean / clean / exit 0（95 包） |
| 错误面对照探针（临时文件，已删除） | 见 §11.3 |

### 11.5 交接（给 Sol 的第三次短审）

```text
任务 ID / 状态：P3-07C / DS-H 第三轮修复已应用 — SOL 短审补轮 PENDING（不生成 P3-08，不标 PASS）
本次修复面：src/photo_search/search/service.py（_authority_drift() 取代 _authority_holds()；
  _publication_refusal() 作为重试耗尽唯一出口并按 filter-first 分类）
  + tests/integration/test_p3_person_search_filter.py（参数化 +2 例）
只审这一点（不重复全量 diff / 不重跑全量成功套件）：
  重试耗尽的错误面分类：suggestion-only → PLACE_SUGGESTION_REVISION_STALE / SUGGESTION_REVISION_CHANGED
  + session_suggestion_revision/current_suggestion_revision；filter 变化（或非 suggested 请求）→
  CATALOG_FILTER_REVISION_STALE + 既有 revision details；两者同时变化时 filter 优先；
  新增错误码 0 个，快照/会话注册/发布闸门位置未改
4 条直接回归：@suggestion、@both（本轮新增）＋ late merge、单次 suggestion bump（第二轮）＋
  既有「每次 rename」用例（catalog 类与 filter-first 的对照）
关键 JUnit：test-results/p3-07c-fix3-full.xml（1341/0/0/48 env skips）、
  test-results/p3-07c-fix3-focused.xml（71/0/0/0）、test-results/p3-07c-fix3-named.xml
未验证项：多进程压测、30 万下重试成本、search_assets() 直接调用者、C 级打包、UI（同 §7）
禁止扩大范围：ADR/companion/冻结件、migration/schema_version、依赖、spec、前端、places|geo|ocr|ingestion|
  workers|face、persons/** 语义（含 §6 merge-undo 缺陷）
```
