# P4-TEST-BASELINE-PLACE-A — 地点基线测试的来源夹具与包恢复预期

日期：2026-10-09。状态：**DEFERRED / NOT STARTED**。用户本轮改为个人相册基础验收，本卡及下方全量基线路线停止自动执行。当前入口回到 [P4-PERSON-ALBUM-PERF-A](P4-PERSON-ALBUM-PERF-A.md) 卡首限定范围；日常使用出现地点实际错误时再按具体反例安排小修。下方保留此前计划，不代表当前必做门禁。

推荐执行：**DS-H / DeepSeek V4.1 Flash / 高推理**，用户在Codex中手动选择；同会话完成本卡实现、调试、定向复测。独立DS-H审查全部改动/直接调用链/授权预期/证据。默认不启用DeepSeek max。

## 1. 目标与已知起点

让当前地点/搜索/人物/备份测试使用真正可指定的活动来源地点；保持旧人工归属和撤销的验证能力；将唯一合法包的缺前驱恢复例外与ADR-0015对齐。不得用取消来源验证修绿。

用户收益：全量基线不再因无效地点setup在到达人物/搜索/备份核心断言前失败，后续真实回归能够被完整执行并发现。

规划起点 HEAD `65386805f5365ad14a879a626f6b4a2613950c87`，工作区另有未提交相册性能实现。开工重新核对 HEAD/index/worktree，保留全部其他任务改动；不将整个工作区归为本卡。

证据：[基线修复设计](../plans/P4-TEST-BASELINE.md)、[逐例清单](../reports/P4-TEST-BASELINE-inventory.json)、[原报告](../reports/P4-PERSON-ALBUM-PERF-A-implementation.md)、`test-results/p4-person-album-perf-full.xml`。本组暂分类 **36 failures / 43 errors**，其中78项来源资格/级联候选、1项包前驱恢复；逐例执行后才确认根因。

## 2. 读取范围

按 `AGENTS.md → docs/development-status.md → 本卡`。当前状态中的性能任务仍待验，本卡为基线修复下一入口，不据旧状态自动执行其他功能。

- 主架构§4.5.1～4.5.4、§9.3～9.5、§11.2；V3.1§2.2～2.4、§3.2/3.4、§7.2、§11。
- ADR-0005地点身份/assignment/批量/撤销/包激活条款；ADR-0015窄恢复例外；P4-PLACE-UNIFIED-A§3.3；P4-PLACE-RECOVERY-B及其报告的缺前驱边界。
- 直接实现只读：`places/{assignments,choices,directory,datasets,filter,revision,service}.py`、地点API路由、相关包启动恢复路径、直接测试/fixtures。需要修产品语义时另立卡；不要全文重读架构或扫描真实数据目录。

## 3. 允许改动与排除

允许修改本组相关测试和它们直接复用的最小test helper、报告及简短交接。精确文件从inventory提取，下列为已观察的失败面：

- `tests/unit/test_p2_place_directory.py`；
- `tests/integration/test_p2_acceptance_{recovery,search}.py`；
- `tests/integration/test_p2_place_{api,page,resolver}.py`；
- `tests/integration/test_p2_search_{api,authority,channels,endpoint,image}.py`；
- `tests/integration/test_p3_person_{place_interleave,search_filter}.py`；
- `tests/integration/test_p4_05_backup_restore.py`、`test_p4_taken_time_api.py`；
- `tests/integration/test_place_package_startup_recovery.py`；
- 对应 `tests/` 下现有包/搜索fixture；需要最小共享来源helper时先核对其调用方，再增加一个测试专用helper，不能形成第二套包校验实现。

不修改 `src/`、历史migration、SUPPORTED_SCHEMA_VERSION、companion/历史ADR字节、依赖、模型/阈值、搜索排名、权限、UI、已有相册性能实现。不能全仓replace创建地点调用；目录创建/改名协议本身的测试仍按其目标保留。

若合法fixture仍证明产品ASSIGN/UNDO/恢复失败，保存最小反例、预期/实际及受影响面，再生成独立D级产品修复前置卡；本卡保持PENDING，不直接扩大。

## 4. 执行步骤

1. 为79项分“setup不合法”“级联错误”“行为预期演进”“未知/产品缺陷”；先分别独立复跑目录、API undo、person-search setup、backup、startup recovery代表，JUnit保存。
2. 复用现有离线小包生成/正式stage-validate-activate接口。为纯GeoNames测试取得真实映射place_id；涉及OSM身份才使用最小CN包，不引入外部下载或实际照片。
3. 仅对待测ASSIGN/batch/search场景替换无来源USER目标为合法来源目标。保持祖先结构/过滤含义/文本匹配/expected_revision，避免换fixture后丢失原断言意义。
4. assign/batch/后续编辑响应先断言status/body成功，再读operation_id。失败必须展示结构化错误，不允许 `.get()`、空对象或捕获KeyError后继续。
5. 新人工指定无包/旧USER/伪造ID拒绝；历史已有USER/custom关系按标记清楚的旧合成数据准备路径生成，验证可读/筛选/取消/恢复/撤销。历史准备代码不能替代当前撤销API或直接算预期返回值。
6. 缺前驱恢复用例分正常导入拒绝与空registry唯一合法包允许恢复。成功时catalog回退字段NULL、磁盘manifest和全部包bytes/mtime不变；正常导入、非空registry、多包、篡改/缺件/不兼容边界仍拒绝自动激活。保留事务故障回滚和重复启动幂等。
7. 逐文件运行所有相关场景，不只跑79个红节点，检查共用fixture是否破坏原本绿色用例。单独运行与文件组合结果一致；fixture不依赖前一测试激活包，monkeypatch不外泄。

## 5. 必须保留的可执行不变量

- 真实有效来源指定成功；任意文本/数字不能伪造来源资格，历史知识不被清除。
- 同request_id同载荷重放早于revision；不同载荷拒绝且零写入。
- 批量上限500；revision/资格有任一失败整批零写入；超过上限拒绝。
- 撤销是补偿和递增revision；后续编辑导致冲突，不能抹掉后来的人工数据。
- 取消/排除不被迟到自动结果恢复；人工地点不被人物操作覆盖，反之亦然。
- 来源/地点/人物/时间AND，FTS/向量/权威复核/分页一致；成员去重、旧游标失效和备份前后集合相同。
- 缺包不破坏历史读取/人工解除；包恢复只在ADR-0015窄条件成立时执行，不重写包、不制造前驱。
- fixture无需访问真实PhotoSearchData或加载真实模型。

## 6. 验收命令与状态

初始范围为测试输入/oracle修复，按V3.1 **A级**执行全部相关定向pytest、受影响Ruff、diff-check及独立DS-H证据核对。不可引用纯文档豁免。整个基线及人物相册卡的B级门禁仍待最终CLOSE卡，不在本卡标整体PASS。

PowerShell开工设置：

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.uv-cache'
$env:PYTHONPATH = (Get-Location).Path
uv run --no-sync python -c "import sys; print(sys.version); assert sys.version_info[:3] == (3,12,10)"
```

修复收敛后完整本组集合：

```powershell
uv run --no-sync pytest -q tests/unit/test_p2_place_directory.py tests/integration/test_p2_acceptance_recovery.py tests/integration/test_p2_acceptance_search.py tests/integration/test_p2_place_api.py tests/integration/test_p2_place_page.py tests/integration/test_p2_place_resolver.py tests/integration/test_p2_search_api.py tests/integration/test_p2_search_authority.py tests/integration/test_p2_search_channels.py tests/integration/test_p2_search_endpoint.py tests/integration/test_p2_search_image.py tests/integration/test_p3_person_place_interleave.py tests/integration/test_p3_person_search_filter.py tests/integration/test_p4_05_backup_restore.py tests/integration/test_p4_taken_time_api.py tests/integration/test_place_package_startup_recovery.py tests/integration/test_p4_place_unified.py --deselect=tests/integration/test_p2_search_endpoint.py::test_strong_match_annotation_does_not_change_search_order_or_candidate_count --basetemp=.pytest-tmp/p4-test-baseline-place-a --junitxml=test-results/p4-test-baseline-place-a.xml
git diff --check
```

唯一deselect明确归入后续SEARCH-A并保留在总清单和最终全量执行中，不是整库验收命令；不得把这次定向命令称为全量通过，不增加新的deselect/skip来掩盖本组失败。共享helper有其他调用方时追加其定向集（执行时核对实际调用方）。

对实际修改Python路径运行 `uv run --no-sync ruff check <显式文件列表>`；每条命令立即记录 `$LASTEXITCODE`，解析JUnit确认总数/failures/errors/skipped。basetemp须用本卡新子目录，保留其他会话临时文件；WinError5仅按正常权限路线，不改ACL。

若需要任何产品人工状态/恢复修改，须另卡D：完整B＋对应故障注入/恢复，涉及worker/依赖/打包时再C。不能为了让本卡更早PASS降低该门禁。

## 7. 独立审查、交接与停止

独立DS-H完整受影响面：全部变更测试/helper、对应真实API/资格/undo/恢复链路、历史与新增行为依据、正反例、JUnit/退出码及未覆盖项。修复没有业务语义变化且没有未决项时可记录 `SOL_NOT_REQUIRED`；若新身份/人工状态/恢复决定仍需裁决，先生成最小决策卡，Sol high聚焦该边界，不能提前标PASS。

DS-MAX只有§2.2六类实际条件可触发；报告写编号、证据和待回答问题。环境/ORT不可推理解决的问题分流C1，不用max替代真实诊断。

保存 `docs/reports/P4-TEST-BASELINE-PLACE-A.md`：起点/实际文件、79节点分类与复跑去向、预期演进依据、命令/退出码/JUnit、覆盖不变量、剩余未知/产品缺陷、独立审查结论。审查报告 `docs/reports/P4-TEST-BASELINE-PLACE-A-review.md` 必须含压缩交接：基线、完整改动清单、预期/实际差异、符号/行定位、最小反例、未验证项与下一阶段边界。

仅本组定向通过但独立审查未做时写 `IMPLEMENTED / SCOPED VALIDATION COMPLETE / REVIEW PENDING`。全部本卡要求完成后才能标本组范围PASS；其他基线及原相册卡依旧VALIDATION PENDING。

下一闭环为 `P4-TEST-BASELINE-SCHEMA-A`（当前尚未生成完整卡），完成本卡后按实时证据生成；若发现产品缺陷则优先生成其高风险前置卡。更新开发状态只写简短摘要和报告链接。停止在本组闭环，不自动继续其他卡、不提交/推送，不访问真实图库。
