# P4-SEARCH-STATE-UX-FIX

- 日期：2026-10-20
- 状态：**IMPLEMENTED / ACCEPTANCE PARTIAL**（隔离 UI/API 验证通过；真实浏览器检查待做）
- 优先级：P1；类型：前端状态修复与结果操作区优化
- 目标：分页、排序切换、新搜索和返回快照期间保持已确认搜索元数据独立于当前页；拒绝跨条件/Session/模型/过期凭据保存；在照片多选之外提供智能合集保存入口。
- 范围：`src/photo_search/web/search.js`、`search.html`、`search.css`、相关前端状态测试及本卡报告。后端只核对现有 token、Session 和 revision 契约，不改 API 或数据结构。
- 排除：数据库迁移、召回/模型和阈值调整、5000 上限变化、真实图库、GPU/大规模测试及 Git 提交。
- 需读：V3.1 §2.4、§3.2/§3.4；UI 时间排序计划 §1.4；直接搜索保存接口与前端状态/快照测试。

## 实施不变量

- 首次成功响应建立 `confirmedSearch`；分页只更新页数据、cursor 和已加载数量。条件指纹、Session、检索模式、Profile/Generation、token/mode、高度匹配统计、候选总数和截断状态与 `lastBody` 分开保存。
- 新搜索开始、条件失配或服务端 Session/Profile/revision 错误清除旧保存资格。仅分页网络失败保留可重试的实际 cursor。恢复快照验证搜索 Session/时间规则版本，但不验证保存 token；恢复结果可查看，保存入口要求重新搜索确认。
- 每次真实保存 POST 前确认 captured search 仍是当前搜索且条件指纹未变。前端不以本地时钟或 capability 缓存推断 token TTL/Profile/Generation 有效性；保存 API 负责核验 token、应用 Session、Session/Profile/Generation/revision 与服务端约 10 分钟 TTL。服务端拒绝后清理旧保存资格，不自动重试。
- 时间排序只改变浏览列表；状态栏从当前模式及确认搜索元数据生成。切回相关度恢复原高度匹配、排名和 cursor，不重跑向量检索；保存智能合集始终使用原始搜索规则。
- 智能合集主操作位于独立结果操作区；次级操作按保存模式展示。照片多选工具栏只操作所选照片。
- 并发沿用 `requestGeneration`、时间 browse `requestSeq` 和身份检查；离开时间模式后丢弃迟到响应。保留照片选择、预览、返回位置和滚动资源回收行为。

## 性能设计与验收

- 链路与风险：触及搜索首屏、续页、临时时间分页和返回快照，不新增数据库查询、图片读取或模型调用。
- 做法与边界：保留现有每页、5000 候选和服务端 token TTL；将确认元数据与临时排序/分页游标分离，不引入全局状态框架。
- 验证与停止：运行前端状态及 API 合约定向测试、Node 语法和静态 diff 检查；浏览器只验证关键结果/排序/返回流程。无大库或 GPU 验收，不新增规模性能结论。

## 验证计划与完成条件

- `tests/unit/test_search_text_mode_ui.py`、`tests/unit/test_api_frontend.py`：覆盖首次显示、分页稳定性、时间/相关度往返、模型身份统计、5000+ 与不可用、条件修改/新搜索失败、快照与 TTL、连续时间分页失败、时间快照游标、异步 token 过期不发 POST。
- 现有 API 合约节点覆盖服务端 token Session 绑定/TTL、Session 过期、活动 Profile 变化与智能规则保存；不修改后端契约。
- 运行 `node --check src/photo_search/web/search.js` 和 `git diff --check`。真实浏览器在可用隔离应用会话中检查首个结果操作、时间排序切换及返回；本次未获得可用浏览器标签，必须记录 PENDING。
- 不触发全量回归：改动局限于前端交互状态，受影响测试已明确选取；无 schema、依赖、API 协议或数据语义变更。
- 不访问真实 `PhotoSearchData`；原卡未授权提交，用户随后明确授权本地提交，不推送。

## 执行结果

- 前端状态/UI 定向 JUnit：100 passed，P1 阻断续修最终文件 `test-results/p4-search-state-p1-fix-final4.xml`；保存 API 合约：7 passed，`test-results/p4-search-state-p1-api-final2.xml`。
- Node 语法、Python Ruff、`git diff --check` 均通过。
- Luna High 跟进复核关闭时间游标重试、时间快照分页入口、异步凭据过期、绑定身份缺失、快照筛选搜索重确认和服务端智能依赖失效共 6 项状态边界 finding；新增游标失效回归已跟进确认关闭。
- 真实浏览器关键流程无可用标签页，保持 PENDING；故当前为 **IMPLEMENTED / ACCEPTANCE PARTIAL**。

### P1 阻断修复（2026-10-20）

- 已在隔离 UI harness 先复现：新搜索响应带有新 token 与模型绑定，但 capability 缓存未返回 active Profile/Generation 时，前端严格比较把“本地身份未知”误作“不匹配”，显示保存凭据失效。当前后端只返回 source token/mode，不返回 expiry 字段；原前端另用本地 `confirmedAt` 推算 570 秒 TTL。
- 移除前端 TTL/模型有效性推断；保存操作使用当前确认搜索的 token，由保存 POST 服务端裁定过期、Session 和模型绑定。当前确认对象及条件指纹仍防止跨搜索串用。快照只验证搜索 Session，因此恢复快照后不把缓存 token 当成已确认凭据，提示重新搜索。
- UI JUnit 100 passed：`test-results/p4-search-state-p1-fix-final2.xml`。API 合约 6 passed：`test-results/p4-search-state-p1-api-final.xml`；增加实际搜索 token 后续页 + 时间页仍可用于保存的合约测试，1 passed：`test-results/p4-search-state-p1-paging-api.xml`。Node、Ruff、diff-check 通过。
- 后续复核关闭两项快照/智能依赖边界：筛选快照恢复后先用新搜索重新签发凭据，再解除重确认门禁；`PHOTO_SMART_DEPENDENCY_CHANGED` 清除保存资格。Luna 另指出会话失效时需清空时间模式保存的相关度 cursor，已修复并增加返回相关度回归。最终 UI JUnit 100/100（`test-results/p4-search-state-p1-fix-final4.xml`），API 合约 7/7（`test-results/p4-search-state-p1-api-final2.xml`）；Luna High 跟进确认 finding 全部关闭。
- 真实浏览器检查仍 PENDING；本次不改后端实现/保存协议、数据库、模型或搜索算法。用户后续要求本地提交，不推送。

## 报告

[P4-SEARCH-STATE-UX-FIX 执行报告](../reports/P4-SEARCH-STATE-UX-FIX.md)
