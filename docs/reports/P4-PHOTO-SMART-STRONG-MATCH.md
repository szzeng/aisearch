# P4-PHOTO-SMART-STRONG-MATCH 执行报告

**日期：** 2026-10-21
**基线：** `900028dd984f0f9ad74cbff2fb036e23e87d39b8`（工作树含并行任务的未提交变更，执行期间保留）
**结论：** **IMPLEMENTED / SCOPED PASS**；完整 unit/integration 原始合并运行未全绿，范围外剩余失败单列如下。未提交、未推送。

## 实施

- 搜索服务在已有向量检索阶段，从距离 `<= strong_match_max_distance(space_id)` 的向量行生成服务端 Session 证据；证据绑定 Session、Profile、Generation、Space、dimension 和阈值，最多覆盖既有 5000 候选，不改变普通搜索候选和融合顺序。
- 智能合集共享 `select_strong_match_candidates(session)`，只按已验证的 Session 证据筛选并保留融合候选顺序。新建和后台刷新共用该路径；无阈值/证据时拒绝发布，纯 FTS 命中不会单独入选。
- COMBINED 缓存加入策略版本及模型阈值身份，并写入 `photo_smart_cache_state.cache_policy_identity`。schema 0043 仅加可空列；旧缓存保留规则、排除和原缓存数据，但不能被浏览或另存，须刷新后才可用。
- Luna H 首轮指出：旧策略缓存处于 `REFRESHING` 时，详情路径仍会做资格搜索，另存也可能误创建空合集。现由详情独立策略失配标记提前返回空页，另存直接 409；新增回归验证无搜索调用且普通合集数量不变。
- schema 从 42 升至 43 同时将既有不可变地点包兼容桥的显式审查上限扩至 43；测试证明原包在 42/43 字节不变、上限 44 仍拒绝。schema43 备份恢复也已验证。
- 智能合集目录和详情显示“仅高度匹配”“高度匹配 · XX 张”“暂无高度匹配照片”“正在更新高度匹配照片”和旧缓存重建状态；TEXT、FILTER、普通合集的筛选路径未接入该行为。

## 验证

- JUnit `combined-regression-r2.xml`：**256 passed / 0 failed / 0 errors / 0 skipped**。覆盖本任务受影响测试、schema 0043 新库/42→43/失败回滚、schema 43 地点包兼容、备份恢复和首次首轮 Luna H 发现的失败路径修复。
- 相关 schema 迁移最小检查（新数据库、旧库升级、迁移失败回滚）均在上述组合中通过。
- Ruff、Python `py_compile`、`node --check src/photo_search/web/app.js`、`git diff --check` 通过。
- Codex 内置浏览器使用项目 `.tmp/P4-PHOTO-SMART-STRONG-MATCH/browser` 中的 3 张合成 JPEG 和确定性查询编码器。一次性 session 登录/API 正常；搜索页显示 3 个候选及“约 1 张高度匹配”；创建的 COMBINED 智能合集显示“高度匹配 · 1 张”；手动刷新返回成功，刷新后保留“索引处理中”状态；打开唯一匹配照片的查看器成功。服务已停止。未连接真实图库、真实模型或 GPU。浏览器未提供可写入 `test-results/screenshots/` 的导出接口，因此未保存截图文件。 查看器打开时一个辅助来源详情请求 `GET /api/v1/sources/1` 返回 HTTP 500；查看器仍显示合成照片信息与相对位置，该辅助错误不影响本任务智能合集过滤链，但未单独调查。
- 完整 unit/integration 初次运行：3493 项中 3411 passed、30 failed、52 skipped。其后重跑失败节点所在模块及本任务扩展面，组合 **256/256** 通过；30 个原失败节点中 29 个与 schema43 地点兼容/备份恢复有关并已关闭。剩余 1 项 `test_all_photo_pages_load_the_versioned_shared_viewer` 属于工作树已有的 Qwen 页面资源版本断言与 `search.html` 不一致（两者均为本轮开始前的并行未提交改动），本任务未改动该页面或断言，未据此声称一次全量通过。原始 JUnit 与各轮记录保留在忽略目录 `test-results/P4-PHOTO-SMART-STRONG-MATCH/`。
- Luna H（`gpt-6-luna` / high）完整受影响面首轮发现 1 项 P2；修复后跟进复核 **PASS，无剩余 blocker**。

## 性能与范围

筛选在候选集上用 ID 集合查找和一次顺序遍历，复杂度 `O(K)`、`K <= 5000`。没有新增编码、向量扫描、逐照片 SQL 或缓存有效时的后台搜索。真实图库、30 万图库压测、真实模型/GPU、发布及提交未执行；这些不在本任务验收范围内。

## 打开智能合集错误跟进（2026-10-21）

- 用户日志中的目录 API `GET /api/v1/photo-smart-collections?limit=20` 在摘要读取 `cache_policy_identity` 时抛 `IndexError`。根因是目录 SQL 使用显式列清单，却漏选了 schema 0043 新增的 `s.cache_policy_identity`；不是通过日志访问或检查真实图库数据库得出的结论。
- 目录查询现显式选择策略身份；摘要函数仍兼容不含该列的旧投影，并将该缓存安全标记为需要重建，避免旧缓存被计数或浏览。新增有效 COMBINED 缓存目录投影及旧投影两项回归。
- 定向 JUnit：**6 passed / 0 failed**（`open-smart-collections-fix-r2.xml`）；Ruff、`py_compile`、`git diff --check` 通过。性能路径仍为目录单次 bounded SQL，不新增逐合集查询、向量计算或模型调用。
- Luna H（gpt-6-luna/high）跟进复核 **PASS**，无 blocker 或缓存泄露风险；确认本次只改读取路径，不需补跑 migration/schema 检查。未在工作树找到原始堆栈日志，因此按用户提供的日志描述与可复现的投影缺列机制核对，不声称直接检查了原始日志文件。未触碰 `PhotoSearchData`。需重启正在运行的本地应用进程，使其加载已修复的目录查询代码；schema 0043 的迁移由应用打开 Catalog 时按既有路径处理，不需要手工改库。
