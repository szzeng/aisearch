# P4-02Y-W-Q — Qwen 高度匹配恢复

**状态：IMPLEMENTED / TARGETED VALIDATION COMPLETE / PERSONAL WEB CONFIRMATION PENDING**

性质：个人试用小改。

推荐执行：**GPT-5.6 Luna / medium**。

## 1. 目标

当前真实图库已经使用：

```text
Qwen3-VL-Embedding-2B
512D MRL
L2 normalized
```

现有“高度匹配”功能只允许 Chinese-CLIP，因此 Qwen 搜索时：

```text
strong_match_available=false
```

前端会隐藏：

```text
约 N 张高度匹配
```

本卡只恢复 Qwen 的高度匹配统计。

高度匹配仍然只是**用户参考数字**：

```text
不删除照片
不减少候选
不改变排序
不改变 Top5000
不影响 RRF / FTS / Vector 检索
```

## 2. Qwen 固定门限

本卡直接采用个人使用的通用默认值：

```text
Qwen cosine >= 0.25
```

当前向量已 L2 normalize，Lance 使用 squared-L2：

```text
distance = 2 - 2 × cosine
```

因此：

```python
QWEN_STRONG_MATCH_MAX_DISTANCE = 1.5
```

即：

```text
distance <= 1.5
→ 高度匹配
```

Chinese-CLIP 原值保持不变：

```python
CLIP_STRONG_MATCH_MAX_DISTANCE = 1.3
```

不要修改 CLIP 的现有行为。

本卡不再做人工作标定、数据集评测或动态阈值。

## 3. 最小实现

主要修改：

```text
src/photo_search/search/service.py
```

把当前单一策略：

```python
STRONG_MATCH_MAX_DISTANCE = 1.3
STRONG_MATCH_SPACE_PREFIX = "chinese-clip-vit-b-16:"
```

改为按向量空间选择门限。

建议保持简单，例如：

```python
CLIP_STRONG_MATCH_MAX_DISTANCE = 1.3
QWEN_STRONG_MATCH_MAX_DISTANCE = 1.5

CLIP_SPACE_PREFIX = "chinese-clip-vit-b-16:"
QWEN_SPACE_PREFIX = "qwen3-vl-embedding-2b:"
```

提供一个内部函数：

```python
strong_match_max_distance(space_id) -> float | None
```

语义：

```text
Chinese-CLIP → 1.3
Qwen3-VL-Embedding-2B → 1.0
未知空间 → None
```

现有：

```python
strong_match_policy(...)
```

可保留为兼容包装：

```text
有已知 threshold → true
没有 → false
```

`_channel_summary()` 必须使用**当前空间自己的 threshold**计算：

```text
strong_match_count
strong_match_more_possible
strong_match_available
```

不能再让 Qwen 使用 CLIP 的 `1.3` 计算。

## 4. UI / API

原则上：

```text
src/photo_search/web/search.js
```

**不需要修改。**

现有 UI 已支持：

```text
strong_match_available=true
→ 显示“约 N 张高度匹配”

strong_match_more_possible=true
→ 显示“5000+ 张高度匹配”
```

Qwen 改成 available 后应自动恢复显示。

不要增加：

```text
相似度百分比
单张照片 cosine
阈值设置 UI
高中低三档
模型设置页
```

API 顶层结构不变。

## 5. 明确禁止扩大范围

本卡不要做：

- Qwen 重新建库；
- 模型切换；
- Chinese-CLIP / Qwen 大规模质量对比；
- 20～100 条人工标注；
- 动态阈值；
- 自适应阈值；
- percentile 阈值；
- reranker；
- RRF/BM25 调参；
- Top5000 调整；
- 30 万性能测试；
- 全量 pytest；
- schema / migration；
- 新诊断工具；
- 打包测试。

如果实现需要上述任一项，先停止并报告原因，不顺手扩卡。

## 6. 精简验证

这是用户明确授权的**个人试用定向验证例外**，仅限本卡，不推广为其他任务的通用测试规则。

### A. Qwen 门限边界

增加最小单测：

```text
Qwen distance = 0.99      → strong
Qwen distance = 1.50      → strong
Qwen distance = 1.500001  → not strong
```

确认：

```text
strong_match_available=true
strong_match_count 正确
```

### B. CLIP 不回归

只确认：

```text
CLIP threshold 仍为 1.3
```

已有：

```text
1.29
1.30
1.300001
```

边界测试保持通过即可。

### C. 未知模型

确认：

```text
未知 space
→ strong_match_available=false
```

不得把 Qwen 阈值扩散给未来其他模型。

### D. 排序不受影响

用构造数据确认改变 threshold 前后：

```text
candidate 数量不变
asset_id 顺序不变
```

只允许：

```text
strong_match_count
strong_match_more_possible
strong_match_available
```

发生变化。

## 7. 最小测试命令

只跑直接相关测试：

```powershell
uv run --no-sync pytest -q `
  tests/unit/test_p4_embedding_provider.py `
  tests/unit/test_p4_02y_w_search_capacity.py

uv run --no-sync ruff check `
  src/photo_search/search/service.py `
  tests/unit/test_p4_embedding_provider.py `
  tests/unit/test_p4_02y_w_search_capacity.py

git diff --check
```

不跑：

```text
全量 unit
全量 integration
30 万 smoke
Qwen 建库 smoke
性能专项
打包
```

## 8. 个人真实库快速确认

代码测试通过后，只用当前：

```text
D:\SZ\project\PhotoSearchData
```

现有 Qwen 索引做**只读搜索**。

不重新扫描、不重新建库。

在现有网页执行 3 条正常中文搜图查询，确认：

```text
活动模型仍为 Qwen3-VL-Embedding-2B
搜索结果正常
出现“约 N 张高度匹配”
N 合理且不同查询可以不同
高度匹配之后的照片仍继续正常显示
```

只做人眼快速确认，不要求建立标注集或计算准确率。

## 9. 文档

实现完成后只需：

```text
docs/reports/P4-02Y-W-Q-qwen-strong-match.md
docs/development-status.md
```

报告保持短小，只记录：

```text
threshold = 1.5
对应 cosine >= 0.25
修改文件
定点 pytest 结果
3 条真实搜索确认结果
未做全量/性能/标定
```

此前 P4-EMBEDDING-ARCH 报告中“Qwen 高度匹配隐藏”属于当时历史事实，**不要回改历史报告**。

## 10. 完成标准

满足以下条件即停止：

```text
Qwen distance <= 1.5
→ 被统计为高度匹配

Qwen 搜索页面
→ 恢复“约 N 张高度匹配”

Chinese-CLIP
→ 保持 distance <= 1.3

未知模型
→ 仍隐藏高度匹配

候选数量 / 排序 / Top5000
→ 完全不变
```

最终状态写：

```text
PASS — 本机个人试用 Qwen 高度匹配恢复
```

不表示：

```text
Qwen 0.25 是经过个人图库严格标定的最佳阈值
正式发行质量验收
全模型通用阈值
```

本卡完成后**不自动生成新的阈值优化任务**。
