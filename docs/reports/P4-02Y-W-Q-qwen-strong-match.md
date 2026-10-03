# P4-02Y-W-Q — Qwen 高度匹配恢复

状态：**IMPLEMENTED / 定向验证通过 / 个人网页确认待完成**

## 实现

- 试用门限按用户反馈从 cosine `>= 0.50` 下调后，现回调到 `>= 0.25`，即 squared-L2 `distance <= 1.5`。Chinese-CLIP 仍为 `distance <= 1.3`。
- 未知向量空间的高度匹配统计不可用。候选在 `_entries_of()` 中冻结后才计算门限统计；统计只读取现有向量距离，未参与检索、融合、过滤、重排、Top5000 或分页。
- 功能逻辑只改 `src/photo_search/search/service.py` 与两个指定单测文件；前端仍只显示返回的统计字段，不按门限过滤或重排照片。
- 回归断言确认不同向量空间的门限统计下，候选数、截断状态、FTS 摘要、权威分页元数据和 hit ID 顺序保持一致。API 仅附带的高度匹配统计字段会随门限变化。

## 定向验证

- JUnit：27 tests，0 failures，0 errors，0 skipped。边界覆盖 Qwen 1.49/1.50/1.500001、CLIP 1.3 与未知空间隐藏，以及现有 Top5000/排序边界。
- Ruff：三个 Python 文件通过；`node --check src/photo_search/web/search.js` 通过。
- `git diff --check`：通过。
- 沙箱首次运行遇到 pytest 清理 `.pytest-tmp` 的 WinError 5；按正常权限路径重跑后通过，未修改 ACL。

## 个人网页确认

未执行三条真实 Qwen 搜索。检查时没有浏览器标签，且本机 8000/8001/8080 均无监听服务。按本卡要求只应在现有网页查询；未启动可能写入 `D:\SZ\project\PhotoSearchData` 的服务。故尚未确认当前门限下页面展示“约 N 张高度匹配”、模型名和三条查询结果。恢复现有网页会话后补做这三条只读查询，再决定是否达到 PASS。

未运行全量 pytest、30 万性能测试、建库、标定或打包。
