# P4-LIBRARY-RETENTION-C2-F — 失效照片清理确认、进度与自动调度

**状态**：IMPLEMENTED / ACCEPTANCE PARTIAL；父目标 `P4-LIBRARY-RETENTION-C2` 仍 IMPLEMENTED / ACCEPTANCE PARTIAL。
**基线**：承接 C2-A/B/C/D/E 工作树及 schema 41；不提交、不推送、不触及真实图库。

## 必读范围

- V3.0 §5.1–5.4、§6、§7、§10.2、§11.1–11.4、§12.4–12.5；V3.1 §2.4、§3.2/3.4、§4.2、§11。
- C2-A 至 C2-E 任务卡与报告；`docs/testing.md`、`docs/performance.md`。
- 直接实现：`library_retention.py`、`retention.py`、清理屏障与 DELETE_VECTOR consumer、`api/context.py` 生命周期和 runner、storage API、失效照片 UI、schema 迁移/备份、现有 UI/browser 合成 fixture。

## 目标与不变量

1. 在现有「图库管理 → 失效照片与数据占用」增加轻量统计、自动清理开关、重新检查、一键清理预览/确认及持久进度；不增加一级页面。
2. 预览展示总数、自动观察中、到期候选、可手动提前清理、需重新扫描/来源异常/人工引用阻塞数量；只读接口不得遍历文件或触发模型/向量维护。
3. 手动确认仅授权跳过 30 天等待，不能跳过新鲜成功扫描、在线来源、跨来源副本、异常 hold、PRESENT 位置、未知引用和活动任务复核。清理前逐资产重新校验；预览后恢复或状态变化时跳过。
4. 默认不删任何人工记录。当前引擎对含人工/受保护记录资产 fail closed；若没有完整实现人工解除与共享身份保护，不提供可勾选删除选项，也必须显示待人工处理数和理由。
5. 以独立 schema 41+ 持久化清理批次身份、授权模式、进度/阶段、跳过原因、统计和重启恢复状态；重试/重复点击不得重复计数或并行启动第二批。复用每批最多 20 个资产、D 的屏障与 C 的 vector delete；完整向量确认前不能进入 D/E 后续阶段。
6. 自动清理默认为关闭；用户显式开启后，仅到期且复核通过的资产进入同一清理引擎。用现有应用受控后台/扫描成功触发，不新增常驻清理守护服务。停止、崩溃、恢复和关闭设置都需持久且可见。
7. 来源异常解除后仍需新的全成功扫描重建观察；旧 MISSING 无可信证据不立即清理；不触碰原图。
8. E 当前累计物理字节有崩溃漏计风险，UI 必须准确标为已提交可记账 `st_size` 的保守估算；不得声称精确总磁盘回收。逐文件持久计量不在 F 静默假装完成。

## 性能设计

- 链路与风险：页面统计/预览、受控清理 worker、vector drain、状态轮询；风险是高频全库查询、过多并发写、轮询阻塞正常浏览。
- 做法与边界：统计用聚合/索引；预览与执行分页不超过 50/20；一个持久批次串行执行，进度轮询退避或低频；自动触发仅在扫描/已有受控工作节点，无全库对象物化。
- 验证与停止：合成库验证边界分页、并发重复确认、重启续做、状态变化重校验及受影响页面；安全状态无法复核即停止该资产并保留可恢复批次，不做 30 万容量/真实图库测试。

## 必须验证与审查

- 合成 API/UI 测预览→确认、手动提前资格、取消、自动默认关闭/启用、异常/人工/旧 MISSING 阻断、照片恢复、重复请求、后台进度与重启后恢复。
- 向量删除需复用既有真实 Lance 合成消费与故障恢复证据；至少新增验证清理 worker 对待确认 outbox 的阶段推进，不用 mock 代替既有真实 Lance证据。
- 使用隔离 data-root 与 Codex 内置浏览器绑定 `127.0.0.1`，新鲜单次 session link 验证登录/API 和页面关键按钮、预览、确认、进度/返回。不访问真实图库、不启动 `--open-browser`。
- 按受影响面运行带 JUnit XML 的 pytest、Ruff、Python 3.12 `py_compile`、Node 语法及 `git diff --check`。如新增 migration，执行新库、旧版升级、失败回滚三个检查并覆盖备份/恢复语义；存在必需检查未完成则保持 partial。
- 用户指定 Luna H 子代理对完整 UI/API/worker/schema 受影响面只读审查；完整 diff 与证据均纳入范围。模型路由只作建议，不自动切换。

## 排除范围

- 不删原图，不自动删除人工记录/人脸身份，不新增常驻清理守护进程，不做 SQLite VACUUM、Lance 全库压缩、GPU/模型重建、大图库或正式发布。
- 不声称 C2-E 崩溃窗口的累计物理字节准确；除非后续另有逐文件账本实现和验收。

## 实施与验收结果（2026-10-20）

- 已实现 schema 41 持久化自动开关、清理批次与逐资产进度；清理 worker 串行、每批最多 20 个资产，逐资产重新核验、复用 retention barrier 与 DELETE_VECTOR 回执，并将阻断、可恢复失败和结果落库。应用退出时等待 worker 退出后再释放 data-root lease；启动会续跑 QUEUED/RUNNING 及含未完成项目的 PARTIAL 批次。自动清理默认关闭，仅在启动、成功扫描/刷新触发时检查。
- Storage 失效照片页新增自动开关、手动清理确认和持久进度；确认文案说明批次上限、停机后受管缓存回收，以及不删除原图和人工记录。schema 41 新库/升级/失败回滚及备份恢复相关验证包含在定向组合中。
- 最终受影响 JUnit：`test-results/P4-LIBRARY-RETENTION-C2-F-affected-r11-20261020.xml`，183 项、0 failed、0 errors、16 skipped。Ruff、Python compileall、Node `--check`、`git diff --check` 通过。r10 的旧阻断码断言失败已改为检查具体复核阻断原因，并由 r11 全组合重跑关闭。
- 用户指定 Luna H 对完整变更面只读复核；关闭时 lease 竞态与 PARTIAL 手动 run 启动恢复 finding 均关闭，无新增 blocker。
- 内置浏览器在隔离 data-root 上已确认登录 API、schema 41 与控件存在；浏览器工具无法完成真实点击，因此 tab 选择、预览/确认、进度及返回交互仍 PENDING。未把 API/静态证据记作浏览器通过。单次 session link 未写入报告。
- C2-F 与父卡保持 **IMPLEMENTED / ACCEPTANCE PARTIAL**。本地 UI 点击验收待完成；C2-E 媒体 unlink 与 SQLite 记账的崩溃窗口仍可能使累计 `media_freed_bytes` 保守低估。未触及真实图库，未跑全量回归、大库或模型/GPU测试，未提交/推送。详见[执行报告](../reports/P4-LIBRARY-RETENTION-C2-F.md)。
