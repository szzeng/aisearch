# P4-PLACE-UNIFIED-A-review — 独立全受影响面工程审查

日期：2026-10-09。状态：**READY / DS-H 独立审查待做 / 本机浏览器闭环待补**。推荐 **DS-H：DeepSeek V4.1 Flash / 高推理**；由用户在 Codex 中手动切换。实现报告与功能卡均禁止在独立审查前写 PASS。

## 固定入口

按 `AGENTS.md` → `docs/development-status.md` → [功能卡](P4-PLACE-UNIFIED-A.md) → [实现与证据](../reports/P4-PLACE-UNIFIED-A.md) → 最近提交和当前 `git status` 开始。基线已发生变化：功能卡旧基线 `5d7bc139` 与实际不符；实现起点为 `fdd10457`，P4-SEARCH-PLACE-NL-A 后由用户提交 `a5aaab1`，本实现叠加在其上。任务进行期间又有用户提交 `85d4653`、`3712b82`、`d822d56`（文档/任务卡）；现审查时以真实 HEAD 为准并确认这些提交未与本实现冲突。必须分清 HEAD、index、工作区及其他任务的未跟踪文件，不重置、不暂存、不提交、不改写它们。

只读功能卡列出的主架构与 V3.1 章节、ADR-0005/0015/0016，以及本卡直接调用链；不要重读两份架构全文或全仓。报告是实现方证据，不能替代独立推演。

## 审查方式与升级路由

本卡新增 Place 来源身份证明、schema 21 映射、单张/批量人工写、地点成员和撤销语义，属于 V3.1 §2.4 的**完整受影响面工程主审（独立 DS-H）**，不是只读展示面审查。覆盖下列全部更改文件、直接调用链、API/迁移兼容、ADR 差异、测试证据与本机浏览器验收；无需重新审未改全仓。

仅当 §2.2 六项有实际证据时才建议 DS-MAX：①跨多个模块异常且现有证据不能定位根因；②transaction/revision/undo 多状态在高推理后仍矛盾；③测试与 ADR 不符且不能判断测试或实现错误；④一次修复导致第二或第三处相关回归；⑤多个状态机耦合需长程整体推演；⑥Sol 聚焦终审要求大范围反查。普通失败、diff 大或环境无法绑定端口不构成升级理由；升级须在报告记录编号、证据与待回答问题。

Sol high 只做聚焦终审：若 DS-H 发现人工归属身份/事务/撤销或 ADR-0016 的高风险语义未决、或本任务必须正式冻结 §11 契约，写 `SOL_FOCUSED_REVIEW_REQUIRED`，交给用户在独立新会话手动切至 GPT-5.6 Sol / high；输入只含 DS-H 压缩交接包、未决边界、关键 diff 定位、ADR 与最小反例。若 DS-H 证明无高风险未决并且策略无必选门禁，记录 `SOL_NOT_REQUIRED` 及理由。不要让 Sol 重走全 diff。审查期间不启动/委托子 Agent。

## 必审受影响面

1. **Schema 与来源证明：** `catalog/migrations/0021_cn_manual_place_refs.sql`、`catalog/schema.py`、`geo/dataset.py`、`places/datasets.py`、ADR-0016。确认支持 schema 21 及 package maximum 16–20 的桥接有明确范围、包本身无需修改且未知/未来 schema 不放行；GeoNames package→Place 和 OSM admin→FK carrier 的唯一性、FK、激活/切换、恢复语义可靠。确认源 ID 只能来自当前 ACTIVE 且验证包/签名索引，不会把永久 provider reference 当当前包证明。
2. **共享选择与解析：** `places/choices.py`、`places/natural_search.py`、`api/routes.py`、`search.js`。独立核对 OSM 先行、仅 `cn_legacy_links` 才折叠来源、同名不同实体保留完整父级消歧、显式缺失地名提示、普通描述不误识别、地点存在但成员为空不回退。选择 key 必须绑定真实来源与活动数据版本；核对查询长度/匹配边界。
3. **人工写的旧身份桥：** `api/models.py`、`places/service.py`、`places/assignments.py`、`places/directory.py`、`places/filter.py`。分别抽查单张与批量受控引用、旧 `target_place_id` 绕过、伪造/退役/非活动来源、包切换竞态；确认逐项和整批拒绝原子性，旧 USER/custom 记录仍可读/撤销但不能作为新增来源身份。代理 Place 只能承担 FK，类型/父链必须来自 OSM source，不得伪装成 city。
4. **权威成员/修订/撤销：** `places/cn_facets.py` 与 `places/albums.py`。选 district 后 district/city/province 的照片成员一致；人工换地时旧 GPS 和旧地点排除不复活；自动建议与手工优先规则、filter revision、operation idempotency、undo 与 batch compensation 保留旧 ADR-0005 行为。一个照片在一个 scope 只计一次。
5. **UI/API 契约：** `web/places.html`、`web/places.js`、`web/places-albums.js`、`web/search.js` 及 models/routes。确认用户只能通过统一来源候选选择，完整 parent/source 可读，不再有任意文本建地/改名入口；搜索 chip 显示来源，消歧和取消后内容查询不丢，游标/活动包/revision绑定仍过期保护。验证响应变更与已冻结 API 契约一致。
6. **测试证据：** 读取 `.tmp/p4-place-unified-final.junit.xml`（116/0/0/0）与 `test_p4_place_unified.py`、`test_p4_place_unified_ui.py`、已列 P4 CN/search/auto regression、migration 与契约测试；只按证据缺口或发现运行最小反例，不机械重跑全部成功测试。Ruff/Node/compileall/diff-check 退出码见实现报告。
7. **必须补齐或记录浏览器闭环：** 报告记录真实 Uvicorn 在 `127.0.0.1:8765` 绑定返回 Windows `WinError 10013`；此限制没有通过改 ACL、重启用户服务或访问真实图库规避。若可在本机正常权限启动一个仅用小型 `.tmp` 合成 catalog/照片的临时服务，则用真实页面完成：同名搜索并选父级 → 显示来源 → 在地点页将合成照片改为 OSM district → 回搜索确认命中 → 取消地点条件/撤销并确认状态。若仍受阻，记录准确环境证据与 `LOCAL_BROWSER_VALIDATION_PENDING`；不得将 Node DOM 桩描述成真实浏览器 PASS，也不得把环境拒绝当产品失败。

## 已有证据和边界

- JUnit：[`.tmp/p4-place-unified-final.junit.xml`](../../.tmp/p4-place-unified-final.junit.xml)，116 tests / 0 failures / 0 errors / 0 skipped。抽查确保 XML 和实现源 revision 相符。
- 报告：[P4-PLACE-UNIFIED-A](../reports/P4-PLACE-UNIFIED-A.md)，记录完整测试命令、Ruff、Node、compileall、diff-check、synthetic UI probe 和浏览器阻塞。
- 不扫描或操作 `D:\SZ\project\PhotoSearchData`；不运行全量 pytest、性能套件、模型、包构建或公开/真实图库扫描；不改 ACL、不启动可能连接用户数据的现有服务，不提交或推送。

## 输出与停止点

DS-H 把逐面结论、发现优先级、关键文件/符号定位、最小反例与验证结果写入 `docs/reports/P4-PLACE-UNIFIED-A-review.md`，更新 development-status 和本卡。仅针对发现修复并重验直接边界；若本机浏览器仍不可用，任务保持 `BROWSER VALIDATION PENDING`，不标总任务 PASS。DS-H 收敛且 Sol 可豁免时，停止于独立审查结论，后续真实浏览器闭环待条件允许时完成；若 Sol 必选，则只交接压缩包并停止，下一阶段推荐 Sol high / 高推理，由用户手动切换。不要自动派发另一个实现任务。
