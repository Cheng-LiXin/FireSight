<script setup lang="ts">
/** 月度火情统计大屏。
 *
 * 给投影/汇报看的整页视图：脱掉顶栏与风险尺，读数放大、表单控件最小化。
 * 数据全部来自事件库——没有事件时给「方向性空态」，不编数字。
 */
import { computed, onMounted, ref, watch } from 'vue'

import { api } from '@/api/client'
import type { MonthlyStats } from '@/api/types'
import { hourlyOption, monthlyTrendOption } from '@/charts/options'
import EChart from '@/components/EChart.vue'

const stats = ref<MonthlyStats | null>(null)
const error = ref('')
const loading = ref(false)

function thisMonth(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

/** 所选月（YYYY-MM）。大屏默认停在当月，可回看任意历史月。 */
const month = ref(thisMonth())

const detail = computed(() => stats.value?.detail ?? null)
const hasData = computed(() => (detail.value?.total ?? 0) > 0)

/** 标题用「2026 年 9 月」，图表轴用「9 月」。 */
const monthTitle = computed(() => {
  const [y, m] = month.value.split('-')
  return `${y} 年 ${Number(m)} 月`
})

const canNext = computed(() => month.value < thisMonth())

/** 环比：上月为 0 时后端给 null——「从 0 涨到 N」不是涨幅，是无从比较。 */
const momText = computed(() => {
  const v = detail.value?.mom_pct
  if (v === null || v === undefined) return { text: '—', tone: 'muted', dir: '' }
  const dir = v > 0 ? '↑' : v < 0 ? '↓' : ''
  return {
    text: `${dir} ${Math.abs(v).toFixed(1)}%`,
    tone: v > 0 ? 'fire' : v < 0 ? 'cool' : 'muted',
    dir,
  }
})

const fpRateText = computed(() => {
  const v = detail.value?.false_positive_rate ?? 0
  return (v * 100).toFixed(1)
})

async function load() {
  loading.value = true
  try {
    error.value = ''
    stats.value = await api.monthlyStats(12, month.value)
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
    stats.value = null
  } finally {
    loading.value = false
  }
}

function shiftMonth(delta: number) {
  const [y, m] = month.value.split('-').map(Number)
  const d = new Date(y, m - 1 + delta, 1)
  month.value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

onMounted(load)
watch(month, load)
</script>

<template>
  <div class="bigscreen">
    <header class="bs-head">
      <div class="bs-brand">
        <span class="bs-brand__mark">Fire</span><span class="bs-brand__rest">Sight</span>
      </div>
      <h1 class="bs-title">月度火情统计 · {{ monthTitle }}</h1>

      <div class="bs-nav">
        <button type="button" class="bs-btn" @click="shiftMonth(-1)">上一月</button>
        <button type="button" class="bs-btn" :disabled="canNext === false" @click="shiftMonth(1)">
          下一月
        </button>
        <RouterLink to="/" class="bs-btn bs-btn--link">返回控制台</RouterLink>
      </div>
    </header>

    <div v-if="error" class="fs-note-error" role="alert">
      {{ error }}
    </div>

    <template v-if="detail">
      <!-- KPI 行：数字本身就是图，大字号独立读数用比例字形 -->
      <section class="kpis" aria-label="本月读数">
        <div class="kpi">
          <div class="kpi__label">告警总数</div>
          <div class="kpi__value">{{ detail.total }}</div>
          <div class="kpi__sub" :class="`is-${momText.tone}`">较上月 {{ momText.text }}</div>
        </div>
        <div class="kpi">
          <div class="kpi__label">明火</div>
          <div class="kpi__value kpi__value--fire">{{ detail.fire }}</div>
          <div class="kpi__sub">按类别各计一次</div>
        </div>
        <div class="kpi">
          <div class="kpi__label">烟雾</div>
          <div class="kpi__value">{{ detail.smoke }}</div>
          <div class="kpi__sub">按类别各计一次</div>
        </div>
        <div class="kpi">
          <div class="kpi__label">误报率</div>
          <div class="kpi__value">{{ fpRateText }}<span class="kpi__unit">%</span></div>
          <div class="kpi__sub">{{ detail.false_positives }} 条已标误报</div>
        </div>
        <div class="kpi">
          <div class="kpi__label">待复核</div>
          <div class="kpi__value" :class="{ 'kpi__value--fire': detail.pending > 0 }">
            {{ detail.pending }}
          </div>
          <div class="kpi__sub">平均置信度 {{ detail.avg_conf.toFixed(2) }}</div>
        </div>
      </section>

      <template v-if="hasData">
        <section class="fs-viewfinder">
          <h2 class="fs-viewfinder__title">近 12 个月告警次数（按类别）</h2>
          <EChart v-if="stats" :option="monthlyTrendOption(stats.months)" :height="320" />
        </section>

        <section class="fs-viewfinder">
          <h2 class="fs-viewfinder__title">本月 24 小时时段分布</h2>
          <EChart :option="hourlyOption(detail.hourly)" :height="260" />
        </section>
      </template>

      <section v-else class="fs-empty">
        这个月没有告警记录。先跑一次推理产生事件，大屏才有内容可看；也可以切换到有数据的月份回看。
      </section>
    </template>

    <section v-else-if="!error" class="fs-empty">正在读取事件库…</section>
  </div>
</template>

<style scoped>
.bigscreen {
  min-height: 100%;
  padding: var(--sp-5) var(--sp-6) var(--sp-7);
  background: var(--soot);
  display: flex;
  flex-direction: column;
  gap: var(--sp-5);
}

.bs-head {
  display: flex;
  align-items: center;
  gap: var(--sp-5);
  padding-bottom: var(--sp-4);
  border-bottom: var(--hairline);
}

.bs-brand {
  font-size: var(--fs-lg);
  font-weight: 700;
  letter-spacing: 0.02em;
  color: var(--bone);
  white-space: nowrap;
}

.bs-brand__mark {
  color: var(--accent);
}

.bs-title {
  margin: 0;
  font-size: var(--fs-xl);
  font-weight: 500;
  color: var(--bone);
}

.bs-nav {
  display: flex;
  gap: var(--sp-2);
  margin-left: auto;
}

.bs-btn {
  height: 36px;
  padding: 0 var(--sp-4);
  font: inherit;
  font-size: var(--fs-sm);
  color: var(--bone);
  background: var(--ash);
  border: 1px solid var(--cinder);
  border-radius: var(--radius-sm);
  text-decoration: none;
  cursor: pointer;
  transition: border-color var(--dur-1) var(--ease), background-color var(--dur-1) var(--ease),
    transform var(--dur-1) var(--ease);
}

.bs-btn:hover:not(:disabled) {
  border-color: var(--dross);
  background: var(--highlight);
}

.bs-btn:active:not(:disabled) {
  transform: translateY(1px);
}

.bs-btn:disabled {
  color: var(--ink-3);
  cursor: default;
}

/* --- KPI 行 ------------------------------------------------------------ */

.kpis {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: var(--sp-4);
}

.kpi {
  padding: var(--sp-4) var(--sp-5);
  background: var(--ash);
  border: var(--hairline);
  border-radius: var(--radius);
  box-shadow: var(--shadow-1);
  transition: box-shadow var(--dur-2) var(--ease), transform var(--dur-2) var(--ease);
}

.kpi:hover {
  box-shadow: var(--shadow-2);
  transform: translateY(-2px);
}

.kpi__label {
  font-size: var(--fs-sm);
  color: var(--dross);
}

/* hero 数字用比例字形：等宽在大字号下显得松散 */
.kpi__value {
  margin-top: var(--sp-2);
  font-size: 2.5rem;
  font-weight: 500;
  line-height: 1.1;
  color: var(--bone);
}

/* 「这个读数表示有火/有告警」——正是 --alarm 的合法用途 */
.kpi__value--fire {
  color: var(--alarm);
}

.kpi__unit {
  margin-left: 2px;
  font-size: var(--fs-lg);
  color: var(--dross);
}

.kpi__sub {
  margin-top: var(--sp-2);
  font-size: var(--fs-xs);
  color: var(--dross);
}

.kpi__sub.is-fire {
  color: var(--alarm);
}

.kpi__sub.is-cool {
  color: var(--ink-1);
}

/* --- 窄屏：KPI 换行，标题让位 ------------------------------------------ */

@media (max-width: 1100px) {
  .kpis {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .bs-head {
    flex-wrap: wrap;
  }

  .bs-title {
    font-size: var(--fs-lg);
    order: 3;
    width: 100%;
  }
}
</style>
