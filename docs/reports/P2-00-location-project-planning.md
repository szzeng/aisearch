# P2-00 — 地点分类与全项目规划报告

日期：2026-09-13。状态：**PASS（仅本次规划文档闭环）**；不是地点实现 PASS，也不是 P2-01 契约 PASS。

## 1. 授权与基线

用户要求在 P2-01 前考虑加入地点分类，详细规划整个项目并更新架构与策略；随后明确选择实际地点（国家／城市／景点／自定义地点），支持 GPS 和手动归类。

Git HEAD 为 `e42c544`，初始 `git status --short` 为空。检查 AGENTS、接手指令、状态、P2-01 卡、两份现行架构／策略全文、ADR-0002 迁移约束、当前迁移／schema 校验器、媒体 GPS/EXIF 和 API 时间排序落点，以及 P1-06 报告。按交接技能进行仓库证据核对，不根据记忆认定现状。

本任务由当前 Codex 会话完成，没有切换模型或启动子 Agent。后续推荐模型仍沿用项目 V3.1 路由，不声称本次核验模型供应商能力。

## 2. 交付物和主要决定

- 主架构需求修订 4：新增 §4.5 地点数据／GPS／人工纠正／搜索，联动 FTS、任务、索引、API/UI、备份、阶段和故障验收。
- 执行策略修订 3：加入四个地点子任务，明确验收等级，更新当前执行点，保留 GPU 硬门槛。
- [ADR-0004](../decisions/ADR-0004-place-classification-plan.md)：规划决策，ADR-0003 编号留给 P2-01，地点 DDL 在 P2-01A 的 ADR-0005 冻结。
- [全项目路线图](../plans/P2-00-location-project-roadmap.md)：P2/P3/P4 每项任务、依赖／模型／验收、地点专项矩阵、成本和既有欠项归属。
- [P2-01 卡](../tasks/P2-01.md)：只增加非模型／非向量处理及目录筛选版本外壳，下一任务改为 P2-01A，保持本卡不改生产代码。
- [开发状态](../development-status.md)：记录规划、最新文档版本、下一任务及欠项；纠正把 P1-05A 已关闭脱敏问题仍写成未解决的陈旧条目。

地点首版选择手动有效归类 + 离线 GPS 附近城市建议。每资产一个最具体地点；无 GPS 可手动；同名允许歧义；取消后有人工锁；批量 500 上限／幂等／版本冲突／补偿撤销；更新地点不重算 embedding。最近城市点不是行政包含证据，建议需要确认。

全项目校准：前端以 P1 已验收静态实现为基线，Vue 迁移后置；原首版拍摄时间、HEIC 与 remap 产品入口分别安排 P4-00A/B、P4-00C、P4-05A/B，不再把目标或夹具验证写成已交付。

## 3. 验证范围与结果

本次仅改 Markdown 文档，没有对应地点生产实现可运行。采用 A 级中适用的文档／既有契约核对：

| 检查 | 结果 |
|---|---|
| 初始 Git 与实际 schema | `e42c544`，工作树干净；SUPPORTED_SCHEMA_VERSION=1，仅 0001_initial.sql |
| 现有迁移测试（完整实际命令见下） | exit 0，11/11 通过；验证现有迁移保护，不是新增地点迁移验证 |
| `uv --cache-dir "$env:TEMP\aisearch-p2-00-uv-cache" run --no-sync ruff check .` | exit 0，All checks passed |
| `git diff --check` | exit 0，无空白错误 |
| 本次 7 份文档的 Markdown 相对链接 | 全部目标存在；尚未生成的后续任务卡使用代码路径而非悬空链接 |
| 任务链／修改范围 | 架构/策略/状态/P2-01 卡一致指向 P2-01 → P2-01A → P2-02；src/tests/依赖/冻结 ADR 差异为空 |

测试执行记录：默认 `uv run` 首次因用户级 uv 缓存 ACL 拒绝访问（exit 1）。改为任务专用缓存并
`--no-sync` 后 Ruff 通过；pytest 默认临时目录 ACL 拒绝访问，10 个 fixture setup error、1 个用例通过，
不能作为测试 PASS。新建独立 `%TEMP%` basetemp 在沙箱内仍受 ACL 限制（exit 1）；同一受限测试命令
经工具提权审查允许后成功（exit 0）。未调整用户目录 ACL、未删除历史测试目录、未同步或安装依赖。

最终实际命令（PowerShell，每次使用不存在的唯一临时目标）：

```powershell
$planTestRoot = Join-Path $env:TEMP ('aisearch-p2-00-pytest-' + [guid]::NewGuid().ToString('N'))
if (Test-Path -LiteralPath $planTestRoot) { throw 'Expected a new task-specific temporary path' }
uv --cache-dir "$env:TEMP\aisearch-p2-00-uv-cache" run --no-sync pytest -q tests/unit/test_catalog_migrations.py --basetemp $planTestRoot -p no:cacheprovider --tb=short
```

链接检查使用 PowerShell 提取本次文档的 Markdown 相对目标，排除 HTTP 和页内锚点后逐个 Test-Path；
任务链再检索并人工对照 V3.1 §7.1 和 P2-01 完成动作。新增文档用 Git no-index diff-check 验证空白，
不将未跟踪文件漏当已受普通 git diff 覆盖。

未运行 P2-01 卡要求的 test_p2_contracts.py：该卡尚未执行、文件尚不存在；不为规划创建镜像式测试。未重复全套 P1 验收、GPU 推理或 PyInstaller 构建，因为未更改相关生产文件、依赖或资源。

## 4. 外部依据与限制

2026-09-13 阅读 [GeoNames 官方导出说明](https://download.geonames.org/export/dump/readme.txt) 和 [CIPA 标准入口](https://www.cipa.jp/e/std/std-sec.html)。它们支持离线点数据／EXIF 字段的方案依据，不证明本项目已经正确解析。未下载数据包、安装依赖或访问真实图库。

尚未验证：GPS 不同设备／格式兼容，中文／景点覆盖，包体积／构建／解析吞吐，城市边界准确性，地点筛选性能，GUI 行为与人工操作恢复，GTX 1660 6 GB 发行性能。后续任务按路线图验收；本次不杜撰 PASS 或数字。

## 5. 交接

下一任务是 P2-01，推荐 **C2 / GPT-5.6 Sol / high**，读取 [任务卡](../tasks/P2-01.md)。P2-01 完成后生成 P2-01A（同 C2）；P2-01A 完成后才生成 P2-02（DS-H）。本轮没有提前创建全部远期任务卡，也没有执行后续实现。

本轮修改保留在工作树供审阅；没有自动提交或推送。原图、生产代码、迁移、冻结 ADR-0001/0002、Python 依赖与锁文件均不在本次修改范围。
