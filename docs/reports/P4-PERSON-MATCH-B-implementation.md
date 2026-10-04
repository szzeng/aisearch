# P4-PERSON-MATCH-B — 合照候选、多样代表与页面补匹配

日期：2026-10-04。基线：`master / 0fa4269`。状态：**IMPLEMENTED / SCOPED VALIDATION COMPLETE / INDEPENDENT REVIEW PENDING**。用户在研究后明确要求“按照你的推荐修一下”；完成一个不重算模型特征的归组改善闭环。未提交或推送，保留地图/首页等并行编辑。

## 完成的行为

1. 合照先汇总各个人物的最佳证据，再检查歧义和同图席位。旧反例“同图A=.53挡住候选B=.90”现在归B；最佳仍是已占位A时保留冲突建议，不为填满照片强行选较弱B。镜子/拼图和近似人物保护仍有效。人工排除同时过滤人物代表及同图已归属列表，防止从后一条路径绕回。
2. 已确认人物从合格、已确认索引成员中先取尺寸排序的最多32张候选，保留第一张，再选与已选代表差异较大的脸，最多8张；相似度≥.985的近重复不重复占位。SQL先限制候选ID，再取向量。canonical人物合并后仍最多32候选/8代表。旧`is_representative`只保留发布时尺寸提示，实际匹配实时选代表，不要求历史重发布或重做人脸模型。
3. “整理一下”新增原因总数、筛选与每张脸的具体提示：小脸、模糊、低检测分、边界相似、人选接近、合照冲突、等待候选、暂无匹配、人工移出。OPEN识别映射另列数量，继续走既有确认流程。汇总/列表共用可见性、有效成员、hold及原因表达式；游标绑定筛选。索引generation未READY时仍计为等待，单个entry确认不能冒充整代就绪。
4. 新增显式“补匹配未归类人脸”按钮。每次20张照片，返回有剩余后由用户点击继续；API上限50张，严格拒绝bool、负数、越界及额外参数。复用已存512D向量及session/CSRF，保持已确认归属、人工锁、排除与OPEN映射；不自动循环，不启动检测、读取原图、加载权重或图文建库。

使用：重启源码应用并刷新人物页 → 整理一下 → 查看原因/筛选 → 补匹配 → 有剩余时继续。识别更新需确认的脸在既有高级映射流程处理。补匹配不会拆散已经CONFIRMED的旧组或修正其旧错归；人工确认后的代表变化可帮助下一次补匹配。

## 改动与边界

- `face/contract.py`：策略revision为`personal-album-candidates-050-v2`，新增代表选择元数据。自动归人.50、建议.29、歧义差.08及检测/质量参数保持；.985是去重复选择保护值，不是新的识别校准。历史CALIBRATION不改；ENGINE_CONFIG/config_hash/spec/artifact/face_id均不变。
- `face/index.py`：完整候选决策、live多样代表、`index_state`实际可用代表数量；只读精确v1/v2同空间READY兼容例外不扩大。CONFIRMED关系、entry/artifact绑定、人工移出/排除仍为门禁；未确认建议不充当身份依据。
- `face/service.py`：显式`after_asset_id`续批与limit+1窥探，兼容既有CLI默认；匹配队列查询限制到所选资产，400个ID分块以避开SQL参数上限。启动候选发布和每处理一张后发布各有400事件上限，单个HTTP请求最多`400 × (1 + 已处理照片数)`次发布事件，并非整个请求共享400预算。待发布/hold项保留持久队列。
- `persons/albums.py`、`api/models.py/routes.py`：只读原因投影与摘要、筛选绑定、补匹配POST；公开返回原因枚举、安全计数及续批资产位置，不公开向量、置信度、坐标、路径或内部decisions。
- `web/persons.html/persons-albums.js`：JS版本更新、筛选、摘要及补匹配；重复点击只发一次，失败保留上次成功续批位置，列表刷新有序号保护。
- 新测试：`test_p4_person_match_policy.py`、`test_p4_person_match_b.py`、`test_person_rematch_ui.py`。未新增依赖/schema。

这是有界资产/事件数，尚未证明最大库响应时间。代表SQL仍需排序合格身份元数据；每个raw人物32张，canonical人物再限制32张，可能错过排名更后的稀少变化。合照仍按ordinal增量处理，不是全图全局联合优化。续批位置不是固定快照，库变化或前面暂缓项需之后从头补匹配；完成一轮后按钮重置起点，刷新页面也从头开始。质量LOW仍不自动归人或建组。原因摘要仅覆盖当前可整理的已检测脸，不代表未分析/失败/零脸的照片级覆盖率，质量只展示已记录的首个原因。

## 验证证据

最终退出0，实际读取JUnit：`test-results/p4-person-match-b.xml`，**216 tests / 0 failures / 0 errors / 0 skipped**，12.789s。下列同一直接受影响套件最后运行在正常权限路线，项目内临时目录；不是collect-only或仅静态检查：

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/test_p4_person_match_policy.py tests/unit/test_p3_face_association.py tests/unit/test_p3_face_contract.py tests/unit/test_p4_person_face_geometry.py tests/unit/test_p3_persons_read.py tests/unit/test_person_rematch_ui.py tests/unit/test_person_cleanup_ui.py tests/unit/test_person_cover_ui.py tests/unit/test_person_preview_navigation_ui.py tests/integration/test_p4_person_match_b.py tests/integration/test_p4_person_rematch.py tests/integration/test_p4_person_face_upgrade.py tests/integration/test_p4_person_albums.py tests/integration/test_p4_person_cleanup.py tests/integration/test_p4_person_cover.py tests/integration/test_p3_face_correction_lifecycle.py tests/integration/test_p3_face_correction_undo.py tests/integration/test_p3_face_redetection_hold.py tests/integration/test_p3_face_redetection_restart.py tests/integration/test_p3_face_redetection_mapping.py tests/integration/test_p3_person_operations.py tests/integration/test_p3_person_merge.py tests/integration/test_p3_person_undo.py tests/integration/test_p3_person_undo_ordering.py tests/integration/test_p3_person_split_lifecycle.py --junitxml=test-results/p4-person-match-b.xml --basetemp=.pytest-tmp/p4-person-match-b-final3 --tb=short
```

覆盖更强候选、重复人物/歧义、同图排除；代表多样/上限/旧false标志/发布后崩溃恢复；v1/v2/未知版本、人工知识/撤销/合并/拆分/hold/NO_FACE退役；真实合成API鉴权/CSRF/参数拒绝零写入、批次推进/重复执行；只读原因/筛选分页一致、未READY计数；实际JS在Node最小DOM中的浏览零POST、重复点击、续批、错误重试、提示、刷新游标。最小DOM不是浏览器验收。

Ruff针对上述Python产品面及三份新测试通过；`node --check src/photo_search/web/persons-albums.js`通过；本任务范围`git diff --check -- <本任务路径>`、最终global diff-check均通过。期间并行`.gitignore`曾有尾空格/文件末空行，最后核查时已由并行编辑消除，本任务未修改该文件。新文件尾空格及文档相对链接也核对通过。曾用AST等价校验恢复`index.py`未改变函数的格式，route也恢复无关格式改动，保留产品语义。

中间失败完整保留：first JUnit42/0/20/0，20项setup error均为沙箱pytest创建临时目录WinError5，正常权限重跑，不改ACL；second42/2/0/0为新夹具直接改PENDING违反confirmed_at约束及误期望筛选游标422，改为真实候选修复/崩溃路径及既有409后third42/0/0/0、fourth44/0/0/0。扩大受影响套件215/0/0/0后补未READY反例，误写generation状态为BUILDING而现有契约为PENDING：`p4-person-match-b-ready-fixture-failure.xml`为216/1/0/0；修正测试状态名后最终216/0/0/0。未放宽产品约束。

## 未验证与交接

未访问真实库/PhotoSearchData/个人服务、未加载模型、未重启用户服务、未真实浏览器验收，未跑全量/30万/性能/打包。合成证据证明决策与保护分支，不能说明真实误归率、召回率或未归类减少比例。

加入[P4-PERSON-UX-A独立DS-H完整受影响面审查](../tasks/P4-PERSON-UX-A-review.md)，随后按既有规则聚焦身份/人工知识边界；不冒称实现者检查为独立审查，不标总PASS。原图裁剪识别与增强检测尚未实现，见[后续升级卡](../tasks/P4-PERSON-DETAIL-A.md)：需新预处理/spec/artifact/generation与旧人工映射纪律，不能直接复用几何兼容例外。
