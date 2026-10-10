# P4-PERSON-SMALL-FIX-A — 人物相册试用修复

2026-10-10。**IMPLEMENTED / 定向验证通过；用户现场试用待确认**。[任务卡](../tasks/P4-PERSON-SMALL-FIX-A.md)。实施入口 HEAD `c19749a`；工作期间 HEAD 由另一项 UI 规划文档提交推进到 `6fa7001`，保留其内容及原 `.tmp/`、清理审计材料。未提交/推送本任务。

## 修复结果

1. 人物、未命名、已忽略页统一固定 `photos_desc`，移除“默认顺序”选择；整理/合并选择弹窗、搜索人物筛选、旧高级人物页也请求已有 browse 排序接口。合集人物在完整 canonical 成员集合上按有效不同照片数降序、person_id 升序分页，并复用当前页计数；照片详情共享人物头像和搜索预览按相同计数排序、canonical 去重。没有把照片流按人物数量排序。自动建议首选人物仍按原置信度判定，不以照片多寡改变识别判断。
2. browse 的姓名和别名采用 Python Unicode casefold 的 SQLite 只读函数，再做字面子串匹配；`Alice` / `ALICE`，`Straße` / `STRASSE` 均可查，中文不变，`%` / `_` 不当通配符。函数只注册在该查询连接中，无 schema/依赖变更。本卡初轮仅改只读搜索；用户随后要求保存同名也忽略大小写，另见 [P4-PERSON-NAME-CASE-A](P4-PERSON-NAME-CASE-A.md) 与 ADR-0019。搜索不会自动合并身份。普通自然语言搜索原本已有忽略大小写处理，本次未改其协议。
3. 用户确认过期名称同时发生在候选分组和选择弹窗。后端原本就实时读取 persons.name，合成正式改名 API 验证两处读返回新名字；前端补齐写后刷新已忽略候选、合集成员以及返回合集时的陈旧 DOM。改名/撤销/创建通过本地 storage 标记通知同源页面，窗口返回和浏览器缓存恢复时检测变更，刷新候选/当前选择弹窗并清除过时的已选目标；无变化的窗口 focus 不重载多页人物。保持已展开的可见/忽略候选组，以及尚未提交的名字输入。旧高级写入口也发送该标记。无需重识别人脸。
4. 根因是“加入并忽略”保留了 person_hidden，而旧移出合集只删成员关系。本次按用户明确要求覆盖原 COLLECTION 卡的“不自动恢复隐藏”策略：移除实际成员时，在同一 Catalog.write 事务恢复其 canonical 人物及本次移除的源锚点可见性，按名字回普通人物/未命名列表；仍属于其他合集、所有原图、人脸归属/控制/排除/人物历史不变。输入包含非成员不会恢复该非成员；重复移出 no-op 不撤销后来显式忽略。无效批次和注入中途失败均零净写入。

合集人物新游标为 `pcp-count1`，位置为 `[asset_count, person_id]`，继续绑定合集 ID/instance_token/revision/catalog_filter_revision。旧 `pcp1` 与非法位置返回 409/INVALID_CURSOR，要求刷新；旧 `/persons` 与 browse 的 ID 排序 API/游标保持可用。没有迁移、识别任务或向量重算。

## 简单模糊搜索评估

“输入部分姓名”已经由字面子串检索实现，这次补齐大小写。拼音需要额外转换与候选映射；错别字/拼写容错需要距离规则和明确的候选展示，否则短姓名易出现不相关结果。均可实现，但不是这四处纠错必需项；本次不增加依赖或隐式人物判断。后续若用户需要，采用显式建议列表而不是自动选定/合并人物。

## 合集内纠正与合并刷新补修

同日用户补充“在合集页面做人物纠正、合并后，人物列表不会改变”。从合集人物卡进入单个人物时，父合集保存在 `returnCollection`，原写后刷新只检查 `state.collection`，因此漏掉暂存父列表。现统一刷新当前或暂存父合集的人物卡；纠正后重新取得有效照片数，合并后重新读取 canonical 成员并去重，撤销同样刷新。返回合集仍重新读取当前第一页并恢复人物标签的选中状态。父合集的移出按钮/加载更多在单个人物页和照片标签下保持隐藏。刷新（含其他窗口的通知）清空旧成员选择，避免用已合并的旧卡继续操作。

当前合集照片标签在收到人物变更通知后也重读照片；合集照片预览本身仍只有浏览入口，纠正从合集进入单个人物后执行。请求序号、合集 ID 和 instance_token 同时校验，切换或替换合集后旧人物响应不能覆盖当前列表。此轮没有后端产品逻辑、成员锚点或身份/人工状态协议改动；合成目录测试使用正式纠正、合并及撤销服务验证读结果即时变化、原成员锚点保留。

补修 JUnit **19 tests / 0 failures / 0 errors / 0 skipped**：[报告](../../tmp/test-reports/P4-PERSON-SMALL-FIX-A-collection-followup.xml)。Node DOM 执行实际纠正/合并/撤销点击链，检查尚未返回时父列表更新、返回后的合并目标卡、跨窗口照片刷新及旧实例响应丢弃；与前轮有重叠，不累计用例数。测试夹具首次漏导出 editBase 导致失败，已修复后实际复测。Ruff、Node 语法和 diff-check 均通过。人物页 `persons-albums.js` 资源版本升至 `p4-person-small-fix-a-2`。

```powershell
$env:UV_CACHE_DIR = (Join-Path $PWD '.uv-cache')
uv run pytest -q tests/integration/test_p4_person_small_fixes.py tests/integration/test_p4_person_collections.py tests/unit/test_person_small_fixes_ui.py tests/unit/test_p4_person_collection_ui.py tests/unit/test_person_inbox_create_ui.py tests/unit/test_person_hide_selection_ui.py tests/unit/test_person_sort_ui.py --basetemp .pytest-tmp/person-collection-followup-final --junitxml tmp/test-reports/P4-PERSON-SMALL-FIX-A-collection-followup.xml
uv run ruff check tests/integration/test_p4_person_small_fixes.py tests/unit/test_person_small_fixes_ui.py
node --check src/photo_search/web/persons-albums.js
git diff --check
```

## 待整理直接确认与收起后刷新

同日用户补充：部分已显示候选的人脸没有“确认是某人”入口，确认一次后全页列表重载，打断连续整理。原前端只给 `BORDERLINE` 原因显示确认按钮；现普通待整理卡只要接口返回有效 `suggestions[0]` 就显示首选候选的直接确认与“选其他人”，覆盖候选接近、合照冲突、其他建议、小脸/质量原因等。无候选仍走“这是谁？”，批量管理与已忽略入口保持各自按钮。人工移出的人脸若有当前有效建议，也必须由用户点击该单脸确认才会手动归类，未改变自动任务权限或人工排除规则。

确认仍立即向原受保护单脸 assignment API 提交 face_id、目标人物和控制 revision，只有服务返回成功才显示“✓ 已确认是某人”，禁用重复确认及旧 revision 的其他选择按钮。保留原人脸卡、分组展开状态、顺序和已有页，不调用整页列表加载。最近操作撤销提示和跨窗口通知保持。保存失败不打勾并恢复按钮；未知网络/5xx 结果保留 request_id 重试，明确 4xx 拒绝后重新生成意图。

候选分组收起后同步重读人物数量和待整理列表；若还有已确认而未收起的分组，等这些分组也收起再同步，避免移走其他正在确认的卡。保存期间收起，等待服务结果成功后才刷新。每次成功会清空所有当前分组的旧服务分页游标，再点组内“再看一些”从新版本重取，按 face_id 去重并保留已打勾卡；最外层旧分页暂等收起刷新后恢复。非分组原因筛选里的卡保留打勾，用户主动刷新或切换视图后同步；本次没有新增整组批量归类。

最新定向 JUnit **28 tests / 0 failures / 0 errors / 0 skipped**：[报告](../../tmp/test-reports/P4-PERSON-SMALL-FIX-A-confirm-tick.xml)。实际 JS DOM 覆盖多原因按钮、无候选/隐藏/批量入口、在途与重复点击、服务成功后打勾、失败和未知结果重试、组内分页去重、多个分组延后刷新、保存中收起、撤销后恢复，以及非分组打勾。同时重测人物相册合成 API、先前合集/改名刷新、质量/隐藏/创建/补匹配 DOM。旧补匹配夹具的 summary 路径补上实际 query 参数，并以真实按钮引用取代过期 DOM 下标。与前轮证据有重叠，不累计。Ruff、Node、diff-check 通过；资源版本为 `p4-person-small-fix-a-3`。本轮只改前端及相关测试/交接文档，无后端数据或接口协议变更。

```powershell
$env:UV_CACHE_DIR = (Join-Path $PWD '.uv-cache')
uv run pytest -q tests/unit/test_person_inbox_confirm_ui.py tests/unit/test_person_rematch_ui.py tests/unit/test_person_inbox_hide_ui.py tests/unit/test_person_inbox_create_ui.py tests/unit/test_person_small_fixes_ui.py tests/unit/test_person_quality_ui.py tests/integration/test_p4_person_albums.py tests/integration/test_p4_person_small_fixes.py --basetemp .pytest-tmp/person-confirm-tick-verified --junitxml tmp/test-reports/P4-PERSON-SMALL-FIX-A-confirm-tick.xml
uv run ruff check tests/unit/test_person_inbox_confirm_ui.py tests/unit/test_person_rematch_ui.py
node --check src/photo_search/web/persons-albums.js
git diff --check
```

## 待整理与未命名已加载分页保留

同日用户补充：点多次“再整理一些”后稍作整理又回到第一页，并要求检查未命名页。两个入口均有同一原因：写后刷新只调用一次第一页并清空原 DOM。现同一筛选范围内刷新记录并重取用户已经加载的页数，以最新服务游标连续读取；不使用写前失效游标，不超出已打开的范围。未命名/普通人物/已忽略人物列表均走同一保留逻辑，待整理候选分组和平铺原因筛选均覆盖。改名/合并/创建、确认后收起、忽略/恢复、补匹配、返回人物列表和变更通知的现有刷新调用直接复用。新的姓名查询、人物可见性、待整理类型或质量范围从新第一页开始。

刷新所需页面全部取得后才替换同范围列表；后续页读取失败保留原已加载 DOM。列表临时变少时保留之前打开的范围，撤销恢复后仍可补齐；真实剩余数据不足时以服务的末页结束。正在展开的候选组会恢复其已加载的人脸页数，组内重取仍按 face_id 去重。候选人变动不能使旧响应覆盖新筛选。返回单个人物之前保存列表位置，回到列表恢复；刷新前后以仍存在的可见卡为定位锚点，卡被移走时尝试邻近卡，再回退保存的滚动位置。返回按钮 focus 使用 preventScroll。

本轮仅前端分页/视图状态，不改 API/schema/人物归属协议，不访问真实库。解释“原图补识别”：读取原始照片更充分的像素检测人脸，主要补找缩小检测漏掉的小脸；与利用已有特征重做人物关联的“补匹配”不同。

最新 JUnit **11 tests / 0 failures / 0 errors / 0 skipped**：[报告](../../tmp/test-reports/P4-PERSON-SMALL-FIX-A-page-preserve.xml)。新增实际 JS 多页夹具覆盖：人物与候选列表各加载三页、第三页确认收起后仍三页、其他展开组内两页恢复、最新游标继续加载、后页未命名改名后返回、滚动锚点/返回 focus、临时末页后恢复、筛选切换、后页失败保留原卡、旧请求不能覆盖新查询。还重测已有确认/隐藏/创建/补匹配/质量/合集/排序/人数 DOM。Ruff、Node、diff-check 通过，前端版本为 `p4-person-small-fix-a-4`。此前 28/19/59 等证据与本轮重叠，不累计。

验证修复记录：新夹具先在未同步人物刷新时强行推进全局 revision，导致加载更多拒绝旧游标；改成先同步再检查后页失败。受影响补匹配夹具补全 window，人物忽略/恢复夹具改用游标定位而非请求次数定位，并将旧“回到40条”预期改为本次要求的“保留120条、恢复人物退出隐藏列表”。产品保留范围逻辑及这些夹具修改后重新实测，不将失败报告视为通过。

```powershell
$env:UV_CACHE_DIR = (Join-Path $PWD '.uv-cache')
uv run pytest -q tests/unit/test_person_loaded_pages_ui.py tests/unit/test_person_small_fixes_ui.py tests/unit/test_person_inbox_confirm_ui.py tests/unit/test_person_inbox_hide_ui.py tests/unit/test_person_inbox_create_ui.py tests/unit/test_person_rematch_ui.py tests/unit/test_person_quality_ui.py tests/unit/test_person_hide_selection_ui.py tests/unit/test_person_count_ui.py tests/unit/test_person_sort_ui.py tests/unit/test_p4_person_collection_ui.py --basetemp .pytest-tmp/person-pages-preserve-fixed --junitxml tmp/test-reports/P4-PERSON-SMALL-FIX-A-page-preserve.xml
uv run ruff check tests/unit/test_person_loaded_pages_ui.py tests/unit/test_person_rematch_ui.py tests/unit/test_person_hide_selection_ui.py
node --check src/photo_search/web/persons-albums.js
git diff --check
```

## 初轮实际验证

Python **3.12.10** / uv，项目 `.uv-cache`、`.pytest-tmp`，只用隔离合成图片/SQLite。综合 JUnit **59 tests / 0 failures / 0 errors / 0 skipped**；主列表固定排序和缓存返回最后改动后，直接受影响 JUnit **9/0/0/0**。两组有重叠，不累计为 68 个不同用例。综合报告：[JUnit](../../tmp/test-reports/P4-PERSON-SMALL-FIX-A.xml)；最后受影响报告：[JUnit](../../tmp/test-reports/P4-PERSON-SMALL-FIX-A-final-affected.xml)。

```powershell
$env:UV_CACHE_DIR = (Join-Path $PWD '.uv-cache')
uv run pytest -q tests/integration/test_p4_person_small_fixes.py tests/integration/test_p4_person_collections.py tests/integration/test_p4_person_albums.py tests/integration/test_p3_persons_page.py tests/integration/test_p3_persons_read_api.py tests/unit/test_person_small_fixes_ui.py tests/unit/test_person_sort_ui.py tests/unit/test_p4_person_collection_ui.py tests/unit/test_person_inbox_create_ui.py tests/unit/test_search_person_album_link_ui.py tests/unit/test_photo_view_info_ui.py tests/unit/test_person_hide_selection_ui.py tests/unit/test_person_count_ui.py tests/unit/test_person_quality_ui.py tests/unit/test_p4_search_nl_ui.py --basetemp .pytest-tmp/person-small-fix-verified --junitxml tmp/test-reports/P4-PERSON-SMALL-FIX-A.xml
uv run pytest -q tests/integration/test_p4_person_small_fixes.py tests/unit/test_person_sort_ui.py tests/unit/test_person_small_fixes_ui.py tests/unit/test_person_hide_selection_ui.py tests/unit/test_person_count_ui.py --basetemp .pytest-tmp/person-small-fix-final-dom --junitxml tmp/test-reports/P4-PERSON-SMALL-FIX-A-final-affected.xml
uv run ruff check src/photo_search/persons/albums.py src/photo_search/persons/collections.py tests/integration/test_p4_person_small_fixes.py tests/unit/test_person_small_fixes_ui.py tests/unit/test_person_sort_ui.py tests/unit/test_search_person_album_link_ui.py tests/unit/test_photo_view_info_ui.py
node --check src/photo_search/web/persons-albums.js
node --check src/photo_search/web/persons.js
node --check src/photo_search/web/photo-info.js
node --check src/photo_search/web/search.js
git diff --check
```

上述测试与静态命令退出码均 0。初轮验证曾因新夹具误用不存在的 add_alias、旧 Node window 模拟/资源版本断言、混合请求数组下标、地图夹具错误预期失败；已更正夹具并实际复测，未把失败当 PASS。首次修复回归另存 `P4-PERSON-SMALL-FIX-A-repair.xml`（9/0/0/0）。

当前会话自检完整产品 diff、直接调用链和新增回归：检索规范独立于命名身份；合集去重在 LIMIT 前；成员/可见性提交同事务；稳定身份、人工锁、排除、旧撤销以及实例 token 保护未改。**这不是独立 DS-H/Sol 工程审查，也不是总任务/真实图库验收 PASS。**

改动范围：persons/albums.py、collections.py；persons-albums.js、persons.js、search.js、photo-info.js；五个 HTML 的共享脚本版本和人物页排序文案；直接相关测试及交接文档。资源版本更新避免继续读取旧前端。

## 试用交接与边界

用户正常重启源码应用并刷新页面后，检查所有人物头像排序、大小写姓名查找、改名后候选/选择更新、移出合集返回普通列表，以及合集进入单个人物纠正/合并/撤销后的返回列表。旧版本已经移出而仍隐藏的人物，使用“已忽略人物 → 恢复所选”；不会批量猜测/修改历史隐藏意图。低质量未命名人物沿用整理页质量开关，可在“包括疑似低质量”中查看，不更改人工控制来绕过质量入口。

未访问 PhotoSearchData/现有服务，未操作真实图库、重启用户服务、运行模型/GPU、全仓/容量/打包/真实浏览器矩阵。DOM 模拟验证不冒充浏览器现场确认。schema 保持 28。下一步仅用户少量个人试用，无自动后续性能/工程卡。

## 本会话整合提交

2026-10-10，用户明确要求将本会话修复整合成一个简单本地提交，覆盖此前任务卡的提交禁止。本卡实现、相关测试和交接文档随本次人物相册整合提交保存；既有验证证据沿用，不重复测试，不推送。无关 .tmp/ 与清理审计材料保持未暂存。
