# P4-QWEN-1024D-TRIAL 执行报告

**状态：IMPLEMENTED / ACCEPTANCE PARTIAL**（2026-10-21）
**基线：**`master @ 900028dd984f0f9ad74cbff2fb036e23e87d39b8`
**性能范围：**PERF-BOUNDED；共享模型前向不变，输出/向量存储增至 1024D。未做大库 SLA 或规模推断。

## 实现

- 新增 `tools/prepare_models/manifests/qwen3-vl-embedding-2b-1024d-exif-v2.json`。自动比较确认它只改变 provider ID、展示名、输出维度三项；共享原 Qwen 模型目录及完全相同的权重、revision、预处理和运行参数。
- 复用既有 Qwen Adapter 与候选 Generation/覆盖检查/停机切换路径。新增测试证明 512D 与 1024D 的 Processor、Space、Profile、Generation 相互隔离；1024D 候选不会自动激活，图库覆盖缺口会拒绝 512D 回切，补齐覆盖后可回切。新库默认仍是 512D。
- 1024D 在“搜索模型”主要列表中明确显示为“试用配置”；性能诊断支持新 Provider 并显示 `Qwen 1024D EXIF v2`。用户要求实际显示高度匹配数量后，为新 Space 配置独立阈值；按用户最新要求，恢复最初的 `squared-L2 <= 1.5`（归一化向量 `cosine >= 0.25`）。该值尚未以真实图库相关性标签单独校准。API 只对已召回的 vector 候选计数，不改召回、排序或 RRF。其他未知/无配置空间仍显示“高度匹配：未标定”。
- 状态栏会在 1024D 返回阈值内的实际候选数量；有标定空间的既有真实计数不变。搜索脚本缓存版本升至 `p4-qwen-1024d-trial-3`。
- 未修改 Adapter、候选任务实现、数据库/schema、依赖或默认模型。
- 独立复核发现同 ID 的有效 data-root 1024D Manifest 可覆盖标准 revision/hash/dimension。现已在 Registry 将本地同 ID 覆盖限制为仅展示名差异；revision、任一权重 SHA、dimension 或其他配置不一致时标记为 skipped 并回退至固定 shipped 清单。畸形 JSON 同样有 skipped/fallback 回归。

## 验证证据

| 场景 | 数据与设备 | 实测 | 结论 |
|---|---|---|---|
| 安装一致性 | 现有模型目录，只读；5 个固定文件 | revision 和文件大小/SHA256 全部匹配标准 512D 清单 | 可安全共享现有权重；未触碰图库数据库/原图 |
| 图文编码 | RTX 4060 Laptop，CUDA 0，FP16；短中文文本、2 张临时合成 JPEG（含 EXIF 方向） | 1024D finite、L2=1；重复文本 cosine=1；模型峰值 4.27 GB allocated / 4.29 GB reserved | CUDA smoke 通过 |
| 同机编码观察 | RTX 4060 Laptop，CUDA 0，FP16；512D/1024D 顺序加载；单张 320x240 合成 JPEG 和同一中文文本；预热后各 2 次 | 512D text 36.0/67.7ms、image 109.2/93.3ms；1024D text 31.9/43.5ms、image 89.0/56.8ms | 短观察存在波动；不据此推断吞吐或延迟优势。原始记录 `.tmp/P4-QWEN-1024D-TRIAL/cuda-comparison.json` |
| 检索观察 | 两张合成图，512D 前缀与 1024D，穷举 cosine 点积 | 分别约 0.031 ms / 0.099 ms | 样本极小，只证明连通性，不代表真实图库性能或排序质量 |
| API/状态 | 隔离合成图库与确定性 Adapter | 文本搜索与上传图片都绑定 1024D Space/Generation；候选隔离、覆盖拒绝与回切通过；旧搜索 Session 与合集凭据的 profile 变化拒绝用既有受影响 API 用例验证 | 受影响行为通过 |
| 浏览器入口 | Codex 内置浏览器，127.0.0.1，独立临时数据根 | 两个 Qwen EXIF v2 配置无需展开“其他配置”即可看到；1024D 标记试用 | 页面检查通过；临时数据根无权重，候选按钮显示未安装，未尝试发起构建 |

**受影响验证：**`pytest -q` 覆盖 `test_p4_embedding_provider.py`、`test_search_perf_diagnostics.py`、`test_p4_embedding_check_ux.py`、Embedding 管理状态/协调、图像方向、UI 缓存版本、`test_p4_embedding_provider_flow.py`，以及旧 profile/session 和智能合集凭据拒绝用例。初始验证 JUnit：`test-results/P4-QWEN-1024D-TRIAL-r7.xml`（134 passed）。复核修复后最终 JUnit：`test-results/P4-QWEN-1024D-TRIAL-review-r2.xml`（168 passed，0 failed，0 skipped；exit 0）。Ruff、py_compile、`node --check`（app.js/search.js）和 `git diff --check` 均 exit 0。初始 r1/r3/r4 失败 XML 保留在 `test-results/`；Manifest 派生、API 测试桩和 Registry 覆盖保护均由后续验证关闭。

用户反馈后的搜索状态栏交互/契约验证：`test-results/P4-QWEN-1024D-TRIAL-status-fix-r3.xml`（100 passed，0 failed，0 skipped；exit 0）。1.5、1.6 与 1.56 阈值验证分别见 r2、r4、r5（均为 59 passed、0 failed、0 skipped）。本次按用户要求恢复最初 1.5 阈值，`test-results/P4-QWEN-1024D-TRIAL-threshold-r6.xml` 为 59 passed、0 failed、0 skipped；Ruff 与 diff-check 通过。没有对真实图库执行搜索；真实 1024D 相关性校准仍未执行。

## 未验边界与试用步骤

- 真实 PhotoSearch 浏览器里的安装态候选开始/暂停/继续/检查/安排切换及进程重启后 1024D 实搜未执行；隔离浏览器不能安全复用真实模型文件而不复制/链接或打开真实数据根。真实图库建库、切换、回切由用户在更新后的应用 UI 中主动操作。
- 未用真实图片测质量；小型合成点积不构成延迟预算保证。未做 30 万压测、GTX 1660 验证或正式发布认证。
- 新增的候选任务管理器专属 1024D 暂停/继续/进程恢复测试尚未补；既有共享状态/协调用例通过。真实 PhotoSearch 浏览器里的安装态候选开始/暂停/继续/检查/安排切换及进程重启后实搜未执行；真实图库建库和切换由用户在更新后的应用 UI 中主动操作。
- Luna High 首轮发现 Registry 覆盖身份 blocker，修复后复核确认关闭，未发现 validator 绕过。无效本地 override 会 fallback 到 shipped Provider；文件仍需通过 shipped 清单 SHA 校验，Registry 的 `installed` 仅表示所需文件存在，不代表已完成 hash 验证。未提交或推送。

用户试用：在现有原数据根打开“图库管理 → 搜索模型”，选择标有“试用配置”的 Qwen 1024D，按现有流程开始候选建库、暂停/继续并在完成后检查；检查通过后安排停机切换并重启。若要恢复 512D，先对 512D 独立执行检查；系统仍会核验其最新照片覆盖。
