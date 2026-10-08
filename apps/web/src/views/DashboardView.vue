<script setup lang="ts">
/** 值班总览。
 *
 * 版面上的第一个东西是「当前风险」，而不是一条 KPI 卡片带。
 * 值班员开机后唯一真正要回答的问题是「现在要不要紧」，所以头号读数
 * 必须是风险分，告警读数排在它下面。
 *
 * 两条纪律在这一页被推到极限：
 *   1. 取不到数就显示「—」，不显示 0。0 是一个结论（「一条都没有」），
 *      而此刻的事实是「还没取到」。滚动动画也会跳过非数字。
 *   2. 加载态给骨架屏，不给结论。「尚无告警记录」是在断言「没有数据」，
 *      此刻只是还不知道。
 */
import { computed } from 'vue'

import { alertTrendOption } from '@/charts/options'
import CountUp from '@/components/CountUp.vue'
import EChart from '@/components/EChart.vue'
import { useConsoleStore } from '@/stores/console'

const store = useConsoleStore()

const ov = computed(() => store.overview)

/** 数据还没到，此刻不能报「0」——那是在把「不知道」说成「没有」。 */
const ready = computed(() => ov.value !== null)
const loading = computed(() => store.loading || !ready.value)

const LEVEL_CN: Record<string, string> = {
  normal: '正常',
  watch: '留意',
  warn: '警戒',
  alert: '告警',
}

const level = computed(() => ov.value?.risk.level ?? 'normal')

function n(v: number | undefined | null): number | '—' {
  return v === undefined || v === null ? '—' : v
}

const daily = computed(() => ov.value?.daily ?? [])
const trendOption = computed(() => alertTrendOption(daily.value))

/**
 * 类别构成。
 *
 * 后端回的 class_ratio 键是英文（fire / smoke），不是中文——唯一真相在
 * fire_sight/services/stats_service.py。这里在展示层翻一次：中文只该出现在
 * 屏幕上，不该泄漏到接口的字段名里。
 *
 * 两个类别不画环形图：两段饼读不出比例差，数字本身就是答案。
 */
const ratio = computed(() => ({
  明火: ov.value?.class_ratio.fire ?? 0,
  烟雾: ov.value?.class_ratio.smoke ?? 0,
}))
const classTotal = computed(() => ratio.value.明火 + ratio.value.烟雾)

function pct(n: number): number {
  return classTotal.value ? (n / classTotal.value) * 100 : 0
}

/** 指标表：直接列 metrics.txt 的键值，并给常见键配一句人话解释。 */
const METRIC_CN: Record<string, string> = {
  'mAP@0.5': 'IoU 放宽到 0.5 时的综合精度与召回',
  'mAP@0.5:0.95': 'IoU 从 0.5 到 0.95 逐步收紧，框歪一点就扣分',
  precision: '报出来的框里有多少是真的',
  recall: '真实存在的目标里找回了多少',
  loss: '训练损失，越低越好',
}

/** metrics.txt 存的是原始浮点串（0.7733534279277221），展示必须收窄到可读位数。 */
function fmtMetric(v: string | undefined): string {
  if (v === undefined || v === '') return '—'
  const parsed = Number(v)
  return Number.isFinite(parsed) ? parsed.toFixed(4) : v
}

const metricRows = computed(() =>
  Object.entries(ov.value?.model.metrics ?? {}).map(([key, value]) => ({
    key,
    value: fmtMetric(value),
    note: METRIC_CN[key] ?? '',
  })),
)
</script>

<template>
  <div class="page">
    <!-- 头号读数：风险。当前最要紧的答案，不是一张 KPI 卡片。 -->
    <section class="risk" :data-level="level" :aria-busy="loading">
      <div class="risk__head">
        <span class="risk__label">当前风险</span>
        <span class="risk__level">{{ LEVEL_CN[level] }}</span>
      </div>

      <div class="risk__body">
        <div class="risk__score">
          <CountUp :value="ov ? ov.risk.score : '—'" :digits="2" />
        </div>

        <!-- 风险尺：0 到 1 的刻度条，游标落在读数上。
             这是「这个数是怎么算的」的图形答案。 -->
        <div class="gauge" role="img" aria-label="风险刻度">
          <div class="gauge__track" />
          <div class="gauge__cursor" :style="{ left: `${(ov?.risk.score ?? 0) * 100}%` }" />
          <span class="gauge__cap gauge__cap--l">低</span>
          <span class="gauge__cap gauge__cap--r">高</span>
        </div>
      </div>

      <p class="risk__basis">{{ ov?.risk.basis ?? '正在读取事件库…' }}</p>
    </section>

    <!-- 告警读数：四条并排，每条自己成卡 -->
    <section class="readouts" aria-label="告警读数">
      <div class="readout">
        <span class="readout__label">近 30 日告警</span>
        <span class="readout__value">
          <CountUp :value="n(ov?.alerts.total)" />
          <span class="readout__unit">起</span>
        </span>
        <span class="readout__basis">按事件库中已落盘的告警统计</span>
      </div>
      <div class="readout">
        <span class="readout__label">待复核</span>
        <span class="readout__value">
          <CountUp :value="n(ov?.alerts.pending)" />
          <span class="readout__unit">起</span>
        </span>
        <span class="readout__basis">还没有人工判定的告警</span>
      </div>
      <div class="readout">
        <span class="readout__label">误报率</span>
        <span class="readout__value">
          <!-- digits 必须给：默认是 0 位，会把 4.4% 四舍五入成 4%，
               那是在报错数，比不显示更难发现。 -->
          <CountUp
            :value="ov ? ov.alerts.false_positive_rate * 100 : '—'"
            :digits="1"
          />
          <span class="readout__unit">%</span>
        </span>
        <span class="readout__basis">已判定为误报的告警占全部已判定的比例</span>
      </div>
      <div class="readout">
        <span class="readout__label">归档模型</span>
        <span class="readout__value readout__value--id">
          {{ ov?.model.run_id ?? (ready ? '未归档' : '—') }}
        </span>
        <span class="readout__basis">
          {{ store.status?.trained_model_count ?? '—' }} 个已归档
        </span>
      </div>
    </section>

    <section class="charts">
      <div class="panel">
        <div class="panel__head">
          <h2 class="panel__title">近 30 日告警次数（按类别）</h2>
          <span class="panel__hint">单位：起</span>
        </div>
        <div class="panel__body">
          <EChart v-if="ready" :option="trendOption" :height="260" />
          <el-skeleton v-else :rows="6" animated />
        </div>
      </div>

      <div class="panel">
        <div class="panel__head">
          <h2 class="panel__title">告警类别构成</h2>
        </div>
        <div class="panel__body">
          <el-skeleton v-if="loading" :rows="3" animated />

          <template v-else>
            <div v-if="classTotal > 0" class="ratio">
              <div class="ratio__bar">
                <span
                  class="ratio__seg ratio__seg--smoke"
                  :style="{ width: `${pct(ratio.烟雾)}%` }"
                />
                <span
                  class="ratio__seg ratio__seg--fire"
                  :style="{ width: `${pct(ratio.明火)}%` }"
                />
              </div>
              <dl class="ratio__legend">
                <div class="ratio__item">
                  <dt><i class="dot dot--smoke" />烟雾</dt>
                  <dd>
                    <span class="num">{{ ratio.烟雾 }}</span>
                    <span class="ratio__pct num">{{ pct(ratio.烟雾).toFixed(0) }}%</span>
                  </dd>
                </div>
                <div class="ratio__item">
                  <dt><i class="dot dot--fire" />明火</dt>
                  <dd>
                    <span class="num">{{ ratio.明火 }}</span>
                    <span class="ratio__pct num">{{ pct(ratio.明火).toFixed(0) }}%</span>
                  </dd>
                </div>
              </dl>
            </div>

            <p v-else class="empty">尚无告警，无法统计类别构成。</p>
          </template>
        </div>
      </div>
    </section>

    <!-- 图表区下方：当前权重。模型没归档时这一块说「还没归档」，
         不是把指标列成空表。 -->
    <section class="panel">
      <div class="panel__head">
        <h2 class="panel__title">当前权重指标</h2>
        <span class="panel__hint">
          {{ ov?.model.run_id ?? '验证集' }}
        </span>
      </div>
      <div class="panel__body panel__body--tight">
        <el-skeleton v-if="loading" :rows="4" animated />

        <table v-else-if="metricRows.length" class="sheet sheet--flush">
          <thead>
            <tr>
              <th>指标</th>
              <th class="num">取值</th>
              <th>含义</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in metricRows" :key="row.key">
              <td class="metric-name">{{ row.key }}</td>
              <td class="num">{{ row.value }}</td>
              <td class="metric-note">{{ row.note }}</td>
            </tr>
          </tbody>
        </table>

        <p v-else class="empty">
          尚未归档模型，或归档产物里没有 metrics.txt。运行 fs-train 后会在此显示指标。
        </p>
      </div>
    </section>
  </div>
</template>

<style scoped>
.page {
  max-width: 1360px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--s5);
}

/* --- 风险：这一页的第一个东西，也是最大的一个数字 ------------------------ */
.risk {
  background: var(--surface);
  border: 1px solid var(--line);
  /* 左边一道 5px 的档位色：这是「当前处在哪一档」的常驻答案。
     不用背景色铺满，因为橙色铺满一整块会盖过下面所有的数字。 */
  border-left: 5px solid var(--line);
  border-radius: var(--r-lg);
  padding: var(--s5) var(--s7);
  display: flex;
  flex-direction: column;
  gap: var(--s4);
  box-shadow: var(--lift-1);
  transition: border-left-color var(--dur-3) var(--ease-out),
    background-color var(--dur-3) var(--ease-out);
}

.risk[data-level='normal'] {
  border-left-color: var(--ok);
}

.risk[data-level='watch'] {
  border-left-color: var(--signal);
}

.risk[data-level='warn'] {
  border-left-color: var(--alarm);
}

.risk[data-level='alert'] {
  border-left-color: var(--alarm);
  background: var(--alarm-soft);
}

.risk__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.risk__label {
  font-size: var(--t-micro);
  color: var(--ink-mute);
  letter-spacing: var(--track-label);
}

.risk__level {
  padding: 3px var(--s4);
  font-size: var(--t-small);
  font-weight: var(--w-bold);
  color: var(--ink);
  background: var(--surface-sunk);
  border-radius: var(--r-pill);
}

.risk[data-level='warn'] .risk__level,
.risk[data-level='alert'] .risk__level {
  color: #fff;
  background: var(--alarm);
}

.risk__body {
  display: flex;
  align-items: flex-end;
  gap: var(--s7);
}

/* 头号读数：这一页唯一的最大字号。字重拉到 650 而不是靠字号堆——
   只放大字号会让页面像「放大了的模板」。 */
.risk__score {
  font-family: var(--font-num);
  font-size: 5.25rem;
  font-weight: var(--w-bold);
  line-height: 0.86;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
  letter-spacing: var(--track-hero);
}

/* --- 风险尺：给「0.62」一个可视的来历 ------------------------------------ */
.gauge {
  flex: 1;
  position: relative;
  height: 32px;
  margin-bottom: var(--s2);
}

.gauge__track {
  position: absolute;
  top: 10px;
  left: 0;
  right: 0;
  height: 4px;
  background: var(--line);
  border-radius: var(--r-pill);
}

/* 刻度线：每 0.1 一根，让尺子是可读的而不是一根装饰横线 */
.gauge__track::after {
  content: '';
  position: absolute;
  inset: -3px 0;
  background-image: repeating-linear-gradient(
    to right,
    var(--line) 0 1px,
    transparent 1px 10%
  );
}

.gauge__cursor {
  position: absolute;
  top: 2px;
  width: 4px;
  height: 18px;
  background: var(--ink);
  border-radius: var(--r-pill);
  transition: left var(--dur-4) var(--ease-out);
}

.gauge__cap {
  position: absolute;
  bottom: -2px;
  font-size: var(--t-micro);
  color: var(--ink-faint);
  letter-spacing: var(--track-label);
}

.gauge__cap--l {
  left: 0;
}

.gauge__cap--r {
  right: 0;
}

.risk__basis {
  font-size: var(--t-small);
  color: var(--ink-mute);
  line-height: 1.6;
}

/* run_id 是一串字符，不是读数：套用读数的等宽大字号会把它读成另一个数字 */
.readout__value--id {
  font-size: var(--t-title);
  letter-spacing: 0;
  word-break: break-all;
}

/* --- 图 ------------------------------------------------------------------- */
.charts {
  display: grid;
  grid-template-columns: minmax(0, 2.4fr) minmax(0, 1fr);
  gap: var(--s5);
  align-items: start;
}

.panel__hint {
  font-family: var(--font-num);
}

.panel__body--tight {
  padding: 0;
}

/* --- 类别构成 ----------------------------------------------------------- */
.ratio__bar {
  display: flex;
  /* 段间留 2px 底色间隙，而不是给段描边 */
  gap: 2px;
  height: 10px;
  margin-bottom: var(--s4);
}

.ratio__seg {
  display: block;
  border-radius: var(--r-pill);
}

.ratio__seg--smoke {
  background: var(--cls-smoke);
}

.ratio__seg--fire {
  background: var(--cls-fire);
}

.ratio__legend {
  margin: 0;
}

.ratio__item {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding: var(--s2) 0;
  border-top: 1px solid var(--line-faint);
}

.ratio__item dt {
  display: flex;
  align-items: center;
  font-size: var(--t-small);
  color: var(--ink-body);
}

.ratio__item dd {
  margin: 0;
  display: flex;
  align-items: baseline;
  gap: var(--s3);
}

/* 百分比是次要读数：压暗，避免和计数抢注意力 */
.ratio__pct {
  min-width: 3ch;
  text-align: right;
  color: var(--ink-faint);
}

.metric-name {
  font-family: var(--font-num);
  font-size: var(--t-micro);
  color: var(--ink-body);
  font-weight: var(--w-medium);
}

.metric-note {
  color: var(--ink-faint);
  font-size: var(--t-micro);
}

@media (max-width: 1100px) {
  .charts {
    grid-template-columns: minmax(0, 1fr);
  }

  .risk__body {
    flex-direction: column;
    align-items: flex-start;
    gap: var(--s4);
  }

  .gauge {
    width: 100%;
  }
}

@media (prefers-reduced-motion: reduce) {
  .risk,
  .gauge__cursor {
    transition: none;
  }
}
</style>