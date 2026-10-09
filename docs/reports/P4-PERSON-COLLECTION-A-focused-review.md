# P4-PERSON-COLLECTION-A 聚焦审核

日期：2026-10-09。当前结论：**FOCUSED REVIEW PASS / F1–F6 CLOSED**。修复后独立定向13/0/0/0；跨升级旧实例请求隔离、迁移数据/token持久性与直接API/DOM复核通过。限当前会话工程/合成范围，不宣称DS-H或指定Sol模型终审，不标真实浏览器/任务总PASS。

## 基线与范围

- 实际 HEAD：`9b06b286113d2566b3f024f2b03deb2ffc951f92`。以 HEAD 至当前工作树差异及合集新增文件为审核对象。
- 已读取 AGENTS、通用接手说明、开发状态、实现卡、原审查交接卡和实施报告；对照 V3.1 §2.4/§11、V3.0 §8.3.3–8.3.5/§11.2 的身份、撤销及迁移约束。
- 覆盖迁移 `0024_person_collections.sql` / schema 版本、`persons/collections.py`、API models/routes/queries、人物页 JS/HTML/CSS、迁移测试、合集 API/DOM 测试；抽查 Catalog 写事务、canonical resolver、MEMBER_PREDICATE、人物摘要、正式合并、会话/CSRF 保护及资产分页调用链。
- 产品代码未修改。保留既有工作树和临时材料；本轮只新增隔离反例及审核记录，更新本任务交接状态。没有访问真实图库或现有服务，没有提交/推送。

## 待修问题

### F1 / P1：合集列表首屏必然返回 500

位置：`src/photo_search/persons/collections.py:96–103`。

无 cursor 时 `where` 为空，SQL 仅有 LIMIT 的一个占位符，`params` 却始终传入 `(0, limit+1)` 两个参数。实际认证请求 `GET /api/v1/persons/collections?limit=40` 返回 500；日志为 `sqlite3.ProgrammingError: Incorrect number of bindings supplied. The current statement uses 1, and there are 2 supplied.` 空库也可复现，和数据规模无关。

页面 boot、人物合集入口、新建后的列表刷新及加入合集选择器均调用此首屏路径，现有 API 四项测试没有调用列表 GET，DOM 测试则 mock 了列表成功。必须让首屏/续页 SQL 与参数一致，并补空集合、非空集合及下一页的真实 API 回归。

### F2 / P1：删除后 collection_id 复用，让旧请求操作另一个合集

位置：`src/photo_search/catalog/migrations/0024_person_collections.sql:3`，`collections.py:124–129/163–167/231`。

`INTEGER PRIMARY KEY` 没有防复用的分配策略。删除当前最大 ID（尤其唯一合集）后，下次创建会获得同一个 ID；revision 又从 1 开始。隔离 API 反例：创建 first（ID=1）→删除→创建 replacement（仍 ID=1）→旧页面再次 DELETE ID=1，返回 200，随后 replacement GET 返回 404。用户在另一标签页保留旧卡片，或重试旧删除请求时，会误删新合集及其成员关系；照片和人物没有被删。

另一反例：旧合集加入一批后 revision=2，保存人物分页 cursor；删除、创建新合集并加入另一批，仍是 ID=1/revision=2/catalog_revision=0。旧 cursor 被接受，返回新合集成员，违反删除后游标失效与跨合集隔离要求。应使合集身份不可复用，或提供不可复用的实例身份并贯穿游标和写入口；只在游标增加绑定仍不能解决旧写请求误操作。

### F3 / P2：合并后同一 face_id 在合集照片中被重复展示

位置：`src/photo_search/persons/collections.py:255–265`。

`canonical_members` 保留 `(collection_id, origin_id, person_id)` 的不同稳定锚点，这是撤销所需的事实；但 `_collection_photo_faces` 直接按 canonical person_id JOIN 这些行，没有先按当前 person_id 去重。A、B 都是合集成员，正式合并 A→B 后，同一张有效脸会匹配两个成员锚点。

使用 `PersonService.merge` 的反例只有一个实际 face_id，照片页中的 `person_faces` 却返回同一 ID 两次。更多合并源会占满前 8 项，造成其他有效脸被挤出，并错误设置 `person_faces_has_more`。资产照片去重本身仍正确。应在脸投影前对 canonical 成员去重/使用 EXISTS，或在窗口和截断之前按 face_id 去重；保留原始成员锚点以供撤销。

### F4 / P2：移出操作使用 CTE DELETE 的 rowcount，重复提交不能正确 no-op

位置：`src/photo_search/persons/collections.py:206–218`。

Python sqlite3 对以 WITH 开始的此条 DELETE 返回 `rowcount=-1`。代码把它直接作为 changed 返回，且 `if changed` 将 -1 视为真。真实删除和零删除都报告 -1；加入→移出→再次移出已不在合集的同一人，实际成员零变更，却返回 `changed=-1` 并把 revision 从 3 增至 4。

这使响应计数失真，重复请求也会无故废弃其他页面的有效游标。应在同一事务读取真实删除数，只有实际成员关系发生变化时递增 revision；补真实删除、零删除和重复提交的断言。

## 证据与命令

所有 fixture、数据库、图片均在项目 `.pytest-tmp` 下合成；使用既有 Python 3.12.10 环境，`UV_CACHE_DIR=.uv-cache`。反例文件：`.tmp/p4-person-collection-review/test_collection_review.py`。

```powershell
$env:UV_CACHE_DIR='.uv-cache'
$env:PYTHONPATH='src;tests'
.venv/Scripts/python.exe -m pytest -q .tmp/p4-person-collection-review/test_collection_review.py --basetemp=.pytest-tmp/p4-person-collection-review-final --junitxml=test-results/p4-person-collection-review-counterexamples.junit.xml --tb=short
.venv/Scripts/python.exe -m pytest -q tests/integration/test_p4_person_collections.py tests/unit/test_p4_person_collection_ui.py --basetemp=.pytest-tmp/p4-person-collection-review-existing --junitxml=test-results/p4-person-collection-review-existing.junit.xml
git diff --check
```

- 最终反例：**5 tests / 0 passed / 5 failures / 0 errors / 0 skipped**，退出码 1。F2 有游标与旧 DELETE 两条独立反例，其余问题各一条；这些是产品反例失败，不是环境故障。
- 当前既有合集 API+DOM：**5 tests / 5 passed / 0 failures/errors/skips**，退出码 0。证明原定向绿灯没有覆盖上述失败入口，不能据此把实现标 PASS。
- 已核对实施 JUnit `test-results/p4-person-collection-a.junit.xml` 为 31/0/0/0；没有机械重跑其余未受影响测试。
- `git diff --check` 通过；本轮没有修改产品 Python/JS，没有新增依赖或运行模型。

## 已核对边界与限制

批量 add 在 SQLite 写事务中先解析全部 ID，再插入成员/隐藏标记；普通搜索 100-ID 上限未解除。照片筛选复用有效成员谓词，资产去重在分页前，空合集不会返回全库；原始人物锚点仍保存。以上没有发现本轮新的阻断问题，但不抵消 F1–F4。

原 160 人测试覆盖人物分页；照片索引计划只覆盖两人合照夹具。既有“合并/撤销”合集测试直接写 `merged_into`，没有执行正式合并/撤销并验证照片与脸投影。本轮补了正式合并脸投影反例，正式撤销及任意深度链的完整合集回归仍应在修复轮补齐。

前端异步隔离和返回状态仅静态抽查及既有 DOM 验证；没有真实浏览器试用，不作动态完整性保证。既有报告明确保留跨进程多语句读取无完整快照的限制，本轮未扩展为并发性能或身份协议改造。未运行全仓、30 万、真实图库/服务、GPU、模型或打包。

没有证据命中 V3.1 §2.2 的 DS-MAX 升级条件。用户页面试用继续 PENDING，当前不能交付总 PASS。

## 修复轮

- F1：无游标首屏不再绑定多余参数；新增真实 API 覆盖空列表、非空列表和游标续页。
- F2：合集主键改为 `INTEGER PRIMARY KEY AUTOINCREMENT`，删除后的 ID 不复用；新增 API/分页回归，验证旧游标返回 409、旧 DELETE 返回 404 且新合集仍存在。
- F3：脸投影先对 canonical 成员和有效 `(asset_id, face_id)` 去重，再排序截取前 9 张用于显示前 8 张及 `has_more`；新增正式 `PersonService.merge` 场景回归。
- F4：移出使用 `DELETE ... RETURNING` 统计实际删除行数；重复移出返回 0 且 revision 不变。
- 修复轮命令：

```powershell
$env:UV_CACHE_DIR='.uv-cache'
$env:PYTHONPATH='src;tests'
uv run --python 3.12.10 -- python -m pytest -q .tmp/p4-person-collection-review/test_collection_review.py tests/integration/test_p4_person_collections.py tests/unit/test_p4_person_collection_ui.py tests/unit/test_catalog_migrations.py --basetemp=.pytest-tmp/p4-person-collection-fix --junitxml=test-results/p4-person-collection-fix.junit.xml --tb=short
uv run --python 3.12.10 -- ruff check src/photo_search/persons/collections.py tests/integration/test_p4_person_collections.py tests/unit/test_catalog_migrations.py
node --check src/photo_search/web/persons-albums.js
git diff --check
```

- 结果：**26 passed / 0 failed / 0 errors / 0 skipped**；Ruff、Node 语法和 diff-check 通过。JUnit：`test-results/p4-person-collection-fix.junit.xml`。
- 第一次修复轮发现脸投影 CTE 拼接少逗号，未通过；已修正并完整重跑 pytest，最终退出码 0。

## 修复后聚焦复核（2026-10-09）

实际 HEAD 仍为 `9b06b286113d2566b3f024f2b03deb2ffc951f92`。读取修复记录和 Sol 聚焦卡，只复核 F1–F4 的修复与直接新增边界；本轮产品代码未修改，未触碰真实图库或用户服务。

- F1：首屏参数与续页参数分别正确，空列表/非空列表/下一页真实 API 回归通过，CLOSED。
- F2：新迁移数据库使用 AUTOINCREMENT 后不复用已删除 ID，旧游标/旧 DELETE 反例通过。此结论仅适用于按修复后迁移创建的表；已有 v24 的兼容缺口见 F5。
- F3：canonical 去重和 face_id 去重发生在窗口排序/截断之前。正式合并、正式撤销、恰好 8 张及 9 张有效脸的展示/has_more 边界均通过，CLOSED。
- F4：RETURNING 统计真实删除数；重复移出零写入、不增加 revision；正式 A→B→C 两级链可一次移除三个原始锚点且返回 changed=3，CLOSED。实际运行时 Python 3.12.10 / SQLite 3.49.1 支持 RETURNING；未验证其他发行运行时。

### F5 / P1：重写 v24 迁移导致已有 v24 数据库无法启动

位置：`src/photo_search/catalog/migrations/0024_person_collections.sql:3`、`src/photo_search/catalog/schema.py:71/128–150`。

修复直接把原 `0024` 的 `INTEGER PRIMARY KEY` 改成 `INTEGER PRIMARY KEY AUTOINCREMENT`，schema 版本仍是 24。`inspect_database` 在升级前会将现有数据库对象 SQL 与当前迁移文件逐项严格比较。修复前程序已经创建的 v24 表没有 AUTOINCREMENT，重新用修复后程序 `Catalog.open` 即在预检查抛出 `SCHEMA_INCONSISTENT: catalog schema does not match schema_version 24`，无法进入迁移或提供服务。

隔离反例没有修改迁移文件，也没有访问真实数据：在合成目录完整创建 catalog，用初轮 v24 的原表定义重建两张合集表并保留 schema_version=24，写入一名人物、一个合集及成员，再关闭并用当前版本重新打开，稳定复现上述错误。现有 26 项修复验证都是按新 DDL 初始化或从更早 schema 升级，没有覆盖修复前 v24→修复后程序。

这是 F2 修复引入的持久化兼容缺陷，违反实现卡“仅增量迁移，不改旧 migration”与 V3.0 §11.2 的数据保护要求。应恢复初轮 v24 迁移定义，通过连续新迁移或仓库认可的版本化兼容方式转换已有表，保留 collection_id、revision、名称和成员关系；迁移后再验证删除 ID 不复用及旧请求拒绝。不应要求用户删 catalog 或重建真实图库。没有证据说明所有已生成的 v24 数据库都可丢弃，不能把新库绿灯视为既有库升级成功。

### 本轮命令与结果

```powershell
$env:UV_CACHE_DIR='.uv-cache'
$env:PYTHONPATH='src;tests'
.venv/Scripts/python.exe -m pytest -q .tmp/p4-person-collection-review/test_collection_review.py tests/integration/test_p4_person_collections.py --basetemp=.pytest-tmp/p4-person-collection-rereview --junitxml=test-results/p4-person-collection-rereview.junit.xml --tb=short
.venv/Scripts/python.exe -m pytest -q .tmp/p4-person-collection-review/test_collection_rereview.py --basetemp=.pytest-tmp/p4-person-collection-rereview-extra --junitxml=test-results/p4-person-collection-rereview-extra.junit.xml --tb=short
git diff --check
```

- 原反例与修复后合集集成：**13 passed / 0 failures/errors/skips**，退出码 0。
- 新增兼容/合并撤销/8–9脸/多级链验证：**3 passed / 1 failure / 0 errors/skips**，退出码 1。唯一失败为已有 v24 无法重开；不是环境错误。
- 合计：**16 passed / 1 failure**；JUnit 已保存，不能标聚焦审核 PASS。
- 核对修复轮 JUnit 的 26/0/0/0 记录；不机械重跑未变化 DOM、迁移全套、全仓或真实性能。`git diff --check` 通过。

没有证据命中 DS-MAX 升级条件；指定模型终审和用户页面试用仍未宣称完成。

## F5 修复与复核

- 恢复 `0024_person_collections.sql` 的原始 `INTEGER PRIMARY KEY` 定义，未改写已存在的迁移历史；schema 版本递增到 v25。
- 新增 `0025_person_collection_autoincrement.sql`：在同一迁移事务中复制合集行及成员行到带 `AUTOINCREMENT` 的临时父表/成员表，保留 `collection_id`、名称、`revision`、时间戳和成员关系，再替换旧表并重建反向索引。失败会由迁移事务回滚；旧数据不会被要求删除或重建。
- 新增 schema 24 → 25 升级测试：用旧 v24 DDL 创建带成员的合集，确认 `migrate_database` 只应用 0025，备份仍符合 v24 schema 且保留成员，升级后合集字段和成员保留；删除该合集后新 ID 大于旧 ID，`PRAGMA foreign_key_check` 为空。
- 修正隔离反例构造：将合成数据库 metadata 标记为 v24 后再关闭重开，避免把新 schema v25 错当作旧 v24。它仍只使用项目内隔离目录。
- 最终修复轮：**31 passed / 0 failed / 0 errors / 0 skipped**。JUnit：`test-results/p4-person-collection-f5-fix.junit.xml`。覆盖 F1–F4 原反例、正式合并/撤销与脸数边界、两级 redirect 移除、旧 v24→v25 数据保留及 ID 不复用、合集集成/DOM 和迁移测试。
- F5 关闭；F1–F4 维持关闭。静态检查结果及完整命令记于实现报告。本结果尚未经指定 Sol high 模型终审。

## F5 修复后的独立短审（2026-10-09）

用户要求继续审核。实际 HEAD 仍为 `9b06b286113d2566b3f024f2b03deb2ffc951f92`；本轮仅核对恢复后的 0024、新增 0025、schema 25、v24 升级测试和 F2/F5 直接影响的请求/游标身份。没有改产品代码，没有重新执行完整主审。

F5 已关闭：原始 v24 定义恢复后，旧 v24 可通过严格预检查并仅应用 0025；父表/成员表按同一事务重建，保留现存 ID、名称、revision、时间戳和成员，升级备份仍符合原 v24。两项独立定向回归通过，覆盖实际重开、数据/备份/FK 和升级后删除的 ID 不复用。原 F1/F3/F4 的已通过边界未被本轮产品修复修改，不重开。

### F6 / P1：升级前已删除的旧 ID 在 v25 再次分配

位置：`src/photo_search/catalog/migrations/0025_person_collection_autoincrement.sql:11–15`，直接影响 `collections.py` 的人员/照片游标与按 ID 写入口。

0025 只复制仍存在的 collection_id，AUTOINCREMENT 的 sqlite_sequence 因此仅取得现存最大 ID。v24 不记录已删除 ID 的历史水位。若旧库先创建 ID=1 和 ID=2、删除 ID=2，再升级，复制后序列只到 1；新合集仍会分配为 ID=2。现有升级测试保留 ID=41，升级完成后才删除 41，不能证明迁移前删除过的 ID 不再复用。

新增隔离反例使用正式合集函数及真实 `Catalog.open` 升级：

1. 旧 v24 创建 survivor=1、deleted=2；为 deleted 加入两人，保存 revision=2 的人物游标，然后删除 deleted。
2. 关闭并升级至 v25，创建 replacement，实际 ID 再次为 2；加入另两人后 revision 仍为 2、catalog revision 仍为 0。
3. 旧人物游标被 replacement 接受并返回它的成员；再用旧 ID=2 执行删除，replacement 消失。

最终断言证据：`old_deleted_id=2, replacement=2, stale_cursor_accepted=True, replacement_survived=False`。这保留了 F2 原始的误删风险，是本轮升级修复直接遗漏的边界；不是对原任务扩大性能或全仓范围。需要在升级边界保证旧实例的写请求和游标不能指向新实例。仅递增 catalog revision/修改游标前缀只能保护续页，不能防止旧 DELETE；也不能把现存 MAX(id) 当成历史分配最大值。选择身份保护或可信分配策略后，补升级前删除最大 ID、旧库已空及旧请求拒绝的回归。照片/人物未被此反例删除；新合集与成员关系被误删。

### 本轮验证

```powershell
$env:UV_CACHE_DIR='.uv-cache'
$env:PYTHONPATH='src;tests'
.venv/Scripts/python.exe -m pytest -q .tmp/p4-person-collection-review/test_collection_rereview.py::test_existing_pre_fix_v24_catalog_reopens_and_preserves_collection .tmp/p4-person-collection-review/test_collection_rereview.py::test_upgrade_does_not_reuse_id_deleted_before_v25 tests/unit/test_catalog_migrations.py::test_schema_24_collections_upgrade_preserves_rows_and_never_reuses_ids --basetemp=.pytest-tmp/p4-person-collection-f5-review-final --junitxml=test-results/p4-person-collection-f5-review.junit.xml --tb=short
git diff --check
```

结果：**2 passed / 1 failure / 0 errors / 0 skipped**，pytest 退出码 1；唯一失败为 F6，JUnit 已保存。首次命令误选测试名称，在收集阶段停止，没有声称通过；更正后以上三项均实际运行。已核对修复轮 `p4-person-collection-f5-fix.junit.xml` 为 31/0/0/0，其证据不覆盖 F6。diff-check 通过。

结论：**CHANGES REQUIRED / F6 OPEN**。F5 启动和现存数据保留关闭，F1/F3/F4 保持关闭；F2 的跨升级旧身份保护由 F6 继续追踪。下一步仅修此身份边界，再短审失败反例与直接影响面。没有证据命中 DS-MAX 条件；没有完成指定模型终审、真实浏览器或真实图库验收；未提交/推送。

## F6 修复和直接影响面复核（2026-10-09）

v24 的 INTEGER PRIMARY KEY 没有保留已删除 ID 的历史高水位，迁移只能依据存活行初始化 AUTOINCREMENT 序列；因此不能依赖 v25 序列在迁移边界拒绝所有旧 ID。保留 v24/v25 迁移语义，新增 schema v26 `0026_person_collection_instance_tokens.sql`，为当时存活的每个合集生成 128 位随机小写 hex `instance_token`，添加唯一索引、插入格式约束与不可变触发器。合集创建通过安全随机数生成新 token。

所有实例操作路径现在同时携带 `collection_id` 和 `instance_token`：读取详情、改名、删除、编辑成员、人物分页、照片分页都在读取/写入时校验 token；旧 ID-only URL 没有匹配的实例操作路由。人物和照片两种 opaque cursor binding 均加入 token。因此即使升级后重用了 v24 历史 ID，旧的无 token 删除路径无法删除新实例，迁移前格式的旧游标会以 409 拒绝。合集列表/创建/分页响应含 token；前端从服务端列表保存对应实例记录，并统一用实例路径发出请求。

新增持久迁移回归：隔离 v24 fixture 插入并删除最大 ID=42，同时保留 ID=41；真实迁移应用 0025/0026，删除 41 后新合集确实重用 42。将新合集 revision/filter revision 匹配为旧格式后，迁移前的人物游标和照片游标均因缺实例 token 返回 409；以空错误 token 删除返回 404，替换合集仍存在。API 集成还断言旧 ID-only people/delete 路径返回 404，现行 token 路径能正常访问。

受影响面复核已检查迁移到期版本连续性、token 唯一/不可变、所有六类实例操作守卫、两类游标绑定、服务端投影携带 token、前端列表缓存和所有调用点，以及 v24→v26 保留现存合集/成员、备份和 FK。**此为原实现会话的修复后受影响面核对，不是独立 DS-H 或 Sol 结论。**

### 修复后验证

`tests/unit/test_catalog_migrations.py`、`tests/integration/test_p4_person_collections.py`、`tests/unit/test_p4_person_collection_ui.py`：**22 passed / 0 failures / 0 errors / 0 skipped**；JUnit：`test-results/p4-person-collection-f6-final-check.junit.xml`。相关 Ruff、`node --check src/photo_search/web/persons-albums.js`、`git diff --check` 均通过。未运行全仓、真实图库、服务、模型或真实浏览器；未提交/推送。

当前结论：**FOCUSED REVIEW PASS / F1–F6 CLOSED**，用户已确认本轮受影响面审查通过。结论限当前工作树的工程/合成范围，不宣称指定 Sol 模型终审或真实浏览器/任务总 PASS；真实页面试用仍待用户确认。

## F6 修复后独立聚焦复核（2026-10-09）

结论：**FOCUSED REVIEW PASS / F1–F6 CLOSED**，限当前工作树的工程/合成边界。用户本次要求在当前会话继续审核；没有自动切换模型，不宣称为指定 GPT-5.6 Sol/high 模型终审或 DS-H 主审。真实浏览器试用及任务总验收未追认 PASS。

实际 HEAD 仍为 `9b06b286113d2566b3f024f2b03deb2ffc951f92`。按 F6 修复面核对 0026/schema26、token 生成及插入/不可变/唯一约束、全部六类实例路由与服务函数、人物/照片游标 binding、响应投影及前端 `collectionPath` 调用。0024/0025 没有改写，现有 token 不因改名、成员编辑或重新打开 catalog 改变；所有写入口在同一写事务先匹配实例再修改。前端从服务端列表/创建/分页保留 token，用于详情、改名、删除、加入/移出、人物页及照片页。

补充独立夹具 `.tmp/p4-person-collection-review/test_collection_f6_review.py`，覆盖：

- 两种修复前 v24 状态：保留一个合集并删最大 ID、旧库所有合集已删。真实 `Catalog.open` 升级后确实复用旧整数，但旧格式人物/照片游标均返回 409；错误 token 删除返回 404，新实例及成员仍存在。
- 存活的合集字段/成员保留；token 生成后修改触发 IntegrityError，事务回滚；foreign_key_check 为空；关闭重开后 token 稳定。
- 六类实例 API（详情、改名、删除、成员编辑、人物分页、照片分页）的错误 token 均 404；旧 ID-only 请求均 404/405、零成员/隐藏/名称写入。正确 token 下上述入口正常工作，分页投影继续返回同一 token。

原 F1/F3/F4 数据/查询不变量未被身份修复改变；复用前序正式合并/撤销和 8/9 脸边界证据，本轮也跑直接受影响的合集 API/DOM。未发现新增阻断问题；F6 及其追踪的 F2 跨升级旧请求隔离关闭。

### 命令与证据

```powershell
$env:UV_CACHE_DIR='.uv-cache'
$env:PYTHONPATH='src;tests'
.venv/Scripts/python.exe -m pytest -q .tmp/p4-person-collection-review/test_collection_f6_review.py tests/unit/test_catalog_migrations.py::test_schema_24_upgrade_protects_stale_instance_when_deleted_id_is_reused tests/integration/test_p4_person_collections.py tests/unit/test_p4_person_collection_ui.py --basetemp=.pytest-tmp/p4-person-collection-f6-review --junitxml=test-results/p4-person-collection-f6-review.junit.xml --tb=short
git diff --check
```

独立定向结果：**13 passed / 0 failures / 0 errors / 0 skipped**，退出码 0；JUnit `test-results/p4-person-collection-f6-review.junit.xml`。另核对修复轮 `p4-person-collection-f6-final-check.junit.xml` 的 22/0/0/0；不重复完整主审或未受影响的历史测试。diff-check 通过。

没有证据命中 DS-MAX 条件。没有改产品代码、扫描真实图库、操作用户服务、运行模型/GPU/全仓/打包或做真实浏览器验收，未提交/推送。本轮聚焦审核闭环，下一步保留任务卡的用户页面试用，不自动启动其他任务。
