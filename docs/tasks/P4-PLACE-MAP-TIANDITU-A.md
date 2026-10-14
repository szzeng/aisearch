# P4-PLACE-MAP-TIANDITU-A — 天地图替换、免费底图切换与本机 Key 配置

> **日期**：2026-10-14。**状态**：IMPLEMENTED / ACCEPTANCE_PARTIAL（2026-10-14；定向验证与浏览器 A/B 完成；Windows ACL 和真实天地图联网待验）。
> **优先级**：P1（中国大陆无 VPN 的地图可用性）。
> **执行建议**：GPT-6 Medium/High，同一会话完成代码、修复、定向验证；模型由用户手动选择。个人使用、最小必要测试。
> **建卡时参考 master HEAD**：`de4497c2e5eda362528b0f1eefb109960b6ab975`。实施时必须先重新核对最新 HEAD，不用本卡旧快照覆盖后续提交。
> **读取顺序**：`AGENTS.md` → `docs/development-status.md` → 本卡 → 直接相关代码、接口与定向测试；既有地图合同参见 [P4-PLACE-MAP-A](P4-PLACE-MAP-A.md) 与 [实施报告](../reports/P4-PLACE-MAP-A.md)。

## 1. 问题与边界

- 当前 `web/map-config.js` 仍写死 `provider: "osm"` 及 `https://tile.openstreetmap.org/{z}/{x}/{y}.png`；`web/map.js` 使用一个 OSM raster source；`web/photo-info.js::showMap()` 也独立硬编码 OSM fallback。大陆网络下可能打不开底图。
- 地图页使用**本地打包** MapLibre GL JS 4.7.1（UMD），照片位置 WGS84，现有 `/api/v1/map/{summary,points,assets}` 的 bbox、版本、聚合、分页及 `/map?asset_id=...` 定位均已可用。只换可视化在线底图，不重做地图业务。
- 用户已有天地图**个人浏览器端 Key**，需要在应用内输入、可改、可清除、下次启动继续使用；**绝不能要求把真实 Key 发给模型、写在任务卡/测试/源码或提交 GitHub**。
- **零付费原则**：仅接入天地图账户当前可用的免费基础地图瓦片；不接收费产品、购买套餐、第三方代理、在线逆地理/路径/搜索、下载离线瓦片。免费配额和使用条款依天地图实际控制台政策，软件不承诺永久免费或无限次调用。

**成功形态**：本机首次打开地图相册 → 提示配置 → 录入 Key 保存（无需重启） → 在大陆非 VPN 网络加载标准图 → 可切卫星影像 → 地图点/列表/详情小地图正常；即使无 Key 或瓦片失败仍可浏览 GPS 照片列表。

## 2. 关键决定：只在地图页配置，不新增初始化向导/独立设置站

### 2.1 入口与视觉

- `/map` 页头增加紧凑的“地图设置”入口；首次无 Key 时显示空状态：“未配置天地图，照片列表仍可用”＋“配置地图”按钮。不要在全站启动或普通看图时强制弹窗。
- 复用地图页上的小型设置面板/对话框（`<dialog>` 或现有模式）；含**地图服务：天地图（固定）**、密码型 Key 输入（默认不显示）、“保存”“测试连接”“清除 Key”、连接/错误状态、官方申请入口。Key 保存成功与瓦片连通成功是两个**独立状态**；网络断开也允许保存正确格式的 Key。
- 已配置时只展示“已配置”，**不回填 Key 全文**；要替换时显式输入新 Key。删除 Key 二次确认，清除后立即进入未配置空状态。支持键盘关闭、焦点返回、窄屏/浅暗主题，继承 UI 2.0 公共视觉 token。
- 不新建 `/settings` 路由，不添加图库初始化步骤。可在图库管理处加一条通往 `/map?settings=1` 的简短入口，但不得再复制一份 Key 表单。设置动作成功后不刷新主页面；`?settings=1` 仅用于打开设置，不包含 Key，关闭后不污染地图深链。
- 地图区域提供“标准地图 / 卫星影像”双态切换，默认标准地图。切换即时、保留视口与照片叠加；将用户选择保存为下一次默认图层。照片详情小地图使用相同的已保存默认类型，不另加单张照片的切换工具。

### 2.2 数据根目录持久配置

- 保存到当前 **data-root** 下的 `settings/map-provider.json`（示例：`PhotoSearchData/settings/map-provider.json`），按项目 `resolve_data_root()` 取得实际路径，**不是**仓库路径、源码 `web/map-config.js`、catalog.db 或浏览器 localStorage。
- v1 结构仅含 `schema_version`、`provider="tianditu"`、`key`、`default_layer="vector"|"imagery"`。不保存坐标、图库数据或用户个人内容。初始文件不存在是正常未配置态；已有配置可升级读取，格式错误可见提示，不覆盖或删除原文件。
- 最小独立本机配置读写模块：输入长度/控制字符/空格及枚举白名单校验（不得假设一个未验证的固定 Key 字符位数）；仅接受固定的天地图 provider 和官方瓦片域名；原子写入（临时文件＋replace）、限制本机文件权限，拒绝符号链接/重解析点和非普通文件，不允许客户端任意传文件路径。写入失败保留旧 Key。
- 本机 Key 属于**敏感配置但不是客户端不可见的服务器秘密**：浏览器加载天地图 raster 瓦片时必然能在自身请求中看到 `tk`；不要做“前端完全保密”承诺。尽量限制系统文件访问，建议用户按天地图允许的来源/Referer 规则绑定本机域名。不要为了藏 Key 新增在线代理或付费服务。
- 禁止在 GitHub、静态 HTML/JS、页面地址栏、window.name、sessionStorage/localStorage、错误报告、JUnit、截图或应用日志记录实际 Key；测试使用明显的假 Key。地图服务本身会收到包含 Key 和当前请求瓦片坐标的 HTTPS 请求，此行为须在设置说明中简短交代。

## 3. 受保护的最小 API

复用现有 `/api/v1/*` session + Host/Origin/CSRF 中间件，无需另设鉴权系统，具体路径若现有路由冲突可按项目风格小幅调整：

1. `GET /api/v1/map/provider-settings`：返回 `configured`、`provider`、`default_layer`、`status`，**不返回完整 Key**；`Cache-Control: no-store`。
2. `PUT /api/v1/map/provider-settings`：JSON 正文新增/替换 Key 和/或更新默认底图，遵守 CSRF；不允许意外空串覆盖旧 Key；保存后只返回脱敏结果。
3. `DELETE /api/v1/map/provider-settings/key`：需要有效会话及 CSRF，清除 Key，之后运行时接口不得泄露旧 Key。
4. `GET /api/v1/map/provider-runtime`：**仅对已认证会话**返回浏览器构建瓦片请求所需的最小运行时 Key/provider/默认图层信息；响应 `no-store`、客户端 `cache:"no-store"`，仅存在内存，页面卸载不保存。无 Key 返回明确 `not_configured`，不返回伪配置。

**禁止**在未受保护的 `/web/map-config.js` 或静态资源/HTML 内注入 Key；仅保留纯公开模板常量和 MapLibre style 构建函数。不要把 Key 放在调用本地配置接口的 query string 中；提交用 JSON 正文，瓦片 URL 的 `tk` 参数仅用于向天地图请求。服务端错误和日志必须脱敏，异常堆栈/浏览器文本不可拼完整瓦片 URL。

## 4. 地图技术实现与必须保留的既有状态

### 4.1 天地图四个 raster 图层

| 图层 | 天地图服务 `T` | 用途 |
| --- | --- | --- |
| 标准底图 | `vec_w` | 道路/河流/地貌 |
| 标准中文注记 | `cva_w` | 中文地名/道路标注 |
| 卫星影像 | `img_w` | 影像 |
| 卫星注记 | `cia_w` | 影像地名/道路标注 |

- 使用官方 `https://t{n}.tianditu.gov.cn/DataServer?T=...&x={x}&y={y}&l={z}&tk=...` 模式，固定官方域名/允许的图层枚举；URL 编码 Key，`tileSize=256`；按实际官方服务能力设定 source min/maxzoom，超过上游最大瓦片 zoom 时由 MapLibre overscale，**不能继续请求不存在的层级**。
- **必须使用 `_w` Web Mercator 的 XYZ 瓦片**，不能混用 `_c` 经纬度瓦片；不采用 GCJ-02 的高德/腾讯坐标处理。原 GPS 与后端查询是 WGS84；天地图底图数据基准差异不得通过修改存储坐标“纠偏”，需以深圳/杭州等合成 GPS 点目视验证实际对齐。
- raster 底图下层、中文注记其上层、照片点/聚合永远处于可点击的叠加层，且需保留合法地图来源署名、审图等第三方要求。不得移除署名以追求视觉一致性。
- 只加载当前可见底图/注记组合；切换时优先更新 raster layer visibility，不直接 `map.setStyle()` 丢失 `photos` source、cluster、单点、聚合数量 DOM Marker 或事件。若现有层未初始化，按受控顺序初始化并正确回收，不能重复堆积 listener/marker。
- `web/map-config.js` / 可选小型 `map-provider.js` 构建工具集中负责图层 URL、署名、类型映射；`map.js` 与 `photo-info.js` 共用，不再保留第二套 OSM fallback URL。脚本仍使用项目本地 vendor 的 MapLibre 4.7.1，不新增线上 JS/CSS/字体依赖。

### 4.2 不动的地图业务合同

以下数据与行为必须原样：

- `/api/v1/map/{summary,points,assets}` 的 WGS84 bbox、成员 revision、一致快照、keyset 游标、点/网格聚合、按拍摄时间排序、48 张分页等；
- `/map?asset_id=...` 单张 GPS 深链、中心与缩放、聚合点击及同位置成员列表、照片详情弹窗/返回时视口与列表恢复；
- 地点识别数据包、人工地点归属、相册与地图点坐标分离、原图 EXIF/向量/人脸/OCR/建库流程；
- 当前页面范围、照片计数与 GPS 成员**不因更换底图或 Key 产生业务 revision**。

重点回归：当前 `map.js` 在 `map.on("load", refresh)` 后才取点/列表、`map.on("error")` 仅在 style 未加载时回退。应将地图瓦片加载状态与**照片成员读取状态解耦**，确保瓦片未加载、Key 缺失或限制时照片列表仍可正常分页；有有效 bbox 时沿用对应 bbox，无地图视野时明确提示“全部带位置照片”，不得误报“无 GPS 照片”。

## 5. 失败状态和免费配额保护

- 区分 `NOT_CONFIGURED`、`CONFIG_UNREADABLE`、`TILE_UNAVAILABLE`、`MAP_RENDER_UNAVAILABLE` 和照片 API 错误；Key/Referer/配额/网络通常无法仅靠瓦片错误精确断因，因此展示“可能是 Key、来源限制、网络或配额，请检查”的帮助，不伪造 HTTP 错误原因或声称已检测准确配额。
- “测试连接”只按需请求**一张固定、无个人定位信息的低缩放瓦片**（或等效受控真实地图 tile load 测试），设置合理超时；不能用成功读取配置、`map.on("load")` 单独冒充有效瓦片可用。不要因为同一个 URL 返回 HTTP 200 就在未验证图片有效时断言成功。
- 浏览视野只加载当前瓦片，不后台扫全图、不预取卫星大范围、不自动多次重试/轮询；短期失败提供手动重试。**不自动回退 OSM 海外服务器**，不会在用户未知时耗额外瓦片费用。
- 配置存储损坏/丢失、删除 Key、真实地图初始化失败时，正常浏览 GPS 照片仍可工作；外部地图服务失败不得误导成 “0 张照片”；错误时继续暴露可打开的地图设置入口。
- 默认标准地图；离线/错误时保留已知 GPS 和详情“在地图相册查看”链接，链接到地图相册后可见合理回退状态。

## 6. 预计文件和实施边界

主要：
- `src/photo_search/web/map-config.js`, `map.js`, `map.html`, `map.css`, `photo-info.js`；
- `src/photo_search/api/map_api.py`（或同前缀小型设置路由）；`src/photo_search/config.py` + 新增数据根本机配置读写模块；必要时 `src/photo_search/app.py` 注册路由；
- 必要的小型 UI 工具和相关 HTML 静态引用、`web.py` 缓存策略、定向 unit/integration 测试与 `docs/reports/P4-PLACE-MAP-TIANDITU-A.md`。

可选：图库管理页添加**链接**到地图设置，不另做表单。**不需要数据库 migration、新依赖、图库重新扫描或模型资源更改**。不修改安全中间件的已有放行规则；若确需改其契约，必须先记录原因并扩大定向安全回归。

实施前核对同一 HEAD 的 `P4-UI2-VIEWER-UNIFY-A`、`P4-UI2-VISUAL-FINAL-A`、`P4-UI2-PLACE-ALBUM-NAV-A` 已有更改，保证共享查看器与 UI 2.0 的可见性、状态、深链均不回退。

## 7. 验收：个人简单测试，必须分清真实联网与模拟测试

**必要自动检查**（用假 Key、临时 data-root、隔离合成 GPS；不触真实 PhotoSearchData）：

1. 新建/保存/改写/重启读取/清除 Key；非法输入、配置文件缺失/损坏、失败写入不破坏旧值；无 Key 的只读接口与运行时响应不返回虚假可用状态。
2. GET 状态不泄密、运行时 Key 只对带认证会话开放、写接口受 CSRF/Origin 保护，禁用缓存；静态文件与报告不含真实 Key；路径不能逃出 data-root。
3. Web Mercator source/四图层/URL 组装准确；模拟切换不重置照片 source、聚合事件、viewport、列表与当前选择，详情小地图使用同一配置。
4. Mock 外部瓦片故障/未配置/过期 Key 样例时，UI 显示失败，**照片 API 列表和分页仍可操作**；异步旧请求不能覆盖新配置或新底图。
5. 执行 Node `--check`、改动 Python 的 Ruff / compileall 和相关定向 pytest，输出 JUnit XML、`git diff --check`；不跑全仓/30 万压测/模型/GPU。

**必要浏览器冒烟**：按 `AGENTS.md` 在 Codex 内置浏览器和 **127.0.0.1 + 一次性会话**，使用项目隔离合成数据，仅核实 2～3 条核心路径：(A) 无 Key → 设置入口与分页回退；(B) 假 Key/受控瓦片失败 → 错误与回退；(C) 有受控可加载瓦片时标准/卫星切换 → 聚合/详情返回保持。截图 0～2 张、日志仅结论不带 Key。

**真实大陆可达性单独判定**：如果执行环境没有用户真实 Key 或不能从无 VPN 的大陆网络访问天地图，应记录 `REAL_TIANDITU_NETWORK=PENDING / USER_MANUAL_CHECK`，不能用 mock 瓦片、OSM 可达或假 Key 标记“大陆联网 PASS”。用户自行在本机设置页面输入自己的 Key，核验深圳等位置在标准/卫星两类图层可显示及图片点与地图对齐；无需把 Key 告诉开发模型。只要真实联网上的必要验证未做，就不得声明在线可用性已验证。

## 8. 完成、报告与审查

- 提交代码、定向检查记录和 `docs/reports/P4-PLACE-MAP-TIANDITU-A.md`；按实际执行状态简要更新 `docs/development-status.md`，不得预写 PASS。
- 同任务会话做一轮**限定于全部改动面**的独立检查，尤其覆盖本机配置文件写入边界、GET runtime Key 暴露的受保护范围、CSRF 与地图/照片状态生命周期；如改已有安全中间件或数据合同，升级到项目要求的全面审查。
- 可以在**功能定向通过、浏览器离线/模拟路径通过**时记录 `FUNCTIONAL_SCOPED_PASS`；外部天地图真实性能/大陆联网没有验证则标 `ACCEPTANCE_PARTIAL`，用户手工实测后再补结论，不用做大规模回归以凑“PASS”。
- 禁止访问真实用户照片或真实数据根；任务卡交付不等于实现完成。

## 9. 服务与技术参考

- [天地图开发控制台](https://console.tianditu.gov.cn/api/key)（实际 Key/白名单和免费配额以该账户控制台为准）；
- [天地图地图服务](https://lbs.tianditu.gov.cn/server/MapService.html)；
- [MapLibre raster source API](https://maplibre.org/maplibre-style-spec/sources/#raster)；
- 原任务 [P4-PLACE-MAP-A](P4-PLACE-MAP-A.md) 要求保留 WGS84、地图成员 API 与回退网格。

## 10. 本轮实施状态

- 状态：IMPLEMENTED / ACCEPTANCE_PARTIAL；定向结果、浏览器 A/B、Luna High repair-only 复核与待验边界见[实施报告](../reports/P4-PLACE-MAP-TIANDITU-A.md)。
- Windows ACL 未检查/未修改：用户明确拒绝授权更改；不声称本机 Key 文件访问控制已收紧。
- 真实天地图 Key/大陆网络与影像坐标对齐：PENDING / USER_MANUAL_CHECK。
