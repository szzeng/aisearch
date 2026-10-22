# P4-OCR-EVERYDAY-QUALITY — 日常照片 OCR 识别质量优化

- 日期：2026-10-22。
- 状态：IMPLEMENTED / SCOPED PASS（Luna H 独立跟进复核 PASS）。
- 基线：`szzeng/aisearch`，`master` / `dabdf032d04b2de261ae6a0ff70e246cc1f2e9b8`；启动时工作树有既存 WEMM 未提交文件，均保留。
- 目标：为新 OCR @4 将文字最低分数设为 0.5，减少普通照片纹理误识别；继续使用 RapidOCR 1.4.4 与 PP-OCRv4 Mobile，旧 @1/@2/@3 配置、Artifact 与结果保持兼容。
- 范围：`ocr/contract.py`、`engine.py`、`processor.py`、`service.py`、`scheduler/projection.py` 及必要调用点/测试。复用现有 jobs、Artifact 和 `OCR_TEXT_CHANGED` 投影；无需 schema、队列、UI、解码或依赖变更。旧版本退役遵循现有安全清理机制并尊重 live lease。
- 不做：真实图库重算、`catalog.db` 或照片访问、Qwen/CLIP/人脸重算、OCR 模型升级/权重下载、性能/30万认证。仅按用户命令对 `PhotoSearchData` 运行只读 `weights --json`，确认现有模型文件校验通过。提交阶段按用户后续请求创建本地提交，不推送。
- 不变量：@4 CPU/CUDA 处理器与 `ocr-execution-config.v4` 有独立 spec/hash；@3 冻结 score=0.0；历史 Artifact 读取规则不变。普通重复入队为 @4 新版本幂等重识别，不需 `--force`；成功结果原子切换，失败/取消/阻塞保留旧有效结果；旧任务不得晚到覆盖新结果。统计按新处理器任务身份/状态计数，旧有效文字仍算可读内容。

## 必须参考

- 主架构：§6.2–6.3（任务身份/lease），§7.2（产物提交），§8.2（OCR），§9.2/9.4（文字检索），§11.3.3–11.3.4（重算与处理器身份），§12.4 相关数据/恢复要求。
- 策略：§2.4、§3.2–3.4、§4.2、§8、§11 中处理器/lease/提交/恢复条款。
- 实现/测试：OCR contract/engine/processor/service、`scheduler/projection.py`、OCR CLI、历史兼容、生命周期、端到端/搜索投影测试。

## 性能设计与验收（PERF-BOUNDED）

- 链路与风险：`enqueue --all` 会有意扫描在线照片并排入新 OCR 身份；主要成本是每个尚无 @4 成功结果的 asset 一次推理，重复入队/任务统计不能引入逐图 Python 查询或重复成功任务。
- 做法与边界：复用现有 CLI/jobs/Artifact 与有界切片/解码；一次最多一个有效逻辑 @4 结果，输入离线跳过，保留已成功 @1–@3 结果，失败不替换。CPU/GPU 配置独立，保持 GPU 优先/限定条件 CPU fallback。
- 验证与停止：定向合成 unit/integration + 可控 OCR 输出阈值测试；约 3–5 张合成代表图的真实模型 smoke 仅在权重已就绪时执行，不下载权重。无30万/吞吐承诺；检查 CLI 命令和状态 SQL；完成必要用例、无缺陷即停止。

## 验证与交付

- 最低覆盖：@3/@4 hash/spec/Artifact 兼容；score<0.5 排除及正常文字保留；CPU/CUDA身份；旧结果存在的入队幂等与任务进度；失败保护/旧晚到拒绝；搜索/显示继续读历史结果及新事件投影。
- pytest 生成独立 JUnit 到 `test-results/P4-OCR-EVERYDAY-QUALITY-final3.xml`，basetemp 用 `.pytest-tmp/P4-OCR-EVERYDAY-QUALITY-final3`；另做所改 Python Ruff/编译和 `git diff --check`。受影响 OCR 测试 183 passed、19 skipped、0 failed；依赖和 schema 不变，未做全仓回归。
- 如配置、Artifact 身份、调度引用或重识别数据语义变化，需独立全受影响面工程审查；执行者自检不算独审。测试未完成不得 PASS。
- 主报告：`docs/reports/P4-OCR-EVERYDAY-QUALITY.md`；更新本卡与 `docs/development-status.md`。报告记录真实 CLI 状态解释：完成、等待、失败/阻塞、离线跳过。
- 真实全库操作仅由用户随后执行：备份 `catalog.db`，确认权重、单 worker；默认 `run` 无步数上限，需人为分段时可用 `--max-jobs 100`；无 `--force`。
