# P4-LIBRARY-RETENTION-C2-A 执行报告

## 目的与范围

建立可靠的缺失观察证据、30 天资格状态和来源批量失效保护，为后续清理阶段提供保守依据。本阶段不删除原图、资产、向量、缓存、人工记录或其他派生数据，也未开放自动清理或一键清理。

## 实现

- 新增 schema migration 0038，记录每个资产/来源的成功缺失确认、资格状态和需人工核实的来源批次异常；旧 MISSING 不回填时间。
- 完整来源扫描的最终 SQLite 事务原子写入扫描状态、缺失对账和 retention 证据。来源失败/离线/重试会清除相关证据并将状态重置为 BLOCKED。
- 只有所有登记位置来源都提供成功缺失扫描证据、没有 PRESENT 副本、相关来源在线且无未解决异常时才开始观察。满 30 天后，仍须所有相关来源在到期后再次成功扫描才转为 ELIGIBLE。
- 按单来源唯一资产 PRESENT 基线实施批量异常阈值；显式确认只解除异常 hold，之后仍须新扫描证据。
- retention 统计 API/UI 展示观察、到期、阻塞与异常；确认异常不删除数据。来源登记删除遇到未解决异常时会拒绝。
- Schema 0038 仅添加 retention 表，不改变地点包合同；地点包兼容审阅边界推进至 38，并保留未来版本拒绝门禁。

## 验证证据

- 迁移三项最小检查（新库、旧库升级、故障回滚）通过；升级保留已有人工收藏数据与原有行。
- 最终受影响组合：

  ```powershell
  uv run --locked --python 3.12.10 pytest -q tests/unit/test_catalog_migrations.py tests/integration/test_p4_library_retention.py tests/integration/test_catalog_scan.py tests/integration/test_p2_scheduler_recovery.py tests/integration/test_place_dataset_schema17.py tests/integration/test_p2_dataset_tool.py tests/integration/test_p4_storage_ui.py --junitxml="test-results/P4-LIBRARY-RETENTION-C2-A-affected-final-r2-20261020.xml" --basetemp=".pytest-tmp/P4-LIBRARY-RETENTION-C2-A-affected-final-r2-20261020"
  ```

  **119 passed，0 failed，0 errors，0 skipped**；实际 exit 0。JUnit：`test-results/P4-LIBRARY-RETENTION-C2-A-affected-final-r2-20261020.xml`。
- 迁移语义要求执行完整 unit/integration：`test-results/P4-LIBRARY-RETENTION-C2-A-full-20261020-022130-906.xml`，3363 项、61 failed、0 errors、52 skipped，耗时约 607 秒。失败主要为已有前端 harness/资源版本不一致、并行适配器时序及首次运行中尚未更新的 schema 38 地点包/表集/retention 文案断言。对本卡相关断言和兼容边界均已修正，最终受影响组合全绿；不称此运行为全量 PASS。
- 最终静态检查：Ruff、Python 3.12 编译、Node `--check`、`git diff --check` 全部通过。
- 内置浏览器使用 `.tmp/P4-LIBRARY-RETENTION-C2-A-browser` 隔离空数据根；一次性会话认证成功，打开“图库管理 → 图库管理与分析明细 → 失效照片”，刷新后 schema v38 正常，显示无资格资产且自动清理未启用。窄视口下页面可见；未导出截图。
- Luna High 独立复核完整 C2-A 变更面，指出资格成立时间字段语义问题及真实扫描事务覆盖缺口；均已修复。跟进复核确认 findings 关闭、无新问题。

## 未验证边界与后续

本阶段只完成可信观察和异常持久化。清理屏障、任务/Artifact/outbox 防复活、DELETE_VECTOR 与真实 LanceDB 故障重放、机器派生记录回收、GC-A/B、人工数据授权、安全删除执行器、确认预览/进度恢复、自动调度仍未实施。父目标继续为 **IMPLEMENTED / ACCEPTANCE PARTIAL**，后续按 C2-B 至 C2-F 推进。

未访问真实照片图库、未读写 `PhotoSearchData`、未运行 GPU/模型或 30 万照片测试。修改未提交、未推送。
