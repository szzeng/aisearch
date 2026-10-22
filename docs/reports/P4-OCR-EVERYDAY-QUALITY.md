# P4-OCR-EVERYDAY-QUALITY 执行报告

- 日期：2026-10-22；基线 `dabdf032d04b2de261ae6a0ff70e246cc1f2e9b8`。
- 结果：**IMPLEMENTED / SCOPED PASS**。按用户请求本地提交，未推送。既有 WEMM 工作区变更保留。

## 实现

- 新活动处理器为 `ocr-text-cpu-fp32@4` / `ocr-text-cuda-fp32@4`，执行配置版本为 `ocr-execution-config.v4`；CPU/GPU spec revision 分别为 `ocr-text-ppocrv4-mobile-cpu-fp32-v4` / `ocr-text-ppocrv4-mobile-cuda-fp32-v4`。@4 的 RapidOCR `text_score` 为 0.5；低置信统计仍为 0.6，切片、方向识别、重叠去重及 PP-OCRv4 Mobile 权重不变。
- @3 配置冻结在 `text_score=0.0` 并保留自身方向复核、去重与 spec revision。@1/@2/@3 的 Artifact 仍按原处理器身份和哈希校验。CPU/GPU @4 配置哈希分别为 `62e2b6c68b573feefc164e70a6d7aadef5889e0baf9f540386ac53efbba60497` 和 `6eb9ec7001be1d5c9acc8714f5f4dce82a499e6ea973b1e844cb48498b280ad0`；@3 分别维持 `2336126e744954f1740ee65c26d9da0ab928558d07466ffa65805590423cb002` 和 `c406619b57b03718f4b73806603b04a75916dae67a2b01a241041304400fa4ad`。
- 普通重复入队只创建当前 @4 身份的未完成任务，不重复成功结果；旧结果不阻止 pending/failed/blocked 统计。离线或无 PRESENT 来源位置的照片不入队，旧结果保留。新结果沿原 Artifact 提交与 `OCR_TEXT_CHANGED` 搜索投影路径替换；旧 lease 与提交身份校验继续阻止晚到旧任务覆盖。
- `weights --json` 输出当前 CPU/GPU 处理器、配置哈希和阈值。按用户给定真实数据根目录只运行了此只读权重检查：四个 PP-OCRv4 文件均存在、尺寸与 SHA-256 匹配；未读写 `catalog.db` 或照片。

## 验证

- 受影响 OCR 单元/集成测试：**183 passed, 19 skipped, 0 failed**，JUnit：`test-results/P4-OCR-EVERYDAY-QUALITY-final3.xml`。覆盖版本身份、旧 Artifact 可读、低于 0.5 过滤与边界/高分保留、CPU/GPU 配置、幂等入队/续跑、离线跳过、失败保留、旧 lease、搜索投影与详情显示、CLI 权重输出；包含 Luna H finding 修复后的回归。
- 所改 Python Ruff 与 `py_compile` 通过；`git diff --check` 通过。
- 使用当前 PP-OCRv4 Mobile 权重和 RapidOCR 1.4.4 在项目内合成图做 CPU smoke：中文/英文/数字招牌文字被保留，空白图及纹理图未输出文本；一个风格化合成景观仍误识别出高分数字 `3`。0.5 是最低保留分数，不代表准确率保证。没有实际 GPU 执行 smoke、真实照片质量评估或全库重识别。
- Luna H 首轮复核发现无版本参数的 `execution_config_hash()` 默认值仍为 @3；已改为 @4，并补 CPU/GPU 默认哈希与显式 @3 哈希断言。跟进复核确认该 finding 已关闭，无其他 findings。

## 用户全库操作

在真实全库开始前先备份 `D:\SZ\project\PhotoSearchData\catalog.db`，并确保同一时间只有一个 OCR worker。先运行权重检查，再 enqueue。默认让 `run` 持续处理所有当前可运行任务，避免每 100 个任务重启一次；如需人为分段，可加 `--max-jobs 100`。无需 `--force`：

```powershell
.\.venv\Scripts\python.exe -m photo_search.ocr.cli weights --data-root "D:\SZ\project\PhotoSearchData" --json
.\.venv\Scripts\python.exe -m photo_search.ocr.cli enqueue --data-root "D:\SZ\project\PhotoSearchData" --all --device auto --json
.\.venv\Scripts\python.exe -m photo_search.ocr.cli run --data-root "D:\SZ\project\PhotoSearchData" --json
.\.venv\Scripts\python.exe -m photo_search.ocr.cli status --data-root "D:\SZ\project\PhotoSearchData" --json
```

- 完成：`state=SYNCED`、无 pending/blocked/failed 任务且无待投影记录；旧结果总量可通过 `current_results_by_processor` 区分版本。
- 等待：`state=LAGGING` 或 pending/running/retry 任务数大于 0；再次运行 `run` 继续剩余可运行任务。搜索投影队列非空表示 OCR 完成后的索引更新仍在追赶。
- 失败/阻塞：查看 `failed_jobs` / `blocked_jobs` 和对应 job 错误；修复明确失败后才对该场景使用 `--force` 重试。
- 离线跳过：enqueue 仅选在线 source root 且有 PRESENT 位置的照片；`unavailable_assets` 是当前无可用在线位置的资产数。离线照片不会清除原有效 OCR 文字，重新在线后可再次普通 enqueue。

## 未验证 / 限制

- 真实库 enqueue/run/status 批次和中断恢复尚未执行；这由用户备份后操作。
- 未运行 GPU 模型 smoke，也未用真实日常照片判断质量变化。合成景观误识别数字说明阈值无法消除所有高分误识别。
- 未进行大规模性能或容量认证；不涉及 schema、模型下载、向量、人脸或解码流程。
