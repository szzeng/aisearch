# P4-LIBRARY-RETENTION-C2-F 执行报告

日期：2026-10-20
状态：**IMPLEMENTED / ACCEPTANCE PARTIAL**
范围：失效照片清理 UI、持久清理批次、自动调度和启动恢复；仅使用项目内合成 data-root。

## 实现

- Schema 41 持久化自动清理开关、run 授权模式、批次进度和逐资产状态/原因。默认关闭；手动确认仅跳过观察期，worker 会逐资产重新核验。
- 清理 worker 串行执行，每批最多 20 个资产，复用清理屏障和 DELETE_VECTOR consumer。未确认向量删除时保留队列状态；阻断写成 SKIPPED，可恢复执行错误写成 FAILED。
- 启动恢复 QUEUED/RUNNING 批次以及含 QUEUED/RUNNING/FAILED 项的 PARTIAL 批次。关闭时设置停止标记并等待 worker 结束，应用之后才释放 data-root lease。
- 现有 Storage「失效照片与数据占用」页加入自动开关、手动确认和持久进度。明确显示不删除原图及人工记录、批次上限、受管缓存需停机回收、累计字节为可记账文件大小估算。

## 验证

- 受影响 pytest 组合（API、retention barrier/cleanup、storage UI/GC、schema/migration/backup、刷新回调）：183 项，0 failed、0 errors、16 skipped。JUnit：`test-results/P4-LIBRARY-RETENTION-C2-F-affected-r11-20261020.xml`。
- 首次最终组合 r10 有一条旧断言仍要求返回内部错误码；worker 当前持久化更具体的复核阻断原因。已将断言改为检查具体来源复核原因，随后 r11 全组合通过。r10 工件保留在 ignored `test-results/`。
- Ruff 检查相关后端和测试文件通过；Python compileall 通过；Node `--check src/photo_search/web/app.js` 通过；`git diff --check` 通过。
- Luna H 对变更面只读复核：close 时 lease 竞态与 PARTIAL 手动 run 恢复两项 finding 均已关闭，无新增 blocker。Luna H 未运行测试。
- Codex 内置浏览器在隔离 data-root 确认登录 API、schema 41 和失效照片控件存在。浏览器工具限制了真实点击；tab 选择、预览、确认、进度及返回交互仍 PENDING，未用静态/API证据代替浏览器验收。未导出截图。

## 未完成与边界

- C2-F 保持 **IMPLEMENTED / ACCEPTANCE PARTIAL**，直至完成内置浏览器交互检查。
- C2-E 的媒体 unlink 与数据库记账间仍有崩溃窗口，累计 `media_freed_bytes` 可能保守低估；不是精确磁盘回收量。
- 未访问真实图库或 `PhotoSearchData`，未运行全量回归、30 万规模、模型/GPU、发布或物理缓存停机回收。未提交或推送。
