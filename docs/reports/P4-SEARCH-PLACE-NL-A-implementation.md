# P4-SEARCH-PLACE-NL-A — 自然语言地点搜索实现与验收

日期：2026-10-08。状态：**映射根因已修 / 定向合成回归通过 / 现场复测待用户完成**。

## 短设计

继续复用 `/api/v1/search/place-aware` 与现有地点目录、相册成员谓词和搜索会话。新增纯解析模块只接收当前有效地点候选和原句，返回识别状态、原句、候选地点、剩余内容词及可选未解析提示；不更改冻结的 `/search` 契约、地点身份、成员语义或revision协议。

解析器为逐字符规范化文本建立回指原文的偏移映射，忽略空白差异时仍从原文正确移除地点/请求框架。只接受纯地点、明确“拍摄”表达、地点“的”表达、空白分隔内容及句末明确地点子句；只在已识别的请求框架中清理“帮我找/所有照片”等通用词。道路、风格、否定和多个地点不生成单地点硬条件。相同地点名的每个候选都单独算出剩余内容；页面选择时使用所选候选内容，无法消歧时继续要求选择。

旧地点入口脚本从搜索页解除，不再逐次发起四种地点相册查询。页面继续显示原查询，结果条件通过可移除地点chip表达；取消时把内容词同步回输入。纯地点仍走现有SQLite完整分页，并显式回显原句；带内容搜索继续复用已有同scope FTS/向量路径。

## 实现与验证

### 实现

- 新增 `src/photo_search/places/natural_search.py`：纯函数只解析调用方提供的当前有效地点候选；归一化字符携带原文偏移，支持空白、英文别名、标点及前置/后置拍摄地点。支持纯地点、地点＋请求框架、内容词、空白分隔地点；仅从句式框架移除通用说法。道路、风格、否定、多地点不建立单地点条件；当前候选目录缺少地点或父级不能证明层级时保留原句并返回提示。
- 候选从ACTIVE本地城市/国家、已安装省份INDEX及当前CN行政facet读取。对当前ACTIVE包的`cn_legacy_links`，仅当对应行政facet仍存在时，把链接旧地点的canonical name/ACTIVE aliases附加为该CN候选的解析别名，并从候选中移除其空成员旧key；该只读链接由旧城市坐标落入行政边界且名称/别名相交生成。没有有效链接的旧地点仍保留来源/父级独立选项，不按名称后缀猜测、不更改ID或成员关系。
- `/api/v1/search/place-aware` 继续使用现有成员集合、带范围的FTS/向量搜索、过滤交集和revision游标。修复纯地点页对`original_query`回显为空；没有迁移、稳定ID、成员谓词、冻结`/search`协议或依赖改动。纯地点分页没有请求编码器。
- 搜索页改为日常说法提示，移除旧地点相册入口脚本的加载与逐次四路查找，把旧`place_id`筛选收进默认折叠的高级条件。地点chip可取消；取消同步保留内容词到输入。选择同名地点带所选选项自己的内容词。空结果提示不扩大范围；GPS附近城市、中国行政区、省级地名资料分别标示。

### 定向自动验证

- 命令：按任务卡执行七个定向测试文件，保存JUnit到`test-results/p4-search-place-nl-a.xml`。首轮收集未设置Python导入路径，`test_p4_place_cn.py`报告`ModuleNotFoundError: tools`；没有将其当产品失败。设置`$env:PYTHONPATH="src;tests;."`后，重跑同一测试集合成功，进程退出码 **0**，JUnit **58 tests / 0 failures / 0 errors / 0 skipped**，包含本次旧key→CN facet映射回归。
- 24张合成图（原测试夹具3张＋21张）验证济南成员完整分页：每页7张，4页共24个唯一成员，total/page offset稳定；替换API query encoder为抛错函数仍完成纯地点查询，原合成encoder调用数为0。另验无照片已知地点仍返回显式地点条件和空成员，以及新增成员后旧游标以`CATALOG_FILTER_REVISION_STALE`拒绝。
- API短句覆盖包括`在济南拍的照片`、`济南的日落`、后置`日落照片，在济南拍的`、不规则空白、否定表达和真正同名候选。地点＋内容通过既有同scope FTS与向量过滤用例；CN父链/人工成员边界及搜索authority/session回归也在定向集合内。
- `uv run --no-sync ruff check src/photo_search/api/routes.py src/photo_search/api/models.py src/photo_search/places/natural_search.py tests/integration/test_p4_place_search_flow.py tests/integration/test_p4_place_auto_flow.py tests/integration/test_p4_place_cn.py tests/unit/test_search_place_nl_ui.py tests/p4_cn_fixtures.py`：退出码 **0**，`All checks passed!`。
- `node --check src/photo_search/web/search.js`：退出码 **0**。
- `git diff --check`：退出码 **0**。新建解析器、UI测试和本报告另外检查了行尾空白/冲突标记，均无发现。

### 合成浏览器闭环

使用已安装 **Chrome 154.0.8037.98** 实际浏览器，通过DevTools Protocol打开本地隔离服务；Windows CUA `getState()`连续两次超时后按恢复规则停止重试，改用Chrome无头浏览器，未安装软件。夹具在`.pytest-tmp/p4-search-place-nl-a-browser`：24张合成图片、济南12张、两个父级不同的“西湖”城市、一个无照片地点；真实页面资源为`/web/search.js?v=p4-search-place-nl-a-3`。

浏览器中实际操作并检查：济南全成员首屏与下一页、缩略图预览打开/关闭、济南＋日落查询、取消地点后输入同步为“日落”且请求保留内容词、同名地点选择文案显示父级且发出的查询仍含“日落”、无照片地点保留地点chip和空状态、详情页“返回浏览”恢复搜索快照、浏览器Back分别恢复现场。退出码 **0**。该服务仅用合成图片/catalog/profile/encoder，未访问或启动用户真实照片库。

### 范围与未验证项

原合成验收只表示本机个人地点搜索和上述小样本闭环。没有验证私人图库召回质量、真实地点目录/CN包覆盖、真实视觉模型的“日落”效果、非测试服务是否刷新、全量测试/容量/性能、安装包或其他系统。没有修改migration、`uv.lock`、真实catalog、GPS或向量；没有提交或推送。无需Sol终审；本卡为V3.1 §9.3个人验证范围，仍待办的其他任务状态保持原样。

## 后续用户反馈（2026-10-08）

用户在实际服务搜索“在济南拍的照片”时看到地点标签但没有照片；用户确认地点相册页的济南有照片，标签来源后缀不确定。未访问该服务、照片或catalog。

用户随后补充结果状态：“0行、候选0、`FILTER_ONLY`，本次没有查询文本”，并确认地点相册页有济南照片；后续截图显示输入为“济南的照片”、来源与可用性皆为全部、地点chip没有来源后缀。用户又重置筛选并重试，仍为0行。这说明该次请求已经形成纯地点筛选，前述“旧解析残留‘照片’查询词”的解释与回显不符，予以撤回。当前定向合成用例证明合成手工城市key能返回其成员，且新增GPS城市测试证明纯地点搜索与GPS附近城市相册返回相同成员；但两者都没有覆盖现场`place_scope.key`与济南相册key的身份对应。

当时一般筛选条件已因重置后仍为0而不再优先怀疑，并将地点key差异列为首要方向。随后用户提供了地点整理页截图；具体根因与修复见本报告末尾“用户相册截图后的根因定位与修复”。未访问用户catalog。

本轮没有检查、启动或重启用户服务，也没有读取真实照片/catalog。当前代码重跑精准自然语言解析及24张济南纯地点分页API用例：JUnit **2 tests / 0 failures / 0 errors / 0 skipped**，退出码 **0**，`test-results/p4-search-place-nl-a-live-feedback.xml`。另在自动GPS附近城市夹具中新增直接API与地点相册成员逐ID比较，JUnit **1 test / 0 failures / 0 errors / 0 skipped**，退出码 **0**，`test-results/p4-search-place-nl-a-auto-place.xml`。这些合成结果不能复现用户现场。状态仍为**合成验收通过 / 现场根因待定位**，不把反馈后的真实运行验收记为PASS。

### 用户相册截图后的根因定位与修复（2026-10-08）

截图明确显示济南卡片为“济南市 / 山东省 / GPS行政区·机器解析 / 30张照片”。以此建立完全合成的旧地点目录与山东省/济南市多边形：旧地点canonical name为“济南旧称”，别名为“济南”，坐标位于济南市行政边界内；当前复合包验证并写入对应`cn_legacy_links`。给旧地点人工归属的4张合成照片通过CN行政相册返回。修复前候选解析会把“济南”匹配到没有成员的旧地点key；修复后只保留有当前有效映射的CN facet候选，`济南的照片`返回`cn:<济南市admin_id>`，地点chip使用CN市名，结果逐ID等于该地点相册且总数为4。用户报告的30张仅作为截图事实；该测试不触碰用户catalog，也不声称现场查询已确认。

验证：任务卡列出的七个直接相关测试文件JUnit **58/0/0/0**（`test-results/p4-search-place-nl-a.xml`）；Ruff覆盖API、解析模块、CN fixture及相关集成/UI测试通过；`node --check src/photo_search/web/search.js`及`git diff --check`通过。第一次测试收集仅因没有配置PYTHONPATH导致`tools` namespace不可导入，设置`PYTHONPATH=src;tests;.`后完整集合通过。合成边界按名称/坐标证明映射；无有效映射地点仍单独列出，不执行名称字符串合并。

尚未访问/启动用户实际服务、私人照片或catalog。需用户重启/刷新当前后端后重新搜“济南的照片”，确认来源标记和照片行恢复；现场复测前此反馈闭环不标PASS。
