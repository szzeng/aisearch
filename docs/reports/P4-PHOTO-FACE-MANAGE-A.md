# P4-PHOTO-FACE-MANAGE-A — 最终验收记录

- 基线：74cfcbd（已同步最新 origin/master）
- 范围：共享照片 viewer 的当前照片人脸管理、有限 SQL 预取、人物/搜索入口复用；无 schema 或权威写协议变更。
- 结果：实施完成；定向测试和两条内置浏览器路径通过。Luna High 对最终差异复核后无阻塞发现。

## 变更

- asset_faces() 保留 200 张上限和既有可见性、成员、排除、版本保护语义；对当前照片的人脸归属、忽略、候选和少量人物摘要做批量读取。测试对 1/20/50 张脸比较 SQL SELECT/WITH 语句数，三者相等。
- 照片信息区复用当前 asset_faces 结果显示 canonical 人物摘要，避免逐人物 HTTP 请求；统一管理面板延迟渲染人脸卡片，排除组默认折叠，批量选项只有进入批量模式后显示。
- 单脸操作沿用现有 assignment/create-person/visibility API；批量写仅针对当前照片已选择的人脸。未知写结果提示先刷新，不自动换 request ID 重放。
- people:false 的人物与搜索 viewer 仍显示共享管理入口。人物扩展通过 viewer 的 onFaces 回调消费同一次结果，普通模式不再列第二套脸卡；封面模式保留当前人物的封面选择。搜索入口移除逐人物 /persons/{id} 请求及重复摘要。
- 五个照片入口同步静态资源版本；无 migration、索引、人脸模型或检测行为修改。

## 验证

- 单元 UI 与收藏入口：25 passed，exit 0；JUnit test-results/P4-PHOTO-FACE-MANAGE-A-unit-final-r6.xml。覆盖共享管理器、批量控件默认隐藏/展开、people:false 回调、搜索入口不发重复人脸/逐人物请求、合集既有入口。
- 受影响集成：50 passed，exit 0；JUnit test-results/P4-PHOTO-FACE-MANAGE-A-integration-final-r2.xml。包含人物相册、人脸更正 API 与生命周期测试。
- node --check：photo-info.js、persons-albums.js、search.js 通过；受影响 Python py_compile、Ruff、git diff --check 通过。
- 内置产品浏览器使用项目内隔离合成图库（20 张人脸：5 已归属、10 待整理、3 暂时忽略、2 明确排除）。首页照片入口显示 20/5/10/5，展开后无排除缩略图请求；对一张合成待整理脸执行暂时忽略后显示 20/5/9/6，viewer 仍打开，关闭返回原列表。人物相册入口保留共享管理按钮，普通模式不显示重复脸网格；进入封面模式后仍显示当前人物的人脸与“设为封面”操作。
- 未访问或修改真实照片库。浏览器工具未提供截图文件导出能力，故未保存截图。未在浏览器中单独跑搜索结果预览、合集路径或恢复按钮；搜索/合集由定向 UI 测试覆盖，恢复由受影响 face-control API/lifecycle 集成测试覆盖。

## 限制与保留证据

- 未跑全仓回归、模型/GPU、超大图库性能或真实图库验证；这些不属于本任务的验收范围。
- Luna High 独立复核结论：无阻塞发现。复核重点包括搜索预览共享摘要/消除 N+1、人物扩展回调、任务记录及浏览器实测描述。
- 中间曾出现测试预期与合成 fixture 人脸数不符的失败，调整断言为验证共享回调确实收到 face page 后，最终 UI 定向集通过。中间与最终 JUnit 均保留在忽略目录 test-results/。
- 无提交或推送。
