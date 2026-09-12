# P1-04 C1 — Windows 路径与本地访问边界复核

基线：`4ed62233`（P1-04 收口提交）。本次复核与修复未读取真实照片库；所有测试夹具位于 `%TEMP%`。

## 结论

**PASS。** 策略书 V3.1 §6 要求的 C1 聚焦检查已完成。发现并修复了 localhost 跨端口 Origin/Host 放宽问题；Windows 路径、长路径、只读源文件、目录联接与受控读取边界保持通过。

## 修复

- 原实现把 `80`、`443` 作为任意本地 HTTP 服务可接受的 Host/Origin 端口。服务运行于 `localhost:8000` 时，`Origin: http://localhost` 或 `:80` 会被接受。
- Host 与 Origin 现将省略端口规范化为协议默认端口，并严格等于 ASGI 实际监听端口；未改变 loopback Host 白名单、cookie、CSRF、CORS 或文件访问契约。

## 复核范围

- `path_key`/`root_key`、相对路径重建、组件 `lstat` reparse-point 拒绝、物理 `realpath` 包含性与 `size`/`mtime_ns` 二次确认。
- loopback bind、单次会话凭证、HttpOnly session、double-submit CSRF、Host/Origin 端口绑定与访问日志令牌脱敏。

## 验证

| Check | Result |
|---|---|
| 新增 `localhost:8000` 端口回归 | `http://localhost`、`:80`、`https://localhost` 和无端口 Host 均 403；仅 `http://localhost:8000` 的变更请求 201 |
| 聚焦集 | `uv run pytest -q tests/unit/test_api_session.py tests/integration/test_api_access.py tests/integration/test_api_files.py tests/integration/test_api_server.py`：真实端口 smoke 在释放本次 C1 临时包副本后通过 |
| B 级回归 | `uv run pytest -q tests/unit tests/integration`：exit 0 |
| 静态检查 | `uv run ruff check .` 与 `git diff --check`：clean |

## 限制
