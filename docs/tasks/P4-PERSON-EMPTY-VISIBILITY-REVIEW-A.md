# P4-PERSON-EMPTY-VISIBILITY-REVIEW-A — 空人物列表统一独立工程审查

**日期：** 2026-10-11
**状态：** COMPLETE / 原 P2 finding CLOSED
**实现基线：** 初始 `a66b0902379a31987805914de699fd746438464f`；首轮审查 HEAD 为 `0940fbcd8fea0010f60967f6063d065832aaec1d`。复核时先确认当前 HEAD、diff 和工作区。
**前置报告：** [P4-PERSON-EMPTY-VISIBILITY-A](../reports/P4-PERSON-EMPTY-VISIBILITY-A.md)
**审查路由：** 用户指定 LUNA / High 子代理审查；这是路由例外，不是 DS-H。用户已授权按此执行。

## 目标

独立核查空人物自动隐藏实现的完整受影响面，确认过滤定义、活动资产及普通照片资格、SQL 计数/排序/分页、另一人物列表入口、canonical/直接 ID 读取、合并保留及撤销测试与任务卡一致。首轮已发现视频专属资产资格缺口；本轮只复核原 finding 修复和新增最小 diff，不把实现者自检当成审查结论。

## 读取范围

- `AGENTS.md`、`docs/development-status.md`、本卡与 `docs/reports/P4-PERSON-EMPTY-VISIBILITY-A.md`。
- V3.1 §2.2、§2.3、§2.4 及 §3.2 的相关条款；只读所需范围，不通读架构全文。
- 完整任务 diff 与调用链：
  - `src/photo_search/persons/albums.py::people_page`
  - `src/photo_search/persons/read.py::person_page`、API 路由与 `persons/members.py::MEMBER_PREDICATE`
  - 对应测试 `test_p4_person_cleanup.py`、`test_p3_person_search_filter.py`、`test_p4_person_albums.py`
  - 报告中对应 JUnit XML 和最终命令结果。

## 必审检查

1. 所有 `kind` 在 COUNT、排序和分页前按活动资产及有效成员规则筛除空 canonical 人物；无效建议、MANUAL_UNASSIGNED、活动排除、失效资产不能使人物出现在列表中。
2. `quality_view=clean`、visibility、查询、合集过滤及 `person_id` / 照片排序游标没有意外语义变化；分页总数、游标、`has_more` 与返回行一致。
3. `/persons` 与 `/persons/browse` 的规则一致，merged source 不单列；直接人物详情、照片读取、历史/撤销所需身份解析仍可工作。
4. 空人物合并到有照片目标的反例确实核对了目标的照片未减少、旧 ID 解析和列表分区；不以隐藏掩盖任何归属异常。
5. 审核全部改动文件、直接调用链、diff、任务卡不变量和 67 项定向测试证据。发现代码缺陷时给出最小复现、严重级别和应修范围；不要自行扩大到 Schema、GC、合并算法或真实图库。
6. 首轮已判断没有 DS-MAX 或 Sol 触发条件；若修复轮新证据改变这一判断，再按 V3.1 §2.2 说明具体证据和必要下一阶段。

## 首轮结论与修复

- 首轮结论：FINDINGS，1 个 P2。两个列表 EXISTS 没有复用 `photo_asset_sql`，可能展示仅有视频路径的 ACTIVE 人物，而相册照片数为 0。
- 修复：两个列表资格 EXISTS 均使用对应资产别名的 `photo_asset_sql`；新增 video-only 隐藏、同 asset 同时有图片和视频 path 仍显示的合成回归。
- 修复后定向 JUnit：70/0/0/0；Ruff 与 diff-check 通过。复核需独立核对新 diff 和原 finding 是否关闭。

## 验收与交付

- 记录实际基线、审查过的文件/链路、测试证据和检查结论；只在发现证据缺口或影响面不符时补跑对应定向命令。
- 新建 `docs/reports/P4-PERSON-EMPTY-VISIBILITY-REVIEW-A.md`，明确首轮 finding 与修复轮 CLOSED / OPEN 结论，不外推全仓或产品运行验收。
- 更新 `docs/development-status.md` 与本卡状态。若需修复，把最小修复交回实现阶段；修复后复核新增 diff 和原 finding，不重复昂贵无关测试。
- 不接触真实图库，不提交/推送，不改不相关的 `geo/dataset.py` 用户修改。

审查已完成：原 P2 finding CLOSED；用户指定 LUNA / High 路由例外（不是 DS-H），无新增 finding；无 DS-MAX 或 Sol 触发条件。证据见 `docs/reports/P4-PERSON-EMPTY-VISIBILITY-REVIEW-A.md`。
