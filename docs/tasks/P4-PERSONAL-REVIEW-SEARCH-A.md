# P4-PERSONAL-REVIEW-SEARCH-A — 搜索与照片查看基础收口

日期：2026-10-09。状态：**ENGINEERING REVIEW PASS / SOL_NOT_REQUIRED；Qwen真实网页效果待验**。按[个人审查约定](../plans/P4-PERSONAL-REVIEW.md)。用户指定当前会话复核即可；W/W-Q/NL受影响面和修复已复核，PHOTO-VIEW沿用用户接受的独立结论并复查基础路径。当前报告：[搜索与查看组审查记录](../reports/P4-PERSONAL-REVIEW-SEARCH-A.md)。

当前交接：人物组与地点组均已 **ENGINEERING REVIEW PASS / SOL_NOT_REQUIRED**，分别见[人物报告](../reports/P4-PERSONAL-REVIEW-PERSON-A.md)、[地点报告](../reports/P4-PERSONAL-REVIEW-PLACE-A.md)。立卡核对HEAD `daf20f1`，tracked工作区干净；`.tmp/`及`docs/reports/tmp-*`保留。执行时重查HEAD/index/worktree，按原任务报告和Git历史重建完整未审面，不只审最近的地点测试/文档提交。前序现场PENDING不自动关闭，也不阻止本组工程审查。

入口：AGENTS → status → 本卡 → 原报告/当前diff；主架构§9.2/9.3/9.5、10.1～10.3、ADR-0009/0011相关条款。不重审地点/人物已核对的共享成员或模型runtime。

## 1. 三块覆盖一次完成

- **W＋W-Q**：[W报告](../reports/P4-02Y-W-5000-results-strong-match.md)、[W-Q报告](../reports/P4-02Y-W-Q-qwen-strong-match.md)。当前CLIP1.3、Qwen1.5、未知空间不可用；只统计Vector距离，不删候选/不重排。Top5000与5000/5001 Session边界、真实scan_truncated、more_possible、分页保持。旧W单常量和旧EMBEDDING隐藏Qwen不再作为当前预期。3项旧容量JUnit与27项W-Q是不同版本证据，按对应变化核对，不把旧全量当新门限验证；30万性能只引用。
- **自然语言**：[NL报告](../reports/P4-SEARCH-NL-UX-A-implementation.md)。日期/人物canonical/all-any/歧义、取消标签保留内容、纯条件keyset完整分页不进encoder/session、旧语义入口保留。已有27项、5005条轻量记录证据；不再扩大到5005张实际照片或全库搜图。已归地点组的来源/成员只引用，NL新增routes/query/UI/test面完整检查。
- **照片查看**：[用户接受的独立报告](../reports/P4-PHOTO-VIEW-A-review.md)。现存113项、API2项、六类入口、canonical/has_more、Back、地图标记/瓦片失败已验证，保留实际证据来源。多位置/UNC/非法路径由直接测试覆盖，不强制再做每入口×路径×地图×人物的浏览器排列。只检查之后的新增diff及未证明的基础动作。

## 2. 基础验证

优先复用完整现存证据；缺口才选 `tests/unit/test_p4_02y_w_search_capacity.py`、`test_p4_embedding_provider.py`中的统计政策节点、`tests/integration/test_p4_search_nl_ux.py`中的纯条件/人物节点、`tests/unit/test_photo_view_info_ui.py`直接分支。禁止运行依赖旧单门限接口的失败用例后简单skip；统计排序必要证明缺失时先更新合法注入点或新增最小对照，保持候选和排序oracle。

直接证据定位（不是全部重跑清单）：

- `tests/integration/test_p4_search_nl_ux.py::test_pure_filter_keyset_pages_past_5000_without_semantic_session`：纯条件分页不受语义Top5000/session限制，不调用encoder。
- `tests/integration/test_p4_search_nl_ux.py::test_named_person_natural_query_uses_canonical_all_and_any_scope`：人物canonical与all/any范围。
- `tests/unit/test_p4_02y_w_search_capacity.py::test_strong_match_distance_boundary_only_adds_vector_counts`及`test_full_top_k_strong_boundary_and_real_scan_truncation_are_distinct`：门限统计与排序/截断分离。
- `tests/unit/test_p4_embedding_provider.py::test_strong_match_uses_representation_specific_thresholds`：CLIP/Qwen/未知空间统计预期。
- `tests/unit/test_photo_view_info_ui.py::test_home_detail_back_restores_browse_and_source_filter`：查看返回和来源恢复，未变部分引用PHOTO-VIEW已通过证据。

先列“必要行为→已有证据→当前差异→是否补测”。原NL报告的`-k`曾排除strong-match和place-edit用例，不能把27项成功当被排除分支也通过；只对当前任务所需且尚无等价证据的分支补直接验证，不默认修全量基线。5005条既有轻量分页记录可复用，不生成5005张照片。

实际补测用项目`.uv-cache`、`PYTHONPATH`指向仓库根及必要tests路径，全新`.pytest-tmp`子目录；执行`uv run --no-sync pytest -q <实际选定节点> --basetemp=.pytest-tmp/p4-personal-review-search-<本次标识> --junitxml=test-results/p4-personal-review-search-<本次标识>.xml`，即时记录退出码及JUnit，collect-only不算通过。没有缺口可不运行pytest。

一次小型合成浏览器：人物/日期纯筛选→续页→打开照片缩略图→详情→人物相册或Back→取消一个条件；一个内容查询检查统计显示。只有缺失环节才补，已验证的头像/地图/地点入口不再轮流重跑。小图库分页通过缩小页面大小验证；超过5000的边界复用直接轻量测试。已有模型接入浏览器文字/上传记录核对后引用，不加载或重新标定Qwen。

W-Q报告明确三条真实Qwen网页查询尚未执行，地点组fake encoder也未证明语义结果；二者不能写成当前门限实测通过。允许固定合成向量/fake encoder证明统计不改候选/排序和真实浏览器页面绑定；只提供文本通道时如实记录其范围。真实Qwen质量与用户网页确认保持单项待验，不借本组访问私人服务/模型或为通过伪造截图。

读只读API/API鉴权及web资源映射的全部未审变更；截图/DOM不证明真实模型质量。基础交互环境不可用时工程与浏览器结论分别记录，不通过改ACL、私库服务或假截图补齐。

## 3. 收口

报告 `docs/reports/P4-PERSONAL-REVIEW-SEARCH-A.md`，分别列W、W-Q、NL、PHOTO-VIEW的证据/剩余/状态。现存PHOTO-VIEW独立审查不重开，基本项核对后才能关闭个人范围；不将本文书变更本身当PASS。

默认SOL_NOT_REQUIRED。无新产品差异时不用再跑Ruff/Node/pytest；补修才跑修改文件及直接JUnit。全量基线、30万测速、跨浏览器/模型准确率均后置。完成人物/地点/搜索个人闭环后可直接日常使用，OCR/模型组按需逐组审，不强制为维持开发继续新功能。

## 4. 执行边界与启动指令

默认只读当前搜索/query/session/API/UI及其直接测试、相关ADR和历史证据；写本组报告、当前卡及development-status。发现本组直接缺陷时允许对该调用面和直接测试做最小修复，在同组会话修复、复测及复核；已审共享面只补实际新增影响。保留并行编辑，不重开人物/地点工程审查。正式改变冻结范围/权限/身份语义、新迁移、依赖或模型标定不在本卡授权内，输出精确反例与最小后续任务。

逐W、W-Q、NL、PHOTO-VIEW记录工程/个人基本验证/现场效果结论；工程通过不等于真实模型或全量B级通过。存在新冻结决定或高风险未决才保留Sol短裁决；没有则SOL_NOT_REQUIRED。闭环完成后只交接[OCR审查卡](P4-02Y-L-review.md)，不自动开始OCR或模型组。禁全量/30万/GPU/打包、私人图库/用户服务操作、下载模型、改ACL、提交/推送。

可复制到新的审核会话：

```text
按照 AGENTS.md → docs/development-status.md → docs/tasks/P4-PERSONAL-REVIEW-SEARCH-A.md，执行搜索与照片查看组基础审查。人物和地点工程审查已完成，照片查看已有用户接受的独立结论，复用这些证据。完整检查W/W-Q/NL未审新增面及查看后续增量，重点确认人物/日期条件、分页、门限统计不改排序、缩略图打开详情及返回条件保留。缺口才补最小合成测试和真实浏览器环节，发现本组问题在同会话修复复测。不要跑全量/30万、加载模型、访问真实图库或重启用户服务；Qwen真实效果未验证照实保留。写 docs/reports/P4-PERSONAL-REVIEW-SEARCH-A.md 并更新开发状态，完成后只交接OCR卡并停止，不提交或推送。
```

## 5. 当前执行记录（2026-10-09）

当前会话已完成解析/标签修复、全受影响面工程复核、定向JUnit 12/0/0/0、Ruff/Node/diff-check和合成浏览器基础流程，详情见[执行报告](../reports/P4-PERSONAL-REVIEW-SEARCH-A.md)。用户指定当前会话复核即可；记录的工程结论不声称为DS-H模型审查。Qwen真实网页效果待验。本组工程复核完成后只交接OCR卡，不自动开始OCR。
