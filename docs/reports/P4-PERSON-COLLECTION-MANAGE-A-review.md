# P4-PERSON-COLLECTION-MANAGE-A 当前会话受影响面审查

日期：2026-10-10。结论：**ENGINEERING REVIEW PASS（按用户要求在实施会话内复核；非独立 DS-H）**。用户报告已在浏览器测试功能并确认 OK；未在本会话连接运行中的应用或图库。

## 审查覆盖与结论

- **API 与过滤 SQL：通过。** `/persons/browse` 只增加可选 `collection_state=all|none`。默认 `all` 的 cursor binding 未改变；`none` 在 total、person_id/照片数排序与 LIMIT 前排除 canonical 成员。现有 `kind/q/visibility/quality_view` 条件保留。`none` cursor 绑定 filter revision 与集合 `(collection_id, instance_token, revision)` 摘要，读取前后检查版本变化并返回 409，防止多页拼接不同身份/成员状态。
- **canonical 与标签投影：通过。** `none` 复用递归 canonical CTE；标签投影从当前页人物沿 `merged_into` 反向遍历来源，只在页内一条批量 SQL 投影合集，按 collection_id 排序并去重。合并后目标只出现一次；撤销后来源按原成员锚点恢复。响应不包含内部 token/revision。
- **前端状态与回归：通过。** 筛选进入查询 binding，列表序号保护晚到响应；切换时清理游标/选择但保留批量模式。成功加入后重读已加载页。审查发现加入、改名、删除合集此前缺少跨标签刷新通知；现已在成功写入后刷新人物列表并调用 `notifyPeopleChanged`，失败仍保持旧列表。
- **DOM 展示：通过。** 标签为只读 span，没有在人物卡 button 中嵌套交互控件；空集合无占位标签，长名字可换行并带 title/aria-label。筛选状态详情返回后保留。
- **边界：通过。** 未改 schema/migration、合集成员写协议、人物身份/人工状态/undo、识别或普通 `/persons` 查询。没有证据命中 V3.1 §2.2 DS-MAX 升级条件。

## 验证证据

按用户要求仅执行任务卡相关验证。最终工作树的定向 pytest：**27 passed / 0 failures / 0 errors / 0 skipped**；JUnit：`test-results/P4-PERSON-COLLECTION-MANAGE-A.xml`。Ruff、Python 编译、`node --check src/photo_search/web/persons-albums.js` 与 `git diff --check` 均通过。

定向集成/DOM 覆盖 85 人、40 人分页、过滤和组合条件、合集标签/改名、canonical 合并/撤销、成员添加/移出/删除后的 stale cursor、批量加入后筛选退出、加入/改名/删除后的通知与刷新、筛选变化清选择、详情返回状态。用户另行确认浏览器功能测试 OK；该项为用户提供的现场结果，本会话未独立重跑浏览器。

## 审查边界

本次遵照用户“在这里审查”指示，在实现会话内完成代码审查和修复，不切换模型；所以结论是当前会话工程审查通过，不称独立 DS-H。用户已确认浏览器功能 OK。未访问 PhotoSearchData、未操作运行中的服务，未运行全仓、GPU、模型、打包或浏览器矩阵；未提交/推送。
