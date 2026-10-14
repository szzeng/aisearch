# P4-UI2-SCROLL-LOAD-B 实施与验证报告

日期：2026-10-14
状态：**IMPLEMENTED / ACCEPTANCE PARTIAL / LUNA HIGH REVIEW COMPLETE**

## 实施范围

- 人物、合集目录、合集成员列表分别使用独立的 `PhotoScrollPager`、sentinel、cursor/has-more 状态。人物与合集目录面板隐藏时不加载；合集成员只在成员 tab 可见且无写入、stale 或移除状态时续页。
- 保留人物/合集/成员服务端每页 40 条和普通照片网格每页 60 条。只在现存普通网格入口（封面选择、局部搜索或时间轴 helper 不存在）启用照片 pager；默认人物/合集时间轴继续使用 `PhotoTimeline`。
- 按权威 ID 去重，页响应必须同时有 `has_more` 和游标才续载。每域请求串行；请求失败后保留游标并显示 pager 重试，合集成员首屏失败也有独立“重试加载合集人物”按钮。合集实例/revision 失效时停止续页并清空旧成员/选集，要求刷新。
- 人物列表同视图刷新最多重放 3 页；超过该范围时返回可信首屏并提示继续滚动，不再重读全部历史页。新增页不会自动勾选；原 Shift/全选/反选仍只作用已加载项目。
- 页面只更新人物样式/资源版本，并加上人物、成员、照片网格三个 sentinel。没有恢复已移除的局部搜索入口，也没有改变后端协议。

## 定向验证

最终 JUnit：[`P4-UI2-SCROLL-LOAD-B.xml`](../../test-results/P4-UI2-SCROLL-LOAD-B.xml)，**24 passed / 0 failed / 0 errors / 0 skipped**。

```powershell
$env:UV_CACHE_DIR='.uv-cache'
$env:PYTHONPATH='.'
uv run --no-sync pytest -q `
  tests/unit/test_person_loaded_pages_ui.py `
  tests/unit/test_p4_person_collection_ui.py `
  tests/unit/test_person_hide_selection_ui.py `
  tests/unit/test_p4_ui2_scroll_load_a.py `
  tests/unit/test_p4_ui_timeline_core.py `
  --basetemp=.pytest-tmp/P4-UI2-SCROLL-LOAD-B `
  --junitxml=test-results/P4-UI2-SCROLL-LOAD-B.xml --tb=short
node --check src/photo_search/web/persons-albums.js
git diff --check
```

行为覆盖包括深页刷新最多读取三页、合集卡/options 去重且保留当前选择、成员 revision stale 清除旧列表和选择、成员续页单请求在途与 loaded-only Shift、首屏失败显示重试并成功恢复、普通照片网格 60 条分页/去重/末页停止。时间轴用既有定向 UI 回归；没有修改时间轴实现。最终 Node 语法检查与 `git diff --check` 通过。

## 产品浏览器（隔离合成数据）

- 应用绑定 `127.0.0.1:18842`，在 Codex 内置浏览器通过启动器新生成的一次性会话链接进入；浏览器交换成功后 API 正常访问。没有复用 session 链接或访问真实 `PhotoSearchData`。
- 使用现有 87 张合成资产的隔离副本，在 `.tmp/P4-UI2-SCROLL-LOAD-B/PhotoSearchData` 仅增加 125 人物、95 合集及 85 个合集成员元数据。
- 人物目录可见 125 人；实际滚动后人数由首批 40 增至 80，再加载到 125，姓名无重复。按 40 条页上限，完成四页（40/40/40/5）。批量模式下新加载项目默认未选中；Shift 操作后 AX 显示 121 人已选，覆盖当前已加载 DOM 范围。
- 展开合集目录后可见 95 个不同合集（40/40/15）；进入 `Synthetic Collection 000` 的人物 tab 后显示 85 人（40/40/5）。浏览器实走了人物及合集成员的多批次续载；合集照片 tab 仍进入现有 `PhotoTimeline`。封面选择进入普通照片网格，但所选人物仅有 1 张照片，真实浏览器未产生第二页。
- `BROWSER_INTERACTION=PARTIALLY_VERIFIED`：已验证人物滚动、Shift 与合集成员多批加载；未在浏览器单独走查未命名/隐藏目录、关闭合集面板暂停、跨合集切换、续页失败按钮和第二页普通网格。`BROWSER_SCREENSHOT=SCREENSHOT_PENDING`：当前 CUA 截图能力只返回内联图像，未能导出到任务卡要求的 artifacts 文件夹。

## 复核与剩余门禁

- 用户指定的 Luna High 子代理检查了完整变更面。它确认人物/合集身份与游标隔离、成员 revision 检查、单请求在途、去重、loaded-only Shift 和有限历史重放未见可执行问题。
- Luna High 指出首屏成员请求失败没有明显重试入口；已新增显式重试按钮及失败→重试→成功 DOM 行为用例，修复复核确认该项关闭。
- 仍有一项 P1：自动续页会让人物、合集、成员卡片 DOM 随加载页数持续增长，不能据当前证据证明 1 万人物目录有界。为避免在批量模式里回收卡片导致“已加载”选择范围静默变化，本任务没有加入不完整虚拟化。该 finding 保持开放，整卡为 `ACCEPTANCE PARTIAL`。
- 浏览器截图仍 pending；浏览器没有覆盖未命名/隐藏视图、关闭面板/切换合集的停载路径及普通网格第二页。`PERF_10K` / `PERF_300K` 未执行，不能声明通过。没有运行全仓、真实图库、GPU/AI、打包或发布检查。
