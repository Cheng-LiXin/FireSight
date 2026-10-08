<script setup lang="ts">
/** 仪表盘：读数行 + 告警趋势 + 置信度趋势 + 类别构成 + 当前模型。
 *
 * 「次数」与「置信度」量纲不同，拆成两张共用 X 轴的图，不做双 Y 轴。
 */
import { computed } from 'vue'

import { alertTrendOption, confidenceOption } from '@/charts/options'
import EChart from '@/components/EChart.vue'
import FrameMarks from '@/components/FrameMarks.vue'
import StatTile from '@/components/StatTile.vue'
import { useConsoleStore } from '@/stores/console'

const store = useConsoleStore()

/** 未取到数据时读数显示什么。
 *
 * 不用 0：0 是一个结论（「一条都没有」），而此刻的事实是「还没取到」。
 * 用破折号——它读起来就是「此处无数」，不撒谎，滚动动画也会跳过它
 * （CountUp 对非数字原样显示）。 */
function tileValue(n: number | undefined | null): number | string {
  return n === undefined || n === null ? '—' : n
}

/** 误报率读数：取不到就是「—」，而不是 0.0%。 */
function tileRate(rate: number | undefined | null): number | string {
  return rate === undefined || rate === null ? '—' : (rate * 100).toFixed(1)
}

const ov = computed(() => store.overview)
const daily = computed(() => ov.value?.daily ?? [])

/** 数据还没到，此刻不能报「0」——那是在把「不知道」说成「没有」。
 *  骨架屏只盖住了趋势图；读数行和两个下方面板也得等，否则页面一进来
 *  满屏都是并不存在的零和「尚无」结论，比不给数据更糟。 */
const ready = computed(() => ov.value !== null)
const loading = computed(() => store.loading || !ready.value)

/** 空态看总量，不看序列长度——序列永远是连续的 30 天（缺日补 0）。 */
const hasData = computed(() => (ov.value?.alerts.total ?? 0) > 0)

const trendOption = computed(() => alertTrendOption(daily.value))
const confOption = computed(() => confidenceOption(daily.value))

const ratio = computed(() => ov.value?.class_ratio ?? { smoke: 0, fire: 0 })
const classTotal = computed(() => (ratio.value.smoke ?? 0) + (ratio.value.fire ?? 0))

function pct(n: number): number {
  return classTotal.value ? (n / classTotal.value) * 100 : 0
}

const metrics = computed(() => ov.value?.model.metrics ?? {})

/** metrics.txt 存的是原始浮点串（0.7733534279277221），展示必须收窄到可读位数。 */
function fmtMetric(v: string | undefined): string {
  if (v === undefined || v === '') return '—'
  const n = Number(v)
  return Number.isFinite(n) ? n.toFixed(3) : v
}

const metricRows = computed(() => [
  { label: 'mAP@50', value: fmtMetric(metrics.value.mAP50) },
  { label: '精确率 P', value: fmtMetric(metrics.value.precision) },
  { label: '召回率 R', value: fmtMetric(metrics.value.recall) },
])
</script>

<template>
  <div class="dash">
    <!-- 读数行：数字本身就是图，不额外画单柱图或两段饼 -->
    <section class="tiles" aria-label="告警读数" :aria-busy="loading">
      <StatTile label="告警总数" :value="tileValue(ov?.alerts.total)" />
      <StatTile
        label="待复核"
        :value="tileValue(ov?.alerts.pending)"
        :tone="(ov?.alerts.pending ?? 0) > 0 ? 'fire' : 'default'"
      />
      <StatTile
        label="误报率"
        :value="tileRate(ov?.alerts.false_positive_rate)"
        unit="%"
      />
      <StatTile
        label="归档模型"
        :value="tileValue(store.status?.trained_model_count)"
        unit="个"
        tone="muted"
      />
    </section>

    <template v-if="hasData">
      <section class="fs-viewfinder">
        <FrameMarks />
        <h2 class="fs-viewfinder__title">近 30 日告警次数（按类别）</h2>
        <EChart :option="trendOption" :height="280" />
      </section>

      <section class="fs-viewfinder">
        <FrameMarks />
        <h2 class="fs-viewfinder__title">近 30 日平均置信度</h2>
        <EChart :option="confOption" :height="220" />
      </section>
    </template>

    <!-- 加载中不给「尚无告警记录」：这句话是在断言「没有数据」，
         而此刻只是还不知道。此刻该说的是「正在取」。 -->
    <section v-else-if="loading" class="fs-viewfinder" aria-busy="true">
      <FrameMarks />
      <h2 class="fs-viewfinder__title">近 30 日告警次数（按类别）</h2>
      <el-skeleton :rows="6" animated />
    </section>

    <section v-else class="fs-empty">
      尚无告警记录。运行一次识别检测后，趋势会出现在这里。
      <RouterLink to="/detect" class="fs-empty__action">去做一次识别检测</RouterLink>
    </section>

    <div class="dash__cols">
      <!-- 两个类别不画环形图：两段饼图读不出比例差，数字本身就是答案 -->
      <section class="fs-viewfinder">
        <FrameMarks />
        <h2 class="fs-viewfinder__title">告警类别构成</h2>

        <el-skeleton v-if="loading" :rows="3" animated />

        <div v-else-if="classTotal > 0" class="ratio">
          <div class="ratio__bar">
            <span
              class="ratio__seg ratio__seg--smoke"
              :style="{ width: `${pct(ratio.smoke)}%` }"
            />
            <span class="ratio__seg ratio__seg--fire" :style="{ width: `${pct(ratio.fire)}%` }" />
          </div>

          <dl class="ratio__legend">
            <div class="ratio__item">
              <dt><i class="ratio__dot ratio__dot--smoke" />烟雾</dt>
              <dd>
                <span class="fs-num">{{ ratio.smoke }}</span>
                <span class="ratio__pct fs-num">{{ pct(ratio.smoke).toFixed(0) }}%</span>
              </dd>
            </div>
            <div class="ratio__item">
              <dt><i class="ratio__dot ratio__dot--fire" />明火</dt>
              <dd>
                <span class="fs-num">{{ ratio.fire }}</span>
                <span class="ratio__pct fs-num">{{ pct(ratio.fire).toFixed(0) }}%</span>
              </dd>
            </div>
          </dl>
        </div>

        <p v-else class="dash__none">尚无告警，无法统计类别构成。</p>
      </section>

      <section class="fs-viewfinder">
        <FrameMarks />
        <h2 class="fs-viewfinder__title">当前模型</h2>

        <el-skeleton v-if="loading" :rows="3" animated />

        <template v-else-if="ov?.model.exists">
          <p class="dash__run fs-num">{{ ov.model.run_id }}</p>
          <div class="metrics">
            <div v-for="row in metricRows" :key="row.label" class="metrics__item">
              <div class="metrics__label">{{ row.label }}</div>
              <div class="metrics__value">{{ row.value }}</div>
            </div>
          </div>
          <p class="dash__hint">
            明火为弱类，召回偏低是可预期的。提高召回可在训练中心调整增强后重训。
          </p>
        </template>

        <p v-else class="dash__none">尚未归档模型。运行 fs-train 后会在此显示指标。</p>
      </section>
    </div>
  </div>
</template>

<style scoped>
.dash {
  display: flex;
  flex-direction: column;
  gap: var(--sp-5);
}

/* 四条读数共用一条顶线，像仪表盘上的一排刻度：
   盒子之间的界线由这一条线给出，不必各画一个边框。 */
.tiles {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--sp-5);
}

/* .fs-readout 原本是纵向读数条，靠「相邻的再往下挪一截」分开；
   这里横排，间距交给 grid 的 gap——否则每条都比它的邻居低 8px，
   四条顶线就错开了，「一排刻度」也没了。 */
.tiles > .tile {
  margin-top: 0;
}

.dash__cols {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--sp-5);
  /* 两个面板各自按内容定高，不互相拉平——否则短的那侧会留一大片空 */
  align-items: start;
}

.dash__none {
  margin: 0;
  color: var(--dross);
  font-size: var(--fs-sm);
}

.dash__run {
  margin: 0 0 var(--sp-4);
  font-size: var(--fs-sm);
  color: var(--dross);
  word-break: break-all;
}

.dash__hint {
  margin: var(--sp-4) 0 0;
  padding-left: var(--sp-3);
  border-left: 2px solid var(--cinder);
  font-size: var(--fs-sm);
  color: var(--dross);
}

/* --- 类别构成 --------------------------------------------------------- */

.ratio__bar {
  display: flex;
  /* 段间留 2px 底色间隙，而不是给段描边 */
  gap: 2px;
  height: 10px;
  margin-bottom: var(--sp-4);
}

.ratio__seg {
  display: block;
  border-radius: var(--radius-pill);
}

.ratio__seg--smoke {
  background: var(--cat-smoke);
}

.ratio__seg--fire {
  background: var(--cat-fire);
}

.ratio__legend {
  margin: 0;
}

.ratio__item {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding: var(--sp-2) 0;
  border-top: var(--hairline);
}

.ratio__item dt {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  font-size: var(--fs-sm);
  color: var(--bone);
}

.ratio__item dd {
  margin: 0;
  display: flex;
  align-items: baseline;
  gap: var(--sp-3);
}

.ratio__dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.ratio__dot--smoke {
  background: var(--cat-smoke);
}

.ratio__dot--fire {
  background: var(--cat-fire);
}

/* 百分比是次要读数：压暗，避免和计数抢注意力 */
.ratio__pct {
  min-width: 3ch;
  text-align: right;
  font-size: var(--fs-sm);
  color: var(--dross);
}

/* 三个指标并排。面板很宽，纵向堆叠会让标签和数值之间拉开一大段空白。 */
.metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--sp-4);
}

.metrics__item {
  padding-top: var(--sp-2);
  border-top: var(--hairline);
}

.metrics__label {
  font-size: var(--fs-xs);
  color: var(--dross);
}

.metrics__value {
  margin-top: var(--sp-1);
  font-size: var(--fs-lg);
  color: var(--bone);
}

@media (max-width: 1000px) {
  .tiles {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .dash__cols {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
