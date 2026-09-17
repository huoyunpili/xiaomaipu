# 个人设计 Token · v0.2 视觉试用版

定位：可跨项目复用的中文效率工具 / 管理后台设计基础。参考飞书的清晰、克制、紧凑的视觉方向，建立自己的命名、使用规则和版本。当前为浅色桌面主题草案，数值是我们的建议值，未经逐项核验为飞书官方数值。

## 参考来源与边界

- [Semi 官网](https://semi.design/)列出了「飞书 Universe Design 主题」。可以作为公开的风格参考，不等同于飞书全部产品当前使用的内部 Token。
- [Semi 设计变量](https://semi.design/zh-CN/basic/tokens)：参考其颜色、字体、间距等变量化方式。
- [Semi 组件级 Token](https://semi.design/dsm_manual/zh-CN/web/componentToken)：参考组件层定制的方法。
- [Semi 布局](https://semi.design/zh-CN/basic/layout)：采用侧边导航、顶部位置栏和内容区的布局结构。

查阅日期：2026-09-16。下面的分层、命名与取值由本项目自行制定，不依赖 Semi 组件库。

## 三层结构

| 层级 | 回答的问题 | 示例 | 使用位置 |
| --- | --- | --- | --- |
| 基础值 | 有哪些原材料？ | `--ui-blue-600`、`--ui-space-4` | Token 定义内部 |
| 语义 | 用于什么目的？ | `--ui-color-action`、`--ui-color-text-primary` | 页面及通用样式 |
| 组件 | 具体控件如何呈现？ | `--ui-button-primary-bg` | 对应组件样式 |

引用方向：组件 → 语义 → 基础值。避免页面直接引用色阶；主题变化时保持语义名称稳定。组件只在需要独立调整时增加 Token，避免为每个 CSS 属性建立变量。

## 第一版视觉基线

| 类别 | 建议 | 使用规则 |
| --- | --- | --- |
| 品牌 | `#3370FF` | 品牌识别与强调；不是所有位置都铺主色 |
| 操作色 | `#245BDB` | 主要操作按钮、焦点和必要的数据图形；普通链接不使用操作色 |
| 文本 | `#1F2329` / `#646A73` | 主文 / 辅助文；禁用色不能拿来显示必要说明 |
| 背景 | `#F7F8FA` / `#FFFFFF` | 侧栏、轻量统计区 / 页面与内容表面 |
| 链接 | `#1F2329` | 中性色，配合悬停下划线、按钮轮廓或上下文保持可辨识 |
| 导航选中 | `#E8EAED` + 深灰文字 | 灰底、字重强调；不靠大面积蓝色表示当前位置 |
| 边框 | `#E4E7ED` / `#8F959E` | 细分隔 / 需要辨识轮廓的输入控件 |
| 字号 / 行高 | 12/18、14/22、16/24、20/28、24/32 px | 说明、正文、小标题、区块标题、页面标题 |
| 字重 | 400 / 500 / 600 | 正文 / 标签 / 标题 |
| 间距 | 4、8、12、16、20、24、32、40、48 px | 默认用 4px 的倍数；边框和图标光学校正例外 |
| 圆角 | 4、6、8、12 px | 小元素、控件、卡片、弹窗 |
| 控件高度 | 28 / 32 / 40 px | 桌面紧凑 / 默认 / 宽松；触屏命中区域至少 44px |
| 阴影 | 浮层 / 弹窗两档 | 普通卡片优先边框，不默认加阴影 |
| 动效 | 120 / 200 ms | 状态反馈 / 展开收起；尊重减少动态效果偏好 |

数字、金额列右对齐，并使用 `font-variant-numeric: tabular-nums`。列表、表单保持同一密度；表格高度应允许多行文字撑开，不能固定高度截断内容。

## 状态与组件规则

- 品牌、成功、警告、危险分别定义；成功不能因为更换品牌色而变化。
- 按钮需要 default、hover、active、focus-visible、disabled、loading；loading 同时禁止重复提交并展示进度语义。
- 输入框需要 default、hover、focus、error、disabled；错误必须同时提供文字说明。
- 表格需要 default、hover、selected、empty、loading、error；选中状态配合复选框或其他明确标记。
- 状态标签使用「浅底色 + 深文字 + 状态名称」，不只靠颜色传递信息。
- 文本正文与背景以至少 4.5:1 为验收目标；控件轮廓和焦点指示至少 3:1。取值并不代替对最终页面组合的检查。
- 圆角、间距、字号不由每个页面单独发挥。新需求先找已有语义，再判断是否新增。

## 在代码中使用

`app/static/tokens.css` 是 CSS 数值的唯一维护入口；本目录的 `tokens.css` 仅转引它。后台现已在根节点启用 `data-ui-theme="light"`，加载 Token 和 `app/static/theme.css` 通用组件规则。组件通过以下方式引用：

```css
.primary-button {
  min-height: var(--ui-button-height);
  padding-inline: var(--ui-button-padding-x);
  border: 0;
  border-radius: var(--ui-button-radius);
  background: var(--ui-button-primary-bg);
  color: var(--ui-button-primary-text);
  font-family: var(--ui-font-family);
  font-size: var(--ui-font-size-body);
  line-height: var(--ui-line-height-body);
}
.primary-button:hover:not(:disabled) {
  background: var(--ui-button-primary-bg-hover);
}
.primary-button:active:not(:disabled) {
  background: var(--ui-button-primary-bg-active);
}
.primary-button:focus-visible {
  outline: var(--ui-focus-width) solid var(--ui-color-focus);
  outline-offset: var(--ui-focus-offset);
}
.primary-button:disabled {
  background: var(--ui-color-bg-disabled);
  color: var(--ui-color-text-disabled);
  cursor: not-allowed;
}
```

若后续需要设计工具同步，再引入机器可读的源文件和生成流程，生成 CSS 与设计工具变量，避免同时手工维护多份数值。Figma 中可用 `color/text/primary` 对应 CSS `--ui-color-text-primary`；字体组合使用 Text Styles。暗色主题需逐项设计语义映射，本版尚未提供。

## 持续统一的约定

1. 每个项目加载同一版本 Token；项目专属覆盖放在独立主题中。
2. 通用组件样式集中维护；业务页面使用语义或组件 Token。Token 统一数值，组件规范统一布局和行为，两者配合。
3. 新增 Token 写清用途；不要使用 `blue-button`、`left-box-color` 这类依赖颜色或位置的名字。
4. 修改已有取值记录视觉变更；删除或重命名先给迁移说明。正式稳定后按版本发布。
5. 评审时检查按钮、表单、表格、弹窗、空状态，以及键盘焦点、禁用、错误、长文本、缩放和窄屏。

## 当前闲鱼后台的接入情况

2026-09-16：已接入后台基础模板，将原有四份样式中的颜色改为语义 Token，并通过 `theme.css` 统一字体、控件、卡片、表格和手机点击区域。桌面默认控件 32px，窄屏或触屏 44px。后续逐页迁移旧页面剩余的固定尺寸。

v0.2：根据实际视觉反馈，改为 208px 浅灰侧栏、56px 顶部位置栏和白色内容区。普通链接、订单名和金额使用中性色；选中导航用灰底加字重。统计项合并为浅灰分组，内容区通过细分隔线组织，减少重复卡片。小屏幕将侧栏恢复为顶部换行导航，保留所有入口与 44px 点击区域。

运行现有工作台浏览器及静态资源测试，覆盖桌面 1365px、手机 390px 的八个主要页面、供应商下拉、保存、利润筛选、导出和售后状态。截图位于 `artifacts/browser/workspace/`，使用隔离测试数据。当前为 v0.2 视觉试用版，尚未覆盖全部历史页面及所有无障碍组合。
