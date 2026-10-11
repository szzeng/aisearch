# P4-PERSON-EMPTY-VISIBILITY-REVIEW-A 审查报告

**日期：** 2026-10-12
**状态：** LUNA / High 用户指定路由例外；原 P2 finding CLOSED（不是 DS-H）
**审查基线：** `0940fbcd8fea0010f60967f6063d065832aaec1d`
**审查卡：** [P4-PERSON-EMPTY-VISIBILITY-REVIEW-A](../tasks/P4-PERSON-EMPTY-VISIBILITY-REVIEW-A.md)

## 审查方式与范围

用户明确指定由 LUNA High 子代理审查。审查覆盖人物列表改动、调用链、`MEMBER_PREDICATE` / `photo_asset_sql`、任务卡要求的 3 个受影响人物列表测试文件、测试 XML 和报告的 Ruff/diff-check 证据。审查未修改文件、未提交、未访问真实图库。

## 首轮发现

首轮发现 **P2**：两个列表新加入的候选 `EXISTS` 只约束 ACTIVE 与 `MEMBER_PREDICATE`，没有应用普通照片资格。仅视频路径的有效资产可让人物卡出现，但相册照片计数为 0；这与空人物可见性规则和既有 `photo_asset_sql` 资格不一致。

## 修复与修复轮结论

- `persons/albums.py::people_page` 在普通与 unnamed/clean 候选条件中，对 `member_asset` 应用 `photo_asset_sql`。
- `persons/read.py::person_page` 对 `a` 应用相同资格。无额外 bind 参数；既有零 location 兼容保持。
- 新增合成回归：仅视频路径 ACTIVE 资产的人物在 `/persons` 与 `/persons/browse` 隐藏；同资产有照片与视频两种路径的人物仍出现，照片排序首项正确。
- 修复后 JUnit XML 核验为 **70/0/0/0**；Ruff 与 `git diff --check` 通过。修复轮审查核对了代码、测试和结果，没有重跑 pytest/Ruff。
- 原 P2 finding：**CLOSED**。修复轮没有新增 finding。

## 其余审查项与升级判断

有效成员条件确实排除 SUGGESTED、MANUAL_UNASSIGNED、活动排除和 superseded machine；列表在计数/排序/游标分页之前过滤；kind、clean、visibility、查询、合集条件和排序游标无其他直接回归。两入口一致，合并来源不重复列出；直接 ID canonical 解析与空来源合并到有照片目标的最小回归符合卡面要求。

没有未决身份、协议或数据安全语义。没有 V3.1 §2.2 DS-MAX 升级条件，也不需要 Sol 聚焦终审；记录 `SOL_NOT_REQUIRED`。此为用户指定的 LUNA / High 审查路由例外，**不是 DS-H PASS**。

## 边界

没有验证全仓测试、真实图库/PhotoSearchData、真实浏览器、GPU/模型、容量或打包运行。报告仅证明审查范围内的代码与定向证据闭环。
