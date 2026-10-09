# P4-PHOTO-VIEW-A-review — 统一照片查看展示面独立复核记录

日期：2026-10-09。状态：**GPT-6 有界独立复核完成（按用户指示替代 DS-H）；整卡 VALIDATION PENDING**。实现报告：[P4-PHOTO-VIEW-A-implementation](../reports/P4-PHOTO-VIEW-A-implementation.md)。

本卡原为 DS-H 复核卡。用户明确接受当前 GPT-6 会话的有界独立复核替代 DS-H；以下记录是 GPT-6 复核结论，不代表 DeepSeek 审查。复核按 V3.1 §2.4 核对本卡改动、直接 API 映射、代码与现存证据，不重复无关历史全量测试。

## 唯一目标

独立确认共享照片信息面板及全部照片入口是否忠实呈现已存在的只读事实，路径是否仅在打开照片后按来源读取，GPS/人物展示是否遵守现有有效性规则，异步切换/关闭是否不会串图或残留地图；核对实现报告的定向测试证据，并明确真实浏览器闭环缺口。没有新增写操作、schema、坐标语义或权限边界的证据时，不扩成 DS-H 全仓审查，不建议 Sol/max。

## 必读顺序与范围

1. `AGENTS.md` → `docs/development-status.md` → `docs/tasks/P4-PHOTO-VIEW-A.md` → 本卡 → `docs/reports/P4-PHOTO-VIEW-A-implementation.md` → 当前真实 Git/index/worktree 状态。实现比较基线固定为主卡记录的`5d7bc139b2b73de5f30c77fbee9e6a1cc648f108`；该基线之后`search.js`等文件还包含并行任务变更，结论只覆盖本卡照片查看改动，不把其他任务改动记入本卡。
2. 按主卡范围读取 V3.1 §2.1/2.2/2.4、§3.2/3.4、§4.1、§11/12；V3.0 §4.1、§4.5.2/4.5.4、§10.1～10.3；ADR-0011 拍摄/文件时间展示条款、ADR-0014 只读 canonical 人物入口条款及 P4-PLACE-MAP-A §2～4。
3. 完整查看以下本卡改动和必要直接调用面：
   - `src/photo_search/web/photo-info.js`, `photo-info.css`
   - `app.js`, `index.html`, `styles.css`
   - `search.js`, `search.html`, `search.css`
   - `persons-albums.js`, `persons.js`, `persons.html`, `persons.css`
   - `places-albums.js`, `places.html`, `places.css`
   - `map.js`, `map.html`, `map.css`
   - `src/photo_search/api/web.py`
   - 新增/调整的 `tests/unit/test_photo_view_info_ui.py`, `test_search_person_album_link_ui.py`, `test_api_frontend.py`
   - `/assets/{id}`, `/sources/{id}`, `/assets/{id}/geo`, `/assets/{id}/faces`, `/persons/{id}`, `/map/assets` 已有响应模型/路由/成员谓词。

## 必核对的边界

- 列表/缩略图加载时没有批量读 source root；只有打开照片才按其 locations 查询 `/sources/{source_id}`。检查`D:\`盘符根目录、UNC/中文路径，以及以`/`或`\`开头、盘符相对路径、`.`/`..`段等非法relative_path；这些路径不得被误显示成完整路径。多位置排序和来源失败状态应可解释；完整路径不得进入媒体请求、地图、URL、日志或返回快照。
- EXIF 时间只依 `taken_time.label` 区分“拍摄时间/文件时间”；相机/缺字段不编造；Civil 排序键等高级字段默认折叠。
- GPS 必须同时满足地图成员总数、VALID/current input revision、WGS84/ASSUMED_WGS84、有限范围坐标及正确经纬顺序；`(0,0)`合法；无GPS、读取失败、底图失败分别不误报。地图对象在切换/关闭时移除。
- 人物以已有有效归属/canonical id 去重；测试不同assigned_person_id合并到同一canonical id时只渲染一次；封面头像→当前脸回退；人物详情失败不导致猜测；has_more 范围说明诚实。点击只进入受保护的人物相册。
- 检查全部六类入口的可聚焦按钮/链接；人物/地点头像卡继续导航其相册；人脸整理选择/纠正等操作不被图片查看截获；原分页、上下张、浏览器 Back 和页内返回状态保留。
- 异步 A→B、关闭后迟到的详情/来源/GPS/人物请求不得刷新旧面板；MapLibre script 加载和错误事件不得留下实例/监听。
- 检查静态资源版本/白名单清单是否完整，未引入依赖或业务接口变化。检查测试 oracle 对现有页面合同的调整是否最小且合理。

## 证据与停止条件

核对本轮最新 `test-results/p4-photo-view-a-review-fixes-r2.xml`（预期 113/0/0/0）与只读 API `p4-photo-view-a-api.xml`（2/0/0/0）、Ruff、全部已修改 JS 的 Node syntax、`git diff --check`。原实现基线报告中的111项统计仅作历史记录；先确认最新JUnit时间/统计与当前代码对应，证据缺失/代码变化时再补相应定向命令。

主实现报告说明真实浏览器闭环因本机 loopback `EACCES` 和 Codex 浏览器 `file:` 策略未能运行。独立复核不得把 Node DOM/HTTP/MapLibre桩当成真实浏览器证据或判整卡PASS；有可用的本机合成浏览器环境时，才补做8～12张合成照片、多位置/路径、GPS地图标记与降级、人物跳转返回、各页面入口、Esc/关闭/Back及状态恢复。禁止为复核连接 PhotoSearchData、访问真实照片、改 ACL 或绕过浏览器安全策略。

按 V3.1 §2.4 核对完整展示 diff、调用链及所需证据；报告写清审查方式、覆盖文件/入口、发现项、浏览器缺口及升级判断。若发现新增写操作或改变人工身份/坐标/权限/分页合同，升级完整受影响面审查；仅在 §2.2 命名条件有证据时建议 DS-MAX。否则 `SOL_NOT_REQUIRED`。未完浏览器必测保持 `IMPLEMENTED / VALIDATION PENDING`，不得标 PASS。


## 用户确认的复核路由（2026-10-09）

用户明确指示“独立复核代替 DS h”，本记录中的 GPT-6 有界复核作为本卡要求的独立复核结论，不再将单独 DS-H 复核列为待办。复核范围与边界见 [展示面复核报告](../reports/P4-PHOTO-VIEW-A-review.md)。主卡其他真实浏览器验收组合仍不完整，因此本卡状态不等于整卡 PASS。
