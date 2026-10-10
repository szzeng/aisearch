# P4-TEST-BASELINE-CORE-A — Schema/人物 fixture/OCR 历史测试轻量收口

日期：2026-10-11。状态：**READY / 未实施**。执行序号 4/4；默认 **GPT-6 Medium 仅处理测试夹具/旧断言**；遇迁移、人工知识、冻结契约、身份/恢复产品语义需要改动时，**停止该分支，用户手动选择 GPT-6 High / 按 V3.1 完成必需独立复核**。由[连续总控](P4-TEST-BASELINE-CONTINUE-A.md)编排。

## 目标与旧证据

历史分类有：**8 项 schema/migration/契约、3 项人物搜索缺 `face_detail_auto_assets`、2 项 OCR/原生环境**。旧 [完整基线计划](../plans/P4-TEST-BASELINE.md) 还记载 strong-match/provider 及未知 face spec 等小组；按本轮最新可复现的 nodeid 才决定是否处理，**不把这些旧数字误当当前剩余量**。

## 四类最小处理

### A. 当前人物 fixture

优先 `tests/unit/test_p3_person_search_contracts.py`：旧手写 SQLite fixture 缺当前 `face_detail_auto_assets` 等成员查询表。先复跑缺表代表节点；优先复用真实 migration 创建合成 catalog，再插最少记录。保留排除、建议脸、原图 detail、人工锁等各自含义；不能靠伪造 `MEMBER_PREDICATE` 或 mock 掉 SQL 让测试通过。

### B. 历史 Schema / migration

重点 `test_p4_upgrade_contracts.py`、`test_p4_02e_migration.py`、scheduler 旧断言。区分“测试一个**历史固定版本**”和“从历史迁到**当前支持版本**”，历史版本和 SQL 哈希照旧验证；只能调整经批准且能从当前迁移清单证实的旧预期。发现丢行、回滚损坏、真实兼容问题立即单列产品缺陷，不改正式 migration。

### C. 历史冻结契约/搜索/人脸

若与 LITE-B 已覆盖的 `p2-contracts.v1.json` 重复，直接复用已通过证据，不重做。遇其他 companion 哈希、门限双 provider、未知 face spec，只在具备批准依据和一正一反测试时修旧 oracle；禁止自动更新 expected 为当前文件、自行决定跨版本兼容。

### D. OCR/原生运行时

先用**一个最小隔离测试/子进程**收集完整 stderr、exit code、预期拒绝码；区分 test mock 边界、pytest 执行顺序和真正 ONNX Runtime 环境问题。不要为了修绿默认换 provider、强制 CPU/GPU 回退、安装 DLL、下载模型或改 `uv.lock`。环境无法稳定复现时记录 `RUNTIME_UNCONFIRMED`，不硬修。

## 最小验收

- 每一类**实际修改的根因**选 1～3 个代表节点，修改后跑其测试文件及共享 fixture 直接依赖；高风险/历史版本至少有 1 个拒绝或反例。
- 修改 Python 测试文件的 Ruff/py_compile、`git diff --check`；有涉及模拟原生环境时核对独立进程退出码。
- JUnit `test-results/P4-TEST-BASELINE-CORE-A.xml` 和实际命令/退出码；不跑真实 OCR/GPU、真实图库、全仓或完整模型。
- 如范围超出测试维护，保存可复现证据并**停在安全边界**，不自动改生产代码，也不凭延期项目强制通过。

## 阶段总结

输出 `docs/reports/P4-TEST-BASELINE-CORE-A.md`、独立提交和简短状态。按总控汇总各阶段已修 nodeid、失败/UNKNOWN 清单、四个提交 SHA。**不默认执行整库全量回归；需要最新完整失败计数时由用户另行授权一次 B/D 全量。**全量基线与原各产品卡的门禁不能因本卡被宣称通过。
