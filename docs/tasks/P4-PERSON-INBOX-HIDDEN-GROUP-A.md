# P4-PERSON-INBOX-HIDDEN-GROUP-A — 已忽略人脸按建议人物聚合展示

日期：2026-10-09。状态：**PASS / 定向验证与用户试用确认**。实际基线：31d3690b3d4fbf9a558335a1bb947a00c52df49e。报告：[P4-PERSON-INBOX-HIDDEN-GROUP-A](../reports/P4-PERSON-INBOX-HIDDEN-GROUP-A.md)。

架构/策略对齐章节：V3.0 §8.3.1、§8.3.4、§10.2；V3.1 §2.4、§3.2。

## 1. 用户目标与边界

在「整理一下 → 已忽略的人脸」中，把当前有相同有效 SUGGESTED 候选人物的人脸折叠为可展开合集，降低页面杂乱。只复用当前数据库中的建议关系，不计算脸与脸之间的向量相似度、不跑模型、不调阈值。

- 每张隐藏脸最多属于一个候选展示组；按当前有效建议 confidence DESC、person_id ASC 取唯一候选，并排除合并人物及该 face/person 的 active exclusion。
- 候选需至少涉及 2 张不同照片才显示为组；photo_count 使用 COUNT(DISTINCT asset_id)。同图多脸可在展开后逐张看到。
- 不足 2 张照片、没有有效建议或建议失效的人脸仍显示为独立脸卡。
- 组标题只负责展开/折叠。忽略标记始终是每个 face_id 独立数据；只能展开后逐脸选择恢复，不提供整组恢复或按 person_id 恢复。
- 隐藏列表仍须包含所有带 face_inbox_hidden 标记的现存脸，即便该脸之后已分配人物或不再符合普通 inbox 资格；不因展示分组遗漏它们。
- 仅增加只读 API 和展示逻辑。保留旧 GET /faces/inbox/hidden 兼容，不改 schema、标记、建议/归属/人工锁、照片详情或普通 inbox 行为。

## 2. 实现范围

直接读取和调整以下文件：

- src/photo_search/persons/albums.py
- src/photo_search/api/routes.py
- src/photo_search/web/persons-albums.js
- 必要时 src/photo_search/web/persons.html 与 persons.css
- tests/integration/test_p4_person_albums.py
- tests/unit/test_person_inbox_hide_ui.py

为隐藏人脸列表提供全局分页的候选组/单脸卡片页，以及按 candidate group key 分页读取展开人脸的只读 API。先在完整隐藏集合中确定候选归组、去重照片数和稳定排序，再对展示卡 LIMIT；组内按 face_id 分页。复用 _face_view 与受保护缩略图，不一次性加载整组，不逐脸额外查询候选关系，不返回分数、向量或路径。

人物页隐藏列表使用新分组 API。组展开及再看一些继续按 cursor 分页，逐脸选择 Set、预览和恢复操作保持现有 face_id 行为。恢复后刷新普通 inbox 与已忽略列表；已展开且仍存在的合集尽量恢复展开状态。普通 GET /faces/inbox/hidden 旧接口格式维持不变。

## 3. 验收与执行

本项是低风险、只读的人脸列表展示增强，不写身份或人工状态。按本卡做两组聚焦测试：

1. 后端定向集成：合成同图多脸加跨照片候选、单照片候选、无建议脸；检查只对同候选且达到 2 张不同照片的人脸聚合、photo_count 去重、其它脸保持单脸、组内分页集合精确、跨页展示卡有序且只读。
2. Node DOM：已忽略候选组跨页加载、组标题不选择整组、脸选择和预览互不影响、恢复请求只包含明确选中的 face_id、隐藏列表刷新时选择与游标按现有行为处理。

运行上述定向 pytest 并输出 JUnit XML、修改面 Ruff、node --check src/photo_search/web/persons-albums.js、git diff --check。遵守用户“简单测试”的范围：不跑全仓/规模/GPU/模型/打包/浏览器矩阵，不访问 PhotoSearchData 或现有服务。

完成后更新报告、当前任务卡状态和 docs/development-status.md。真实浏览器查看分组仍由用户试用；不提交、不推送、不继续生成无关优化任务。
