# P4-UI2-VIEWER-SIZE-STABLE-A — 照片查看器固定外框与视口自适应修复

- **状态**：READY / 待实施。本次仅创建任务卡，不代表产品代码已修改。
- **优先级**：P1；关闭/缩放按钮被遮挡或无法使用按 P0。
- **规划基线**：`szzeng/aisearch` / `master` HEAD `228132018fa6a7cbf82d608b332f56ea61d0ef24`（2026-10-14）；执行前再次核对最新 HEAD 和工作树。
- **推荐模型**：GPT-6 Medium，小范围 CSS/JS 布局修复；若涉及 viewer 状态机、路由、权限/人物写入协议则停止扩大范围，按仓库要求提升复核与模型。
- **验收预算**：个人项目；必要的定向 pytest/JUnit、静态检查和 **1 条 Codex 内置产品浏览器几何闭环**。不跑全仓、GPU、真实图库或 20/30 万性能测试。
- **前置关系**：[P4-UI2-VIEWER-UNIFY-A](P4-UI2-VIEWER-UNIFY-A.md) 共享查看器已实施（FUNCTIONAL SCOPED PASS / ACCEPTANCE PARTIAL）；本卡只修查看器外框几何，不能自动关闭前卡其它 PENDING。

## 1. 用户确认的行为合同

**照片查看器外框大小只由当前浏览器可用视口、明确的响应式断点与安全边距决定，绝不能反过来取决于图片显示比例或当前照片的长宽。**

- 固定浏览器窗口：`适合窗口`、`预览100%`、加号/减号（含 10%–800%）、双击、Ctrl+滚轮、拖动、切换横/竖/长竖图、换下一张、加载/回退缩略图时，**弹窗外框的 x/y/width/height 不变**；信息栏/扩展区展开状态不变时，**图片舞台 width/height 也不变**。
- 图片缩小时：仍在原有大舞台内居中，不把弹窗缩小为图片大小；放大时：仅内部图片缩放、裁切、可拖动平移，不把工具栏和外框撑大。
- 照片信息（完整路径、人物、GPS、地图等）异步变长，只可增加**内部**滚动，不能拉高或拉宽弹窗。
- 用户显式折叠/展开信息栏、人物/搜索扩展区域时，**仅内部重新分配面积**；浏览器窗口真正变化、全屏、移动设备横竖屏或动态可用视口变化时，弹窗可依视口规则跟随。
- 任何布局修改不能损坏统一 viewer 现有缩放算法、上一张/下一张、Esc/遮罩/Back、搜索返回、地图范围和人物脸部业务按钮。

**主要验收标准**：视口尺寸和信息栏展开态相同的情况下，任意缩放、切图、加载前后，`dialog.getBoundingClientRect()` 的四项 `x/y/width/height` 分别变化 **≤1 CSS px**；`.photo-viewer-stage` 的 `width/height` 变化 **≤1 CSS px**。浏览器视口实际改变时应按公式变化，不能错误套“外框不变”。

## 2. 最新代码事实与待证根因

执行先读 `AGENTS.md` → `docs/development-status.md` → 本卡 → [统一查看器实施报告](../reports/P4-UI2-VIEWER-UNIFY-A.md) → `photo-info.css/js` 与相关 UI 测试；不要重复阅读无关历史。

| 当前代码 | 需要修复/核实的点 |
| --- | --- |
| `.photo-viewer-dialog { width:min(96vw,82rem); max-width:96vw; max-height:94dvh; overflow:auto; }` | 只有最大高度，缺少**确定的外框高度**。需检查原生 dialog UA 默认 max-size、box-sizing、定位和内容相关高度 |
| `.photo-viewer-layout` 双列 grid、`.photo-viewer-figure` 的 `min-height:15rem` / `max-height` | 内容和各层 min-content/min-height 可能影响外框；需要构建固定剩余空间约束链 |
| `.photo-viewer-stage { height:100%; overflow:hidden }`、`img` 的 CSS 宽高无限制 | 百分比高度依赖祖先的确定尺寸；图片由 JS 设置内联宽高，不能反向撑开 grid 轨道 |
| `photo-info.js` `applyScale` 写入 `image.style.width/height` 并改变 transform | 此行为本身可以保留；必须由 CSS 舞台隔离这些尺寸，使它们只影响内部图片 |
| `ResizeObserver` 观察 `stage` 和 `panel` 并在适窗模式重算 | 若布局未解耦可能形成反馈循环；确认只响应真实舞台可用尺寸变化 |
| `@media(max-width:760px)` 改单列、figure/信息栏上限使用 dvh | 不能把多段固定 min-height 累加后撑高弹窗；窄屏仍要视口驱动外框 |

以上是**基于源码的根因推断**，不是已记录的真实布局度量。用户已报告弹窗随照片缩小。执行者先用项目隔离合成 viewer **测量**：视口 `innerWidth/innerHeight`、dialog/layout/figure/stage 的 `getBoundingClientRect()`，明确变化的是 x、y、高度、宽度还是滚动条，然后修复反馈链路。不能仅加一条 `height:92dvh` 就宣称完成。

## 3. 最终外框与内部空间规则

### A. 桌面尺寸

- 默认 `width:min(94vw,1500px)`，`height:92dvh`，`box-sizing:border-box`，在浏览器可用视口中水平/垂直居中。极小窗口留至少约 8px 安全边距：可结合 `calc(100vw - 16px)` / `calc(100dvh - 16px)` 限制，不能固定死某屏幕像素宽高。
- 必须是**明确 width + height**，而不是单独 `max-height`；清理冲突的 UA dialog `max-width/max-height`、默认 margin/位置与旧布局约束。关闭态始终不可见：若用 `display:grid`，应仅用于 `dialog[open]` 或内部布局容器，不覆盖原生关闭隐藏。
- 94%/92%/1500px 作为产品设计默认，若实际窄屏出现无解遮挡，允许记录依据后针对断点小幅调整；禁止基于当前图片的 `naturalWidth/Height`、缩放比例、加载状态或文件名计算外框尺寸。

### B. 内部布局

单向布局链：**浏览器可用视口 → 弹窗固定外框 → 顶部标题栏/中部内容/受限扩展区 → 图片舞台与工具栏/照片信息栏 → 图片自身**。

1. 外框建议使用 `grid-template-rows:auto minmax(0,1fr) auto`（或等价的稳定 flex）：标题栏自然高度；中间内容吸收剩余空间；下部人物/搜索扩展区最大高度受限并自己滚动。**没有内容的扩展区不得无故占一整行**；同一会话不同照片扩展内容长度变化，不能挤压图片舞台。
2. 中部两列采用 `minmax(0,1fr)` + 合理的信息栏宽度（如约 280–360px/现有比例），`min-width:0;min-height:0` 必须贯穿 grid/flex 子项；长文件路径应在信息栏中折行或滚动，不能撑开整窗。
3. 左侧 figure 使用 `minmax(0,1fr) auto auto` 等稳定行轨：舞台占用确定剩余高度；缩放工具栏、状态文字的布局空间要受控，避免某张照片显示“原图离线”、搜索位置或长按钮导致舞台忽大忽小。导航按钮出现与否只可由**入口配置**改变，不能因同一列表换图忽然换行跳高。
4. 舞台 `overflow:hidden`，确保图片即使设为 800% 也不会决定舞台尺寸。优先用正确 grid 约束、定位图层或局部尺寸隔离；**不要盲目对整个 dialog 用 `contain:size`**，否则可能破坏对话框/辅助信息排版。
5. 信息栏 `overflow-y:auto`、`min-height:0`；GPS 小地图、人脸列表、长路径、错误和缩略图回退不能让 dialog 长高。内部滚动条若导致舞台宽度抖动，可局部采用 `scrollbar-gutter:stable`，不要改变全站滚动条。
6. 显式收起信息栏可以改变舞台宽度，但外框不变；若当前模式为 `fit`，按新的舞台尺寸重新适配。手动 `100%` 或 zoom 应保持用户选择的倍率，必要时只重算并限制 pan 位移。

### C. 窄屏/矮窗

- 保留约 760px 的布局断点：统一弹窗外框继续按可用视口比例给出**确定高度**；内部改成图片优先的单列，信息和人脸操作在下方收纳/独立滚动。
- 以约 `470×764` 和 `720×450` CSS 视口检查：`min-height:12rem/15rem` 之类不能叠加到超过可用高度；不足时缩短说明文字/留白、压缩工具栏并使用内部滚动，但关闭/适窗/放大缩小以及必要确认取消应可达。
- 浏览器地址栏、软键盘、系统窗口变化可能真的改变 `dvh/visualViewport`，此时外框允许响应；**单纯缩放图片不能响应为弹窗几何变化**。不把窄视口当作原生浏览器缩放 200% 的证明。

### D. 操作/尺寸不变量

| 动作（浏览器尺寸未变） | 弹窗几何 | 舞台几何 | 图片行为 |
| --- | --- | --- | --- |
| 放大/缩小、适窗、100%、双击、Ctrl+滚轮、拖动 | **不变** | **不变** | 缩放/位移，必要时内部裁切 |
| 下一张/上一张、横图→竖图/长图/小图 | **不变** | **不变** | 切图并重新适窗 |
| 预览加载成功/失败、缩略图回退、长文件路径/GPS/人物加载 | **不变** | **不变** | 只改变内部内容 |
| 用户显式打开/关闭信息栏或人物扩展区 | **不变** | **允许内部重新分配** | fit 重算，手动倍率保留 |
| 浏览器 resize、全屏、横竖屏/可用视口改变 | **按比例变化** | **按可用区域变化** | fit 重算，手动倍率保留并 clampPan |

## 4. 实施顺序与禁止项

1. **先量再改**：固定浏览器视口测当前几何，找出造成弹窗跟随图片缩放的具体布局链，记录修复前至少一组数据。
2. **CSS 优先**：原则上只改 `src/photo_search/web/photo-info.css` 的 dialog、layout、figure、stage、controls、panel、extension、窄屏媒体查询；避免在 `search.css`、`persons.css`、`map.css` 各造一套尺寸。检查 `dialog[open]` 可见、关闭态不可见。
3. **JS 只补必要修复**：如有证据才动 `photo-info.js` 的 `fitPreview`、`clampPan`、`ResizeObserver`；只在实际舞台几何变化时 fit，切勿因为图片宽高变更自激。手动缩放状态在窗口变化后要保留倍率并夹紧位移。不要修改现有历史、焦点、上下张、异步 generation、人脸写操作协议。
4. **静态资源同步**：若共享 CSS 修改，核对 `index.html` / `persons.html` / `places.html` / `search.html` / `map.html` 对 `photo-info.css` 的现有版本，只更新它们对应 CSS URL cache-bust 与必要的静态断言，禁止回退最新 search-scope/人员任务的其它脚本版本。
5. **共享入口抽验**：首页时间轴/普通列表共用一个弹窗；人物/合集附加操作、搜索结果导航、地点和地图继续使用同一个 `AIPhotoInfo.open()`。无需重做全部跨页业务测试。

**禁止范围**：不改数据库、来源扫描、媒体预览规格（目前预览长边 1600）、CR2/RAW 解码、AI 模型、搜索 session/cursor、时间轴 SQL、地图 bbox、人物/地点人工归属、权限/CSRF、批量选择、主题系统及原图库。不得访问个人真实 `PhotoSearchData`，不得依赖外部浏览器驱动/绕过产品认证。若涉及高风险协议变更，先记录并拆单独任务。

## 5. 最小验收（必须证明真实几何）

| ID | 场景与期待 |
| --- | --- |
| **S01 必测** | 固定桌面视口，横图`适窗→100%→多次减号→多次加号→适窗`：各步 dialog `x/y/width/height` 变化 ≤1 CSS px、stage `width/height` 变化 ≤1 CSS px；图片缩小仍居中、放大可平移 |
| S02 | 固定视口切横图/竖图/长图/小图及合法上下张：dialog 和 stage 不变，图片完整适窗，工具栏可触达 |
| S03 | 人物扩展、长文件路径、GPS 信息、缩略图回退/缺图模拟：dialog 不变，内容内部滚动；单独测试信息栏收起仅内部重排 |
| S04 | 浏览器从 1440×900 调到 1280×720，再调回：dialog 按宽 94%/最多 1500px、高 92dvh 与安全边距更新；fit 自动重算；手动 zoom 不丢 |
| S05 | 470×764 窄视口、720×450 矮视口：窗口内完整容纳弹窗，关闭与缩放可用，无不可控横向溢出 |
| S06 | 简单检查 Esc/关闭、搜索上下张、人物操作扩展、地图入口中的至少一类：布局改动不影响原查看器行为 |

**几何测试方法**：测量前固定 `window.innerWidth/innerHeight`（有 `window.visualViewport` 也记录），等待当前布局稳定，再收集 `dialog/layout/figure/stage.getBoundingClientRect()`；确认比较前后视口没变。S01/S02 应记录真实数字，**源码字符串断言不能替代像素几何证明**。S04/S05 必须验证跟随视口的规则与控件可用，不能错误应用 S01 的固定尺寸容差。

**轻量自动测试**：优先复用 `tests/unit/test_photo_view_info_ui.py`，必要时新增一个直接相关测试；必须写 JUnit XML 到 `test-results/P4-UI2-VIEWER-SIZE-STABLE-A.xml`，读取真实测试数和失败数。只改 CSS 时无需全站 JS 检查；若改 `photo-info.js` 则 `node --check src/photo_search/web/photo-info.js`；最后 `git diff --check`。保留实际命令/结果，不虚构 PASS，不跑全仓/GPU/大图库压力测试。

**浏览器**：按 `AGENTS.md` 的已固化流程使用 Codex 内置浏览器，产品服务绑定 `127.0.0.1`、项目内隔离合成图库、启动器生成的一次性认证会话。重点**1 条 S01 产品浏览器几何闭环**，工具允许时再补 1 条 S05 窄屏/长图。仅按需要导出 0–1 张真实截图至 `docs/reports/artifacts/`；不可读取 rect/无法调整窗口时记 `GEOMETRY_BROWSER=PENDING_TOOL_LIMIT`，可用可信计算布局测试辅助证明，但**不能将静态 DOM 测试说成实际产品浏览器几何通过**。避免重复无用截图、浏览器工具重试和外部驱动。

## 6. 交付与停止条件

实施后产出 `docs/reports/P4-UI2-VIEWER-SIZE-STABLE-A.md` 和 `docs/development-status.md` 简短记录，说明**问题复现数据、根因、最终外框公式、受影响文件、S01–S06 真实结果/JUnit/截图路径及未验项**。单列 `LAYOUT_CODE`、`GEOMETRY_BROWSER`、`ZOOM_INTERACTION`、`NARROW_VIEWPORT`、`REGRESSION` 状态。

若代码/定向测试通过但产品几何因工具不可测，只可写 `FUNCTIONAL SCOPED PASS / GEOMETRY_BROWSER=PENDING`，整卡验收保留 PARTIAL；**若实际外框仍随图片缩放，记 GEOMETRY_FAIL 并定向修复复测**，不可用工具限制掩盖已观察到的产品错误。

本卡完成后停止，不追认 `P4-UI2-VIEWER-UNIFY-A`、`VIEWER-F`、`VISUAL-FINAL-A` 等遗留 PENDING，不扩大到全部 UI2 浏览器/性能专项。不自动开始其他任务。
