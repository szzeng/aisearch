# P4-STORAGE-UI-A-REVIEW — 当前会话受影响面复核记录

日期：2026-10-11
结论：**代码面未发现可据此开放执行门闩的缺陷；用户批准 GPT-6 Luna High 替代 DS-H 路由，独立 Luna High 审查仍待完成，自动维护保持关闭。**
审查方式：当前会话聚焦复核，按用户“在这里执行”指示完成；用户后续明确批准 GPT-6 Luna High 替代 DS-H 作为独立审查路由。此记录仍是当前会话复核，不冒称独立 Luna High 审查，也不是产品 PASS。

## 覆盖范围

- 产品改动：`src/photo_search/storage_ui.py`、`src/photo_search/api/routes.py`、`src/photo_search/__main__.py`、`src/photo_search/backup.py`、`src/photo_search/web/index.html`、`src/photo_search/web/app.js`。
- 验收与状态：`tests/unit/test_p4_storage_ui.py`、`tests/integration/test_p4_storage_ui.py`、GC-A/GC-B 定向测试、实现报告、JUnit XML、实现任务卡、开发状态。
- 直接调用链：`storage.py`、`storage_maintenance.py`、`runtime/data_root_lease.py`、`app.py` lifespan、`api/security.py`、catalog/source_roots 读取和备份恢复过滤。
- 契约：V3.0 §5.3、§7.1、§10.2、§11.1、§11.4；V3.1 §2.2、§2.4、§3.2；P4-STORAGE-UI-A 全卡。

## 核对结果

1. **权限与门闩**：所有 `/api/v1/*` 写请求统一经 SecurityMiddleware 的 session、Origin、CSRF 检查。确认 API 在 `MAINTENANCE_REVIEWED=False` 时拒绝排队；UI 默认禁用确认按钮。取消仅移除排队请求。门闩仍为 `False`。
2. **启动与锁**：正常支持的个人启动器调用 `python -m photo_search`；`_serve()` 在会话凭据、模型/GPU wiring、ASGI lifespan 与服务 lease 之前消费请求。协调器不预持有 GC 锁；GC-A/GC-B 执行入口各自只获取一次 data-root lease 和扫描锁，忙时返回错误。持锁测试证明请求已消费且再次启动不重试。
3. **一次性授权**：pending 文件以原子 rename 移出授权路径后再校验/执行；校验白名单、状态、过期时间和 data-root/catalog 文件身份。运行失败会落真实 error 结果，异常中断会留下 `running` 状态供 UI 显示且不会重放。旧/新整库备份均排除待执行及 consuming 授权。
4. **备份路径与一致性**：服务端要求绝对目录，检查 data-root 与已登记来源重叠、各段 reparse point、目录类型、可写性及空间；保存设置不移动旧备份。用户点击后才以 SQLite backup API 在线生成 catalog 快照，做 integrity_check 后以同目录硬链接无覆盖发布。快照范围与 UI 文案明确限于 catalog，不承诺原图、LanceDB/外部产物或模型恢复。
5. **数据保护与结果**：执行入口调用现有 GC-A/GC-B；直接测试覆盖 Artifact 引用/身份保护、离线来源、忙锁、向量一致性、VACUUM 人工事实保持及实际 bytes 口径。无自动备份调用。维护状态呈现候选、partial/busy、删除量与实际释放量。
6. **现有证据**：逐条核对实现报告命令与 JUnit XML，XML 为 23 tests、0 failures/errors/skips；测试源码覆盖手动快照、路径拒绝、CSRF、单次消费、锁拒绝、catalog 身份变化、默认门闩关闭和旧授权恢复过滤。现有实现报告记录 Ruff、Python/Node 语法与 diff-check 通过。未重跑测试，未访问真实数据。

## Findings 与门闩决定

- 本次代码面复核未发现未经授权维护、重复加锁、人工事实删除、隐式备份或授权重放的具体缺陷。
- 未发现 V3.1 §2.2 的 DS-H→DS-MAX 升级条件。用户明确批准 GPT-6 Luna High 替代 DS-H；按该路由继续安排独立审查。
- 因本卡要求首次审查独立于实现者，且本次复核在当前会话完成，**不将本记录视作独立审查，不开放 `MAINTENANCE_REVIEWED`**。实施状态继续为 PARTIAL。下一步由独立 GPT-6 Luna High 按用户批准的替代路由审查，再决定是否开放；若开放，必须补做启用后 API/UI/启动闭环和定向测试。

## 未验证边界

未重跑实现定向测试；未做真实浏览器、真实 PhotoSearchData/图库、GPU/模型、全仓回归、灾难恢复或打包验收。JUnit 和现有测试均为合成夹具证据，不能扩大为真实产品 PASS。
