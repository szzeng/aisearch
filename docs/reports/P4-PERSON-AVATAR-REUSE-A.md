# P4-PERSON-AVATAR-REUSE-A 执行报告

日期：2026-10-10。实现基线：`f140e82`（任务卡提交）；工作区变更尚未提交。

## 结果

- **A 完成**：成功加载人物列表后记录加载时间和查询绑定；60 秒内、绑定未变且没有待刷新状态时，人物相册返回直接恢复原列表 DOM。焦点/`pageshow` 检查会在过期时复核；本页写入通知、跨标签变化、显式刷新及条件变化优先触发读取。相同条件的并发列表读取合并；刷新会使刷新前的在途结果失效。重复点击当前人物/整理视图不再清除选择、查询或重载。
- **B 完成**：刷新全部已加载页成功后按 `person_id` 调和人物卡片，复用卡片和相同头像身份的 `<img>`；文本、照片数、合集、排序和最新点击对象随权威结果更新。URL/头像身份变化会替换头像。相同头像的成功刷新重设原 URL，使现有媒体缓存可按 ETag/304 协议复核。新增分页保留去重及 eager/lazy 优先级。
- **C 后置**：没有新增整理视图的独立会话缓存。现有整理页 DOM 仍由原来的刷新/分组恢复逻辑处理。
- 没有改媒体 HTTP 缓存协议、Python 接口或数据库；未访问真实 `PhotoSearchData`、真实图库或用户服务。

## 验收

按任务卡运行：

```powershell
node --check src/photo_search/web/persons-albums.js
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_person_loaded_pages_ui.py tests/unit/test_person_count_ui.py --junitxml=test-results/P4-PERSON-AVATAR-REUSE-A.xml --basetemp=.pytest-tmp
git diff --check
```

结果：Node 语法检查通过；定向 JUnit **2 passed, 0 failed, 0 errors, 0 skipped**；`git diff --check` 通过。JUnit 文件：[P4-PERSON-AVATAR-REUSE-A.xml](../../test-results/P4-PERSON-AVATAR-REUSE-A.xml)。Node 桩覆盖两次无变化返回不额外请求 `/persons/browse`、同模式点击不重载、稳定头像 `<img>` 对象复用、资料/排序和点击对象更新、头像身份变化、过期复核、分页/滚动回放、写入后的新游标、失败时保留旧列表及迟到请求保护。

## 2026-10-11 回归修复

用户反馈人物列表完全不显示。根因是新增代码对真实 DOM 的 `HTMLCollection` 直接调用数组方法 `.find()`，首个人物卡片渲染即抛异常；此前测试桩将 `children` 建模为普通数组，未捕获该浏览器 API 差异。已改为 `Array.from(children).find(...)`，并将 UI 测试桩的 `children` 改为不提供 `.find()` 的 HTMLCollection 模拟。同时更新 `persons.html` 中脚本版本参数，避免浏览器继续使用旧缓存脚本；测试会检查该版本参数。重新执行上面的任务卡命令：Node 检查通过，JUnit **2/0/0/0**，`git diff --check` 通过。

## 未验证

Node 桩模拟了 `HTMLCollection` 的无 `.find()` 行为，但没有真实浏览器图片加载器；未实测相同 URL 的媒体请求是否实际收到 304，也未观察用户图库上的 Network 面板、头像占位或主观加载变化。冷缓存性能不在本卡承诺内。C 未实施。
