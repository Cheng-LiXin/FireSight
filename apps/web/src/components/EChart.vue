<script setup lang="ts">
/** ECharts 容器：负责生命周期与尺寸自适应，图表配置由调用方给出。
 *
 * 容器高度由调用方通过 height 指定，且必须包住 X 轴文字带——
 * 固定高度裁掉轴标签会长出一条嵌套滚动条。
 */
import * as echarts from 'echarts'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { withMotion } from '@/charts/motion'

const props = withDefaults(defineProps<{ option: echarts.EChartsOption; height?: number }>(), {
  height: 260,
})

const el = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null
let observer: ResizeObserver | null = null

/** 用户在系统里关了动效：图表直接落到终态，不画进场。
 *  这一条必须在这里单独判一次——base.css 那条 prefers-reduced-motion
 *  规则只压得住 CSS 动画，而 ECharts 画在 canvas 上、由 JS 驱动时间轴，
 *  CSS 那道闸门管不到它。 */
function reduced(): boolean {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
}

function apply(option: echarts.EChartsOption): echarts.EChartsOption {
  return reduced() ? { ...option, animation: false } : withMotion(option)
}

onMounted(() => {
  if (!el.value) return
  chart = echarts.init(el.value, undefined, { renderer: 'canvas' })
  // 动效口径在 charts/motion.ts 里合成，配置本身保持纯数据描述
  chart.setOption(apply(props.option))
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(el.value)
})

watch(
  () => props.option,
  (opt) => chart?.setOption(apply(opt), { notMerge: true }),
  { deep: true },
)

onBeforeUnmount(() => {
  observer?.disconnect()
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div ref="el" class="echart" :style="{ height: `${height}px` }" />
</template>

<style scoped>
.echart {
  width: 100%;
}
</style>
