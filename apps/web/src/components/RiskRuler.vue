<script setup lang="ts">
/** 风险尺 —— 全局记忆点。
 *
 * 横贯整宽的温度刻度尺，游标位置 = 当前风险。刻度是素色线，只有游标承载
 * 严重度颜色，因此界面上任何橙黄都能回答「它代表多少风险」。
 * 动效只在入场时发生一次：游标从 0 滑到当前值。
 */
import { computed, onMounted, ref, watch } from 'vue'

import type { RiskLevel, RiskReading } from '@/api/types'

const props = defineProps<{ risk: RiskReading | null }>()

const CURSOR_COLOR: Record<RiskLevel, string> = {
  normal: 'var(--risk-normal)',
  watch: 'var(--risk-watch)',
  warn: 'var(--risk-warn)',
  alert: 'var(--risk-alert)',
}

const LEVEL_CN: Record<RiskLevel, string> = {
  normal: '正常',
  watch: '留意',
  warn: '警戒',
  alert: '告警',
}

/** 21 个刻度，0.00 ~ 1.00 */
const TICKS = Array.from({ length: 21 }, (_, i) => i / 20)

const displayed = ref(0)

onMounted(() => {
  // 下一帧再赋值，游标才会有一次可见的入场滑动
  requestAnimationFrame(() => {
    displayed.value = props.risk?.score ?? 0
  })
})

watch(
  () => props.risk?.score,
  (v) => {
    displayed.value = v ?? 0
  },
)

const level = computed<RiskLevel>(() => props.risk?.level ?? 'normal')
const cursorColor = computed(() => CURSOR_COLOR[level.value])
const cursorLeft = computed(() => `${Math.min(Math.max(displayed.value, 0), 1) * 100}%`)
const scoreText = computed(() => (props.risk?.score ?? 0).toFixed(2))
</script>

<template>
  <section class="ruler" :style="{ '--cursor-color': cursorColor }">
    <div class="ruler__head">
      <span class="ruler__caption">当前风险</span>
      <span class="ruler__level">{{ LEVEL_CN[level] }}</span>
    </div>

    <div class="ruler__track">
      <span
        v-for="t in TICKS"
        :key="t"
        class="ruler__tick"
        :class="{ 'is-major': Math.round(t * 4) === t * 4 }"
        :style="{ left: `${t * 100}%` }"
      />
      <span class="ruler__cursor" :style="{ left: cursorLeft }" />
    </div>

    <div class="ruler__scale">
      <span>低</span>
      <span>中</span>
      <span>高</span>
    </div>

    <div class="ruler__foot">
      <!-- hero 数字用比例字形：等宽在大字号下会让数字显得松散 -->
      <span class="ruler__score">{{ scoreText }}</span>
      <span class="ruler__basis">{{ risk?.basis ?? '正在读取事件库…' }}</span>
    </div>
  </section>
</template>

<style scoped>
.ruler {
  padding: var(--sp-3) var(--sp-5) var(--sp-4);
  border-bottom: var(--hairline);
}

.ruler__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: var(--sp-3);
}

.ruler__caption {
  font-size: var(--fs-sm);
  color: var(--dross);
}

/* 档位文字用游标同色，把「文字—刻度—颜色」绑成一件事 */
.ruler__level {
  font-size: var(--fs-sm);
  font-weight: 500;
  color: var(--cursor-color);
}

.ruler__track {
  position: relative;
  height: 22px;
  border-bottom: 1px solid var(--cinder);
}

.ruler__tick {
  position: absolute;
  bottom: 0;
  width: 1px;
  height: 5px;
  background: var(--cinder);
}

.ruler__tick.is-major {
  height: 10px;
  background: var(--dross);
}

.ruler__cursor {
  position: absolute;
  top: 2px;
  bottom: 0;
  width: 2px;
  margin-left: -1px;
  background: var(--cursor-color);
  /* 同色柔光晕：让游标从刻度线里浮起来，颜色仍只来自档位本身 */
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--cursor-color) 18%, transparent);
  transition:
    left 700ms cubic-bezier(0.22, 1, 0.36, 1),
    background-color 400ms ease,
    box-shadow 400ms ease;
}

.ruler__cursor::before {
  content: '';
  position: absolute;
  top: -5px;
  left: 50%;
  margin-left: -4px;
  border-left: 4px solid transparent;
  border-right: 4px solid transparent;
  border-top: 5px solid var(--cursor-color);
}

.ruler__scale {
  display: flex;
  justify-content: space-between;
  margin-top: var(--sp-1);
  font-size: var(--fs-xs);
  color: var(--dross);
}

.ruler__foot {
  display: flex;
  align-items: baseline;
  gap: var(--sp-4);
  margin-top: var(--sp-3);
}

.ruler__score {
  font-size: var(--fs-2xl);
  font-weight: 500;
  line-height: 1;
  color: var(--cursor-color);
}

.ruler__basis {
  font-size: var(--fs-sm);
  color: var(--dross);
}
</style>
