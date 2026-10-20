# P4-PHOTO-OCR-DISPLAY-A 执行报告

日期：2026-10-20。状态：**IMPLEMENTED / ACCEPTANCE PARTIAL**。

## 实施

- `GET /api/v1/assets/{asset_id}/ocr-text` 仅读取已发布 OCR Artifact 的 `original_text`，兼容可读历史 v1/v2 与当前 CPU/GPU processor；基础资产 SHA 与 OCR 指针在文件读取前后短查询核对。
- Artifact 内容验证、哈希与 JSON 解析在 Catalog 查询 worker 之外的 `asyncio.to_thread` 执行，并由共享 semaphore 限制最多两个并行读取。读取前校验 catalog `byte_size` 不超过 16 MiB；响应文本按 UTF-8 字符边界限制为 128 KiB。
- 共享 `AIPhotoInfo.render()` 在基础资产信息挂载后加载 OCR；正文使用 `textContent`，保留原始换行，复制失败时提供文本选择回退，切图与重试响应受当前视图序号保护。同步更新五个入口 HTML 的共享资源版本。

## 验证

- 定向 JUnit：`13 passed, 0 failed, 0 errors, 0 skipped`，结果：`test-results/P4-PHOTO-OCR-DISPLAY-A-r8.xml`。覆盖历史 v1/v2 与当前 CPU/GPU 文档读回、错误字节局部不可用、超 16 MiB 元数据拒绝、旧 SHA 静默隐藏、无 OCR、401/404、UTF-8 上限，以及共享 viewer DOM 安全/复制/迟到响应不串图。
- Ruff、Python 3.12 `py_compile`、`node --check src/photo_search/web/photo-info.js`、`git diff --check` 通过。
- 内置浏览器使用项目内隔离合成数据验证首页共享查看器和 `/#/asset/{id}/details` 均显示同一已保存历史 OCR 原文及复制入口。未导出截图。应用通过 loopback 启动并在验证后停止。
- Luna High 首轮只读复核发现未限制 Artifact 文件读大小；现已在读取前拒绝超过 16 MiB 的元数据并增加超限测试。跟进只读复核确认该 finding 关闭；指出的空/异常状态矩阵、真实指针竞态、UI 重试/关闭后迟到响应、截断与布局、读取并发边界测试仍未齐，故保持 ACCEPTANCE PARTIAL。

## 未完成验收

任务卡 B–H 未全部闭环：当前测试尚未完整覆盖 `region_count=0`/纯空白原文快速路径、metadata 缺失/RECLAIMED/身份错配/路径越界/非法 JSON 的矩阵、实际文件读取期间 SHA/指针竞态、Catalog worker 池隔离的并发实证、重试先后返回及关闭重开、截断态 UI 和长文本滚动几何，也未逐项回归 Esc/缩放/地图/人物等既有交互。当前浏览器证据只覆盖两个显示入口的合成历史 OCR 展示。故本报告不声明全卡 PASS；上述项需后续任务或续验补齐。

未读取真实照片库，未运行 OCR 模型/GPU、全仓测试、30 万规模压力、打包；未提交、未推送。
