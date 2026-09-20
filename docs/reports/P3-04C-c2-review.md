# P3-04C — P3-04 独立 C2 审查报告

- 日期：2026-09-20
- 审查模型：C2 / GPT-5.6 Sol / high
- 审查基线：`032069c1843cb0c5ceb25103229f2dfe6a0ea51f`
- 被审实现提交：`980f41d`（P3-04B）
- 结论：**PASS（发现缺陷后完成最小修复，并重新通过 A + D + C）**
- 数据库：`schema_version = 12`；未新增或修改 migration

## 1. 范围与结论

本次独立复核 ADR-0007、机器可读 companion、0012 迁移、人物/脸级操作实现、自动关联门禁、API、
只读计数和 P3-04B 的测试证据。审查发现 5 类确凿实现缺陷；修复均是把既有实现收敛到冻结语义，
没有修改 ADR-0007、companion、0012、依赖、打包配置、前端或任何 P1/P2/OCR/place/search 协议。

修复后，52 个 P3-04 专项用例、6 个独立关键复现和 1116 个完整 unit/integration 用例全部通过；真实
CPU 夹具、onedir 重建和新 D 盘目录中的打包 smoke 也通过。因此 P3-04 可以关闭。

## 2. 发现与最小修复

### F1：操作记录没有持久化实际 filter revision

`_close_face_operation()` 计算了本次派生发布后的 `catalog_filter_revision`，但没有写回
`person_operations.catalog_filter_revision`。首次响应看似正确，幂等 replay 却会从操作记录读到 `0`。

修复：在同一事务关闭操作时持久化实际 revision，并新增“revision 已变化后 replay 仍返回原结果”的
API/生命周期断言。这是补齐 ADR-0007 已冻结的 replay envelope，不是新协议。

### F2：共享 undo replay 丢失脸级 contract envelope

脸级 undo 首次响应包含 `contract_version`，但共享 `operation_result()` 重放路径只构造人物操作形状，导致
同一个 request_id 的 replay 可能不再符合 companion 的 `additionalProperties: false` 响应契约。

修复：共享读取器按 operation family 保留脸级 `contract_version`，并新增脸级 undo replay 的完整响应断言。

### F3：恢复控制 bundle 时 revision 可能倒退

`control_bundle_differences()` 没有比较 assignment/link revision；恢复已删除的 link 时，`_establish_link()`
又可能从 revision 1 重新开始。因此补偿撤销虽然产生新操作，权威 link revision 却可能不满足向前递增。

修复：把 assignment revision 纳入差异判断；恢复 link 时以记录快照与当前行的最大 revision 为 floor，
新 revision 必须高于它。新增 link revision floor 与 assignment revision 冲突用例。没有改变快照语义。

### F4：活动排除被误当成整脸人工锁

自动关联复用了 `human_controlled_face_ids()`；该函数把“存在任一活动排除”也视为整脸锁定，导致
`AUTO_ALLOWED + exclusion` 永远无法自动归到任何未排除人物，ADR-0007 的 canonical 排除过滤分支实际上
不可达。

修复：自动关联只把 `MANUAL_ASSIGNED`/`MANUAL_UNASSIGNED` 视为整脸锁；活动排除继续在候选层按 canonical
人物逐项过滤。处理器重检测时保护人工知识的宽口径不变。新增“仍可归到允许人物”和“合并后的 canonical
排除仍生效”用例。

### F5：脸级纠错可能误写图片向量 outbox

脸级操作调用共享 `publish_derived()` 时带入图片投影 refresh；若资产已有 confirmed image vector，可能写
`index_outbox`。ADR-0007 明确脸级控制只需 filter revision、搜索投影与独立 face candidate repair，不能因此
刷新图片向量索引。

修复：脸级纠错发布时显式关闭 image projection refresh，同时保留 `catalog_filter_revision`、
`PERSON_CHANGED` 搜索队列和 face candidate `PENDING_REPAIR`。新增 refresh callback 不得执行的断言。

## 3. 十项审查结论

| # | 审查项 | 结论 | 证据 |
|---|---|---|---|
| 1 | 迁移 | PASS | 0012 只重建两张 operation 表；真实 v11→v12 含 undo/alias 引用逐列保留；备份、单事务、损坏回滚、缺件和过新拒写专项通过。源码与 bundle 0012 SHA-256 同为 `6E4E56F03AE93D20EF5BBC8A14037F1CA86E1B593B3C7E153D3A32D27EA1CF12`。 |
| 2 | 模式/排除矩阵 | PASS（修复 F4） | revision 0、assign/unassign/restore_auto、保留排除、只解除目标排除、canonical 合并排除均有行为断言；AUTO_ALLOWED 带排除仍可归到允许人物。 |
| 3 | 不变量 | PASS | 数据库唯一约束与服务校验维持每脸至多一个 CONFIRMED；MANUAL 两态与 link 数量一致；操作目标按明确 face_id 集合，siblings 不变。 |
| 4 | 幂等与批量 | PASS（修复 F1） | 全局 request_id 门早于 revision；同 key 同 payload replay、异 payload 零写冲突；1..500、重复 face、顺序无关 hash、整批预校验/回滚均有断言。 |
| 5 | 撤销 | PASS（修复 F2/F3） | undo 是新操作，整批冲突零写，禁止 undo an UNDO；脸级/人物级错误分派与 replay envelope 有测试；assignment/link revision 向前。 |
| 6 | 自动路径门禁 | PASS（修复 F4） | `_control_revision_gate()` 在权威写事务内比较；MANUAL 两态不被覆盖；AUTO_ALLOWED 的 canonical exclusions 逐候选过滤；建议不会提升为 CONFIRMED。 |
| 7 | 权威→派生 | PASS（修复 F1/F5） | changed 操作最多 +1 filter revision 并写 `PERSON_CHANGED` 与 face repair；no-op/replay 零副作用；脸级操作不触发图片向量 refresh/outbox。 |
| 8 | API/错误/隐私 | PASS（修复 F2） | 四条路径、HTTP/错误闭集、session/Host/Origin/CSRF 和响应 envelope 专项通过；返回值不暴露路径、bbox、landmark、向量、相似度或原始幂等 payload。 |
| 9 | 只读计数 | PASS | 四个计数使用 canonical pair 去重；建议不计归属，未命名分组不计待整理；坏/过新 schema 仍走既有只读诊断边界。 |
| 10 | 测试质量 | PASS（补强） | 用例断言数据库、revision、operation、queue、replay envelope 与负向零写；没有吞异常或用“未报错”代替行为证据。专项由 50 增至 52 例。 |

## 4. 独立关键复现

以下 6 个指定行为以精确 node id 独立执行，结果 **6 passed**，JUnit
`test-results/p3-04c-six-reproductions.xml`：

1. 含 undo/alias 引用的真实 v11→v12 无损迁移；
2. 无控制行 `expected_revision=0` 首次 unassign → revision 1；
3. request_id replay 门早于 revision；
4. batch 第二项冲突使第一项、operation、filter revision、队列全部回滚；
5. 后续人工编辑后 undo 整批拒绝且 revision 不倒退；
6. AUTO_ALLOWED 活动排除在 canonical 合并后仍阻止回到被排除人物。

## 5. 验证记录

| 验证 | 结果 |
|---|---|
| P3-04 专项 | `52 passed`；JUnit `tests=52 failures=0 errors=0 skipped=0`，4.425 s；`test-results/p3-04c-focused.xml` |
| 6 组独立复现 | `6 passed`；JUnit `tests=6 failures=0 errors=0 skipped=0`，0.621 s；`test-results/p3-04c-six-reproductions.xml` |
| 完整 unit/integration | exit 0；JUnit `tests=1116 failures=0 errors=0 skipped=1`，419.408 s；`test-results/p3-04c-full.xml` |
| `uv run --no-sync ruff check .` | exit 0，`All checks passed!` |
| `git diff --check` | exit 0 |
| `uv run --no-sync uv lock --check` | exit 0，resolved 95 packages；依赖未变 |
| 真实 CPU 夹具 | `face_correction_smoke_fixture.py --device cpu` exit 0；`status=ok`、`problems=[]`；4 assets / 9 faces / 8 unnamed groups；纠错、撤销、重启一致，siblings 不变 |
| C 级 onedir | `pyinstaller --noconfirm PhotoSearch.spec` exit 0；真实重建完成 |
| 打包 API smoke | 新目录 `D:\PhotoSearchSmoke\P3-04C-20260920-1040`；exit 0；`status=ok`、`problems=[]`、startup 1.2 s、capabilities 200、无本地路径泄露 |
| bundle migration | `0001`～`0012` 共 12 个连续文件；源码/bundle 0012 SHA-256 一致 |

第一次受限环境中的 focused 运行因既有 `.pytest-tmp` ACL 在创建 basetemp 时出现 `WinError 5`，只跑到
7 个通过、42 个 setup error；未把它记作产品失败，也没有修改 ACL。改用新的项目内 basetemp 并在正常
Windows 权限路由复跑后得到上表完整结果。第一次完整 focused 复跑另暴露一个旧测试使用 1 byte 假 embedding，
F4 修复后该无效维度才真正进入解码；测试改为维度正确的合成零向量后通过，未使用模型或真实照片。

PyInstaller 保留了既有 TensorRT DLL、tzdata 与 scipy hidden import 警告；构建及真实打包 smoke 均成功，
本任务没有把这些非阻塞警告解释为环境能力提升。

## 6. 变更边界

生产最小修复：

- `src/photo_search/persons/face_correction.py`
- `src/photo_search/persons/operations.py`
- `src/photo_search/face/index.py`

测试补强：

- `tests/integration/test_p3_face_correction_api.py`
- `tests/integration/test_p3_face_correction_lifecycle.py`
- `tests/integration/test_p3_face_correction_undo.py`
- `tests/unit/test_p3_face_correction_contract.py`

未改：ADR-0001～0007、contract JSON、`0001`～`0012`、`schema_version`、`pyproject.toml`、`uv.lock`、
`PhotoSearch.spec`、前端、模型与真实照片数据。

## 7. 未验证与后续边界

- 未做真实多进程并发压力或 500 项 batch 性能基准；事务/revision 冲突由确定性集成测试覆盖。
- 未使用真实家庭照片库，也未验证大规模 exclusion 集合；只使用合成/既有公共领域夹具。
- 新旧检测 `face_id` 的可审查映射仍属于 P3-08，不应混入 P3-05 人物拆分。
- 人物页面、脸框与浏览器交互属于 P3-06；真实 USB/SMB 故障不属于本卡。

