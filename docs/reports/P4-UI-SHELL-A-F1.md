# P4-UI-SHELL-A-F1 执行报告

日期：2026-10-13。状态：**IMPLEMENTED / VISUAL VALIDATION PENDING**。

## 实施

- 公共 tokens 对齐 demo 亮/暗色；新增强调底色、危险色、语义圆角/间距/阴影/字体变量。根字号保持浏览器默认，正文用局部 14px 视觉层级。
- 共享外壳统一系统字体、链接选中/悬停/focus-visible、品牌符号、主/次导航文本图标及页头搜索样式；五页同步 CSS 缓存参数。HTML 保持既有导航文本、路由和访问能力门控。
- 搜索页增加 `body.search-page` 根作用域，重整查询面板、高级条件、提示状态、4:3 照片结果卡、命中说明与对话框；上传和覆盖率保持按需读取并降为次级视觉。原生可访问说明折叠区收纳长技术文案。
- 保留 `#search-form`、`#query-text`、高级筛选 submit 关联、所有结果/状态/分页 ID；未改 JS、API、搜索参数、排序、召回或会话。
- 更新 [公共视觉规范](../plans/P4-UI-VISUAL-TOKENS.md) 记录实际 CSS 选择器和待验差异。LUNA High 首轮复核发现浅色 `--fg-muted` 对比不足及导航伪元素图标语义问题，均已修正；复核关闭状态见下文。

## 定向验证

最终定向命令：

```powershell
$env:UV_CACHE_DIR = '.uv-cache'
uv run --no-sync pytest -q tests/unit/test_p4_ui_shell.py tests/unit/test_p4_ui_search_entry.py tests/unit/test_p4_search_nl_ui.py tests/unit/test_search_place_nl_ui.py --basetemp=.pytest-tmp/P4-UI-SHELL-A-F1 --junitxml=test-results/P4-UI-SHELL-A-F1.xml
uv run --no-sync ruff check tests/unit/test_p4_ui_shell.py
git diff --check
```

- JUnit：`test-results/P4-UI-SHELL-A-F1.xml`，**37 passed / 0 failed / 0 errors / 0 skipped**。
- 首次运行发现 `test_p4_ui_shell.py` 仍要求侧栏含 `/search`；这与 C 已落地的唯一页头搜索入口冲突。将测试改为检查每页共用页头搜索、无侧栏重复搜索，再跑后通过。此为测试契约同步，不改产品搜索行为。
- Ruff、`git diff --check` 均通过。JS 未修改，未跑 Node；未运行全仓、GPU、OCR、模型、20 万照片性能或打包验收。

## 隔离合成浏览器观察

- 建立 6 张脚本生成 JPEG，调用生产扫描器登记，使用诊断 index generation 创建纯合成 search profile。浏览器直接看见 6 个 FILTER_ONLY 结果、真实命中字段与语义/投影提示；高级筛选可展开，来源和人物等原条件仍可见。
- 人物、地点、地图、首页照片和 `/?view=sources` 均能加载共用导航/搜索。地点数据未伪造；地图空态符合零个带位置照片的合成库。
- 浏览器中文查询“海边”返回明确 `QUERY_ENCODER_UNAVAILABLE` 提示，文本保留；该隔离进程没有查询编码器，不以空结果冒充命中。内置浏览器宽约 374 CSS px；另用本机 Chrome 100% scale 保存了 1440/640 CSS viewport 截图，确认窄屏无横向溢出。
- 曾尝试运行旧 `search_smoke_fixture.py`，其地点归属步骤因当前目录不满足 active validated source directory 而中止。保留其已生成的隔离图片/扫描/向量世代，手动建立同一诊断 profile，未修改 fixture 或产品数据代码。
- 对照最终 `demo.html#search` 与模拟“海边”结果布局；demo 的合成图片/30 条结果仅作观感参考，没有搬入产品。

## 截图与待完成验收

Chrome 本机截图保存于 `.tmp/P4-UI-SHELL-A-F1/screenshots/`，并已目视复核：

- `photos-home-1440-light.png`：`/`，1440×1200 CSS viewport，浅色，隔离合成库已加载 6 张卡片。
- `search-desktop-1440-light.png`：`/search`，1440×1100，浅色，空查询初始态。
- `search-narrow-640-light.png`：`/search`，640×1100，浅色，高级筛选收起态。
- `search-desktop-1440-dark.png`：`/search`，1440×1100，暗色。
- `search-advanced-filter-only-1440-light.png`：`/search`，1440×1200 CSS viewport，浅色，高级筛选展开。

**工程定向测试：PASS**。**视觉：PENDING**：真实浏览器 200% 页面缩放未完成。尝试 Chrome zoom extension API 和浏览器快捷键后，有效 viewport/DPR 均未达到 200%；`search-desktop-zoom-attempt-not-200.png`、`search-desktop-zoom-key-no-change.png` 是失败诊断图，不是验收证据。内置浏览器曾观察到的 6 条 FILTER_ONLY 搜索结果未保存截图，搜索命中态截图仍待补。整卡不标 PASS。
- **独立 LUNA High 复核：首轮发现已处理，LUNA High 修正复核已关闭**；按用户指定路由，不记为 DS-H PASS。
- 因任务卡要求的精确视口截图、暗色和真实 200% 缩放证据仍待完成，整卡不标 PASS，不启动 CORE。本次未访问真实图库或真实 `PhotoSearchData`，未下载模型。


## 独立 LUNA High 复核与修正

- 首轮审查确认 DOM/搜索表单不变量、页面 CSS scope、五页缓存版本及定向 JUnit；提出一项 P2：浅色 `--fg-muted: #74847e` 对 `--bg` 为 3.68:1、对 `--panel` 为 3.93:1，普通次文字未达 4.5:1。
- 将浅色 muted 改为 `#5f7068`；计算对比度为 `#f6f8f7` 4.92:1、`#ffffff` 5.24:1、`#eaf3ed` 4.63:1。暗色 token 未改。
- 将五页导航图标从 CSS 生成内容改为 HTML `<span aria-hidden="true">`，明确装饰性且不进入可访问名称；五页 CSS 缓存引用同步升至 `p4-ui-shell-a-f1-5`。
- 原 LUNA High 复核者已确认对比度问题关闭、图标语义正确、缓存版本一致且无新问题。此路由不构成 DS-H。

- 修正后定向 JUnit：37 passed / 0 failed / 0 errors / 0 skipped；Ruff 通过。

## 首页焦点边框修复（2026-10-13）

- 用户截图中的首页列表外框来自路由切换时程序聚焦 `#main`，命中全局 `:focus-visible` 后给整页主内容画出的轮廓。为 `#main:focus-visible` 单独取消轮廓；按钮、链接、输入框和下拉框的全局焦点轮廓保留。
- 五个页面的共享 `styles.css` 缓存版本同步为 `p4-ui-shell-a-f1-6`。在 shell 定向测试中加入主区域轮廓豁免与通用焦点规则仍存在的回归断言。
- 验证：四个 F1 定向 pytest 模块 JUnit **37 passed / 0 failed / 0 errors / 0 skipped**；Ruff 和 `git diff --check` 通过。此修复未改 JS，未跑 Node。
- 该修复处理用户报告的默认首页焦点框；F1 原定真实 200% 浏览器缩放和搜索命中态截图仍未完成，因此整卡继续为 VISUAL VALIDATION PENDING。
