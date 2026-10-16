# Development status

## Current: 搜索质量——同类项目及SigLIP2追加对照完成

2026-10-17，继续 [搜索质量任务卡](tasks/P4-SEARCH-QUALITY.md) 与 [同一份报告](reports/P4-SEARCH-QUALITY.md)，分支 `codex/search-quality`、PR #6。已同步远端 `0dd903d` 人物整理功能，并保留上游产品逻辑。

本轮核对Immich、Ente、LibrePhotos、PhotoPrism、digiKam的官方实现，实测模糊文字检索、双视图单向量、双模型融合及模型门槛。新600张公开图片固定200调整/400确认，453条确认查询；标准化双模型融合Top1为77.92%，Qwen512为72.19%，有收益也有退化，不作为全图库精度承诺。默认生产搜索、索引空间和依赖保持。

本阶段公开准备/视觉重放/文字对照工具 **12+21+9项通过**，上游合流受影响 **52项通过**；数据准备、CLIP/Qwen实际运行、冻结后确认和独立重算均exit0并保存JUnit。仅工具/有界合流改变，复用前阶段未变搜索代码验证，不称一次全仓最终回归。raw保留在忽略的 `.tmp/peer-search-20261016/`、`test-results/peer-search-20261016/`。

用户批准的Google SigLIP2 NaFlex（约1.54GB）已下载校验并完成同一600图追加对照。453条确认查询Top1/Top5为62.03%/88.08%，低于现有CLIP/Qwen，保留默认；这是已揭示基线上的固定追加试验。新增13项测试通过，真实GPU运行20.99秒exit0，CPU对照及独立文件/预处理/全部排名核验通过，666句均未截断。测量工具与小型模型校验清单入Git，权重/图片/向量/日志留在项目忽略目录；不改变产品依赖或活动索引。

上游人物整理写入/恢复及前次双页面版本冲突浏览器验收仍PENDING，分别见 [人物整理卡](tasks/P4-PERSON-ORGANIZE-UX-CLEANUP.md) 与 [合流余项卡](tasks/P4-UPSTREAM-REVIEW-REMAINDER.md)。本轮不将这些余项标为通过，未访问真实图库。
