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
import CountUp from '@/components/CountUp.vue'

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

const fpRateNum = computed(() => (detail.value?.false_positive_rate ?? 0) * 100)

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
        <button type="button" class="btn btn--ghost" @click="shiftMonth(-1)">上一月</button>
        <button
          type="button"
          class="btn btn--ghost"
          :disabled="canNext === false"
          @click="shiftMonth(1)"
        >
          下一月
        </button>
        <RouterLink to="/" class="btn btn--ghost btn-link">返回控制台</RouterLink>
      </div>
    </header>

    <div v-if="error" class="fs-note-error" role="alert">
      {{ error }}
    </div>

    <template v-if="detail">
      <!-- KPI 行：数字本身就是图，大字号独立读数用比例字形 -->
      <section class="kpis" aria-label="本月读数">
        <div class="kpi kpi--lead">
          <div class="kpi__label">告警总数</div>
          <div class="kpi__value"><CountUp :value="detail.total" /></div>
          <div class="kpi__sub" :class="`is-${momText.tone}`">较上月 {{ momText.text }}</div>
        </div>
        <div class="kpi">
          <div class="kpi__label">明火</div>
          <div class="kpi__value kpi__value--fire"><CountUp :value="detail.fire" /></div>
          <div class="kpi__sub">按类别各计一次</div>
        </div>
        <div class="kpi">
          <div class="kpi__label">烟雾</div>
          <div class="kpi__value"><CountUp :value="detail.smoke" /></div>
          <div class="kpi__sub">按类别各计一次</div>
        </div>
        <div class="kpi">
          <div class="kpi__label">误报率</div>
          <div class="kpi__value"><CountUp :value="fpRateNum" :digits="1" /><span class="kpi__unit">%</span></div>
          <div class="kpi__sub">{{ detail.false_positives }} 条已标误报</div>
        </div>
        <div class="kpi">
          <div class="kpi__label">待复核</div>
          <div class="kpi__value" :class="{ 'kpi__value--fire': detail.pending > 0 }">
            <CountUp :value="detail.pending" />
          </div>
          <div class="kpi__sub">平均置信度 {{ detail.avg_conf.toFixed(2) }}</div>
        </div>
      </section>

      <template v-if="hasData">
        <section class="panel">
          <div class="panel__head">
            <h2 class="panel__title">近 12 个月告警次数（按类别）</h2>
            <span class="panel__hint">按月堆叠 · 单位：起</span>
          </div>
          <div class="panel__body panel__body--chart">
            <EChart v-if="stats" :option="monthlyTrendOption(stats.months)" :height="320" />
          </div>
        </section>

        <section class="panel">
          <div class="panel__head">
            <h2 class="panel__title">本月 24 小时时段分布</h2>
            <span class="panel__hint">按告警发生时刻</span>
          </div>
          <div class="panel__body panel__body--chart">
            <EChart :option="hourlyOption(detail.hourly)" :height="260" />
          </div>
        </section>
      </template>

      <section v-else class="empty empty--inset">
        这个月没有告警记录。先跑一次推理产生事件，大屏才有内容可看；也可以切换到有数据的月份回看。
      </section>
    </template>

    <section v-else-if="!error" class="empty empty--inset">正在读取事件库…</section>
  </div>
</template>

<style scoped>
/* 大屏脱掉顶栏，整页就是这一个视图：所以底色直接用页面底色，
   靠左右不对称的内边距和分区留白撑住版面，不再另铺一层灰。 */
.bigscreen {
  min-height: 100%;
  padding: var(--s6) var(--s8) var(--s8);
  display: flex;
  flex-direction: column;
  gap: var(--s5);
}

.bs-head {
  display: flex;
  align-items: center;
  gap: var(--s5);
  padding-bottom: var(--s4);
  border-bottom: 1px solid var(--line);
}

.bs-brand {
  font-size: var(--t-read);
  font-weight: var(--w-black);
  letter-spacing: var(--track-tight);
  color: var(--ink);
  white-space: nowrap;
}

.bs-brand__mark {
  color: var(--alarm);
}

.bs-title {
  margin: 0;
  font-size: var(--t-title);
  font-weight: var(--w-bold);
  color: var(--ink);
}

.bs-nav {
  display: flex;
  gap: var(--s2);
  margin-left: auto;
}

/* 大屏沿用标准按钮（.btn--ghost），只留一处本页特有的差异：
   站两米外看要更大一点。此前这里自己抄了一整套按钮配方（底、边、圆角、
   hover、按下、禁用），于是这一页成了全站唯一绕过标准按钮的地方——
   在控制台学会的按钮高度与禁用态，进了大屏全都不成立。 */
.bs-nav .btn {
  height: 38px;
  padding: 0 var(--s5);
}

/* KPI 行：大屏是站在两米外看的，五个读数要能一眼分出主次，
   所以不做等权卡片——五个一样大的盒子会把「告警总数」和「待复核」
   拉平成同一件事。改成一组共用顶线的读数条，数字靠字号和基线分
   节奏，界线由这一条线给出。也不做悬停反馈：墙上的屏没人去 hover。 */
.kpis {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: var(--s7);
}

.kpi {
  padding-top: var(--s2);
  border-top: 1px solid var(--line);
}

/* 领读的那个：全月唯一的总量，其余四项是它的分解。
   让它比邻居大一档，视线第一落点才在总量上，而不是五选一。 */
.kpi--lead .kpi__value {
  font-size: 3.75rem;
  color: var(--ink);
}

.kpi__label {
  font-size: var(--t-small);
  font-weight: var(--w-medium);
  color: var(--ink-mute);
}

/* hero 数字用比例字形：等宽在大字号下显得松散 */
.kpi__value {
  margin-top: var(--s2);
  font-size: 2.75rem;
  font-weight: var(--w-medium);
  line-height: 1.1;
  color: var(--ink);
}

/* 「这个读数表示有火/有告警」——正是 --alarm 的合法用途 */
.kpi__value--fire {
  color: var(--alarm);
}

.kpi__unit {
  margin-left: 2px;
  font-size: 1.5rem;
  color: var(--ink-mute);
}

.kpi__sub {
  margin-top: var(--s2);
  font-size: var(--t-small);
  color: var(--ink-mute);
}

.kpi__sub.is-fire {
  color: var(--alarm);
}

.kpi__sub.is-cool {
  color: var(--signal-deep);
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
    font-size: var(--t-read);
    order: 3;
    width: 100%;
  }
}
</style>
