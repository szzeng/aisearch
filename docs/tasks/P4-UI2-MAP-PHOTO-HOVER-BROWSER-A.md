# P4-UI2-MAP-PHOTO-HOVER-BROWSER-A — 地图照片 hover 产品浏览器验收

- **状态**：READY / 待用户发起。
- **前置实现**：[P4-UI2-MAP-PHOTO-HOVER-A](P4-UI2-MAP-PHOTO-HOVER-A.md)，当前代码已实现并通过定向自动验证；本卡只补产品浏览器门禁。
- **基线**：执行时先读 `AGENTS.md`、`docs/development-status.md`、A 卡和[A 报告](../reports/P4-UI2-MAP-PHOTO-HOVER-A.md)，确认真实 Git 状态。
- **范围**：仅使用项目本地隔离合成 GPS 数据和 Codex 内置浏览器；不访问真实照片库或真实 Key。

## 验收路线

1. 按 `AGENTS.md` 启动本机应用，不带 `--open-browser`；在同一 Codex 内置浏览器打开 launcher 新生成的一次性 session link，并先确认认证 API 可用。
2. 在地图页对右侧 2–3 张不同 GPS 照片依次悬停，观察左侧标记分别落在各照片真实坐标；同一卡片内移动不闪烁，离开后标记消失。
3. 点击照片打开共享查看器，确认 hover 标记已清除；关闭查看器后地图点点击/拖动及右侧分页仍可用。
4. 记录浏览器交互结果；若导出可用，保存最多一张代表性截图至 `docs/reports/artifacts/`。分别记录 browser interaction、screenshot 和 visual 状态，不能用自动 probe 冒充浏览器证据。

仅执行上述受影响路线。浏览器工具限制或隔离 fixture 缺失时记录具体 blocker，不重复尝试绕过保护。完成后更新 A 报告与 `docs/development-status.md`；只有任务卡要求的浏览器路线实际观察通过后才评估是否关闭整卡。
