# P4-PERSON-UX-SIMPLIFY-A — 执行与当前会话复核报告

日期：2026-10-11。状态：**SCOPED PASS / 用户指定当前会话受影响面复核**。

任务卡：[P4-PERSON-UX-SIMPLIFY-A](../tasks/P4-PERSON-UX-SIMPLIFY-A.md)。开工 HEAD 为 `43c257c`；本任务按用户后续授权与两项人物整理优化一起本地提交，未推送。用户明确要求“直接这里复核就好”，因此本报告记录同会话实现、修复、测试和完整受影响调用链复核；**不称独立 DS-H 或 Sol PASS**。用户补充“直接在头像上勾选、支持多图”已纳入人物批量管理、人物相册逐脸整理和待整理/已忽略人脸。

## 交付行为与旧功能替代矩阵

| 旧能力 | 新入口和实际行为 |
| --- | --- |
| 已命名/未命名人物浏览、取名、封面、局部搜索 | 原人物/整理一下及普通人物相册保留；头像普通点击仍能看照片 |
| 多源人物合并 | 人物 → 批量管理 → 头像勾选 → 合并到；跨人物页保留源 revision 快照，一次 `/persons/merge`，目标选择排除所选源 |
| 批量 assign/unassign/restore_auto | 相册 → 整理照片 → 头像勾选；待整理/已忽略区也可头像多选，批量指定、移出或恢复自动判断；恢复保留排除 |
| 1～500 脸拆分 | 人物相册 → 整理照片 → 跨照片/跨人脸页选择 → 拆成新的未命名人物；一次原有 split 事务 |
| 普通单脸更正、新建人物 | 照片预览 → 对应脸更正；符合资格的待整理单脸仍可新建未命名人物，保留不可撤销提示 |
| 映射 confirm/reject/defer/undo | 整理一下的实际待确认数量入口，或照片自己的提示 → 按旧脸审核；候选头像明确勾选，人工归属继承另行选择；无候选无确认按钮 |
| 历史、快捷撤销、过期快照清理 | 整理一下 → 更多 → 操作历史；中文类型、服务器状态、影响数量，UNDO/新建人物/过期记录不提供撤销；清理先预览再确认 |
| 忽略/恢复、候选组分页、质量范围、识别批次 | 沿用原列表和 API；识别/补匹配控件按需展开，运行中的批次状态独立可见 |
| 旧页面锚点 | `#panel-persons/name/merge` 回普通人物，`#panel-faces` 定位相册整理（无人物时整理一下），`#panel-mappings` 定向或有界审核，`#panel-operations` 打开历史 |

旧高级区域的重复人物列表、技术 ID/revision 输入、脸与拆分、映射和操作历史 DOM 已退场。`persons.js` 保留为有实际调用方的按需整理模块，复用 `persons-albums.js` 的请求、CSRF、媒体、人物选择和反馈；没有第二次旧页面启动或空 DOM 绑定。

## 接口、数据和加载边界

唯一新增后端接口是受既有会话保护的 `GET /api/v1/assets/{asset_id}/face-review`。路由 → `PersonService.asset_mapping_page` → `persons.read.asset_mapping_page`，按 `idx_faces_asset` 定位照片再 JOIN 已有映射组，只读 OPEN，默认 50/上限 200，`fa1` keyset 游标绑定 asset 与旧脸 ID。复用原摘要投影，仅增加旧脸是否存在活动 exclusion 的布尔字段。没有路径、框、向量或置信度输出，没有新的映射真值表。

照片提示只读当前 asset 的 `limit=1`，审核页有界 `limit=20`；打开具体旧脸才读原详情候选。人物首屏不请求映射/历史/维护数据，人物与人脸分页分别保留选择；历史每页 30。头像 DOM 复用、全局排序、合集按需加载和整理分区独立加载保持。

所有身份写入沿用原 label、单脸 assignment、`/face-assignments/batch`、merge、split、mapping decisions 和 undo：保留 contract_version、选择时的 control/assignment/person/review/operation revision，以及 request_id、CSRF 和服务端零部分写入。没有改 `operations.py`、`face_correction.py`、`split.py`、`mapping.py` 或 retention 的冻结写语义，没有 schema/依赖/uv.lock 变更。

网络/5xx 结果未知时，统一写入口保留原请求内容和 request_id，并提供“重试上次保存”；不同新事务不能覆盖未知事务。已知失败不会显示成功。成功后重新读取服务端可见列表和审核/历史；照片、关闭预览、审核/候选、同照片重试都有独立序号或绑定保护。人工建议、manual assign/unassign、exclusion、hold 的资格仍由原服务决定。

## 六组必要证据

| 分组 | 实际验证 |
| --- | --- |
| T1 普通更正/人工保护 | 原 correction lifecycle/undo 集成用例覆盖合照其他脸、人工排除与恢复、幂等/冲突；新 UI 验证所选脸及 revision 构造；浏览器执行普通单脸更正和撤销、跨两张照片的两脸批量更正 |
| T2 合并/拆分 | merge、operations、split lifecycle/undo 原集成用例验证 canonical、重复照片去重、空源/资格/stale/原子失败与撤销；新 UI 验证两页源一次 merge、两页脸一次 split 及 revision 漂移清空选择；浏览器多源合并及历史撤销、两脸拆分及快捷撤销 |
| T3 映射保护 | redetection decisions/hold 原集成用例覆盖确认/拒绝/undo、hold、人工排除、后续人工修改及原子拒绝；UI 覆盖无默认候选、多候选继承基数、无候选拒绝/暂缓；浏览器确认、拒绝及各自撤销恢复待确认 |
| T4 照片直达 | 新只读/API 三项测试：同图有其他有效脸仍有 OPEN、两旧脸稳定分页、asset-bound 游标、会话 401、missing 404、limit/cursor 422、私有字段排除、零写入和索引查询计划；UI 覆盖跨照片与同照片重试旧响应；浏览器同图有效王五脸与待审核提示同时可见并直达 |
| T5 历史/清理 | 原 oplog GC 集成测试实际验证严格时间/最近窗口/每批 100/保护跳过/回放/清理后不可撤销/事务回滚及会话-CSRF；UI 验证新建/UNDO/过期无撤销、真实冲突无虚假成功、预览前不能清理；浏览器历史撤销合并、预览 0 条时确认禁用 |
| T6 页面/加载 | 人物/人脸至少两页选择、取消、刷新、错误与保留同请求重试；预览关闭/切换、头像复用、质量/候选组/忽略恢复/合集/批次按需请求的相关 UI 用例；真实浏览器头像直接勾选、相册/待整理/映射/历史及旧映射/历史锚点 |

后端最终 JUnit：[contracts-final](../../test-results/P4-PERSON-UX-SIMPLIFY-A-contracts-final.xml) **107/0/0/0**（tests/failures/errors/skipped），退出码 0。

前端最终 JUnit：[delivery-ui](../../test-results/P4-PERSON-UX-SIMPLIFY-A-delivery-ui.xml) **38/0/0/0**，退出码 0。包含新 JS harness 的 faces/inbox/merge/mapping/history/retry/stale 七场景，以及原人物、合集、照片详情相关 UI 测试；不是只做静态字符串断言。最后补强同照片重试反例的七场景补测记录见 [delivery-supplement](../../test-results/P4-PERSON-UX-SIMPLIFY-A-delivery-supplement.xml)。总范围为 145 个不同用例，不把复跑数相加。

最终 Ruff、三个产品 JS 与测试 harness 的 Node 语法、Python 编译及 `git diff --check` 均退出码 0。

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
uv run --no-sync pytest -q tests/unit/test_person_asset_face_review.py tests/integration/test_p3_person_merge.py tests/integration/test_p3_person_operations.py tests/integration/test_p3_person_split_lifecycle.py tests/integration/test_p3_person_split_undo.py tests/integration/test_p3_face_correction_lifecycle.py tests/integration/test_p3_face_correction_undo.py tests/integration/test_p3_face_redetection_decisions.py tests/integration/test_p3_face_redetection_hold.py tests/integration/test_p4_person_oplog_gc.py --basetemp=.pytest-tmp/P4-PERSON-UX-SIMPLIFY-A-contracts-final --junitxml=test-results/P4-PERSON-UX-SIMPLIFY-A-contracts-final.xml --tb=short
$uiTests = @(rg --files tests/unit | Where-Object { $_ -match 'test_(person.*|p4_person_collection.*|search_person_album_link|photo_view_info)_ui.py$' })
uv run --no-sync pytest -q @uiTests --basetemp=.pytest-tmp/P4-PERSON-UX-SIMPLIFY-A-delivery-ui --junitxml=test-results/P4-PERSON-UX-SIMPLIFY-A-delivery-ui.xml --tb=short
uv run --no-sync pytest -q tests/unit/test_person_ux_simplify_ui.py --basetemp=.pytest-tmp/P4-PERSON-UX-SIMPLIFY-A-delivery-supplement --junitxml=test-results/P4-PERSON-UX-SIMPLIFY-A-delivery-supplement.xml --tb=short
```

Ruff 检查本任务修改的 Python（含 models/routes/read/service、oplog GC 测试和相关 UI pytest 文件及新只读/工作流测试）；`node --check` 检查 `persons.js`、`persons-albums.js`、`photo-info.js`、`person_ux_simplify_harness.cjs`。直接 `.venv/Scripts/python.exe -m py_compile` 校验新增/修改的后端模块。

## 真实浏览器及复核修复

使用实际 create_app/Uvicorn、受保护 API、媒体裁剪和真实 DOM，仅访问 `.tmp/P4-PERSON-UX-SIMPLIFY-A/browser2` 的合成 PNG/合成人物/Catalog，没有访问真实图库。45 人提供两页人物；跨照片两脸更正/移出/拆分实际生效，合照重复计数去重，多源合并由历史撤销恢复；普通照片单脸改为李四后撤销回王五。映射候选明确选择后确认/拒绝，各自撤销后再读 OPEN；最新浏览器控制台无 error。

IAB 拦截原 `/api/v1/session` 启动 URL，smoke 服务器在 `.tmp` 中提供测试专用 `/smoke-start` 别名，调用原 `exchange_session` 设置原 session/CSRF，**不是产品路由或认证绕过**。原 session API 另由 ASGI 测试实际验证。合成结构化识别文档原 SHA 与替换 PNG 不一致曾令候选裁剪拒绝；仅 fixture 改为已有手工种子使用的 header-frame 裁剪，不修改产品验证，不声称运行检测模型。

真实浏览器发现并修复：写入期间刷新生成了 disabled 的勾选框，撤销完成后未重新启用；现统一在写状态释放时同步。当前会话复核还补齐待整理脸的头像勾选和原批量更正替代入口、同照片重试独立序号、历史 BATCH_MERGE 中文名称/冲突反馈及维护预览失效处理。普通模式头像仍打开整张照片，批量模式头像直接勾选；该区别有新的行为断言。

早期失败与处理：浏览器集合/替换能力不完整的旧 DOM mock 已补齐；旧测试写死 schema 最新为 27，但 HEAD 已有 28，改为验证 27 迁移存在并保留实际 v26→v27 与回滚检查。旧缓存版本/旧 DOM ID、延迟识别控件预加载断言按新入口更新。排序及地点脚本版本的旧断言落后于 HEAD，分别与已有 `photos_recent_desc`、`p4-place-album-perf-a-2` 对齐，没有改产品排序/地点逻辑。一次 PowerShell 不展开 pytest 文件通配符造成未收集，随后使用 `rg` 实际文件列表。新 Node harness 的 stderr 使用 UTF-8 解码，避免 Windows GBK 诊断读取失败。最终结果均读取 JUnit，未以无输出或 collection 代替通过。

截图：

- [相册头像跨照片多选](assets/p4-person-ux-simplify-avatar-selection.png)
- [待整理区头像多图勾选](assets/p4-person-ux-simplify-inbox-selection.png)
- [照片定向映射审核](assets/p4-person-ux-simplify-mapping-review.png)

## 范围与交接

复核覆盖本任务完整 HTML/CSS/脚本启动与事件绑定、共享照片详情、路由/响应模型/只读查询、所有写调用参数、ADR-0006/0007/0008/0010/0014 直接相关不变量和测试证据。没有遗留未替代旧高级能力或已知阻断 finding。完整范围验收只针对本卡；独立 DS-H/Sol、全仓回归、真实图库/运行性能、模型/GPU、打包及 30 万照片验证均未运行，也不借此关闭历史全仓失败。

已保留开工已有 LIBRARY-SYNC/OPS 的 app.js、styles.css、refresher、相关库测试及报告等工作树改动；共享 models/routes/index.html 只叠加本任务必要改动，不据本报告验收其他任务。本次按用户后续授权仅暂存并本地提交人物任务范围，未推送；其他任务变更保留。

[P4-UI-PEOPLE-D](../tasks/P4-UI-PEOPLE-D.md) 已注明本卡完成的工作流及次级维护入口，剩余纯视觉参考/布局目标保持独立待实施，不追认整张 D 卡完成。下一步是用户自行少量试用本卡入口并反馈；没有新的算法/事务安全缺口，不为连续开发生成额外优化卡。OCR、地点冷首屏及存储 C2 原有边界维持，C2 删除仍未授权。
