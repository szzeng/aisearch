# P4-UI-TIMELINE-REVISION-A

## 目标

补齐 Timeline CORE-A 的 revision 一致性链路。

当前 Timeline 已支持：

- cursor 绑定 `catalog_filter_revision`
- stale revision 检测
- 分页一致性保护

但部分图库写入流程没有触发共享 revision，导致图库变化后旧 cursor 可能继续使用。

本任务只补齐已有 revision 机制，不设计新的 revision 系统。

---

## 非目标

本任务不包含：

- Timeline 查询性能优化
- SQLite 索引调整
- schema 修改
- 新增 revision 表
- 新增事件系统
- 修改 cursor 协议
- UI 调整

必须继续使用现有：

`catalog_filter_revision`

作为唯一版本依据。

---

## 实施内容

### 1. 梳理现有 revision 机制

确认：

- revision 存储位置
- revision 增加入口
- 当前已经接入的 mutation

禁止创建第二套版本机制。

---

### 2. 检查遗漏 mutation

重点检查影响 Timeline 结果的操作：

#### 图片生命周期

- 新资产导入
- 扫描发现新增照片
- 删除照片
- 恢复照片
- 图片状态变化

#### 人物变化

- 人物绑定照片
- 移除人物照片
- 合并人物
- 忽略人物

#### 合集变化

- 添加合集成员
- 移除合集成员
- 合集删除

只要会改变 Timeline 查询结果，必须触发 revision。

---

### 3. 接入已有写事务

优先复用现有 mutation 边界。

推荐：

```
transaction
  update data
  update relations
  bump catalog_filter_revision
commit
```

不要：

- Timeline 查询时检测变化
- 后台定时扫描变化
- 新建独立 revision

---

## Revision 策略

批量操作需要避免 revision 无意义增长。

**用户决策（2026-10-13）**：当前 scanner 为保留流式扫描与逐文件恢复，资产绑定按文件分别提交；每个文件绑定事务作为 revision 变更单位。因此一个扫描中每个真正新增的 asset 各增加一次 revision。若一个事务批量写入多个资产，则该事务只增加一次；no-op 或重复内容不增加。不得把 revision bump 延迟到扫描结束，否则扫描过程中的旧 cursor 仍可跨已提交图库变化。

例如：

一次导入 10000 张照片：

正确：

```
single write transaction imports 10000 assets
    |
    +- catalog_filter_revision +1
```

错误：

```
10000 assets written in one transaction
    |
    +- revision +10000
```

---

## 验收测试

### Test 1：新增照片

步骤：

1. 打开 Timeline
2. 获取 cursor
3. 导入新照片
4. 使用旧 cursor 翻页

期望：

返回现有 revision stale 错误。

---

### Test 2：普通分页

无图库变化：

连续翻页正常。

---

### Test 3：批量导入

一次导入多张照片。

确认 revision 增长合理，不按照片数量增长。

---

### Test 4：人物变化

修改照片人物归属。

旧 Timeline cursor 应失效。

---

### Test 5：合集变化

修改合集成员。

旧 Timeline cursor 应失效。

---

## 测试范围

要求：

- 增加 revision unit test
- 增加 mutation regression test

不要求：

- 全仓测试
- 30万图库测试
- 浏览器测试

---

## 完成标准

- Timeline cursor 与图库变化保持一致
- 所有影响 Timeline 的 mutation 正确触发 revision
- 不引入第二套 revision
- 不修改现有 API
- 定向测试通过

---

## 推荐模型

实现：中等代码模型即可。

审核：LUNA H / GPT-6。

重点关注 mutation 边界，不扩大范围。
