# P4-UI-SHELL-A-F1 — UI 2.0 公共视觉与搜索页收口

日期：2026-10-13。**IMPLEMENTED / VISUAL VALIDATION PENDING**。推荐 GPT-6 High（LUNA High 可执行），A 级前端展示小改，个人定向简测与视觉复核。实现与定向代码验证已完成；1440/640 与暗色截图已落盘，真实 200% 浏览器缩放和搜索命中态截图仍待补验，未标整卡 PASS。

## 定位与施工次序

以最新 `master`、`AGENTS.md`、`docs/development-status.md`、[A 报告](../reports/P4-UI-SHELL-A.md)与[C 报告](../reports/P4-UI-SEARCH-C.md)为执行基线；本卡设计时 HEAD 为 `2df1428`。开工时重查 HEAD 和工作区，不覆盖其他任务改动。

A 已完成五页统一导航和外壳，C 已完成五页唯一顶部搜索框、中文输入和既有提交链路，C 定向 44/44 通过、LUNA High 复核通过；但两卡最终 demo 视觉、暗色/真实缩放/截图仍待补验。**本卡只做 A 公共视觉补差 + C 搜索页完整视觉 + 后续 UI 的公共样式合同；不重做已验功能，不改变后端。**

实际后续顺序：**A（已施工）→ C（已施工）→ F1 → CORE → B → D → E → SEARCH-MAP → F**。F1 的公共视觉不等于 B/D/E/F 页面主体视觉已完成；不能自动将 A/C 历史状态改 PASS。

## 唯一现行视觉参考

[最终可点击演示](../mockups/ui2-interactive/demo.html)，辅以[页面映射](../mockups/ui2-interactive/README.md)及同版 `index.html`。旧 SVG/PNG 仅留档。执行者实际打开演示的导航、顶部搜索、搜索结果、条件 chips、展开筛选、空态与错误状态；再与合成数据产品页面同窗口宽度对照。若 `file://` 受限，尝试允许的隔离本地静态 HTTP；浏览器仍无法查看则记录 `VISUAL_PENDING`，不能只凭 README 标记对照通过。演示里的假照片/180条/假计数不得复制成产品事实。

业务真实身份、权限、日期、成员资格、分页、人工写入和搜索状态合同优先；视觉表现以最终 demo 为准，发生冲突应说明并保留正确业务功能。

## 一、全站公共视觉（只做共享外壳）

- 更新 `src/photo_search/web/styles.css` 现有 `--bg`、`--panel`、`--fg`、`--fg-muted`、`--line`、`--accent`；增加必要的 `--tint`、间距/字体/圆角/阴影语义变量。演示亮色：`#f6f8f7 / #fff / #253a38 / #74847e / #e3eae5 / #247967 / #eaf3ed`；暗色：`#182320 / #22312b / #e3ece4 / #a3b3a7 / #36483b / #9ac8ac / #2d4436`。警告、错误、危险操作仍保留独立高对比语义色。 demo 的浅色 `--fg-muted: #74847e` 实测普通文字对比度不足；按可访问性要求由 `#5f7068` 覆盖，F1 报告记录对各底色的对比度。
- 系统字体，不引外部字体/图标包。按 demo 校准正文约14px、页面标题约27px、区块标题约18px、辅助文字约12px 的**视觉层级**；**禁止直接将全局 `html` 16px 根字号一刀切改为14px**导致旧 rem 组件整体缩小。通过语义变量与局部组件样式适配浏览器字体放大。
- 五页 `/`、`/search`、`/persons`、`/places`、`/map`（含 `/?view=sources`）统一品牌、导航图标+文字、当前态、辅助导航、页头、顶部搜索框、主内容留白及基础按钮/输入框。主导航照片/人物/地点，地图与图库管理次级；明暗主题、窄屏、focus-visible 都需可操作。
- 保留 C 单一全局自然语言搜索框和原 `/search` 路由；人物/地点目录局部筛选与全局搜索明确区分。保持人物能力门控、地图高度/resize、Esc、详情返回、来源管理入口与无障碍名称。不得用裸 `header/main/section/.grid/.panel` 改法污染页面。
- **不在本卡重排照片时间轴、人物头像/合集、地点卡片、图库管理内部控件**；仅继承公共基础，页面主体仍交 B/D/E。

## 二、搜索页完整视觉（必须交付，不是只换色）

- 顶部单框：保留 `#search-form`、`#query-text`、原 `search.js` 提交/IME/快照、`q` 深链；按 demo 统一尺寸、图标、圆角、间距、搜索按钮、焦点和窄屏布局；不得产生第二自然语言表单或双请求。
- 首屏：突出搜索、已生效条件、结果；将大段技术说明收进可访问的折叠说明，不隐藏真实错误、限制或来源解释。
- `#panel-query`：chips、`#result-intent-scope`、`#result-place-scope`、展开/收起高级筛选、按钮与表单层级符合 demo。保留原来源/地点/时间/人物条件、`#query-apply-filters`、`#query-reset` 功能；服务端范围尚未贯通时不得假展示可用 scope。
- `#panel-results` / `#result-grid`：调整照片为主的网格、缩略图比例、卡片间距、圆角、命中说明、辅助文字及加载更多/无结果/错误/重试/截断提示；不虚构数量，不在前端全量排序，不改算法或分页。
- `#panel-upload` / `#panel-coverage`：保留现有上传协议、限制提示和按需读取，把这两区整理成不抢占搜索首屏的次级工具。搜索预览弹窗仅修公共主题；完整查看器由 F 负责。
- 搜索专属 CSS 用 `body.search-page` 或等价明确根作用域，避免修改其他页面的 `.grid/.panel/.chip`；梳理重复旧规则而不是在 CSS 尾部无止境追加覆盖。最小 HTML 调整必须保留 DOM ID、`hidden`、form 关联及脚本生命周期；静态资源引用版本同步更新。

## 三、允许范围、文档交付

主要是 `web/styles.css`、`web/search.css`、五页公共外壳 HTML、搜索页展示结构和必要少量页面 CSS 兼容；JS 仅在必要时做展示兼容。禁止改 API、schema、搜索召回/排序/会话/游标、人脸、地点、撤销、人工归属与用户真实 `PhotoSearchData`；不引入框架、CDN 或演示用虚假业务数据。

- 完成并核对 [统一视觉规范](../plans/P4-UI-VISUAL-TOKENS.md)，按实际 CSS 选择器与偏差更新；演示仍为整体观感权威。
- CORE/B/D/E/SEARCH-MAP/F 六卡必须继承已落地公共视觉且各自负责对应内部页面；仅补少量引用和验收文字，避免重复抄调色板、反复重改外壳。
- 写报告 `docs/reports/P4-UI-SHELL-A-F1.md` 和简短 development-status；保留 A/C 原报告历史、独立记录 F1 新证据。

## 四、最小验收

1. **实图对照**：隔离合成图库，至少首页公共外壳、搜索有结果、搜索高级筛选展开、空或失败态；人物/地点/地图抽查公共壳未回归。1440px 桌面、640px 窄屏；暗色、**真实浏览器** 200% 缩放各抽查一次。`visualViewport.scale` 不能单独判定桌面浏览器 zoom，不能用缩小视口冒充真实200%。
2. **截图**：合成产品截图保存 `.tmp/P4-UI-SHELL-A-F1/screenshots/`，注明路由/宽度/主题，与 demo 对照；无法模拟暗色或缩放或落盘须分别记录 PENDING，不能整卡写视觉 PASS。
3. **小回归**：按影响复用 `tests/unit/test_p4_ui_shell.py`、`test_p4_ui_search_entry.py`、`test_p4_search_nl_ui.py`、`test_search_place_nl_ui.py`；浏览器核对中文 Enter 只发一次请求、空词零请求、高级筛选、历史返回及局部筛选。改 JS 才做对应 `node --check`，改 Python 测试才 Ruff；`git diff --check` 必做。A 原有其他5项失败单列，不能伪修测试造绿。
4. **成本**：不跑全仓/GPU/OCR/模型/20万图片性能；后续 CORE/E/SEARCH-MAP 的20万元数据性能硬门槛不变。浏览器/工程/视觉分别记录 PASS 或 PENDING，不以 DOM 测试冒充视觉验收。

F1 完成后重新检查 A/C 各自待验，证据不足仍维持待验。**本卡完成即停止，下一候选 CORE，不自动施工后续卡。**
