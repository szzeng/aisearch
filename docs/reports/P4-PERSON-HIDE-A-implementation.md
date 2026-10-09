# P4-PERSON-HIDE-A 实现与定向验证

日期：2026-10-09。实际基线：`02179b90eecbdf1e4b2ffb4541a8f4c8077416bd`（任务卡原计划基线 `d94f3c9`）。结论：**IMPLEMENTED / 本卡定向验证通过 / 同会话个人范围影响面复核通过 / 用户前端试用待完成**。本报告只覆盖个人相册合成数据范围，不表示全仓或真实图库验收。

## 实现

- schema 22 通过新增 `0022_person_hidden.sql` 建立独立 `person_hidden` 表；无记录表示可见，有记录表示忽略，外键使用 `ON DELETE RESTRICT`。既有 v21 人物在迁移后默认可见。
- `GET /api/v1/persons/browse` 增加 `visibility=all|visible|hidden`。默认 `all` 保留旧调用者与 `people1` / `people-count1` 游标；新模式游标绑定 visibility、kind、规范化查询与过滤 revision。隐藏条件进入候选 SQL，在照片数排序和分页前执行。
- 新增受会话、Origin、CSRF 中间件保护的 `POST /api/v1/persons/visibility`。请求限 1–500 个严格正整数且不重复；一个写事务先解析所有 canonical ID，再原子插入或删除标记。重复操作幂等，未知 ID 整批回滚，旧 ID 操作会解析到当前 canonical 人物。
- `persons-albums.js` 增加批量选择、跨页保留、计数、取消、确认、忽略视图和恢复。日常列表显式请求 `visible`；被 `loadChoices()` 和其他旧调用使用的缺省 `all` 不过滤。切模式、完成写操作和离开页面时清理选择。没有修改人脸归属、收件箱、照片/向量索引或自动匹配代码。
- 合并不写隐藏表：活动目标人物标记决定当前可见性；来源记录保留，撤销后其原隐藏状态可恢复。

## 验证

- 定向 JUnit：`tests/integration/test_p4_person_albums.py` 与 `tests/unit/test_catalog_migrations.py`，**25 passed / 0 failed / 0 errors / 0 skipped**。产物：`test-results/p4-person-hide-a.junit.xml`。
- 合成覆盖：v21 有既有人物时升级到 v22 并默认可见；50 人中隐藏 5 人，跨 40 人边界分页无空洞，visible/hidden 游标不能混用，缺省旧列表仍包含隐藏人物；关闭并重开 SQLite 后标记仍在；重复隐藏/恢复幂等；混入未知 ID 后无部分写入；会话与 CSRF 拒绝；照片数排序在过滤后分页；隐藏来源并入可见目标、可见来源并入隐藏目标，以及合并撤销恢复来源标记。
- 修改 Python 范围 Ruff、`node --check src/photo_search/web/persons-albums.js`、`git diff --check`：通过。
- 按用户最新范围确认，仅把任务卡列出的定向验收作为验证门槛。此前额外尝试的全量收集未进入测试执行：设置 `PYTHONPATH=.` 后仍被既有 `tests/integration/test_p4_02y_l_identity.py:824` 的语法错误阻断；不属于本卡门槛，也未扩大修复范围。

## 未验证与复核交接

- 用户需在个人前端手动完成「批量选择 → 忽略 → 查看已忽略 → 恢复 → 重启应用」流程；本轮没有启动真实应用或访问 `PhotoSearchData`。SQLite 重开验证了标记持久化，但不等同于应用重启和浏览器验收。
- 未运行全仓/规模测试、浏览器矩阵、GPU/模型、打包或真实照片库；没有做新的吞吐基准。
- 按用户指示，本会话完成了简短个人范围影响面复核，检查项见下节；结论不是独立 DS-H 主审。没有发现阻断问题，无需派生复核任务或 Sol 终审。

## 同会话个人范围影响面复核（2026-10-09）

- 持久化面：迁移只新增 `person_hidden`；FK restrict 且 schema22；批量写入在 `Catalog.write()` 中先解析全部 canonical ID，再统一写入，未知 ID 会在任何修改前失败。重复插入/删除是幂等操作。
- 查询面：`visible` / `hidden` 条件位于 active canonical `p` 的候选 SQL 中，因此照片数聚合、排序和 LIMIT 都在过滤后；新游标绑定 visibility/kind/q/revision，缺省 `all` 的旧游标 binding 未改变。
- 身份与匹配面：合并实现未修改隐藏表；active target marker 决定结果，source marker 保留供撤销还原；测试覆盖旧 source ID 经 canonical 解析隐藏/恢复当前 target。详情、照片搜索、face inbox/candidate 与 `loadChoices()` 均未增加隐藏过滤；后者仍用默认 `all`。
- UI 面：批量模式仅更改人物卡片点击行为；跨页 Set 保留选择，筛选/排序重载时不清理，切人物模式、切隐藏视图、取消、成功操作和 pagehide 会清理。500 上限、重复提交保护、确认、失败提示及写后重置游标均有对应路径。
- 复核结论：改动限于任务卡边界，当前定向 JUnit 与静态检查覆盖上述关键语义，**PERSONAL-SCOPED IMPACT REVIEW PASS**。用户实际浏览器试用与重启后观察仍待完成；未将本次复核称作独立 DS-H。
