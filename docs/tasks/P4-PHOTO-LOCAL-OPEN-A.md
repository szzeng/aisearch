# P4-PHOTO-LOCAL-OPEN-A — 用系统程序打开原图、在文件管理器中定位

日期：2026-10-10。状态：**PLANNED / 未实施**。基于 `szzeng/aisearch` 的 `master` 当前代码立卡；开工前复核最新 HEAD。
推荐执行：**GPT-6 Medium**，个人项目小范围定向测试。本卡只有任务设计，**未实现功能、未测试产品**。

## 目标和交互

在已有照片详情的公共信息区「文件位置」增加两个按钮：

- **系统打开原图**：调用 Windows 对该图片扩展名关联的默认应用，打开**实际原始文件**，不是 `previews/`。
- **定位文件位置**：打开 Windows 资源管理器，并**选中对应文件**，不只进入上级文件夹。

不关闭当前查看器；不清理搜索结果/浏览筛选/上一张下一张状态。单位置直接显示；多位置对**每一条已登记位置**分别提供按钮，标明来源/相对路径，不悄悄切到其他副本。无有效位置时显示离线/不可用，保留当前预览及复制路径能力。

## 代码落点与边界

先按 `AGENTS.md → docs/development-status.md → 本卡` 阅读；仅核对直接相关的代码和 `个人图片搜索系统_V3.0_开发主架构.md` §10.1–10.3、执行策略 V3.1 §2.2–2.4/§9.4。

- `src/photo_search/web/photo-info.js` / `photo-info.css`：公共照片信息面板已有文件位置、来源和「复制完整路径」。优先在这里加按钮，不在搜索/人物/地点/地图查看器各写一套逻辑。
- `src/photo_search/api/models.py` 的 `AssetLocation` 已有 `location_id`；`api/queries.py` 的资产详情已有 `locations`，无需数据库变更或增加路径字段。
- `src/photo_search/api/files.py` 的 `resolve_present_locations` / 已有受控路径解析可复用；**不信任前端用 `sourceRoot()+relative_path` 拼出的路径**。后端只从数据库选择 `(asset_id,location_id)` 并重新做已登记、PRESENT、根目录包含、reparse/link、文件元数据校验。
- `src/photo_search/api/routes.py`：加一个薄的 Windows 本机动作接口；仅必要时拆小 helper，避免修改旧 `/original`、`/preview` 契约。
- `src/photo_search/api/security.py`：沿用现有 session/Host/Origin/CSRF；**不放宽**安全中间件规则。

现有 `docs/tasks/P4-UI-VIEWER-F.md` 负责查看器视觉与返回，本卡只新增操作，不接管样式重排/页面导航；两卡不并行修改同一个公共组件，有冲突时在执行报告说明并顺序合并。

## API 与系统调用

建议 `POST /api/v1/assets/{asset_id}/local-action`，请求体严格限定：

```json
{"action":"open","location_id":123}
```

- `action` 仅允许 `open` 或 `reveal`。明确要求 `location_id`，且必须属于 URL 指向的当前 `asset_id`；不得接受原始路径、可执行文件名、启动参数、任意协议或自由命令。
- 仅允许在 **Windows + 服务确实只绑定 loopback + 真实请求对端为本机 loopback** 时执行；不信任 `X-Forwarded-For` 等头。非本机/非 Windows 返回结构化拒绝，不启动子进程。保持现有会话和 CSRF 强制校验。
- 执行动作前重新解析选定 location 并通过现有受控读取的路径边界和 size/mtime 校验；若不可用，返回原图不可用/位置失效，不自动选择其他副本。校验到启动间的变化尽可能缩小；不声称能消除全部 TOCTOU。
- `open`：仅接受明确图片扩展名白名单（常见 JPEG/PNG/WEBP/BMP/GIF/HEIC/HEIF/TIFF/AVIF/MPO，以及项目确有需求的相机 RAW 格式），用 `os.startfile(verified_path)` 调用系统关联；拒绝快捷方式、脚本、可执行程序及扩展名异常文件。**不使用浏览器 `file://` 跳转**。
- `reveal`：只调用系统 `explorer.exe` 并选中已校验文件；使用受控参数、`shell=False`，正确处理 Windows 中文、空格、UNC 路径，避免让文件路径被解释为命令或开关。不要打开文件默认关联程序。
- 系统调用在不长期阻塞 ASGI 事件循环的执行路径发起；成功只表示**已请求操作系统启动**，不能保证应用窗口已显示或文件一定被第三方程序成功读取。失败按统一 API 错误信封反馈，不返回绝对路径、异常栈、命令或敏感信息。
- 这是用户主动点击才发生的动作，不提供 GET 触发、后台自动打开、批量打开或任意程序执行能力。

## 前端细节

- 复用已收到的 `asset_id` 与每条 `location_id`，通过现有携带 CSRF 的写请求模式发起 POST。
- 按钮放在对应位置附近，正常情况下仅对 `availability=PRESENT` 提供可点击操作；后端仍必须二次校验。
- 请求中禁用点击目标按钮、防止重复请求；结束后恢复。成功提示「已请求系统打开」/「已请求资源管理器定位」，失败提示简短原因。
- 组件 `dispose`、快切照片、关闭弹窗后的旧响应不准修改新照片 UI；保持现有预览/人物/地点和键盘关闭逻辑。
- 不依赖 `previews` 是否存在；原图离线但预览仍可看时，动作明确不可用。

## 最小验收（仅隔离合成数据）

用现有 API/前端测试夹具和 Mock 系统调用；不访问真实 PhotoSearchData，不真正启动成批外部程序。

1. **成功路径**：JPG 且中文/空格路径，两个 action 调到正确的系统适配器，返回仅表示启动请求已接受。
2. **位置与拒绝**：一个 asset 两个位置必须精确选中指定 `location_id`；跨资产、缺失/变更文件、恶意路径、非图片扩展名均不执行。
3. **权限边界**：未登录、缺/错 CSRF、非 loopback 客户端/非 Windows 均不能触发系统调用。
4. **公共前端**：同一组件显示正确按钮/状态；重复点击受控；切换照片、关闭弹窗后旧返回不串图。

只运行新增及直接受影响的定向 `pytest -q`（输出 JUnit XML）、修改 Python 文件的 Ruff、`node --check src/photo_search/web/photo-info.js` 和 `git diff --check`。不跑全仓、30 万容量、模型/GPU/打包、浏览器矩阵或独立多轮审核。Windows 真实手动验收由用户选择一张照片分别试点两个按钮，不冒称已经执行。

## 不在本卡范围

不改 schema、迁移、索引、来源扫描、识别人脸/OCR/GPS、搜索/人物状态机、撤销记录、媒体生成与 GC；不改原图内容，不移动/重命名/删除文件；不添加第三方依赖、桌面客户端或新的 viewer。

## 交付与停止

实现完成后写简短 `docs/reports/P4-PHOTO-LOCAL-OPEN-A.md`，记录必要定向测试及本机手动未验项；按仓库约定更新 `docs/development-status.md`。仅任务完成后才能标 IMPLEMENTED/PASS。如果发现必须放宽本机调用安全边界或需要跨平台桌面架构，停止扩大范围、记录问题另行决策。
