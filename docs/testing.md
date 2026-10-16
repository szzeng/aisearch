# 日常验证

先看本次 diff、直接调用者和共享 fixture，再选测试。完整规则见[执行策略 §3.2/3.4](../个人图片搜索系统_V3.1_Codex_DeepSeek_开发执行策略.md)。测试数量不作为选取目标。

| 本次变化 | 通常需要 |
| --- | --- |
| 纯说明文档 | 链接/交叉条款核对、diff-check；不新增 pytest |
| 局部 UI、资源缓存版本、测试期望/夹具 | 受影响测试及必要浏览器流程；共享夹具覆盖全部使用者，说明期望为何修改 |
| 业务行为、API、查询、跨模块调用 | 修改模块、调用者和契约边界的完整受影响 B |
| 人工状态、事务、lease/outbox、撤销、恢复 | 受影响 B 加相应故障和不变量验证 D |
| 广泛合流、依赖/运行环境、迁移语义、无法界定的公共变更、正式发行 | 完整 unit/integration；按风险补构建、真实运行和 D |

迁移语义包括迁移 SQL、版本、数据转换及升级/回退契约或行为；纯 schema 派生计算缓存不因文件名自动触发全量，但仍须覆盖受影响迁移、兼容、拒绝和缓存失效，无法界定公共影响时扩大到全量。

已有可信完整记录后，只重跑新增与受影响节点，复用未变代码/依赖对应的通过证据。公共 fixture 或 runner 改动须扩大到使用者。既有失败逐项解释和复验，原失败 XML 不覆盖；不要通过删断言、放宽不变量或 skip/xfail 隐藏失败。报告说明基线版本、后续 diff、复用/重跑范围与缺口，称为“基线＋增量验证”，不冒称最终版本的一次全量通过。没有可信基线时可完成范围明确的任务验收，但不能据此声称全仓覆盖。

测试代码、可复用夹具和测量脚本纳入 Git；每轮生成的 JUnit、JSON、截图、日志和审查流水放在忽略的 `test-results/<任务>/` 或 `.tmp/`，CI 中保存为构建附件。仓库只保留必要的最终说明、复现命令、结果摘要和未验边界，同一改动复用已有报告与任务卡。原始失败记录仍保留供本地复核，不因归档而变成通过；不要为每次重跑新增一组待提交文档。旧工具输出到 `docs/reports/artifacts/` 的新增文件也默认忽略。

在仓库根目录使用已准备的 Python 3.12.10 环境，避免为一次测试重建依赖。下面使用本机当前隔离环境；其他工作区改为其现有 `.venv/Scripts/python.exe`。依赖维护仍使用 uv 和项目内 `.uv-cache`。

```powershell
$testPython = ".tmp/runtime-review-20261015/full-env/Scripts/python.exe"
$testRun = Get-Date -Format "yyyyMMdd-HHmmss-fff"
$testTemp = "$PWD\.tmp\daily-tests\$testRun\tmp"
New-Item -ItemType Directory -Force -Path $testTemp | Out-Null
$env:TEMP = $testTemp
$env:TMP = $testTemp
$env:UV_CACHE_DIR = "$PWD\.uv-cache"
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONUTF8 = "1"
$env:PYTHONPATH = "$PWD;$PWD\src"
$env:PHOTO_SEARCH_DATA_ROOT = "$PWD\.tmp\daily-tests\$testRun\default-data"
```

例如只改照片详情返回状态，先选对应 UI 回归，再按实际 diff 补直接调用链；浏览器只验必要的返回流程。每次使用不同的 XML/basetemp 名称，保留原失败证据；并行运行必须使用独立目录。

```powershell
& $testPython -m pytest -q tests/unit/test_photo_view_info_ui.py `
  --junitxml="test-results/photo-view-affected-$testRun.xml" `
  --basetemp=".pytest-tmp/photo-view-affected-$testRun" --durations=10
Write-Output "pytest exit=$LASTEXITCODE"
& $testPython -m ruff check src tests tools
Write-Output "ruff exit=$LASTEXITCODE"
git diff --check
Write-Output "diff-check exit=$LASTEXITCODE"
```

触发完整回归时使用全部单元/集成测试，并记录具体理由；不能以完整回归替代任务需要的故障、浏览器或真实模型证据。

```powershell
& $testPython -m pytest -q tests/unit tests/integration `
  --junitxml="test-results/full-regression-$testRun.xml" `
  --basetemp=".pytest-tmp/full-regression-$testRun" --durations=20
Write-Output "pytest exit=$LASTEXITCODE"
```

成功只需摘要、实际退出码和 JUnit。失败先看节点与 trace，再跑失败节点及其受影响范围；中断、`--collect-only` 和缺少输出都不代表通过。跳过/未选/工具受限项单列，不算通过。定位慢测先用现有 JUnit/`--durations`，优化重复建库和未清理计时器；不共享可变测试状态、不以降低产品安全设置换速度。正式性能基准串行执行以免相互干扰，普通测试是否并行取决于数据、端口和进程隔离。
