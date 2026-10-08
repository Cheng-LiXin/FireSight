/** 图表动效的统一口径。
 *
 * 六张图的配置构造函数（`charts/options.ts`）保持纯函数——只描述数据长什么样，
 * 不描述它怎么动。这样做有两个理由：
 *
 * 1. 动效参数是「跨页的一致性决策」，不是每张图的个性。曲线慢一点、柱状图快一点
 *    这类差异，写进 options.ts 就会散成六份互相矛盾的数字；
 * 2. options.ts 可以被单测直接调用，注入动效会让测试开始关心动画帧数。
 *
 * 所以动效集中在这里合成，`EChart.vue` 在 setOption 之前过一遍。
 *
 * 三条口径：
 * - 柱状图按数据下标错峰（每根晚 18ms 立起来），读起来是「数据逐项落位」；
 * - 折线图整条一起画完，不逐点错峰——曲线是连续量，逐点跳出来会读成「在抖」；
 * - 时长按 ECharts 默认档（700/1000）压到 760ms 以内。真实网络下，一张图铺开
 *   超过 1 秒，用户会以为卡住了。
 */
import type * as echarts from 'echarts'

/** 离散项（柱子）的错峰步长。数据多时 ECharts 自己会摊薄，这里不按数据量自适应。 */
const ITEM_STAGGER_MS = 18

/** 一根柱子立起来要多久 */
const BAR_MS = 520

/** 一条线画完要多久 */
const LINE_MS = 760

/**
 * 给一份配置补上动效。返回新对象，不改入参——
 * options.ts 的返回值可能被调用方缓存，原地改会污染下一次渲染。
 */
export function withMotion(option: echarts.EChartsOption): echarts.EChartsOption {
  const series = option.series

  // 没有 series 的配置（少见）只给全局档位。
  if (!series || !Array.isArray(series)) {
    return {
      ...option,
      animation: true,
      animationDuration: BAR_MS,
      animationEasing: 'cubicOut',
    }
  }

  const moved = series.map((s) => {
    const one = s as Record<string, unknown>
    // 调用方显式写了 animation:false 就是明确不要动效，尊重它。
    if (one.animation === false) return s

    if (one.type === 'line') {
      return {
        ...one,
        animation: true,
        animationDuration: LINE_MS,
        animationEasing: 'cubicOut',
      }
    }

    return {
      ...one,
      animation: true,
      animationDuration: BAR_MS,
      animationEasing: 'cubicOut',
      // 逐项错峰：柱子按 x 轴下标依次立起来
      animationDelay: (idx: number) => idx * ITEM_STAGGER_MS,
    }
  })

  return { ...option, series: moved as echarts.EChartsOption['series'] }
}