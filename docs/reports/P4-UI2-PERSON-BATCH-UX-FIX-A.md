# P4-UI2-PERSON-BATCH-UX-FIX-A 执行报告

- 日期：2026-10-14
- 结论：**代码层 SCOPED PASS；整卡 ACCEPTANCE PARTIAL**（浏览器 Shift 与合集数据场景仍待验）
- 范围：人物、人脸和合集批量交互；未改服务端 API、数据库或数据语义。

## 已实施

- 未识别人脸和已忽略人脸现在各有独立的整理模式、选集、清空和范围锚点。人脸图、卡片空白和 checkbox 在管理模式下只切换一次；Shift 按展开且已加载的实际可见人脸顺序跨候选组加选。折叠候选组会清除对应分区共享锚点。离开管理模式会还原查看与确认控件。
- 合集人物默认浏览；进入「管理合集人物」后才显示 checkbox 和成员操作。反选交换当前已加载成员的选中状态并保留页外选集；旧合集实例、忙碌写入及结果未知状态受到保护。改名和删除按钮直接显示，沿用原有按钮 ID/处理逻辑。
- Escape 优先交给原生 dialog，并继续保护文本输入；checkbox 焦点可退出批量管理。
- 更新人物页面 JS/CSS cache-bust 和对应静态断言。未创建新的后端协议或业务写操作。

## 独立 LUNA High 复核

首次复核报告两项 P1：头像点击冒泡导致双切换，以及折叠组未清除实际共用的范围锚点。两项已修复并补上断言。repair-only 回看又指出清除整个分区锚点过宽：折叠无关组会误删仍可见锚点。实现现改为仅当锚点人脸位于正在折叠的组内才清除；定向测试同时检查无关组保留锚点、锚点组折叠后清除。最终 LUNA High 回看确认修复成立、无新可执行 finding；该复核是指定范围检查，不是 DS-H。真实浏览器验证仍按下节待验。

## 检查结果

以下命令按任务卡范围执行：

```powershell
node --check src/photo_search/web/persons.js
node --check src/photo_search/web/persons-albums.js
node --check tests/unit/person_ux_simplify_harness.cjs
git diff --check
$env:UV_CACHE_DIR='.uv-cache'
uv run --no-sync pytest -q tests/unit/test_p4_ui2_batch_select_unify.py tests/unit/test_person_ux_simplify_ui.py --basetemp=.pytest-tmp/P4-UI2-PERSON-BATCH-UX-FIX-A --junitxml=test-results/P4-UI2-PERSON-BATCH-UX-FIX-A.xml --tb=short
```

最终定向 pytest：**14 passed，0 failed/errors/skipped**；JUnit：[test-results/P4-UI2-PERSON-BATCH-UX-FIX-A.xml](../../test-results/P4-UI2-PERSON-BATCH-UX-FIX-A.xml)。三项 JS 语法检查及 `git diff --check` 通过。未跑全仓测试、性能、模型/GPU、打包或发布检查。

## 浏览器证据与待验

- 使用 Codex 内置浏览器打开 `127.0.0.1` 的真实应用，服务数据根目录为项目内隔离合成 fixture `.tmp/p4-photo-view-browser/people-env2/PhotoSearchData`。从首页进入 `/persons`；确认默认人脸卡显示常规动作且无 checkbox，进入「批量整理」后 checkbox 可见。仅进行了选择交互，没有提交业务写操作。
- 浏览器交互工具未将 Shift 修饰键传到页面：尝试 Shift 点击和 Shift+Space，观察到均按普通单项动作处理。因此记为 **`SHIFT_BROWSER_PENDING_TOOL_LIMIT`**；真实浏览器中的 Shift 连选尚未通过。该结果不由 DOM/JUnit 模拟替代。
- 此合成数据有人脸卡，但没有展开候选组可用于跨组选择，也没有合集成员数据；合集浏览/管理/改名/删除路线记为 **`COLLECTION_BROWSER_FIXTURE_MISSING`**。未伪造该场景证据。浏览器截图未导出。
- 浏览器状态：基础页面/模式切换已观察；交互验收 **PARTIAL**；真实 Shift、合集成员操作、截图和视觉检查 **PENDING**。窄视口不作为 200% 缩放证据。

未访问或修改真实照片库。报告未将当前结果宣称为浏览器或发布 PASS。
