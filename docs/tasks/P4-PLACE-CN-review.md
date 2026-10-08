# P4-PLACE-CN-review — 独立受影响面工程审查

2026-10-05。状态：**READY / 尚未审查**。基线 HEAD `856f26e` 加工作区 CN 实现；不得把实现者自检或75项通过当作独立审查。建议 **DS-H 全受影响面 → GPT-5.6 Sol/high 聚焦终审**，用户手动切换；没有证据要求 DeepSeek max，不启动其他 Agent。

入口：AGENTS.md → development-status.md → [功能卡](P4-PLACE-CN.md) → [实现报告与命令](../reports/P4-PLACE-CN.md) → [ADR-0016](../decisions/ADR-0016-cn-admin-boundaries.md)。仅读功能卡指定的 V3.0/V3.1 章节、ADR-0005/0015及直接调用链；不重审全仓。

## 审查范围

- 构建/来源：`tools/places/{prepare_china_places,compose_china_places}.py`，源 PBF/MD5/hash、四区真实组面、ODbL/WGS84、拒绝项与不完整覆盖、原GeoNames内容字节、独立addon/checksum及未来schema拒绝。源文件在 `.tmp/p4-place-cn/source`，准备/候选包在同目录下；不重新下载或提取1.6GB数据。
- 数据/生命周期：`catalog/migrations/0018_cn_admin_facets.sql`、`catalog/schema.py`、`geo/{cn_admin,dataset,resolver}.py`、`places/{cn_facets,datasets,albums}.py`；完整stage/validate/activate/rollback/cleanup/recovery调用链、稳定ID和FK、不可变派生结果、来源/GeoFact/input/processor隔离、缓存版本与空间层级。0019/0020仅为并行人物增量，保留schema20，不把人物改动归到CN提交。
- 权威成员：人工指定/取消/排除、无GPS人工映射、无法证明排除的保守策略及只读原因；省/市/区县浏览计数、纯地点SQL keyset与地点＋内容检索共用成员。旧Place枚举/人工历史/manual-only FTS/国外GeoNames不漂移，未知边界不能退化成附近城市行政归属。
- 发布/并发：持久高水位/上界/generation、100张有界批次/暂停继续/完成后新一轮；迟到输入、包切换、竞争重启、GPS/位置变化和游标过期。完整文件验证不持写锁，状态页和中国冷加载工作线程不碰SQLite，发布在原线程复核。核对回退指针revision与读快照，不能混合多版页。
- API/UI：`api/{routes,models}.py` 中CN片段及直接调用；`web/{places.html,places-albums.js,search-place-entry.js}`。机器标签、区县父链、能力声明、只读 `/assets/{asset_id}/admin-place`、CSRF/会话、不支持人工区县直接确认、快速切kind和旧照片响应、更新暂停/继续/失败。共享routes中人物刷新、详情/批次接口属于并行任务，保留其变更。
- 依赖：`pyproject.toml`、`uv.lock`；Shapely2.1.2运行依赖、Osmium4.3.1仅places-build。不要下载AI模型、扩大部署或跑exe。

## 已有证据及检查方式

报告列出完整执行命令；另核对 [schema20兼容补修](../reports/P4-PLACE-CN-schema20.md) 的51项定向/两份公开包校验和未来21拒绝；先读JUnit/JSON/截图和对应测试，不机械重跑所有已成功证据。最终 `p4-cn-final.xml` **75/0/0/0**；`p4-cn-evidence.xml` **2/0/0/0**（真实公开复合包与30万合成）；最终SQL计时 `p4-cn-timing-final.xml` **1/0/0/0**。`p4-cn-real-source.json` 四地区、东京JP及100张真实几何，`p4-cn-300k-timings.json`首次＋3次重复计数/两页，`p4-cn-browser.jpg`107张合成页面。

测试受影响面：`tests/p4_cn_fixtures.py`、`tests/unit/test_p4_place_cn_{prepare,ui}.py`、`tests/integration/test_p4_place_cn{,_300k,_real_source}.py`、`test_place_dataset_schema17.py`及报告列出的原相册/搜索/包/恢复/GeoNames/地图API回归。核对真实断言、退出码/JUnit和测量边界：30万是SQL成员夹具；100张吞吐排除冷加载；浏览器内容向量为FakeEncoder；暂停只通过实际JS受控桩验证，无真实全库中断演练。

发现问题才补最小反例、修复与关联JUnit，临时目录 `.pytest-tmp`、uv缓存 `.uv-cache`。不得访问/扫描/修改真实照片或catalog；真实候选包尚未安装。不得追加全国准确率评估、全量套件或性能矩阵，不删除用户原始PBF/MD5，不自动提交/推送。

## 输出与停止点

保存 `docs/reports/P4-PLACE-CN-review.md`：逐项受影响面审查、按优先级可定位的发现、反例/验证证据、未覆盖项及建议。修复收敛后Sol high只聚焦层级/身份、人工排除保护、包切换/不可变产物、ADR对齐与剩余发现，不重复完整diff。

审查未收口维持IMPLEMENTED，不写正式冻结或PASS。全部必需检查与独立审查收口后仅标 **PASS — 本机个人中文地点与小样本验证**；另由用户决定将已生成复合包导入运行数据并点击更新，地图OSM底图不需要重下载。同步报告、状态和功能卡；无新缺口则交接个人试用，不自动扩建下一功能。
