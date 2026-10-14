# P4-UI2-PERSON-BATCH-UX-FIX-A 执行报告

- 日期：2026-10-14
- 结论：**代码层 SCOPED PASS；人脸 Shift 浏览器路线已通过；整卡 ACCEPTANCE PARTIAL**（合集成员浏览器场景仍待验）
- 范围：人物、人脸和合集批量交互；未改服务端 API、数据库或数据语义。

## 已实施

- 未识别人脸和已忽略人脸现在各有独立的整理模式、选集、清空和范围锚点。人脸图、卡片空白和 checkbox 在管理模式下只切换一次；Shift 按展开且已加载的实际可见人脸顺序跨候选组加选。折叠候选组会清除对应分区共享锚点。离开管理模式会还原查看与确认控件。
- 合集人物默认浏览；进入「管理合集人物」后才显示 checkbox 和成员操作。反选交换当前已加载成员的选中状态并保留页外选集；旧合集实例、忙碌写入及结果未知状态受到保护。改名和删除按钮直接显示，沿用原有按钮 ID/处理逻辑。
- Escape 优先交给原生 dialog，并继续保护文本输入；checkbox 焦点可退出批量管理。
- 更新人物页面 JS/CSS cache-bust 和对应静态断言。未创建新的后端协议或业务写操作。

## 本轮返工（2026-10-14）

用户复测后指出待整理人脸批量入口/Shift 忽略以及合集人物多选样式仍不符合预期。复核时发现 `loadedInboxCards()` 对浏览器原生 `HTMLCollection` 直接调用 `.forEach()`，真实页面一按 Shift 就抛 `TypeError`；现在递归使用 `Array.from(item.children || [])`。普通 checkbox 点击现由浏览器原生 `change` 事件读取最终 `checked` 状态；只有 Shift 范围和忙碌保护路径取消默认激活，并在浏览器完成取消回滚后延迟同步 checkbox，避免勾选框和 Set/计数不一致。合集 checkbox 使用同一时序。

合集成员卡现在与普通人物卡共享 `.pa-person-card` 根、相同头像/名字/照片数/合集标签槽/选择提示/checkbox 子项顺序，以及 `.pt-person-checkbox`、role、键盘与整卡选择行为。定向 harness 比较这套结构，并模拟 checkbox 原生预激活、取消回滚和延迟渲染；待整理跨两组的人脸选择、单项取消和忽略 API 的 `face_ids`/`ignore_state` 也有断言。

## 独立 LUNA High 复核

早期复核报告头像冒泡双切换、折叠组锚点清理范围过宽；此前均已修复。此次 LUNA High 复核发现 checkbox 被 `preventDefault()` 后同步勾选会被浏览器取消激活回滚；改由原生 `change` 处理普通单选，Shift 取消默认后延迟同步。repair-only 回看确认这一修复和 `HTMLCollection` 遍历修复均关闭 finding，无新增可执行问题。该复核是指定范围检查，不是 DS-H。

## 检查结果

以下命令按任务卡范围执行：

```powershell
node --check src/photo_search/web/persons-albums.js
node --check tests/unit/person_ux_simplify_harness.cjs
$env:UV_CACHE_DIR='.uv-cache'
uv run --no-sync pytest -q tests/unit/test_p4_ui2_batch_select_unify.py tests/unit/test_person_ux_simplify_ui.py tests/unit/test_person_loaded_pages_ui.py tests/unit/test_p4_person_collection_count_ui.py tests/unit/test_photo_view_info_ui.py::test_all_photo_pages_load_the_versioned_shared_viewer --basetemp=.pytest-tmp/P4-UI2-PERSON-BATCH-UX-FIX-A --junitxml=test-results/P4-UI2-PERSON-BATCH-UX-FIX-A.xml --tb=short
git diff --check
```

最终定向 pytest：**17 passed，0 failed/errors/skipped**；JUnit：[test-results/P4-UI2-PERSON-BATCH-UX-FIX-A.xml](../../test-results/P4-UI2-PERSON-BATCH-UX-FIX-A.xml)。两项 JS 语法检查及 `git diff --check` 通过。未跑全仓测试、性能、模型/GPU、打包或发布检查。

## 浏览器证据与待验

- 使用 Codex 内置浏览器打开本机 `127.0.0.1:18841`，隔离合成数据根为 `.tmp/p4-photo-view-browser/people-env2/PhotoSearchData`。进入「整理一下」后可见本区独立「批量整理」入口；首项点击后 Shift 点击第三项，真实 DOM 的前三张 checkbox 与 `data-selected` 均为 true，计数为 3；再点取消中间项后只剩两项，计数为 2。最后退出管理，未提交业务写入。
- 浏览器初次实点发现上述原生 `HTMLCollection.forEach` 异常；改为 query `p4-ui2-person-batch-ux-fix-a-5` 后同一路线成功。跨两个展开候选组的结构由定向 harness 检查；当前 fixture 展示的是未分组人脸，所以浏览器跨候选组路线仍未验。
- 当前隔离合成库无合集成员。尝试用「新建合集」准备临时成员 fixture 时，内置浏览器不支持该页面 `prompt()`，未创建数据；合集默认浏览/管理/改名/删除的浏览器路线记为 **`COLLECTION_BROWSER_FIXTURE_MISSING`**。代码层结构断言通过，但不替代真实页面验收。
- 未保存截图。浏览器状态：待整理批量入口和 Shift 连选/取消 **BROWSER_VERIFIED**；候选组跨组与合集成员 **PENDING**；截图/目视主题检查 **PENDING**。窄视口不作为 200% 缩放证据。

未访问或修改真实照片库。报告未将当前结果宣称为浏览器或发布 PASS。
