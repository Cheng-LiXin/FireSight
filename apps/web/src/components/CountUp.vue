<script setup lang="ts">
/** 数字滚动：数值变化时从旧值滚到新值。
 *
 * 为什么不直接用 CSS transition 做一个 0→N 的补间——CSS 只能插值两端，
 * 而读数是「从上一次读数滚到这一次」，中间可能隔着好几天的跨度；而且
 * requestAnimationFrame 能被中断，被新值打断时从当前帧接着走，不会跳。
 *
 * 三条让它不至于变成炫技的规矩：
 *
 * 1. **差值小于阈值不滚**（--count-min-delta，默认 2）。3 → 4 这种变化滚一趟
 *    只有一帧，肉眼看成一串噪声，而不是「读数动了」。
 * 2. **时长与差值挂钩，但封顶**。差得多滚得久一点（人来得及跟上），
 *    但不超过 --dur-4；否则从 0 滚到 9999 要等好几秒，比不滚更糟。
 * 3. **尊重 prefers-reduced-motion**：直接跳到终值，不做中间过程。
 *    这一条不是可选项——前庭功能敏感的用户看到滚动数字会真的难受。
 *
 * 滚动过程中不触发任何 layout 之外的工作，也不在滚动期间改 width，
 * 所以 8 个 KPI 同时滚也不会引起重排抖动。
 */
import { onBeforeUnmount, ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    /** 目标值。非数字（null / undefined / 非数值字符串）原样显示，不滚。 */
    value: number | string | null | undefined
    /** 小数位。给了就按小数显示，不给则按值本身的量级取 0 或 1。 */
    digits?: number
    /** 大数缩写：9999 → 9,999；超过万走「万」。用于避免滚动过长的数字串。 */
    separator?: boolean
  }>(),
  { digits: 0, separator: true },
)

const emit = defineEmits<{ settled: [] }>()

/** 当前显示的文本。空串表示还没开始（SSR/首帧）。 */
const shown = ref('')
/** 用来给 key 做过渡的最终值，防止两个不同目标值共用同一帧动画。 */
const phase = ref(0)

let raf = 0
let fromValue = 0

/** 把数字格式化成带千分位的字符串。digits 固定小数位。 */
function format(n: number): string {
  const fixed = n.toFixed(props.digits)
  if (!props.separator) return fixed
  const [int, frac] = fixed.split('.')
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return frac ? `${grouped}.${frac}` : grouped
}

/** 用户在系统里关了动效：不做中间过程，直接落终值。 */
function reduced(): boolean {
  return typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
}

/** 从 :root 读动效令牌，让 JS 里的时长与 CSS 里的动画共用同一份定义，
 *  而不是各写一个数字、改了一处忘了另一处。 */
function token(name: string, fallback: number): number {
  if (typeof window === 'undefined') return fallback
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  if (!raw) return fallback
  if (raw.endsWith('ms')) return Number.parseFloat(raw)
  if (raw.endsWith('s')) return Number.parseFloat(raw) * 1000
  const n = Number.parseFloat(raw)
  return Number.isFinite(n) ? n : fallback
}

/** 取某个目标值应当显示的文本；非数字原样返回。 */
function targetText(v: typeof props.value): string | null {
  if (v === null || v === undefined || v === '') return null
  const n = typeof v === 'number' ? v : Number(v)
  if (Number.isNaN(n)) return String(v)
  return format(n)
}

function stop(): void {
  if (raf) {
    cancelAnimationFrame(raf)
    raf = 0
  }
}

/** 从当前显示值滑到目标值。 */
function rollTo(target: number, durationMs: number): void {
  stop()
  const start = performance.now()
  // 起点用「当前显示值」而不是上一次的原始值：用户连点切换月份时，
  // 动画会从屏幕上看得到的位置接着走，而不是从两个月前跳回来。
  const startValue = shown.value === '' ? target : Number(shown.value.replace(/,/g, ''))
  fromValue = Number.isFinite(startValue) ? startValue : fromValue

  const tick = (now: number) => {
    const t = Math.min(1, (now - start) / durationMs)
    // ease-out：起手快、收尾稳。不用线性，线性滚动到一半看着像卡住了。
    const eased = 1 - Math.pow(1 - t, 3)
    shown.value = format(fromValue + (target - fromValue) * eased)
    if (t < 1) {
      raf = requestAnimationFrame(tick)
    } else {
      raf = 0
      shown.value = format(target)
      emit('settled')
    }
  }
  raf = requestAnimationFrame(tick)
}

watch(
  () => props.value,
  (v, old) => {
    const text = targetText(v)
    if (text === null) {
      stop()
      shown.value = ''
      return
    }

    const target = typeof v === 'number' ? v : Number(v)
    const prev = old === undefined || old === null || old === '' ? NaN : Number(old)

    // 首次渲染直接显示，不滚：页面刚打开时数字从 0 滚起来像在做宣传片。
    if (shown.value === '' || Number.isNaN(prev)) {
      shown.value = text
      return
    }

    if (reduced()) {
      shown.value = text
      emit('settled')
      return
    }

    // 差值太小不滚（--count-min-delta）
    const minDelta = token('--count-min-delta', 2)
    if (Math.abs(target - prev) < minDelta) {
      shown.value = text
      emit('settled')
      return
    }

    phase.value += 1
    // 时长随跨度增加但封顶：跨度越大越需要时间看清数字在动。
    // 封顶取 --dur-4（450ms），另外留一点余量给跨度大的情况。
    const span = Math.min(1, Math.abs(target - prev) / 200)
    const base = token('--dur-4', 450)
    rollTo(target, base + span * 200)
  },
  { immediate: true },
)

onBeforeUnmount(stop)
</script>

<template>
  <span class="count" :key="phase" :aria-label="targetText(value) ?? undefined">{{ shown }}</span>
</template>

<style scoped>
.count {
  display: inline-block;
  font-variant-numeric: tabular-nums;
  /* 滚动时给一点点回弹，让"读数变了"被看见又不至于夸张。
     位移只有 2px——数字是在垂直方向动的，横向一点抖动就够了。 */
  animation: count-pop var(--dur-3) var(--ease-back);
}

@keyframes count-pop {
  from {
    transform: translateY(2px);
  }
  to {
    transform: translateY(0);
  }
}

@media (prefers-reduced-motion: reduce) {
  .count {
    animation: none;
  }
}
</style>