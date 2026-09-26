/** ECharts 配置构造函数。纯函数，便于单测与复用。
 *
 * 两条硬约束来自 dataviz 规范：
 *   1. 绝不做双 Y 轴。告警「次数」与「置信度」量纲不同，拆成两张共用 X 轴的图，
 *      否则两个刻度的对齐关系是任意的，会凭空造出数据里没有的相关性。
 *   2. 类别颜色按固定槽位分配，不随排名变化，也不按数值深浅着色。
 */

import type { EChartsOption } from 'echarts'

import type { DailyPoint } from '@/api/types'

import { AXIS_FONT, PALETTE } from './palette'

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
