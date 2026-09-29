/** ECharts 配置构造函数。纯函数，便于单测与复用。
 *
 * 两条硬约束来自 dataviz 规范：
 *   1. 绝不做双 Y 轴。告警「次数」与「置信度」量纲不同，拆成两张共用 X 轴的图，
 *      否则两个刻度的对齐关系是任意的，会凭空造出数据里没有的相关性。
 *   2. 类别颜色按固定槽位分配，不随排名变化，也不按数值深浅着色。
 */

import type { EChartsOption } from 'echarts'

import type { DailyPoint, MonthBucket } from '@/api/types'

import { AXIS_FONT, PALETTE, TRAIN_PALETTE } from './palette'

/** 曲线里允许 null：缺失的那一轮留断点，不要连过去，也不要画成 0。 */
type Series = (number | null)[]

const axisLabel = { color: PALETTE.inkMuted, fontSize: 11, fontFamily: AXIS_FONT }

/** 网格线是实线细线，比底色深一档。虚线会被误读成「阈值」或「预测」。 */
const splitLine = {
  show: true,
  lineStyle: { color: PALETTE.hairline, width: 1, type: 'solid' as const },
}

const tooltipBase = {
  backgroundColor: PALETTE.surface,
  borderColor: PALETTE.hairline,
  borderWidth: 1,
  padding: [8, 12] as [number, number],
  textStyle: { color: PALETTE.ink, fontSize: 12 },
}

const GRID = { left: 44, right: 24, top: 32, bottom: 34 }

function datesOf(daily: DailyPoint[]): string[] {
  // 只显示 MM-DD：30 个 ISO 日期会把轴挤爆
  return daily.map((d) => d.date.slice(5))
}

/**
 * 每日告警次数，按类别堆叠。
 *
 * 段间留 2px 底色间隙（靠 1px 边框在接缝两侧各出一条），而不是给每段描边——
 * 描边会让图变重，间隙才是让堆叠可读的做法。
 */
export function alertTrendOption(daily: DailyPoint[]): EChartsOption {
  const smoke = daily.map((d) => ({
    value: d.smoke,
    // 圆角只给「顶段」，贴着基线的一端保持方角
    itemStyle: { borderRadius: d.fire > 0 ? [0, 0, 0, 0] : [4, 4, 0, 0] },
  }))
  const fire = daily.map((d) => ({ value: d.fire, itemStyle: { borderRadius: [4, 4, 0, 0] } }))

  return {
    backgroundColor: 'transparent',
    grid: GRID,
    legend: {
      top: 0,
      left: 0,
      itemWidth: 10,
      itemHeight: 10,
      itemGap: 18,
      textStyle: { color: PALETTE.inkMuted, fontSize: 12 },
      data: ['烟雾', '明火'],
    },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, ...tooltipBase },
    xAxis: {
      type: 'category',
      data: datesOf(daily),
      boundaryGap: true,
      axisLine: { lineStyle: { color: PALETTE.hairline } },
      axisTick: { show: false },
      axisLabel,
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel,
      splitLine,
    },
    series: [
      {
        name: '烟雾',
        type: 'bar',
        stack: 'alerts',
        barMaxWidth: 16,
        data: smoke,
        itemStyle: {
          color: PALETTE.catSmoke,
          borderColor: PALETTE.surface,
          borderWidth: 1,
        },
      },
      {
        name: '明火',
        type: 'bar',
        stack: 'alerts',
        barMaxWidth: 16,
        data: fire,
        itemStyle: {
          color: PALETTE.catFire,
          borderColor: PALETTE.surface,
          borderWidth: 1,
        },
      },
    ],
  }
}

// --------------------------------------------------------------------------
// 月度（大屏）
// --------------------------------------------------------------------------

/** 近 N 个月告警量，按类别堆叠。空月份补 0 柱，不画「无数据」大字。 */
export function monthlyTrendOption(months: MonthBucket[]): EChartsOption {
  const smoke = months.map((m) => ({
    value: m.smoke,
    itemStyle: { borderRadius: m.fire > 0 ? [0, 0, 0, 0] : [4, 4, 0, 0] },
  }))
  const fire = months.map((m) => ({ value: m.fire, itemStyle: { borderRadius: [4, 4, 0, 0] } }))

  return {
    backgroundColor: 'transparent',
    grid: GRID,
    legend: {
      top: 0,
      left: 0,
      itemWidth: 10,
      itemHeight: 10,
      itemGap: 18,
      textStyle: { color: PALETTE.inkMuted, fontSize: 12 },
      data: ['烟雾', '明火'],
    },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, ...tooltipBase },
    xAxis: {
      type: 'category',
      // 只显示 MM 月：12 个 YYYY-MM 会把轴挤爆
      data: months.map((m) => `${Number(m.month.slice(5))}月`),
      boundaryGap: true,
      axisLine: { lineStyle: { color: PALETTE.hairline } },
      axisTick: { show: false },
      axisLabel,
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel,
      splitLine,
    },
    series: [
      {
        name: '烟雾',
        type: 'bar',
        stack: 'alerts',
        barMaxWidth: 22,
        data: smoke,
        itemStyle: { color: PALETTE.catSmoke, borderColor: PALETTE.surface, borderWidth: 1 },
      },
      {
        name: '明火',
        type: 'bar',
        stack: 'alerts',
        barMaxWidth: 22,
        data: fire,
        itemStyle: { color: PALETTE.catFire, borderColor: PALETTE.surface, borderWidth: 1 },
      },
    ],
  }
}

/** 24 小时时段分布。不是类别身份，不占类别色——用主文字的中性灰。 */
export function hourlyOption(hourly: number[]): EChartsOption {
  const data = Array.from({ length: 24 }, (_, h) => hourly[h] ?? 0)
  return {
    backgroundColor: 'transparent',
    grid: GRID,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      ...tooltipBase,
      valueFormatter: (v: unknown) => `${v} 次`,
    },
    xAxis: {
      type: 'category',
      data: data.map((_, h) => (h % 3 === 0 ? String(h) : '')),
      boundaryGap: true,
      axisLine: { lineStyle: { color: PALETTE.hairline } },
      axisTick: { show: false },
      axisLabel: { ...axisLabel, interval: 0 },
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel,
      splitLine,
    },
    series: [
      {
        name: '告警次数',
        type: 'bar',
        barMaxWidth: 14,
        data,
        itemStyle: { color: PALETTE.ink, borderRadius: [4, 4, 0, 0] },
      },
    ],
  }
}

// --------------------------------------------------------------------------
// 训练曲线
// --------------------------------------------------------------------------

/** 训练曲线共用的坐标轴与网格。 */
function trainAxes(epochs: number[]) {
  return {
    grid: { left: 56, right: 76, top: 44, bottom: 34 },
    xAxis: {
      type: 'category' as const,
      data: epochs.map(String),
      boundaryGap: false,
      name: 'epoch',
      nameLocation: 'end' as const,
      nameGap: 8,
      nameTextStyle: { color: PALETTE.inkMuted, fontSize: 11, fontFamily: AXIS_FONT },
      axisLine: { lineStyle: { color: PALETTE.hairline } },
      axisTick: { show: false },
      axisLabel: { ...axisLabel, interval: Math.max(0, Math.floor(epochs.length / 10) - 1) },
    },
    yAxis: {
      type: 'value' as const,
      scale: true,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel,
      splitLine,
    },
  }
}

const TRAIN_TOOLTIP = {
  trigger: 'axis' as const,
  axisPointer: { type: 'line' as const, lineStyle: { color: PALETTE.inkMuted, width: 1 } },
  ...tooltipBase,
}

/** 一条曲线。线宽 2、不画点（50 个点会把图糊掉），断点不连。 */
function line(name: string, data: Series, color: string, dashed = false) {
  return {
    name,
    type: 'line' as const,
    data,
    symbol: 'circle' as const,
    showSymbol: false,
    connectNulls: false,
    lineStyle: { width: 2, color, type: dashed ? ('dashed' as const) : ('solid' as const) },
    itemStyle: { color },
  }
}

export interface LossPanel {
  title: string
  option: EChartsOption
}

/**
 * 损失曲线 —— 每种损失一张小倍数图。
 *
 * 不做「六条线一张图」：实测某一轮 val/cls_loss 能到 87，而其余五条全程在 1.1–2.2，
 * 58 倍的差距摆在同一根线性轴上会把其余五条压成贴底的直线，整张图失去作用。
 * 也不改用对数轴——那是对读者耍花招。分成三张、各自自适应量程，震荡照实显示，
 * 别的图照样看得清。
 *
 * 每张图内只有「训练 / 验证」两条，用实线与虚线区分，因此一个颜色就够，
 * 不需要类别色板。哪种损失由图的标题说明。
 */
export function trainingLossPanels(
  epochs: number[],
  losses: Record<string, Series>,
): LossPanel[] {
  const color = TRAIN_PALETTE[0]
  const kinds: [string, string, string][] = [
    ['box', 'train_box', 'val_box'],
    ['cls', 'train_cls', 'val_cls'],
    ['dfl', 'train_dfl', 'val_dfl'],
  ]

  return kinds
    .filter(([, trainKey, valKey]) => trainKey in losses || valKey in losses)
    .map(([cn, trainKey, valKey]) => ({
      title: `${cn} 损失`,
      option: {
        backgroundColor: 'transparent',
        ...trainAxes(epochs),
        legend: {
          top: 0,
          left: 0,
          itemWidth: 14,
          itemHeight: 8,
          itemGap: 16,
          textStyle: { color: PALETTE.inkMuted, fontSize: 11 },
          data: ['训练', '验证'],
        },
        tooltip: TRAIN_TOOLTIP,
        series: [
          line('训练', losses[trainKey] ?? [], color),
          line('验证', losses[valKey] ?? [], color, true),
        ],
      } as EChartsOption,
    }))
}

/**
 * 指标曲线。四条都是 0–1 的同量纲指标，放在一张图上。
 *
 * 不做双轴：它们刻度相同，没有对齐问题。
 */
export function trainingMetricOption(
  epochs: number[],
  metrics: Record<string, Series>,
): EChartsOption {
  // 四条各占一个色槽（白底重调后的 4 色板，相邻最差 deutan ΔE 21.8）。
  // 曾试过「两级灰 × 实线/虚线」的纯灰阶方案：灰阶在 #FFFFFF 上够 3:1 的只有 5 档、
  // 相邻 ΔE 掉到 6.8，四条线挤在一起读不出来，故改回彩色。
  const specs: [string, string, string][] = [
    ['mAP@50', 'mAP50', TRAIN_PALETTE[0]],
    ['mAP@50-95', 'mAP50_95', TRAIN_PALETTE[1]],
    ['精确率 P', 'precision', TRAIN_PALETTE[2]],
    ['召回率 R', 'recall', TRAIN_PALETTE[3]],
  ]
  const series = specs
    .filter(([, key]) => key in metrics)
    .map(([cn, key, color]) => line(cn, metrics[key] ?? [], color))

  return {
    backgroundColor: 'transparent',
    ...trainAxes(epochs),
    yAxis: { ...trainAxes(epochs).yAxis, scale: false, min: 0, max: 1 },
    legend: {
      top: 0,
      left: 0,
      itemWidth: 14,
      itemHeight: 8,
      itemGap: 14,
      textStyle: { color: PALETTE.inkMuted, fontSize: 11 },
      data: series.map((s) => s.name),
    },
    tooltip: TRAIN_TOOLTIP,
    series,
  }
}

/**
 * 每日平均置信度。单序列，因此不要图例——图上的标题就是它的命名。
 * 只在末端做一次直接标注，其余交给坐标轴与十字准线。
 */
export function confidenceOption(daily: DailyPoint[]): EChartsOption {
  // 无告警的日子，平均置信度是「未定义」而不是 0。
  // 填 0 会让折线砸到谷底，读起来像「模型置信度为零」——那是另一回事。
  const values: (number | null)[] = daily.map((d) => (d.count > 0 ? d.avg_conf : null))
  const lastDefined = [...values].reverse().find((v) => v !== null) ?? null

  return {
    backgroundColor: 'transparent',
    // 右侧留出端点标注的宽度，否则 0.xx 会被画布边缘裁掉
    grid: { ...GRID, right: 52 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line', lineStyle: { color: PALETTE.inkMuted, width: 1 } },
      ...tooltipBase,
    },
    xAxis: {
      type: 'category',
      data: datesOf(daily),
      boundaryGap: false,
      axisLine: { lineStyle: { color: PALETTE.hairline } },
      axisTick: { show: false },
      axisLabel,
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: 1,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { ...axisLabel, formatter: (v: number) => v.toFixed(1) },
      splitLine,
    },
    series: [
      {
        name: '平均置信度',
        type: 'line',
        data: values,
        symbol: 'circle',
        showSymbol: false,
        // 断点就是断点，不要跨越它连线，否则等于凭空补出数据
        connectNulls: false,
        lineStyle: { width: 2, color: PALETTE.ink },
        itemStyle: { color: PALETTE.ink },
        // 端点直接标注：规范允许「选择性直接标注」，端点是最该标的那个点
        endLabel: {
          show: lastDefined !== null,
          formatter: () => (lastDefined ?? 0).toFixed(2),
          color: PALETTE.inkMuted,
          fontSize: 11,
          fontFamily: AXIS_FONT,
        },
        emphasis: { scale: 1.4 },
      },
    ],
  }
}
