# P4-UI-PEOPLE-D — 人物相册与整理入口层级

日期：2026-10-10。状态：**PLANNED / 未实施**。规划收口 HEAD `c19749a`。依赖 [A](P4-UI-SHELL-A.md)；人物新建已有[用户指定同会话复核与修复](../reports/P4-PERSON-INBOX-CREATE-A-review-current-session.md)，不重开旧复核。执行时重新读取最新人物卡/报告及用户试用反馈。

推荐 GPT-6.1 Sol / High；限定 A 级 UI 小改，独立 DS-H 覆盖全部受影响入口与原写协议的直接调用链。不是人脸数据语义改造。

> **2026-10-11 并行范围协调：** 新立 [P4-PERSON-UX-UNIFY-A](P4-PERSON-UX-UNIFY-A.md)（READY，尚未实施）专门整合人物归属更正、批量拆分、重检测映射审核、历史撤销与旧高级 UI 退场。本卡 D 仍以原计划的**视觉、导航层级与已有功能展示**为主；两张卡不得并行修改相同人物前端文件。下文「旧高级工具保留」仅作为**替代功能尚未实现或验收前的过渡保护**，而非禁止未来退场。先完成并确认替代能力后，再依据 UNIFY-A 移除重复入口；缺少替代能力的工具必须保留，不得静默删功能。本卡原 API 零改动和 A 级测试范围只约束 D 本身，不能削弱 UNIFY-A 的只读扩展与数据保护要求。

## 视觉参考（执行前必须看图）

打开[人物页模拟图](../mockups/P4-UI-PERSONAL-people.png)、[HTML草图人物区域](../mockups/P4-UI-PERSONAL.html#people)与[参考规则](../mockups/README.md)。实际看图后实施头像/名字/照片数层级、浏览与整理分区、合集和更多工具入口。示意头像换真实裁剪，人数/照片数用原服务事实。报告保存产品合成截图对照；单脸身份、选择、人工状态、原撤销优先，不为了画面精简关键反馈。

## 现状盘点（备份参考）

[已有页面信息盘点](../plans/P4-UI-INFORMATION-CHECKLIST.md)供查阅和设计取舍，不要求119项全部保留、逐项汇报或逐项验收。用户此前明确强调必须增加/保留的项目，按盘点第0节的需求证据落实；其余展示可按方便、清新、少改核心接口的目标精简或调整。图中未展示不自动等于必须新增。此次纠正优先于本卡对参考信息作出的泛化“全部保留”解读；业务/人工状态及接口正确性边界不变。

## 读取与范围

AGENTS → status → 本卡 → [总计划](../plans/P4-UI-PERSONAL.md)§4人物/6/8；架构§8.3.3～8.3.5、§10.1～10.3；策略§2.2～2.4、§3.2～3.4、§11只核对不触碰边界。允许 `web/persons.html/persons.css/persons-albums.js/persons.js` 的局部展示、必要 UI 测试，不整理整个巨大 JS。

## 唯一闭环

- 头像和人物照片优先；人物/整理一下分明；已有合集为局部入口，已忽略/恢复仍可找到。
- 取名与看照片常用，封面/合并/拆分/识别维护按次级整理；不隐藏重要纠错入口，不新增强制确认弹窗。
- 改按钮位置保留 ID/监听处理函数；只可加薄展开/菜单逻辑，不能复制或改请求字段、资格判断、批次选择、undo 调用。
- 多页人物/人脸选择、候选组分页、clean/all质量入口与准确分类计数保持；展开/收起不能刷新首屏清掉选择。
- 无名字不要求先命名才能看图；新建仍仅一张脸且永久不可撤销，原操作快捷撤销仍按既有类型提供。
- 高级旧工具保留在更多整理；旧锚点若位于折叠区，点击后先展开并定位，不能导航到不可见内容。

## 验证

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
uv run --no-sync pytest -q tests/unit/test_person_hide_selection_ui.py tests/unit/test_person_inbox_hide_ui.py tests/unit/test_person_inbox_create_ui.py tests/unit/test_p4_person_collection_ui.py tests/unit/test_person_count_ui.py tests/unit/test_person_quality_ui.py tests/unit/test_person_preview_navigation_ui.py --basetemp=.pytest-tmp/P4-UI-PEOPLE-D --junitxml=test-results/P4-UI-PEOPLE-D.xml
node --check src/photo_search/web/persons-albums.js
node --check src/photo_search/web/persons.js
git diff --check
```

按实际改变再补封面/高级锚点等受影响测试；若新增 Python 测试跑对应 Ruff。合成浏览器至少两页人物选择/取消与一次忽略恢复、待整理单脸对话框、人物/合集预览返回；只操作隔离记录，不访问真实图库/模型。

独立 DS-H 核对完整 DOM/事件→原 API 链路、人工动作含义、请求参数/CSRF/失败/不可撤销显示及 JUnit；不能只验人物首页外观。若改资格/人工状态/事务/revision/undo，停止扩展并另立 D 级业务卡按原全受影响面主审与高风险聚焦规则；无高风险未决不默认 Sol。

报告 `docs/reports/P4-UI-PEOPLE-D.md`；未完成的必测保持 PENDING，不追认前人物卡现场通过。完成本卡停止；下一候选 [E](P4-UI-PLACES-E.md) 或个人试用。
