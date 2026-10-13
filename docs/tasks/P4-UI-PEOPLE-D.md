# P4-UI-PEOPLE-D — 人物相册与整理入口层级

> **2026-10-13 最新视觉验收入口（20万＋大图库）**：[17页图集/离线HTML](../mockups/ui2-large/README.md)；本卡参考 [05-people](../mockups/ui2-large/05-people.svg)、[06-person-album](../mockups/ui2-large/06-person-album.svg)、[07-person-collection](../mockups/ui2-large/07-person-collection.svg)、[08-organize](../mockups/ui2-large/08-organize.svg)。本目录 SVG 为按最新方案重制的仓库可预览矢量设计稿，并非聊天高清 PNG 原件；原 `ui2/` 参考降为历史视觉对照。视觉稿不替代业务和性能证据。


> **2026-10-13 新视觉参照：** [UI 2.0 全页面索引](../mockups/ui2/README.md)；本卡对应 [02-people](../mockups/ui2/02-people.svg)、[03-person-album](../mockups/ui2/03-person-album.svg)、[04-collection](../mockups/ui2/04-collection.svg)、[05-organize](../mockups/ui2/05-organize.svg)。最新 SVG 是仓库内的轻量静态效果稿；旧版 PNG 仅作导航/色彩对照，不得据旧图删掉时间轴能力。产品数据契约、布局取舍及真实功能以 [UI 2.0 规范](../plans/P4-UI-TIMELINE-V2.md) 与本卡正文为准。此处并非实施截图。


日期：2026-10-10；2026-10-13 UI 2.0 修订。状态：**PLANNED / 整理工作流已完成，照片时间轴与视觉目标未实施**。规划收口 HEAD `c19749a`。依赖 [A](P4-UI-SHELL-A.md)、[CORE](P4-UI-TIMELINE-CORE-A.md)、[B](P4-UI-PHOTOS-B.md)；人物新建已有[用户指定同会话复核与修复](../reports/P4-PERSON-INBOX-CREATE-A-review-current-session.md)，不重开旧复核。执行时重新读取最新人物卡/报告及用户试用反馈。

推荐 GPT-6 / High；限定 A 级 UI 小改，独立 DS-H 覆盖全部受影响入口与原写协议的直接调用链。不是人脸数据语义改造。

> **2026-10-11 并行范围协调（新方案优先）：** 用户另立 [P4-PERSON-UX-SIMPLIFY-A](P4-PERSON-UX-SIMPLIFY-A.md) 作为最终人物整理实施卡；旧 [P4-PERSON-UX-UNIFY-A](P4-PERSON-UX-UNIFY-A.md) 仅作历史参考。新卡（2026-10-11 SCOPED PASS，用户指定同会话复核）专门整合人物归属更正、批量拆分、重检测映射审核、历史撤销与旧高级 UI 退场。本卡 D 仍以原计划的**视觉、导航层级与已有功能展示**为主；两张卡不得并行修改相同人物前端文件。下文「旧高级工具保留」仅作为**替代功能尚未实现或验收前的过渡保护**，而非禁止未来退场。先完成并确认替代能力后，再依据 SIMPLIFY-A 移除重复入口；缺少替代能力的工具必须保留，不得静默删功能。本卡原 API 零改动和 A 级测试范围只约束 D 本身，不能削弱 SIMPLIFY-A 的只读扩展与数据保护要求。

## 2026-10-11 完成范围协调

[SIMPLIFY-A 执行报告](../reports/P4-PERSON-UX-SIMPLIFY-A.md)已完成人物/相册/待整理头像多图跨页勾选、原子合并/拆分、归属更正、照片映射直达、历史撤销/清理及识别控件按需展开；旧高级重复 DOM 经替代矩阵核对后退场。D 后续不得重新实现这些工作流、恢复旧技术面板或重复本卡已验证用例；只核对执行时新增差异和必要缺口。原 D 的纯视觉参考、布局与导航展示目标仍待实施，本次不称整张 D 卡 PASS；也不将同会话复核记为独立 DS-H。

## 视觉参考（执行前必须看图）

打开[人物页模拟图](../mockups/P4-UI-PERSONAL-people.png)、[HTML草图人物区域](../mockups/P4-UI-PERSONAL.html#people)与[参考规则](../mockups/README.md)。实际看图后实施头像/名字/照片数层级、浏览与整理分区、合集和更多工具入口。示意头像换真实裁剪，人数/照片数用原服务事实。报告保存产品合成截图对照；单脸身份、选择、人工状态、原撤销优先，不为了画面精简关键反馈。

## 现状盘点（备份参考）

[已有页面信息盘点](../plans/P4-UI-INFORMATION-CHECKLIST.md)供查阅和设计取舍，不要求119项全部保留、逐项汇报或逐项验收。用户此前明确强调必须增加/保留的项目，按盘点第0节的需求证据落实；其余展示可按方便、清新、少改核心接口的目标精简或调整。图中未展示不自动等于必须新增。此次纠正优先于本卡对参考信息作出的泛化“全部保留”解读；业务/人工状态及接口正确性边界不变。

## 读取与范围

AGENTS → status → 本卡 → [总计划](../plans/P4-UI-PERSONAL.md)§4人物/6/8；架构§8.3.3～8.3.5、§10.1～10.3；策略§2.2～2.4、§3.2～3.4、§11只核对不触碰边界。允许 `web/persons.html/persons.css/persons-albums.js/persons.js` 的局部展示、必要 UI 测试，不整理整个巨大 JS。

## UI 2.0 人物/合集日期浏览

仅**人物相册/人物合集的照片页**接入 CORE 的统一日/月/年照片时间轴，拍摄/入库依据切换、年月筛选和直接定位、自动续页、同日跨页合并、查看器返回原位置。人物列表、未命名/待整理/已忽略脸组及批量管理依旧使用各自工作流，不改为时间轴。人物目录默认数量+最新入库排序，不按拍摄日期排序，时间未知不影响目录总张数。保留 canonical 身份、合集有效成员按 asset 去重、同照片多脸提示、封面/纠错/合并/拆分/撤销/跨页勾选及原选择上限（该上限**不约束相册照片总数**）。成员改变必须使日期游标过期。SIMPLIFY-A、SELECT-RANGE、ESC 已做的功能不重复实现，也不复活其已退场的高级重复 DOM。规范见 [UI 2.0](../plans/P4-UI-TIMELINE-V2.md)。
## 20万图库适配限制

人物相册/合集**只使用 CORE 的服务端有界日期游标、年月摘要和前端回收组件**，不可为当前人物/合集加载全部 ID 到 JS 后分组、逐图读取日期或为所有人物预计算月度快照；同人合照只按唯一照片计算。人物/合集局部成员数量大时同样应符合 CORE 的单页/直接年月 seek/未知日期状态与短期缓存修订失效规则。性能页 API 目标 P95≤600ms（20万元数据场景），复用 CORE 基准/仅补人物与合集差异，不重建大夹具或重测旧人物工作流。

## 2026-10-13 人物目录和相册两层规则（本次最高优先）

- **人物目录**：默认**有效唯一照片数降序**，相同数量按该人物照片的**最新入库时间降序**、再 person_id 稳定排序；优先复用现有 `persons/albums.py` 的 `photos_recent_desc` 读取模式和分页、头像与合集标识，别把“最近加入照片时间”叫成“最后手动整理时间”或“最新拍摄时间”。同人无可靠 EXIF 的照片仍计入数量，并可作为人工封面候选；未命名、待整理、已忽略工作流继续独立。
- **进入人物相册/合集照片页**后才开启 CORE 共享时间轴：总数来自有效唯一 asset 集，正常拍摄日历仅可信 EXIF；**已提取无可信日期**、**未就绪/规则不兼容**在该人物/合集内有各自直接入口，按该相册资产 `created_at DESC,asset_id DESC` 分页；所有三类相加等于相册总数（同版本、去重）。切入库时间则全部进入真实入库年月。合照中同一照片的多张脸不重复统计、不过滤没有拍摄日期的图。
- **统计按需**：目录只请求头像、总数、既有排序/合集信息；**禁止打开人物目录时同时对每个人物算未知/待就绪张数或所有年份**。进入一个人物/合集后再异步请求其日期摘要，按成员/修订缓存；不影响首屏人物卡渲染。人物相册的年份筛选/未知桶属于该人物 scope，不进入另一个人或全集。时间依据与粒度偏好可复用。
- **验收小例**：张三 12 张中可信8、未知3、待就绪1；李四6张中日期未知2；跨页合照重叠。人物目录计数 12/6 不变，排序不会因日期状态变化；进入张三后 8+3+1=12；切至李四无张三年份条件，且张三截图/返回位置不丢。大图库20万元数据沿用 CORE 人物/合集代表场景。测试以行为为准，不新增全仓/整库 AI。

## 唯一闭环

- 头像和人物照片优先；人物/整理一下分明；已有合集为局部入口，已忽略/恢复仍可找到。
- 取名与看照片常用，封面/合并/拆分/识别维护按次级整理；不隐藏重要纠错入口，不新增强制确认弹窗。
- 改按钮位置保留 ID/监听处理函数；只可加薄展开/菜单逻辑，不能复制或改请求字段、资格判断、批次选择、undo 调用。
- 多页人物/人脸选择、候选组分页、clean/all质量入口与准确分类计数保持；展开/收起不能刷新首屏清掉选择。
- 无名字不要求先命名才能看图；新建仍仅一张脸且永久不可撤销，原操作快捷撤销仍按既有类型提供。
- 只保留仍有真实独立功能的高级工具；SIMPLIFY-A 已替代并退场的重复 DOM 不可恢复；有效锚点展开后仍须可准确定位。

## 验证

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
uv run --no-sync pytest -q tests/unit/test_person_hide_selection_ui.py tests/unit/test_person_inbox_hide_ui.py tests/unit/test_person_inbox_create_ui.py tests/unit/test_p4_person_collection_ui.py tests/unit/test_person_count_ui.py tests/unit/test_person_quality_ui.py tests/unit/test_person_preview_navigation_ui.py --basetemp=.pytest-tmp/P4-UI-PEOPLE-D --junitxml=test-results/P4-UI-PEOPLE-D.xml
node --check src/photo_search/web/persons-albums.js
node --check src/photo_search/web/persons.js
git diff --check
```

按实际改变再补人物/合集日期两页合并、去重、成员更新与旧游标失效、年月直达、返回滚动及封面/有效锚点等受影响测试；若新增 Python 测试跑对应 Ruff。合成浏览器至少两页人物选择/取消与一次忽略恢复、待整理单脸对话框、人物/合集预览返回；只操作隔离记录，不访问真实图库/模型。

独立 DS-H 核对完整 DOM/事件→原 API 链路、人工动作含义、请求参数/CSRF/失败/不可撤销显示及 JUnit；不能只验人物首页外观。若改资格/人工状态/事务/revision/undo，停止扩展并另立 D 级业务卡按原全受影响面主审与高风险聚焦规则；无高风险未决不默认 Sol。

报告 `docs/reports/P4-UI-PEOPLE-D.md`；未完成的必测保持 PENDING，不追认前人物卡现场通过。完成本卡停止；下一候选 [E](P4-UI-PLACES-E.md) 或个人试用。
