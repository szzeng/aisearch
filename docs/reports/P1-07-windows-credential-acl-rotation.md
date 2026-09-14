# P1-07 — Windows 凭据 ACL 轮换修复

状态：**PASS**（C1 / B + C）。日期：2026-09-14。

## 问题与修复

`runtime/session.json` 会在每次启动时通过同目录临时文件加 `os.replace` 原子轮换。此前 Windows
路径使用 `icacls /inheritance:r /grant:r <user>:(R,W)`；目标文件缺少 DELETE，第二个进程不能替换
它，因而在同一 `data_root` 上的第二次启动抛 `PermissionError [WinError 5]`。

`src/photo_search/api/session.py` 现仍移除继承、仍只授予当前用户，但权限改为 `(R,W,D)`。这保留了
凭据文件的受限边界，同时让同一用户能够删除旧目标以完成 `os.replace`，也允许正常回收临时数据目录。
没有改动会话令牌、单次兑换、Cookie、API、迁移、依赖或打包入口语义。

## 回归与打包证据

- `tests/unit/test_api_session.py` 新增同一 `data_root` 连续两次 bootstrap（新 token 覆盖旧文件）和
  Windows `icacls` 参数断言，专项 **31 passed**。
- 完整 `tests/unit tests/integration` 运行至 **100%**，无失败输出；Ruff、`git diff --check`、
  `uv lock --check` 均通过（95 packages，依赖未变）。
- 重新构建 onedir（`PhotoSearch.exe` 35,557,274 B），随后对新建的 D 盘隔离工作目录运行
  `packaged_api_smoke.py`。报告 `status=ok`、`problems=[]`；关键新增检查为
  `credential_rotation_second_startup=true`、`credential_rotation_uses_fresh_token=true`、
  `credential_rotation_health=200`、`credential_rotation_stderr_free_of_token=true`。
- 该 smoke 工作目录随后以普通 `Remove-Item -Recurse -Force` 完整移除，证明新的 DACL 不再阻止回收。

## 边界

此修复只处理新版本写出的凭据文件。此前已由旧 `(R,W)` DACL 锁住的遗留 `session.json` 仍可能需要
所有者/管理员手动接管后清理；本任务没有对任何既有用户数据根执行接管、改 ACL 或删除。

P2-07 仍是主线当前任务，未因本 C1 修复改变。
