# P4-RELEASE-060 — 0.6.0 版本标记更新

日期：2026-10-22  
状态：**VERSIONED / VALIDATION SKIPPED AT USER REQUEST**  
基线：`8718b605f9560953b749a3adb53e4089e3d02d46`

用户要求简单升级到 0.6.0、更新 tag，并明确表示不需要验证。

## 范围

仅统一版本元数据与版本展示：`pyproject.toml`、`APP_VERSION`/OpenAPI、`uv.lock` 本地项目项、直接版本断言、README 与 CHANGELOG。Catalog Schema 42、API v1 和第三方依赖保持不变。不新增功能或推断本版本功能验收。

## 性能（PERF-N/A）

- 链路与风险：只改版本标记，不改变运行时执行路径或数据操作。
- 做法与边界：复用既有实现；不新增模型、服务、数据库或性能工作量。
- 验证与停止：用户明确要求不验证；不运行测试、构建或性能检查，并如实记录为未验证。

## 交付与验证

更新主报告与开发状态摘要。按用户要求跳过所有验证。版本更新提交 `3a23852c58266b1a2a595bc39eeb167455418f8d` 已完成，本地 annotated `0.6.0` tag 指向该提交；不推送、不创建 GitHub Release。该 tag 的远端状态未检查。
