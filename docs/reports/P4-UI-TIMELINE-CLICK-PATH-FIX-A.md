# P4-UI-TIMELINE-CLICK-PATH-FIX-A 执行报告

日期：2026-10-14
结果：实现完成；任务卡最小验收通过。
范围：照片首页时间轴卡片点击、文件名/目录显示、详情关闭与 Escape 返回。

## 修改

- `photo-timeline.js` 现在会在自定义 `renderAsset()` 返回后统一绑定 `adapter.onOpen`，让首页卡片沿用已有 `showDetail(asset_id)`。内层按钮、链接和表单控件不触发卡片打开；默认卡片仍只绑定一次。地点相册未传 `onOpen`，其内部预览按钮路径保持原样。
- `timeline.py` 在当前页 SQL 中选择每个资产的一个文件实例，沿用受控文件 API 的稳定顺序：`PRESENT` 优先，其次 `location_id` 升序。通过该实例的 `relative_path` 和 `source_roots.root_path` 生成 `filename` 与带结尾分隔符的绝对 `directory_path`。无位置时返回空字符串；没有逐图详情请求，也没有修改游标、排序、时间、revision、分页大小或 schema。
- 首页卡片将文件名和目录作为纯文本两行显示，不再显示 `time_label`；CSS 为两行分别设置单行省略。完整路径由目录与文件名拼成 `title`。静态资源版本已更新，首页、人物和地点页面会加载共享时间轴脚本的新版本；原默认时间标签样式继续保留。
- 详情返回按钮和 Escape 共用同一返回逻辑；已打开的对话框优先处理 Escape。

## 验证

- JUnit：`33/0/0/0`，结果文件：[JUnit XML](P4-UI-TIMELINE-CLICK-PATH-FIX-A.junit.xml)。覆盖游标续页、存在多个位置时的 `PRESENT` 选择、无文件位置返回空元数据、首页自定义卡片只触发一次 `onOpen`、页面脚本版本、既有照片详情与人物/地点集成契约。
- `node --check`：`app.js`、`photo-timeline.js` 通过。
- `py_compile`：`api/timeline.py` 通过。
- `git diff --check`：通过。
- 本机产品浏览器使用 `%TEMP%` 下 6 张合成照片：时间轴显示原文件名及中文目录，目录行截断并可从卡片 `title` 取得完整路径；点击照片进入 `#/asset/6` 一次，返回按钮及 Escape 均回到原时间轴位置。详情数据仅在点击后按 `asset_id` 请求。首次浏览器检查发现子元素点击被冒泡过滤条件误拦，已修正并重测通过。
- 时间轴 API 的会话保护由相关集成测试确认；匿名请求仍返回 401。没有访问 `PhotoSearchData`、真实相册、模型或 GPU。

## 有限复查边界

- 人物/地点页面没有另行进行产品浏览器点击巡检；现有地点自定义卡片未提供共享 `onOpen`，其预览事件未受新增分发影响。相关集成/UI 契约测试通过。
- 暗色主题未作单独浏览器检查；新增文字沿用现有前景、弱化文字和面板主题变量。未进行大图库性能测试，任务卡明确不要求。
- 静态资源 cache-bust 已处理此次共享脚本更新；未改数据库、公共分享接口或照片原文件。
