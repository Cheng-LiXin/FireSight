# 第三批：Element Plus 与动效体系（2026-10-08）

用户在第一批八项决策、第一批/第二批落地之后，拍板了第三批范围：**全面启用
Element Plus**（能用 EP 的都用）+ **明显强度动效**（数字滚动、曲线绘制动画、
全屏转场）+ **原生 `window.confirm` 换成 `ElMessageBox`**。

这是用户第二次直接指定实现范围（第一次是那八项决策）。

---

## 1. EP 早就装好，却一个组件都没用

| 事实 | 证据 |
| --- | --- |
| 依赖早就在 | `apps/web/package.json` 有 `element-plus` |
| 早就全量注册 | `apps/web/src/main.ts:11,23` 有 `app.use(ElementPlus, { locale: zhCn })` 与 `import 'element-plus/dist/index.css'` |
| 361KB 样式一直在产物里 | `node_modules/element-plus/dist/index.css` = 361441 字节；`dist/assets/index-*.css` ≈ 370.8 KB |
| 但零组件在用 | grep `apps/web/src` 的 `el-\|ElementPlus` 只命中 4 处，全是巧合子串（`.model-note`、`.panel--params`）或定义处 |

**结论：不是「引入」，是「一直没兑现」。**

## 2. 纪律：启用组件 ≠ 保留默认皮

EP 的默认皮正是它最强的识别特征，而参考项目 FireObserve 的历史页用的正是
**原装 `el-table :border="true"` + `el-progress`**。照搬默认样式等于把两套界面
拉回同一个模子，直接违背「一眼看不出同源」这条最初约束。

`apps/web/src/styles/ep.css`（约 300 行）逐项拆掉：

| EP 默认 | 换成 |
| --- | --- |
| 表格方卡 + 灰底 + 竖线 | 透明底、去圆角、伪元素边框 `display:none`、只留发丝横线 |
| 分页蓝底蓝字 | 当前页 `--accent` 近黑底白字 |
| 骨架屏微光扫过 | `--el-skeleton-color: var(--soot)` 安静灰条 |
| 空状态具象插画 | `.el-empty__image { display: none }`，容器交给 `.fs-empty` |
| 确认框圆角阴影 | 发丝线 + `--shadow-3`；`.el-message-box .el-button--primary` 用 `--alarm` 底（确认框本身是危险操作外壳） |

**导入顺序**（`apps/web/src/main.ts:4-11`）：
`element-plus/dist/index.css` → `tokens.css` → `ep.css` → `base.css`。
ep.css 是覆盖层，必须排在 tokens 之后——令牌要先到位它才知道往哪接。

## 3. 四张最密的账册表不迁 el-table（撤回的计划）

- **告警事件表不迁**。`/api/events` 只暴露
  `keyword/label/status/start/end/limit/offset`，排序写死在
  `apps/fire-sight/src/fire_sight/alerting/store.py:196`
  的 `ORDER BY ts DESC, event_id DESC`。没有 sort 参数时，el-table 的列排序只会
  重排**当前这一页**的行，却长得像全局排序——比不排更误导。
  另外 el-table 的 `<tr>` 由组件内部生成
  （`node_modules/element-plus/es/components/table/src/table-body/render-helper.mjs:37-45`），
  只接受 `rowClassName`/`rowStyle`（`styles-helper.mjs:18-29`），挂不上
  `data-event-id`——而 `apps/web/e2e/events.spec.ts` 有 8 处靠这个属性定位行。
- **归档权重 / 对比评估 / 训练记录暂不迁**：同样没有后端排序，迁过去只买到分页
  控件，而分页逻辑本来就自写且与筛选状态耦合很深。
- 反向理由也成立：自研 `.fs-ledger` 与 EP 方卡**本来就差得更远**，保持原样零风险。

## 4. 动效体系

`tokens.css` 新增：

| 令牌 | 值 | 分工 |
| --- | --- | --- |
| `--ease-out` | `cubic-bezier(0.16,1,0.3,1)` | 进场：起手快收尾稳 |
| `--ease-back` | `cubic-bezier(0.34,1.56,0.64,1)` | 回弹：轻微过冲，让「变了」被看见 |
| `--dur-3` | 320ms | 块级转场 |
| `--dur-4` | 450ms | 整页级别的读数变化 |
| `--stagger` | 40ms | 列表错峰步长 |
| `--count-min-delta` | 2 | 差值小于此不滚动 |

三条缓动**各有分工不可混用**。时长按**元素离眼睛多远**分档，而不是按效果复杂
不复杂；超过 600ms 的等待动画在真实网络下显得卡。

### 4.1 数字滚动 `components/CountUp.vue`

props `{ value: number|string|null|undefined; digits?: number = 0; separator?: boolean = true }`，
emit `settled`。rAF + `1-(1-t)^3` 三次缓出。三条让它不至于变成炫技的规矩：

1. 差值小于 `--count-min-delta` 不滚（3→4 滚一趟只有一帧，肉眼看成噪声）；
2. 时长随跨度增加但**封顶**在 `--dur-4`（0→9999 等好几秒比不滚更糟）；
3. 尊重 `prefers-reduced-motion`——**前庭功能敏感的用户看到滚动数字会真的难受**。

**滚动起点取「当前显示值」而非上次原始值**：连点切换月份时，动画从屏幕上看得
到的位置接着走，而不是从两个月前跳回来。

接入 `DashboardView` 的四条读数（只有真数字才滚，上游已格式化的字符串原样显示）
与 `BigScreenView.vue` 五个 KPI。

### 4.2 图表绘制 `charts/motion.ts`

`withMotion(option)` 在 `EChart.vue` 的 `setOption` 之前统一合成。柱状图按 x 轴
下标错峰 18ms 立起来；折线整条一起画完不逐点跳——**曲线是连续量，逐点跳出来
会读成「在抖」**。

`charts/options.ts` 保持纯函数：**只描述数据长什么样，不描述它怎么动**。
动效是跨页的一致性决策，写进 options 会散成六份互相矛盾的数字；而且那份文件
可以被单测直接调用，注入动效会让测试开始关心动画帧数。

### 4.3 进出大屏转场 `App.vue`

转场必须**横跨**「壳」与「大屏」两个分支。把 `<transition>` 放在壳内部是无效的——
`v-if` 会先销毁整个壳节点，壳内的 transition 根本没机会播完。

```js
const stageName = computed(() => (isFullscreen.value ? 'stage-in' : 'stage-out'))
```

名字跟着**目标分支**走，于是切换时新旧两套类自动成对：去大屏 = `stage-out`，
回大屏 = `stage-in`。新增 `.stage` 容器（flex column + `min-height:100%`），
让 `.bigscreen` 自己的 `min-height:100%` 有得参照。

## 5. 关键教训：降级不能只靠 CSS

`base.css` 那条 `prefers-reduced-motion` 规则只压得住 CSS 动画与过渡。
**ECharts 画在 canvas 上、由 JS 驱动时间轴；`CountUp` 更是自己
`requestAnimationFrame`——这两者都要各自判一次 `matchMedia`。**

漏判的后果不是「动得太多」，而是「宣称支持降级但实际没降」。

## 6. 关键教训：加载态诚实

Dashboard 与 ModelsView 在数据到达前会显示 `0` / 「尚无告警记录」 /
「还没有归档训练」——这是在把「不知道」说成「没有」。改为：

- 未取到数据时读数显示 **`—`**（`0` 是一个结论）；
- 面板给 `el-skeleton`，`v-else-if="loading"` 分支要放在 `v-else` 空态**之前**。

其余各页本来就有 `busy` + 显式「加载中…」文案或 `v-else-if="!busy"` 守卫。

## 7. ElMessageBox 替换的 e2e 陷阱

原生 `window.confirm` 靠 `page.on('dialog')` 拦；**换成 ElMessageBox 之后它
渲染成页面里 `role=dialog` 的元素，原生 dialog 事件永不触发**。

`apps/web/e2e/resume.spec.ts` 已改写并**加强**：先点「取消」断言 dialog 隐藏 +
`payload === null` + `nativeDialog === false`，再点一次确认断言
`payload.resume_run_id` 且 `imgsz/batch/epochs` 仍为 `undefined`。

## 8. 踩坑

| 坑 | 结论 |
| --- | --- |
| 一次性 Playwright 探针放在仓库根 | `require('@playwright/test')` MODULE_NOT_FOUND，必须放 `apps/web/` 下 |
| `page.route('**/api/overview')` 匹配不到 | 实际请求带 `?days=30`，glob 必须写 `**/api/overview*` |
| `charts/motion.ts` 一开始写成 `.vue` | 纯 TS 模块不能有 `<script setup>`/`<template>`；且纯模块不该放 `components/`，用 `Move-Item` 移到 `charts/` |
| 把 `<transition>` 放进壳内部 | 根本不触发，见 4.3 |

---

*本阶段验证时产生的 `shots/` 截图与 `.tmp-*.cjs` 探针脚本都未提交。截图目录已于
2026-10-09 的代码审查中作为空目录删除；即便还有截图，也不要为验证产物新建受跟踪目录。*