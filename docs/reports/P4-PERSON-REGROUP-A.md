# P4-PERSON-REGROUP-A — 两项人物整理优化与当前会话复核

日期：2026-10-11。状态：**SCOPED PASS / 用户指定当前会话受影响面复核**。
实施起点 HEAD：`43c257c`；用户已授权与前置 UX 改动一起本地提交，未推送。基于此前已完成的
[UX 整理实现](P4-PERSON-UX-SIMPLIFY-A.md) 继续；其他图库任务工作树变更保留。
[任务卡](../tasks/P4-PERSON-REGROUP-A.md)、[最小协议扩展 ADR-0020](../decisions/ADR-0020-selected-face-regroup.md)。
本报告不是独立 DS-H/Sol 冻结或全仓 PASS；按用户“直接这里复核”和本轮必要定向测试要求执行。

## 实际交互

人物相册点“整理照片”后，原按钮变成“完成整理”。继续直接在头像上跨图、跨页勾选，
显示“已选 X 张脸”；取消选择只清空选择，仍处整理模式；完成整理恢复普通浏览。
每次归属操作独立保存，完成按钮不提交、不撤销先前成功的动作。刷新读取仍处整理模式，
切换人物或正常页面重载恢复普通模式；旧异步响应仍由请求序号保护。

三种操作分别保留：

| 操作 | 实际语义 |
| --- | --- |
| 仅移出，留待整理 | 原 unassign：MANUAL_UNASSIGNED，不能自动归组 |
| 移出并重新智能分组 | 选中脸允许自动判断，保留源人物及既有排除，按原安全门限逐脸处理 |
| 拆成新的未命名人物 | 原 split：一个新人物、人工归属、原成员限制和补偿撤销 |

重新分组反馈实际“已归组 N 张脸 · 新增 M 个人物 · K 张仍需整理”，持续显示供核对。
机器建议不算归组成功；无可靠同伴、低质量、相似度不足或歧义不会强行确认。
批量保存中/响应未知等待重试时禁用完成、选择、清空和刷新，拦截人物/合集切换，
浏览器 reload/离页通过 beforeunload 请求确认；不同新写入不能越过未知的旧写入。
重试使用原 request_id，返回已提交结果；如果原动作已撤销，明确显示已撤销。

## 后端最小扩展与保护

最新代码证实 rematch_unassigned 按 asset 调度，直接调用会扩大至合照未选脸；
原三个更正 action 又没有所需原子语义。因此新增独立 `p4-face-regroup.v1`、
`POST /api/v1/persons/{person_id}/regroup`、`FACE_REGROUP` 和 0029 迁移，
没有改变旧 action、冻结 companion、自动门限、模型或向量空间。

复用 FaceIndexCoordinator.candidates、decide_for_face、_new_person/_confirm/_suggest，
复用 split 的明确选择/双 revision 校验、FACE_CONTROL before/after、共享幂等与 undo、
publish_derived 的一次 PERSON_CHANGED/filter revision/projection queue/候选修复。
正常及已验证兼容的 source-detail 候选分别恢复现有 outbox，总计最多 2000 事件；
该步骤只发布已有向量，不检测、不生成向量、不关联其他脸。

权威操作仅一个 BEGIN IMMEDIATE：先全批确认有效源成员/源 revision/控制与关联 revision、
当前资产与兼容世代、OPEN 旧脸/候选 hold，以及候选发布资格；再结束选中脸旧有效链接，
激活 canonical 源排除并保留其他排除，一次 +1 转 AUTO_ALLOWED，再匹配并记录最终快照。
有效链接 revision 也严格前进，机器结果标为 AUTO；原人工成员只有明确选中并通过 CAS 才释放。
候选缺失/修复未完成时保留原归属并报告 index_not_ready，不冒称重新分组成功。

NEW_GROUP 至少需要另一张不同照片的高质量选中脸达到原 MERGE_THRESHOLD；
现有人物的 borderline/near-tie/同图冲突/低质量规则不变，单张无可靠匹配留待整理。
只更新所列 face_id，未选合照脸及其他人工归属保持原值。

没有拼接两个人工操作或部分提交的身份任务。事务内故障全回滚；提交后丢失响应从最终快照重放。
派生索引继续通过旧 outbox 恢复。共享 undo 比较全部最终快照，新 OPEN hold 或后续人工修改
使撤销整体拒绝；无冲突恢复原语义且 revision 前进。生成的机器组身份不删除，空未命名组
按旧浏览规则隐藏；后来加入该组的其他人工脸/命名/合集不会被撤销连带删除。
新动作及其 UNDO 暂不参与旧快照清理，防止结果元数据或保护证据被裁掉。

0029 仅扩展闭合 operation type，按旧 0028 重建方式逐行保留历史、items、retention、
undo 自引用和 alias 引用。没有新真值表、依赖、模型或整个模块重构。

## 本轮修改文件

后端新增 `src/photo_search/persons/regroup.py`、`src/photo_search/catalog/migrations/0029_face_regroup.sql`。
修改 `catalog/schema.py`；`persons/contracts.py`、`face_correction.py`、`operations.py`、
`retention.py`、`service.py`；`api/models.py`、`api/routes.py`。
前端只修改 `web/persons.js`、`persons-albums.js`、`persons.html`，两个人物 JS 更新 cache key。
头像复用、分页、排序、合集、识别模型及其他页面业务保持原实现。

测试新增 `tests/integration/test_p4_person_regroup.py`、`test_p4_person_regroup_migration.py`；
增加 `tests/unit/person_ux_simplify_harness.cjs` 与 `test_person_ux_simplify_ui.py` 的 mode 场景；
`test_catalog_migrations.py` 更新连续迁移预期；`test_person_loaded_pages_ui.py`、
`test_p4_person_collection_count_ui.py`、`test_photo_view_info_ui.py` 仅同步人物 JS cache key 预期。
文档新增本报告、本卡与 ADR-0020，更新 development-status 和原 UX 卡的后续链接。
本轮不重算上一任务其余改动的归属，不覆盖其他图库任务。

## 验证结果与复核结论

| 证据 | tests / failures / errors / skipped |
| --- | --- |
| `test-results/P4-PERSON-REGROUP-A-accepted.xml` | **133 / 0 / 0 / 0** |
| `test-results/P4-PERSON-REGROUP-A-ui-delivery.xml` | **72 / 0 / 0 / 0** |
| `test-results/P4-PERSON-REGROUP-A-mode-final.xml`（beforeunload 补充） | **8 / 0 / 0 / 0** |

按 testcase 身份去重 **197 项**，重复的 8 个 UI 场景不累计。
新增业务专项 21 项，包括每组 10 脸：同人新组、已有人物、两人分组、低质量、独立无同伴、
两个近似候选歧义、borderline；有效向量逐行保持不变且禁止模型加载；原人物排除、后续补匹配不回源、
同图未选脸/人工排除不变、同图已有同人只给建议；中途第 2 个确认故障整体回滚、关闭/重新打开
catalog 后重放、同 key 不同载荷冲突、新 key 陈旧选择冲突；control/assignment/source/重复/索引/OPEN
冲突零写入；正常 undo、后续人工修改冲突、新 OPEN 候选与 undo hold、后来人工组成员保留；
API session/CSRF/隐私、兼容 detail 候选 outbox 恢复。

迁移专项 3 项：含历史 undo/alias/retention 的 v28→v29 逐列保留、备份与 FK 检查、
迁移尾部故障全回滚、旧 v28 程序遇新库拒写，以及旧迁移未改。
相关旧脸更正 API/生命周期/undo、补匹配、拆分生命周期/undo、hold、单脸新建、历史清理及通用迁移回归通过。
人物 UI 定向集及原冻结 contract 单测通过；执行真实分发脚本的 Node DOM/API 场景，
验证按钮/选择/退出、取消不发写请求、保存忙时不重复、不切页、完成后不撤销，以及最终数量反馈。

命令使用 `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`，
各轮 `--basetemp=.pytest-tmp/p4-regroup-* --junitxml=test-results/P4-PERSON-REGROUP-A-*.xml`。
accepted 集为本报告所列 13 个受影响测试文件；UI 集为人物/合集/人物搜索链接/照片查看 UI 文件
加两个冻结 face-correction contract 文件；mode-final 单独跑新的八场景驱动文件。
修改的 Python 和测试 Ruff **通过**；9 个 Python 模块 py_compile **通过**；
两个产品 JS 与 CJS harness `node --check` **通过**；最终 `git diff --check` **通过**。

早期失败已关闭：OPEN 合成行缺必填旧 spec、重复合成人名、测试模块导入与 cache/version 预期；
API 初稿同步端点导致 SQLite 线程亲和错误，改为与现有写端点相同 async 后通过真实 ASGI 校验。
source-detail 夹具原为未授权手动文档，补明实际已授权的待发布 outbox 后验证恢复；
没有放宽生产资格来让测试通过。原 FACE_OPERATION_TYPES/companion 保持冻结，新增类型独立声明。
相关 failed/collection XML 留存，不计成功；最终以上 JUnit 是交付证据。

复核覆盖新端点→薄服务→索引发布→事务→共享快照/派生→重放/undo/retention、0029 升级/拒写、
头像选择→新动作→统一 mutate→结果/刷新→完成/忙时退出全部新增面。没有未关闭 finding。

## 未验证与停止边界

仅隔离合成 SQLite/向量/ASGI 与 Node DOM；本轮两个新行为未另做真实浏览器手点。
上一 UX 卡的浏览器证据只用于未改部分，不能当本轮新动作已做浏览器验收。
未访问或修改真实 PhotoSearchData/图库；未做真实人脸质量评估、GPU、全库重检测/重建、
大性能/全仓回归、打包或真实用户 catalog 升级。
没有保证 10 张真实脸一定能归组；索引尚未可用时安全拒绝，用户须先恢复已有索引再重试。
新增同步有界匹配没有做大库耗时测量，不能宣称性能验收通过。
完成本卡即停止，下一步由用户少量试用两项行为；不自动扩展其他任务；本次按用户后续授权本地提交，未推送。
