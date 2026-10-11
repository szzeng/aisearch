# P4-STORAGE-UI-A-REVIEW — 独立窄审与维护入口开放判断

日期：2026-10-11。状态：**COMPLETE / 用户确认审查报告通过，维护门闩已开放并完成启用路径定向验证**。
项目：`D:\SZ\project\aisearch`。开始前按 `AGENTS.md` → `docs/development-status.md` → 本卡恢复真实 HEAD、工作区及报告。
审查路由：**GPT-6 Luna / High**。用户明确批准该路由替代 DS-H；由独立审查者执行。用户在 Codex 中手动切换模型；不得自动切换或宣称已切换。

## 目标

独立审查 P4-STORAGE-UI-A 的完整改动文件、直接调用链、卡片/架构契约和指定合成证据。审查重点为权限、路径边界、lease/扫描锁与重复加锁、一次性请求消费和失败后不重试、未授权误删、人工人物/合集/地点事实保护，以及维护结果展示是否如实。此次多选批量扩展还须核对固定执行顺序、逐项状态落盘、单项失败后的继续策略和消费后不重试。

用户确认审查报告通过并明确授权开闸；`src/photo_search/storage_ui.py` 的 `MAINTENANCE_REVIEWED=True`。增加了门闩开放后的 API→启动消费合成闭环用例，原门闩关闭拒绝用例改为显式模拟关闭。运行本卡定向 pytest、Ruff、语法与 diff-check；结论不扩大成全仓或真实图库 PASS。

## 必读与审查面

- `AGENTS.md`、`docs/development-status.md`、`docs/tasks/P4-STORAGE-UI-A.md`、`docs/reports/P4-STORAGE-UI-A.md`
- V3.0 §5.3、§7.1、§10.2、§11.1、§11.4；V3.1 §2.2、§2.4、§3.2
- 完整 diff：`src/photo_search/storage_ui.py`、`src/photo_search/api/routes.py`、`src/photo_search/__main__.py`、`src/photo_search/backup.py`、`src/photo_search/web/index.html`、`src/photo_search/web/app.js`，以及新增/修改测试、报告和状态/任务卡
- 直接调用链：`storage.py`、`storage_maintenance.py`、`runtime/data_root_lease.py`、`app.py`/服务 lifespan、SQLite catalog/source_roots、SecurityMiddleware CSRF、备份创建/恢复

不得访问真实 PhotoSearchData 或图库。不要重跑全仓回归、GPU、浏览器矩阵或大规模图库。

## 必须核对

1. `MAINTENANCE_REVIEWED=False` 时，API、UI、CLI/启动及任何文件状态均不能执行 GC/VACUUM/向量整理；遗留/伪造/过期/身份变化请求不能误执行或重试。
2. 正常 CLI 启动先消费请求，再生成会话凭据、解析模型/GPU、启动 HTTP 并获取服务 lease；GC-A/GC-B 入口内部锁只获取一次，持锁即安全拒绝。
3. 备份仅用户点击后发生；在线 SQLite backup API 覆盖 WAL 一致性，完整性验证和不覆盖发布成立；目录拒绝 data-root/注册来源重叠、链接/junction/异常路径；换目录不搬迁或清除历史快照。
4. 新备份和旧备份恢复均排除一次性授权。catalog 快照保留人工人物/人脸关联、合集、地点、锁/排除和所需身份；无原图、LanceDB/外部产物、模型恢复承诺。
5. GC 仍遵循引用、身份、健康、保护期、空间、来源只读规则；未做手动备份不暗中触发备份或成为虚假执行门禁；结果状态/实际 bytes/partial 如实展示。
6. 确认测试证据与当前 diff/命令/XML 对应；若门闩通过后开放，复核启用后的 API/UI/启动交互和定向测试。
7. 多选维护批次仅接受非空、去重白名单；按固定顺序串行执行，逐项记录；单项错误不会覆盖其他项目结果，批次中断不重放。确认 `index.html` 的 `app.js` 缓存版本随 UI 改动更新；批次每项按实际后端结果显示删除文件数和释放字节，向量维护需读取阶段释放量。

## 验证边界

首轮实现定向 JUnit 为 23 项；多选批量扩展基线为 26 项；修复两个 UI P2 后为 27 项；用户确认报告通过并开放门闩后的最新定向 JUnit 为 28 项，均见 `docs/reports/P4-STORAGE-UI-A-junit.xml` 与实现报告。核对测试源码，不以 JUnit 存在或 collection 成功当作通过。启用路径证据来自合成 data root，不证明真实图库执行结果。

## 交付

形成独立审查报告，逐项列覆盖文件/调用链、证据、finding 及门闩开放决定；更新 `docs/tasks/P4-STORAGE-UI-A.md` 与 `docs/development-status.md`。通过时如开放门闩，必须给出该次 diff 和启用后定向测试。未审查真实图库、浏览器、模型/GPU与全仓回归仍明确保持未验证。
