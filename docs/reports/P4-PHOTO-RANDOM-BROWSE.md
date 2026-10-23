# P4-PHOTO-RANDOM-BROWSE 执行报告

**结果：IMPLEMENTED / VALIDATION PENDING**（2026-10-23）。本次由用户后续授权本地提交；不推送或发布。

## 实现

- 照片浏览页新增第三种“随机浏览”模式。每批默认 300 张、可调 1–1000 张；共享列表的 50 张滚动页、缩略图、收藏与 viewer。显式“下一批”替换当前批。时间轴/列表切换、查看器、完整详情及刷新会恢复当前批和滚动位置；来源变化会清空旧范围并忽略迟到响应。
- 随机 API 使用既有 Session/CSRF。全库与单图库都从资产 ID 首尾边界选取 12 个随机索引窗口，每窗最多 1000 个候选；跨边界时将环形窗口拆成两个非重叠范围。候选复用 `photo_asset_sql` 与 PRESENT 谓词、去重后打乱，摘要按最多 50 个 ID 批量读取并重核资格。每批不逐图加载详情；未改通用 `/assets` 排序或游标。
- 环形区间使连续 ID 范围中每个 ID 在均匀窗口起点下具有相同覆盖机会，修复了初版端点偏差。每次随机抽样最多读取 12,000 个候选，不做 `ORDER BY RANDOM()`，也不全库扫描已排序结果；每次请求只读取索引边界和有限 ID 范围。新增照片在下一次抽样进入候选池。
- 跨批避重使用前端最近 1,200 个 ID 的排除提示；候选不足时逐步释放排除项。因此小图库、候选欠填或浏览距离超过最近窗口时允许重复；此方案不维护服务端固定随机排列，也不承诺严格的一轮无重复或准确“剩余总数”。
- 轮次/返回数据仅保存在当前浏览器标签页 `sessionStorage`：最多当前批 1,000 ID、recent 1,200 ID、pending 1,000 ID，加数量、页数、滚动位置和 30 分钟写入时间。服务端不保存轮次 ID 列表或消费游标，没有服务端随机状态的重启/回收问题。刷新会重读保存数据并在摘要接口重核；服务重启本身不清除浏览器数据。浏览器存储丢失或超过 30 分钟后不再恢复旧批，重新进入随机模式会创建新的抽样结果，不会伪称为旧批次。服务端内存不随轮次增长。
- 摘要读取失败时保留当前批次，并在存储中保留待重试 ID；切换模式或刷新只恢复当前批，不自动应用 pending。显式点击重试复用同一组 ID/顺序；请求期间或有 pending 时数量输入禁用。失败文案不会被 `finally` 状态刷新覆盖。

## 验证证据

- 最终定向 JUnit：**18 passed，0 failed/errors/skipped**；包含随机算法/端点覆盖、SQLite 索引计划、来源/PRESENT/资产去重、授权 API 和 Node 行为测试（摘要失败→恢复原批→显式重试相同 pending IDs）。原始结果：`test-results/p4-photo-random-browse/p4-photo-random-browse-focused-20261023-r11.xml`。
- Ruff 受影响文件、相关 Python `py_compile`、`node --check src/photo_search/web/app.js` 与 `git diff --check` 均通过。pytest 有两条 Starlette/httpx 与 AnyIO 依赖弃用提示。
- Luna High 完整受影响面只读复核先发现 pending 恢复和区间端点偏差两项 blocker；修复后跟进未发现 blocker。最终 Node 故障行为测试补入后由实现者定向执行；reviewer 未执行测试。
- 内置浏览器使用项目内 65 张隔离合成图库、`127.0.0.1` 同一认证会话：实际检查随机首批、滚动续页、换批、列表/时间轴往返、照片 viewer、完整详情返回、来源切换和刷新恢复；最终修复版重载后再次换批。窄视口截图约 450 px，控件对齐、两列网格且未见横向溢出；控制台未见相关错误。截图在浏览器工具中实际观察，但工具未提供导出到项目路径的能力。缩略图可显示，合成素材卡片显示“原文件不可用”。
- 桌面宽视口：尝试浏览器视口调整后，内置面板仍呈窄内容宽度，无法取得可信的桌面宽视口验收，故 **PENDING**。本卡必需浏览器检查未全完成，不能标 `SCOPED PASS`。

## 性能和边界

- 300,000 资产合成数据库未找到/不可用；没有运行 30 万计时、内存增长或吞吐测量，不声称该规模 PASS。仅验证查询计划使用 assets INTEGER PRIMARY KEY 与 `idx_file_locations_present_source_asset`，以及合成小库功能/去重；这些不是容量或延迟结论。
- 未访问真实照片库、真实模型或 GPU；未做全仓测试。批次摘要会基于当前 PRESENT 状态跳过已失效照片，并展示实际摘要数，不补重复项。
- 本轮报告与状态按浏览器桌面视口检查待验记录为 `IMPLEMENTED / VALIDATION PENDING`；完成宽视口验收后可再评估 scoped closeout。

## 复现

```powershell
.venv\Scripts\python.exe -m pytest -q tests/unit/test_p4_random_browse.py tests/unit/test_p4_ui_photos.py tests/integration/test_p4_random_browse_api.py --junitxml=test-results/p4-photo-random-browse/p4-photo-random-browse-focused-20261023-r11.xml --basetemp=.pytest-tmp/p4-photo-random-browse-focused-r11
.venv\Scripts\ruff.exe check src/photo_search/api/random_browse.py src/photo_search/api/queries.py src/photo_search/api/routes.py tests/unit/test_p4_random_browse.py tests/unit/test_p4_ui_photos.py tests/integration/test_p4_random_browse_api.py
.venv\Scripts\python.exe -m py_compile src/photo_search/api/random_browse.py src/photo_search/api/queries.py src/photo_search/api/routes.py
node --check src/photo_search/web/app.js
git diff --check
```
