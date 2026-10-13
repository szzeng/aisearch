# P4-UI-PEOPLE-D — 人物相册与整理入口层级

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

仅**人物相册/人物合集的照片页**接入 CORE 的统一日/月/年照片时间轴，拍摄/入库依据切换、年月筛选和直接定位、自动续页、同日跨页合并、查看器返回原位置。人物列表、未命名/待整理/已忽略脸组及批量管理依旧使用各自工作流，不改为时间轴。保留 canonical 身份、合集有效成员按 asset 去重、同照片多脸提示、封面/纠错/合并/拆分/撤销/跨页勾选及原选择上限（该上限**不约束相册照片总数**）。成员改变必须使日期游标过期。SIMPLIFY-A、SELECT-RANGE、ESC 已做的功能不重复实现，也不复活其已退场的高级重复 DOM。规范见 [UI 2.0](../plans/P4-UI-TIMELINE-V2.md)。
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
