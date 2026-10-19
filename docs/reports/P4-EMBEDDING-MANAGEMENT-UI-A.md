# P4-EMBEDDING-MANAGEMENT-UI-A

**结果：IMPLEMENTED / ACCEPTANCE PARTIAL；已按用户后续指示本地提交。**

实现图库管理模型状态、搜索页活动 Profile 标识、有界候选建库/暂停恢复、模型资源互斥、孤儿 Lance 表隔离申请、停机 Profile 切换和 DataRootLease 连续交接。候选建库不会自动激活 Profile；覆盖率分母不可信时显示待检查。Luna High 独立复核后修复了候选线程终态竞态、离线请求互相覆盖、历史 FAILED job 误报，并发现/修正候选 worker 启动参数错误。

## 验证

- Ruff：本任务变更的 Python 文件通过。
- Node：`app.js`、`search.js` 语法检查通过。
- 受影响 pytest：66/66 通过；JUnit：`test-results/P4-EMBEDDING-MANAGEMENT-UI-A-final.xml`。
- 内置浏览器隔离数据根：认证会话成功；图库管理页显示 provider 未安装/覆盖待检查，搜索页显示未选择模型并链接回模型区。模型未安装，所以建库、暂停/继续、检查和安排切换的浏览器操作未执行；未保存截图（该浏览器接口未提供本地导出路径）。
- `git diff --check` 通过。用户原有 `src/photo_search/api/models.py` 的 `StrictInt` 修改保留，未纳入本任务意图。

实现提交 `885fde1e3b832f84e6bea3532ba444677d419a7e`；未推送。尚未验收项仍为 PENDING，当前不标记 PASS。

## 尚未验收

没有运行真实 Qwen/CUDA smoke；没有在合成真实编码器或重启服务中执行完整的切换链；孤儿表备份失败/进程崩溃恢复、换库/过期/锁占用、搜索与图库刷新/人脸任务争用等矩阵仍需单独补足。当前证据不支持将任务标为 PASS。全程未访问真实 PhotoSearchData；未做 30 万规模、GTX 1660、全仓或打包验证。
