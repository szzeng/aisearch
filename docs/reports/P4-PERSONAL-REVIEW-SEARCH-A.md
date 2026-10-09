# P4-PERSONAL-REVIEW-SEARCH-A — 搜索与照片查看组审查记录

日期：2026-10-09。基线：`daf20f1`。状态：**ENGINEERING REVIEW PASS / SOL_NOT_REQUIRED；Qwen真实网页效果待验**。

## 审查方式与工作区

按个人审查约定复用已接受的 PHOTO-VIEW 独立报告和 W/W-Q/NL 现存证据；核对当前代码、ADR-0009、ADR-0011、主架构 §9.2/9.3/9.5 与 §10.1～10.3，以及 V3.1 §2.2/2.4/9.4。用户指定在当前会话复核即可，因此本会话完成了全受影响面工程复核；这不是 DS-H 模型会话，也不声称为 DS-H 独立审查。

开始时 HEAD 为 `daf20f1`。工作区已有用户文档修改和 `.tmp/`、`docs/reports/tmp-*` 未跟踪材料；均予以保留。新增代码差异仅在自然语言人物解析、搜索条件标签、该页面静态资源版本和直接回归测试；未改依赖、schema、检索协议或模型。

## 覆盖和状态

| 原任务 | 必要行为、代码与证据 | 当前结论与待验 |
|---|---|---|
| P4-02Y-W | `DEFAULT_TOP_K`、`MAX_TOP_K`、`MAX_SESSION_IDS` 均为 5000；`scan_truncated` 来源于真实扫描结果；高度匹配计数在候选冻结后计算。容量边界、5000 项强匹配边界、真实扫描截断区分和统计前后候选数/命中顺序对照均通过。 | 当前会话检查确认统计不参与过滤、融合、重排、分页或候选截断，工程复核通过。旧 30 万 smoke 仅引用原报告，不作为本轮性能验收。 |
| P4-02Y-W-Q | 当前 CLIP / Qwen squared-L2 门限分别为 1.3 / 1.5；未知向量空间隐藏统计。representation-specific policy 测试及排序不变集成对照通过。 | 工程复核通过。真实 Qwen 网页查询与效果未验证；当前合成浏览器进程没有查询编码器，明确返回 `QUERY_ENCODER_UNAVAILABLE`。不加载模型、不访问用户服务。 |
| P4-SEARCH-NL-UX-A | 检查了确定性日期/人物解析、canonical all/any、纯条件 keyset 路径、session 绑定、API 和搜索页状态。修复了不同长度人物名漏识别，以及新查询失败时旧条件标签仍能覆盖新内容词的问题；搜索脚本资源版本已更新。 | 工程复核通过。解析/标签回归、canonical all/any、强匹配统计与分页节点合计 JUnit 12/0/0/0。合成浏览器 65 张照片的 2026 年＋人物纯筛选命中 65 项，20 项分页从 1–20 翻到 21–40；内容词提交在此隔离进程因没有查询编码器被结构化拒绝，输入仍保留。真正语义检索及用户页面模型效果待验。 |
| P4-PHOTO-VIEW-A | 复用用户已接受的独立复核结论、六类入口及原报告的多路径/API证据，不重做全入口矩阵。当前页面版本下再次实际浏览：搜索缩略图打开共享详情、人物入口打开人物相册、浏览器 Back 回到详情、关闭后恢复搜索结果和日期/人物条件。 | 本组基础查看路径已在合成数据中复核。原 PHOTO-VIEW 报告保留其自身“IMPLEMENTED / VALIDATION PENDING”的未覆盖项；本组不将该报告或原任务追认为整卡 PASS。 |

## 直接修复

1. `parse_natural_intent` 之前从所有命中里只保留查询中的最长拼写，导致“Alice和Bo”丢失 Bo。现在只在候选拼写互相重叠时优先较长者，保留同一查询里不同长度、互不重叠的人名。
2. `submitSearch` 现在清空前一查询的人物/日期标签，避免新请求失败时旧标签回写旧 `intentPlan` 并丢掉新内容词。搜索脚本资源版本同步更新，确保浏览器加载修复版本。
3. 原排序回归测试仍 monkeypatch 已删除的单常量 `STRONG_MATCH_MAX_DISTANCE`。按当前服务接口改为注入 `strong_match_max_distance` 的合成值，保留实际 `hits` 顺序和候选数断言；未 skip 旧用例。
4. 最终复核又发现短人名只取首次匹配会漏掉其后独立出现的同名片段，例如“Anna和Ann”。现改为枚举全部出现位置，再仅抑制与更长名称实际重叠的片段；新增反向重叠回归并通过。

## 最终验证

- 命令：`uv run --no-sync pytest -q tests/unit/test_p4_search_nl_parse.py tests/unit/test_p4_search_nl_ui.py tests/integration/test_p4_search_nl_ux.py::test_named_person_natural_query_uses_canonical_all_and_any_scope tests/unit/test_p4_02y_w_search_capacity.py::test_strong_match_distance_boundary_only_adds_vector_counts tests/unit/test_p4_02y_w_search_capacity.py::test_full_top_k_strong_boundary_and_real_scan_truncation_are_distinct tests/unit/test_p4_embedding_provider.py::test_strong_match_uses_representation_specific_thresholds tests/integration/test_p2_search_endpoint.py::test_strong_match_annotation_does_not_change_search_order_or_candidate_count --basetemp=.pytest-tmp/p4-personal-review-search-20261009-review-r4 --junitxml=test-results/p4-personal-review-search-20261009-review-r4.xml`
- JUnit：**12 tests / 0 failures / 0 errors / 0 skipped**。
- 修改 Python 文件的 Ruff：通过。`node --check src/photo_search/web/search.js`：通过。`git diff --check`：通过。
- 浏览器 fixture 在 `.tmp/p4-personal-review-search-20261009-browser`，仅含程序生成照片和合成目录记录；未读取真实图库、启动用户服务、加载模型或访问 GPU。

## 未验证与交接

- **工程复核结论：PASS / SOL_NOT_REQUIRED。** 当前会话已按用户指定完成 W/W-Q/NL 完整受影响面复核，并审查本轮修复与直接证据；未切换模型，结论不声称为 DS-H 审查。
- Qwen真实网页效果、实际查询编码器下的搜索页面统计展示仍待用户在可用模型进程中确认；本轮保留清晰的 `QUERY_ENCODER_UNAVAILABLE` 证据，不把它说成无命中或 Qwen 通过。
- 未运行全量、30 万性能、GPU、打包；未访问真实照片库或私人服务。用户随后授权本地提交，本次不推送。无新增冻结决定，暂记 `SOL_NOT_REQUIRED`。
- 本组工程复核完成，可按任务卡交接 OCR 卡；这不代表启动 OCR，也不将人物/地点/照片查看的既有 PENDING 项追认为完成。
