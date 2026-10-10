# P4-PERSON-COLLECTION-MANAGE-A-REVIEW — 人物合集标签与未加入筛选独立审查

日期：2026-10-10。状态：**SUPERSEDED / 用户要求在实施会话内完成审查**。结论记录于[当前会话审查报告](../reports/P4-PERSON-COLLECTION-MANAGE-A-review.md)。未切换模型，不声称独立 DS-H。

## 输入

按仓库顺序读取 `AGENTS.md`、`docs/development-status.md`、本卡、`P4-PERSON-COLLECTION-MANAGE-A.md`、`docs/reports/P4-PERSON-COLLECTION-MANAGE-A.md`。核对当前 Git HEAD、index/worktree 和实际 diff；本次实施基线为 `3b43b1c`。

## 审查范围

首次独立核对以下全部变更面及直接调用链：

- `src/photo_search/api/routes.py` 的 `/persons/browse` 参数与响应路径。
- `src/photo_search/persons/albums.py` 的过滤候选、total、两种排序/游标、成员版本检查及标签投影调用。
- `src/photo_search/persons/collections.py` 的 `(collection_id, instance_token, revision)` 摘要和当前页 canonical 标签投影。
- `src/photo_search/web/persons.html`、`persons.css`、`persons-albums.js` 的筛选绑定、卡片标签、状态保持、批量选择清理、请求序号和写后刷新。
- `tests/integration/test_p4_person_collection_manage.py`、`tests/unit/test_p4_person_collection_ui.py` 及报告所列已有定向回归、JUnit XML。

逐项核对任务卡 §2–§4：

1. 标签名称和 collection_id 去重/排序正确；查询只处理当前页及其 canonical 合并来源，长名称可换行且辅助文本可读，无嵌套交互按钮。
2. `none` 先于 count、照片数排序和分页应用；已命名/未命名/已忽略及 q、visibility、quality_view 组合均正确；待整理中无 `person_id` 的人脸不受此过滤影响。
3. 任意深度合并链、合并/撤销、同合集多锚点均按 canonical 语义处理；加入/移出/删除/实例变化后旧 `none` cursor 拒绝续页；默认 `all` 旧 cursor 仍可读取。
4. 同请求期间成员版本变化会拒绝混合结果；跨页过滤状态与成员版本绑定。检查 COUNT 与 data 读取的可见性假设，并确认未承诺 SQLite 不提供的跨请求快照。
5. UI 切换筛选时旧请求不落地、游标失效；已有选择被清除并提示，批量模式保留；成功加入后重新读取已加载页，失败不预先移除卡片；详情返回仍保留筛选。
6. 没有 schema/migration、成员写协议、身份/人工状态/撤销语义或非目标 API 变化；普通 `/persons` 与识别/搜索未被改动。

## 证据与复核动作

- 主要证据：`test-results/P4-PERSON-COLLECTION-MANAGE-A.xml`（实施报告记录命令与摘要），以及对应代码与测试 diff。
- 独立核对证据的命令、退出状态、HEAD 和工作树；按需复用已通过的定向测试，不重复跑全仓、GPU、模型、打包或浏览器矩阵。
- 若有缺口，补跑任务卡 §5 直接相关的隔离测试；不访问真实 `PhotoSearchData` 或运行中服务。
- 写入 `docs/reports/P4-PERSON-COLLECTION-MANAGE-A-review.md`，记录结论、逐项覆盖、最小反例、遗漏风险及是否命中 V3.1 §2.2 升级条件。DS-MAX 只在有证据命中条件时建议。

## 停止点

仅独立工程审查，不自动执行现场手测或下一个产品任务。审查通过后将开发状态更新为“ENGINEERING REVIEW PASS / 现场试用待完成”；不得据此标真实图库/总验收 PASS。用户页面手测仍按原任务卡 §5 进行。
