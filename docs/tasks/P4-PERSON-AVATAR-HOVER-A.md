# P4-PERSON-AVATAR-HOVER-A — 人物页头像悬停边框统一

- 状态：IMPLEMENTED / BROWSER ACCEPTANCE PENDING（用户要求不再继续浏览器验证）。
- 用户需求：统一人物页面所有头像的底框；鼠标悬停时出现轻微变化。
- 实现：在人物页统一为头像加边框颜色和柔和光圈过渡；涵盖 `.pa-avatar`、`.pa-choice-avatar`、`.avatar` 与 `.avatar-placeholder`，尊重减少动态效果设置。头像卡原有可点击指针及选中态不变。
- 修改范围：`src/photo_search/web/persons.css`、`src/photo_search/web/persons.html` 及 CSS 版本断言。
- 不变量：不改人物归属、选中、导航或合集业务行为；不访问真实图库。
- 验收：未运行测试；未继续浏览器验收（用户要求跳过）。本轮较早的一次单头像 DOM 样式探查读到 hover 边框使用强调色，但没有截图或跨区域视觉验收，不据此标记浏览器通过。
- 报告：[P4-PERSON-AVATAR-HOVER-A](../reports/P4-PERSON-AVATAR-HOVER-A.md)。
