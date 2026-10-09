# P4-PERSON-COLLECTION-A 实施报告

日期：2026-10-09。当前状态：**FOCUSED REVIEW PASS / F1–F6 CLOSED / 用户试用待确认**，见[聚焦审核与修复记录](P4-PERSON-COLLECTION-A-focused-review.md)。本次为用户要求的当前会话独立聚焦复核，不宣称指定Sol模型结论，不标总PASS。

## 基线与范围

- 开始时实际 HEAD：`31d3690b3d4fbf9a558335a1bb947a00c52df49e`；任务卡记录的 `ad86d389...` 是设计核对基线，不是本次代码基线。实施期间前置任务提交 `9b06b286` 到达 `master`；已保留该提交，当前未提交差异仅为合集工作。
- schema 从 v23 连续增加到 v26：0024 新增合集表和反向成员索引，0025 将合集表安全迁移到 AUTOINCREMENT 身份分配，0026 为合集实例增加持久不可变 token；保留已发布的迁移定义和既有合集数据。
- 新增受会话/CSRF 保护的合集列表、创建、改名、删除、成员编辑、成员分页和照片分页 API。名称使用 NFC/空白折叠并以 casefold key 拒绝重复。
- 成员行保留原始稳定 `person_id`。查询用递归 CTE 解析任意长度 redirect 并去重；移除当前 canonical 时一起移除该集合中解析到它的锚点。加入并忽略共用一个 SQLite 写事务。
- 合集照片由固定参数化、集合级 SQL 子查询一次限定有效 CONFIRMED 成员，再走 `capture_desc` keyset 分页；`DISTINCT asset_id` 在分页前去重。普通 `person_ids` 上限未变。
- 人物页新增合集入口、创建/改名/删除、跨页批量加入/可选忽略、平铺照片/成员切换、单人相册导航及批量移出；合集预览隐藏与合集无关的人脸整理动作。

## 验证

- 定向 pytest：合集集成、合集 DOM、catalog 迁移、通用 asset query、既有人物跨页选择：**31 passed / 0 failed / 0 errors / 0 skipped**。
- JUnit：`test-results/p4-person-collection-a.junit.xml`。
- Ruff、`node --check src/photo_search/web/persons-albums.js`、`git diff --check`：通过。
- 合成覆盖：100 个单照片未命名人物、20 个预先隐藏、原子加入并隐藏、无效 ID 零部分写入、重开 catalog 后仍在；人物/人脸/归属/向量/照片行数不变。
- 合成覆盖：A/B 共同合照与单照，`limit=1` 按拍摄时间翻页得到三个唯一资产；待确认建议脸不进入；空合集为空；后来新增的 CONFIRMED 脸在下一次查询出现；隐藏/恢复不改变集合照片。
- 合成覆盖：合并后递归 canonical 去重、撤销后成员自然恢复、移除 canonical 清除全部解析锚点、跨合集/改名后游标拒绝、删除合集不删人物。
- 160 人集合可分页；冻结的普通 100-ID SearchScope 仍拒绝第 101 人。`EXPLAIN QUERY PLAN` 覆盖集合照片谓词并命中合集成员表/索引；定向测试验证了反向成员索引。
- 迁移夹具覆盖从 v21 连续升级到当前 v25；新增 v24→v25 旧 schema、合集/成员保留、迁移备份和删除后 ID 不复用验证。

## 未验证与下一步

- 未做真实浏览器手测、真实 `PhotoSearchData`、GPU/模型、全仓回归、30 万规模、打包或发布验收。用户可按任务卡简测“未命名多页勾选 → 新建路人合集并忽略 → 合集平铺照片 → 切人物视图 → 已忽略入口恢复”。
- 同一请求内的集合 revision 与 catalog filter revision 会绑定游标；照片/人员读取没有跨进程多语句快照保证。并发识别或人工归属若恰好落在多条只读 SQL 之间，当前响应可能观察到短暂混合状态；下一次续页会按 revision 拒绝，需刷新。未声称提供完整并发快照。
- 初轮聚焦审查发现合集首屏参数绑定、删除后 ID 复用、合并后脸投影重复和移出 rowcount/revision 四项，详见[聚焦审核报告](P4-PERSON-COLLECTION-A-focused-review.md)。均已修复并通过审核反例及任务卡相关定向回归。当前会话审查不宣称独立 DS-H，也不替代策略要求的独立模型终审；不标总 PASS。
- 未提交或推送。保留工作区已有 `.tmp/` 与 `docs/reports/tmp-*` 文件。

## 受影响面审查与修复轮

- 初轮核对迁移连续性、表约束/索引、严格请求模型、路由顺序、原子批量写入、revision 更新、稳定 person ID redirect CTE、成员有效性谓词、资产去重与 capture 游标、跨合集/陈旧游标绑定、会话写保护和响应投影；发现 F1–F4 四个反例并据此修复。
- 修正合集照片卡复用单人相册说明文字的问题：合集头像现标为“合集成员”，不暗示合集成员是同一身份。
- 修复轮复核审核反例与相关合集集成、DOM 和迁移测试：**26 passed / 0 failed / 0 errors / 0 skipped**，JUnit 为 `test-results/p4-person-collection-fix.junit.xml`；Ruff、`node --check`、`git diff --check` 通过。
- 160 人合成证据覆盖合集人物分页，集合照片 SQL/索引计划由两名有效成员的合照夹具覆盖；没有另跑 160 人均带照片的场景。真实浏览器、真实图库、大规模性能、并发快照和发布验收仍未验证。
- 下一步唯一任务为 GPT-5.6 Sol / high 聚焦终审；卡片：[Sol 聚焦审核卡](../tasks/P4-PERSON-COLLECTION-SOL-REVIEW-A.md)。

## 后续聚焦复核

F1–F6 均已修复并关闭。初轮审查、修复轮反例和最终通过记录见[聚焦审核报告](P4-PERSON-COLLECTION-A-focused-review.md)。F6 修复后定向 JUnit 22/0/0/0，用户指定当前会话聚焦复核的独立定向 JUnit 13/0/0/0；Ruff、Node、diff-check 通过。用户已确认审核通过；未宣称指定 Sol 模型终审或总任务/真实页面验收 PASS。

## 最新短审交接

F5启动兼容和现存数据/备份保持已复核通过。F6 已通过 v26 不可变 `instance_token` 修复，验证 v24 迁移前删除的最大 ID 在迁移后确会复用但旧人物/照片游标均拒绝、旧 ID-only 路径及错误 token 不会作用于新实例。定向 JUnit 22/0/0/0；随后用户指定当前会话聚焦复核通过，JUnit 13/0/0/0。仅剩真实页面试用；未标真实页面或任务总验收 PASS。

## 最新聚焦复核闭环

F6原失败边界及直接API/DOM独立定向13/0/0/0，无新增阻断；旧v24已空/已删除ID复用的实例隔离、token持久/不可变、旧游标与旧/错误实例请求零写入通过。F1–F6关闭，仅限工程/合成范围。用户真实页面试用待确认，不自动开始其他任务。
