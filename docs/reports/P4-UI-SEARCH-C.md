# P4-UI-SEARCH-C 执行报告

日期：2026-10-13
状态：**IMPLEMENTED / 定向工程验证与 LUNA High 复核通过 / 完整视觉验收待完成**

## 执行范围与决策

按用户明确许可，在前置 P4-UI-SHELL-A 仍为 **VALIDATION PENDING** 时先执行 C；A 的状态与待验项目未改变。没有变更搜索后端、解析/召回/排序、会话或字段协议。

## 变更

- 五个页面统一在页头保留一个全局文字搜索框，删除侧栏重复的“搜索”入口；/search 复用原 #search-form、#query-text 与既有提交流程，高级筛选保留在内容区。
- 全局输入以 GET q 跨页进入结果页。初始化完成后只消费一次；显式新 q 清除旧快照并优先执行，消费后以同源 history.replaceState 移除参数。空 q 只聚焦，不发搜索。
- 增加中文输入法 composition 期间 Enter 拦截；高级筛选清除保留全局查询词和已提交意图/地点范围。人物和地点目录局部输入分别标为“筛选人物”“筛选地名”；人物相册原有 pa-search-photos 保留。
- 更新本卡改动页面的缓存版本号；新增搜索入口行为/页面 DOM 定向测试，并修正受影响面测试中已过期的静态资源版本断言。

## 验证

- JUnit：test-results/P4-UI-SEARCH-C.xml，**44/44 通过，0 失败、0 错误、0 跳过**。覆盖 test_p4_search_nl_ui.py、test_search_place_nl_ui.py、test_search_person_album_link_ui.py、test_photo_view_info_ui.py 和新增 test_p4_ui_search_entry.py。
- node --check src/photo_search/web/search.js：通过。
- node --check src/photo_search/web/search-place-entry.js：通过。
- uv run --no-sync ruff check tests/unit/test_p4_ui_search_entry.py tests/unit/test_photo_view_info_ui.py：通过。
- git diff --check：通过。
- 隔离合成图库浏览器观察：检查首页、搜索、人物、地点、地图五页页头入口；首页提交中文查询后进入 /search，输入保留，日志中只有一次搜索请求；搜索页再次提交发出一次请求；返回首页未重复请求。缺少活动模型/图库时出现既有错误状态，故此浏览器观察只证明入口、保留输入和请求次数，不证明语义搜索效果。人物与地点页局部输入分别显示正确用途。
- 服务使用项目内临时数据 .tmp/P4-UI-SEARCH-C/data，无真实图库或照片被读取/修改；未下载模型。

## LUNA High 受影响面复核（2026-10-13）

用户手动切换到 GPT-6 Luna / High 后，在当前会话完成入口页面、表单和直接请求/快照链路复核。发现并修复 2 项：

1. 高级筛选区保留了名称模糊的第二个“搜索”按钮。改为明确的“应用高级筛选”，仍关联原 search-form，沿用唯一 submit listener；页头全局输入继续是唯一自然语言入口。
2. 清除空查询的筛选时未使旧请求代数失效，迟到响应可能重新填充结果。增加 requestGeneration 递增和 loading 清理，使旧响应被现有代数检查忽略。

修复后完整定向 JUnit **44/44/0/0**、Node 两项语法、Ruff 与 diff-check 均通过。记录为 **LUNA High 当前会话受影响面复核 PASS（2 项已修复）**，不是 DS-H PASS。该复核不替代剩余视觉验收。
## 未完成验收

- 浏览器仅在 1280×720 检查。实际 200% 缩放/窄屏焦点与布局、暗色视觉检查未完成。
- 浏览器画面曾用于本轮即时观察，但未保存项目内截图文件。任务卡指定的 demo.html 本地参考页也被浏览器安全策略拦截；本轮查看了其 README 和实际产品页面，参考页对照仍待补验。
- LUNA High 受影响面复核已完成，具体 finding 与修复见上节；未执行 DS-H。
- A 继续保持 **VALIDATION PENDING**；用户已明确允许 C 在 A 待验时先行，并未豁免 A 验收。
- 未运行全仓、真实图库、真实模型/GPU、20万规模、打包验收；这些不属于本卡此次定向验证。

## 交接

LUNA High 复核完成；补做可完成的视觉验收后再评估 C 是否验收。A 与 C 都达到各自验收条件后，下一候选为 ../tasks/P4-UI-TIMELINE-CORE-A.md。未自动开始下一卡。
