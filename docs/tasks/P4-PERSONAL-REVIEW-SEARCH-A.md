# P4-PERSONAL-REVIEW-SEARCH-A — 搜索与照片查看基础收口

日期：2026-10-09。状态：**READY / 未执行本组收口**。按[个人审查约定](../plans/P4-PERSONAL-REVIEW.md)。独立DS-H/高推理审W/NL尚未审过的面；PHOTO-VIEW沿用用户接受的GPT-6独立结论，不新增DS-H展示审查或Sol。

入口：AGENTS → status → 本卡 → 原报告/当前diff；主架构§9.2/9.3/9.5、10.1～10.3、ADR-0009/0011相关条款。不重审地点/人物已核对的共享成员或模型runtime。

## 1. 三块覆盖一次完成

- **W＋W-Q**：[W报告](../reports/P4-02Y-W-5000-results-strong-match.md)、[W-Q报告](../reports/P4-02Y-W-Q-qwen-strong-match.md)。当前CLIP1.3、Qwen1.5、未知空间不可用；只统计Vector距离，不删候选/不重排。Top5000与5000/5001 Session边界、真实scan_truncated、more_possible、分页保持。旧W单常量和旧EMBEDDING隐藏Qwen不再作为当前预期。3项旧容量JUnit与27项W-Q是不同版本证据，按对应变化核对，不把旧全量当新门限验证；30万性能只引用。
- **自然语言**：[NL报告](../reports/P4-SEARCH-NL-UX-A-implementation.md)。日期/人物canonical/all-any/歧义、取消标签保留内容、纯条件keyset完整分页不进encoder/session、旧语义入口保留。已有27项、5005条轻量记录证据；不再扩大到5005张实际照片或全库搜图。已归地点组的来源/成员只引用，NL新增routes/query/UI/test面完整检查。
- **照片查看**：[用户接受的独立报告](../reports/P4-PHOTO-VIEW-A-review.md)。现存113项、API2项、六类入口、canonical/has_more、Back、地图标记/瓦片失败已验证，保留实际证据来源。多位置/UNC/非法路径由直接测试覆盖，不强制再做每入口×路径×地图×人物的浏览器排列。只检查之后的新增diff及未证明的基础动作。

## 2. 基础验证

优先复用完整现存证据；缺口才选 `tests/unit/test_p4_02y_w_search_capacity.py`、`test_p4_embedding_provider.py`中的统计政策节点、`tests/integration/test_p4_search_nl_ux.py`中的纯条件/人物节点、`tests/unit/test_photo_view_info_ui.py`直接分支。禁止运行依赖旧单门限接口的失败用例后简单skip；统计排序必要证明缺失时先更新合法注入点或新增最小对照，保持候选和排序oracle。

一次小型合成浏览器：人物/日期纯筛选→续页→打开照片缩略图→详情→人物相册或Back→取消一个条件；一个内容查询检查统计显示。只有缺失环节才补，已验证的头像/地图/地点入口不再轮流重跑。Qwen真实门限效果不重新标定/加载模型，已有真实浏览器文字/上传记录核对后引用；用户自己日常查看显示即可。

读只读API/API鉴权及web资源映射的全部未审变更；截图/DOM不证明真实模型质量。基础交互环境不可用时工程与浏览器结论分别记录，不通过改ACL、私库服务或假截图补齐。

## 3. 收口

报告 `docs/reports/P4-PERSONAL-REVIEW-SEARCH-A.md`，分别列W、W-Q、NL、PHOTO-VIEW的证据/剩余/状态。现存PHOTO-VIEW独立审查不重开，基本项核对后才能关闭个人范围；不将本文书变更本身当PASS。

默认SOL_NOT_REQUIRED。无新产品差异时不用再跑Ruff/Node/pytest；补修才跑修改文件及直接JUnit。全量基线、30万测速、跨浏览器/模型准确率均后置。完成人物/地点/搜索个人闭环后可直接日常使用，OCR/模型组按需逐组审，不强制为维持开发继续新功能。
