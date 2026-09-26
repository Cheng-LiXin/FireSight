<script setup lang="ts">
/** ECharts 容器：负责生命周期与尺寸自适应，图表配置由调用方给出。
 *
 * 容器高度由调用方通过 height 指定，且必须包住 X 轴文字带——
 * 固定高度裁掉轴标签会长出一条嵌套滚动条。
 */
import * as echarts from 'echarts'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = withDefaults(defineProps<{ option: echarts.EChartsOption; height?: number }>(), {
  height: 260,
})

const el = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null
let observer: ResizeObserver | null = null

onMounted(() => {
  if (!el.value) return
  chart = echarts.init(el.value, undefined, { renderer: 'canvas' })
  chart.setOption(props.option)
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(el.value)
})

watch(
  () => props.option,
  (opt) => chart?.setOption(opt, { notMerge: true }),
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
