# P3-08C 阶段 B：映射修复边界 Sol 聚焦终审

- 状态：**PASS（C2 FINAL SHORT RE-REVIEW）**
- 日期：2026-09-22
- 路由：C2 / GPT-5.6 Sol / high；模型由用户在 Codex 中手动选择，本报告不声称自动切换
- 输入：`docs/reports/P3-08C-dsh-correction-round.md` §6 压缩交接包
- 基线：`c7fa68b` + P3-08B/P3-08C 的全部未提交改动
- 结论：修复轮点名的三条高风险边界全部通过；P3-08C 可以标记 PASS

## 1. 聚焦结论

| 边界 | 结论 | 核对结果 |
|---|---|---|
| 真实 HTTP 的逐码闭集 | **通过** | `limit=999`、`limit=many`、`state=decided`、坏 `old_face_id`、外来游标、未知 group、`FACE_MAPPING_STALE` 与 `FACE_NOT_FOUND` 的真实响应均落在 ADR-0010 §8.2 的严格 `oneOf`；没有 `errors`、`field`、`maximum` 或跨码键 |
| 保持/释放/队列与 §10 顺序修复 | **通过** | 临时保持仍在自动关联写事务内与人工锁/活动排除同一时点读取；confirm/reject 释放后重排队，`undo → OPEN` 恢复保持；merge/undo 仍先整体清除影响集合内的有效链接，再按 before 快照前向写回 |
| `STALE → SUGGESTED` 与 `OPEN` 边界 | **通过** | 复活只发生在 pair 存活、重新合格且 group 为 `OPEN` 时；映射 revision `+1` 且不自动确认。`CONFIRMED`/`REJECTED` group 不被重检测重开，重复决策仍拒绝 |

没有发现需要退回 DS-H 的阻塞项，也未命中 V3.1 §2.2 的 DS-MAX 条件。

## 2. `closure_details` 裁定

`ApiError.closure_details` 是最小且不放宽契约的修复：

- 默认值为 `False`，既有端点继续省略空的可选 `details`；
- 只有映射读取面的 `INVALID_CURSOR` 显式设为 `True`，用于输出冻结要求的必填空对象 `details={}`；
- 非空映射错误仍由各错误构造点给出逐码受控键，最终真实 HTTP 响应继续交 companion 校验器验证；
- `SESSION_REQUIRED`、`/persons`、`/faces`、`/places` 的既有信封没有因该机制改变。

该机制只解决“空对象必须存在”和“历史空对象默认省略”的信封差异，不改变错误码、HTTP 状态、允许键或
companion，因此没有把 ADR-0010 §8.2 放宽为自由对象。

## 3. 保持、队列与顺序修复核对

- `persons.mapping.hold_face_ids()` 只返回 `OPEN` group 的存活候选；`STALE` 行不进入保持集合。
- `face.index._reconcile_asset()` 在同一 `BEGIN IMMEDIATE` 内读取 late-control gate、人工锁、临时保持和活动排除；
  命中保持的脸只记录 `PROVISIONAL_HOLD`，不会写有效关联。
- 关联遍在仍有保持或晚到控制冲突时保留/upsert `face_association_queue`；只有没有待处理项时才删除队列行。
- reject/confirm 决策本身不替释放后的脸选择人物；后续正常关联遍处理重新排队的资产。
- `undo → OPEN` 后，候选重新进入保持集合且队列行存在；紧随其后的关联遍继续跳过该脸。
- `persons.operations` 的 §10 修复仍由 `_faces_needing_link_restore()` 收窄真实差异、
  `_clear_effective_links()` 先清除影响集合内全部 `CONFIRMED` 行，再通过 `forced_keys` 与
  `revision_hints` 按 before 快照恢复；目标/来源 ID 顺序、多个来源、未归类/排除和后编辑冲突回归均通过。

本轮修复文件没有改写 `face/index.py` 或 `persons/operations.py` 的上述语义；`persons/mapping.py` 的复活分支
经直接回归证明不会削弱保持或人工决定。

## 4. `STALE` 复活裁定

同一对脸重新出现时，当前检测已经证明 pair 存活且再次满足匹配资格。若 group 仍为 `OPEN`，继续把该行永久
留在 `STALE` 会让审核视图错误报告 `NO_CANDIDATE`，并使用户只能 reject、不能 confirm。因此恢复为
`SUGGESTED` 与 ADR-0010 §3.3 的“本次检测没有匹配脸”定义一致。

实现保留三道边界：

1. 仅现有 `STALE` 行进入复活分支；
2. group 必须仍为 `OPEN`；
3. 只写 `SUGGESTED`、刷新 evidence、清零 `inherits_person` 并把 revision `+1`，不写 control/link/exclusion，
   不推进 `catalog_filter_revision`，也不自动确认。

已决策 group 的行保持 `STALE`，group 状态保持 `CONFIRMED`/`REJECTED`，候选数组为空，再次提交仍返回
`FACE_MAPPING_CONFLICT/review_already_decided`。该分支与 ADR-0010 §7 的人工决定不可被重检测改写一致。

## 5. 独立复跑证据

阶段 B 只复跑修复轮 §6 点名的直接边界，没有重复阶段 A 的全量工程主审：

```powershell
$env:UV_CACHE_DIR=(Resolve-Path '.uv-cache').Path
uv run --no-sync python -m pytest -q -p no:cacheprovider `
  tests/unit/test_p3_face_redetection_api_privacy.py::test_mapping_endpoints_are_closed_over_http `
  tests/integration/test_p3_face_redetection_hold.py `
  tests/integration/test_p3_face_redetection_decisions.py `
  tests/integration/test_p3_person_undo_ordering.py `
  tests/integration/test_p3_face_redetection_mapping.py::test_removed_candidate_becomes_stale `
  tests/integration/test_p3_face_redetection_mapping.py::test_a_decided_group_is_not_reopened_by_a_reappearing_pair `
  --basetemp=.pytest-tmp-p308c-sol-20260922a `
  --junitxml=test-results/p3-08c-sol-focused.xml
```

| 证据 | 结果 |
|---|---|
| `test-results/p3-08c-sol-focused.xml` | **22 tests / 0 failures / 0 errors / 0 skipped** |
| DS-H 修复轮专项（引用，未重跑） | `109 / 0 / 0 / 0` |
| DS-H 修复轮完整 unit/integration（引用，未重跑） | `1451 / 0 / 0 / 48`，48 项均为未安装 pinned 权重的环境跳过 |
| `git diff --check` | exit 0，clean |
| `uv lock --check` | exit 0，95 packages |

首次尝试使用 `.pytest-basetemp-p308c/sol-short-rereview` 时，22 个用例均在 fixture setup 阶段遇到既有的
`PermissionError [WinError 5]`；这不是产品测试失败。未修改 ACL，改用全新的项目内 basetemp 并经正常 Windows
权限路由后得到上述有效 JUnit。

夹具只使用合成数据与测试临时目录；未扫描、移动或修改真实照片库。

## 6. 未验证项与最终裁决

阶段 A §8 的范围裁定保持不变：30 万容量、多进程、真实权重升级/回退、`0014` 实测迁移耗时和 C 级打包均不
属于 P3-08C 的审查等级，也没有被写成 PASS。P3-08B 的记录式浏览器 smoke 仍有效；本修复轮没有改前端。

`BROW-002` 的 `/search` 人物条件输入仍未实现，因此“人物 any/all × 地点 AND”的页面级真实浏览器步骤没有
在 P3-08C 内补做。它不阻塞本审查卡，但必须作为独立的 **P3-08D** 收口，不能把 API 级等价证据改写成页面
PASS。

最终裁决：**P3-08C PASS（C2 FINAL SHORT RE-REVIEW）**。本任务没有新增生产修改；阶段 B 只生成本报告、
更新状态/任务卡，并生成 P3-08D 卡。
