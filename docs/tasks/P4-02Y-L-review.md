# P4-02Y-L — OCR个人基础独立工程审查

日期：2026-10-09。状态：**READY / 独立工程审查未执行**。推荐DS-H/高推理，用户手动选择。

按[个人审查约定](../plans/P4-PERSONAL-REVIEW.md)执行。输入：[主卡](P4-02Y-L.md)、[实现报告](../reports/P4-02Y-L-ocr-cuda.md)及该卡关联的Sol冻结报告、ADR和当前Git。只读涉及OCR/device/processor条款，不重读全仓。

## 保留的完整未审面

- scheduler/spec、伴随parser与模型身份：non-vector/non-model CPU规则、产物身份和冻结语义不变；OCR几何及生命周期拒绝仍有直接证据。
- 实际OCR session/provider/device/cache绑定与GPU失败保护：不能把静默CPU回退、假provider、fake输出记成GPU成功。
- 实际诊断工具的输入、JSON/counter真实性；runtime/import后续改动与模型切换组共享检查，不分别重审同一段。

首次独立审查覆盖全部尚未审过的新增文件及直接调用链。先核对已有Sol冻结66项、required138项（119 passed/19 skipped）、CPU/GPU对照与建库JSON的版本和覆盖；不重做冻结、GPU加速门槛或两次速度测量。

优先[Sol冻结报告](../reports/P4-02Y-L-sol-contract-freeze.md)、`test-results/p4-02y-l-required.xml`与实现报告直接JSON。冻结assert/hash不一致须按真实冻结条款判断，不为了修绿生成新hash；模型切换依赖/import改变若影响OCR路径，只补该变化证据。

## 最小补测与收口

缺失/改变的保护才补原报告直接节点；例如 `tests/unit/test_p3_ocr_contract.py`、`tests/unit/test_p3_ocr_geometry.py`、`tests/unit/test_runtime_device.py`中相应行为。不默认重跑整文件集合；实际补测生成JUnit XML。不能用跳过关键失败来收口。

输出 `docs/reports/P4-02Y-L-review-dsh.md`，列覆盖表、当前差异、补测及未验环境。无新冻结或高风险未决时SOL_NOT_REQUIRED；已有Sol结论引用即可。关键证据缺失保留PENDING，真实效果/现场未验照实列出。

不访问私人图库、不加载/下载模型、不重跑全量/30万、构建exe或提交/推送。只完成本组，不自动开始下一组。
