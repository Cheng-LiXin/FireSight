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
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

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

/** 左栏图表的高度：跟右栏两段加起来的自然高度走，两栏就等高。
 *  EChart 需要一个具体像素数（它把自己画进 canvas，不能只给 flex:1），
 *  所以量一次右栏的盒子高度再回填。
 *
 *  一次成环：图表撑高左栏 → 右栏被 stretch 撑高 → 再量回去就更高，
 *  每轮加几十像素，最后能涨到几万像素。所以量的是右栏的**内容**高度
 *  （去掉被 stretch 拉出来的那部分），并且只在内容真的变高时才更新，
 *  且设一个上限兜住任何没预料到的级联。 */
const CHART_MIN_PX = 220
const CHART_MAX_PX = 520

const stackEl = ref<HTMLElement | null>(null)
const chartHeight = ref(300)
let ro: ResizeObserver | null = null

onMounted(() => {
  const sync = () => {
    const el = stackEl.value
    if (!el) return
    // 量各段自己：.stack__seg 没有 flex-grow，它的高度就是内容高，
    // 加起来才是右栏真正需要的那个数。量容器 scrollHeight 会在
    // .stack 写满（height:100%）之后变成被拉伸后的高度，越量越高。
    // getBoundingClientRect 已经含 padding 和 border，不要再加一遍。
    const segs = el.querySelectorAll<HTMLElement>('.stack__seg')
    const need = [...segs].reduce(
      (sum, s) => sum + s.getBoundingClientRect().height,
      0,
    )
    const h = Math.min(
      CHART_MAX_PX,
      Math.max(CHART_MIN_PX, Math.round(need) - 1),
    )
    if (h !== chartHeight.value) chartHeight.value = h
  }
  sync()
  ro = new ResizeObserver(sync)
  if (stackEl.value) ro.observe(stackEl.value)
})

onBeforeUnmount(() => ro?.disconnect())

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
      <div class="panel panel--chart">
        <div class="panel__head">
          <h2 class="panel__title">近 30 日告警次数（按类别）</h2>
          <span class="panel__hint">单位：起</span>
        </div>
        <div class="panel__body panel__body--chart">
          <!-- 图表高度跟着右栏走，不写死 260：右栏两段的自然高度会随数据变，
               写死就会出现「右栏比图表高、左栏底下空一片」，
               而这一页的图表只有一条 30 日趋势，向上吃空白最不浪费。 -->
          <EChart v-if="ready" :option="trendOption" :height="chartHeight" />
          <el-skeleton v-else :rows="6" animated />
        </div>
      </div>

      <!-- 右栏两段，从上到下：类别构成 → 近 24 小时峰值。
           一盒两段、段间一条发丝线，不各自成盒——盒中盒会让这一栏碎掉。
           峰值那段贴着盒子下沿（margin-top:auto），空白落在两段之间，
           这样上面那条色带和它的图例仍然挨在一起（它们是一件事）。 -->
      <div class="panel panel--stack">
        <div ref="stackEl" class="stack">
          <section class="stack__seg">
            <h2 class="stack__title">告警类别构成</h2>
            <div class="stack__body">
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
          </section>

          <!-- 风险分的两根料。风险尺只说「0.34」，这两条说明它是被什么撑起来的，
               值班员要判断的不是分数高低，而是「24 小时里多了几起、
               最高置信度到没到 0.7」——这两个数后端已经算好了，
               之前只拼进了 risk.basis 一句话里，读不出结构。

               这里刻意不再放「当前归档」：它已经是上面那条读数卡（归档模型），
               再抄一遍就是把同一个 run_id 在一屏里念两遍。 -->
          <section class="stack__seg">
            <h2 class="stack__title">近 24 小时峰值</h2>
            <div class="stack__body stack__body--readouts">
              <div class="readout">
                <span class="readout__label">告警</span>
                <span class="readout__value">
                  <CountUp :value="n(ov?.risk.alerts_24h)" />
                  <span class="readout__unit">起</span>
                </span>
              </div>
              <div class="readout">
                <span class="readout__label">峰值置信度</span>
                <span class="readout__value">
                  <CountUp :value="ov ? ov.risk.peak_conf_24h : '—'" :digits="2" />
                </span>
              </div>
            </div>
          </section>
        </div>
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

/* --- 图 -------------------------------------------------------------------
   拉齐两栏。base.css:724-728 的全局 .charts 带着 align-items:start，
   同为单类选择器、scoped 也不加权重，后加载的它赢——所以这里必须写回
   stretch 而不是靠「不写」（不写就是继续吃 start）。
   实测：左图 369px、右「告警类别构成」214px，不拉齐就在右栏底下空出 155px。 */
.charts {
  display: grid;
  grid-template-columns: minmax(0, 2.4fr) minmax(0, 1fr);
  gap: var(--s5);
  align-items: stretch;
}

/* 右栏是一个盒子装两段，不是两个盒子。
   两段各自成盒的话盒子之间还会再空出一圈，反而更散；
   一盒两段靠发丝线分区，底边自然与左图对齐。 */
.panel--stack {
  padding: var(--s5) 0;
  overflow: hidden;
}

.stack {
  display: flex;
  flex-direction: column;
  /* 写满整栏：两段之间靠 .stack__seg + .stack__seg 的 margin-top:auto
     把空白让出来，写不满的话空白就掉到盒子下沿，
     右栏底部会空一大截（左栏图表把它撑高了）。 */
  height: 100%;
}

.stack__seg {
  display: flex;
  flex-direction: column;
  padding: var(--s4) var(--s5);
  /* 不写 flex：两段按内容各自的高来分，剩下的空白落在一起，
   而不是把空白摊进每段、把色带和图例拉散。
   类别构成那段内容多、峰值那段矮，写死比例反而会在数据稀疏时露出空当。 */
}

.stack__seg + .stack__seg {
  border-top: var(--hairline);
  margin-top: auto;
}

.stack__title {
  font-size: var(--t-title);
  font-weight: var(--w-bold);
  color: var(--ink);
  letter-spacing: var(--track-tight);
  margin: 0 0 var(--s3);
}

.stack__body {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}

.stack__body--readouts {
  flex-direction: row;
  gap: var(--s5);
  align-items: flex-start;
  justify-content: flex-start;
}

/* run_id 是标识符不是读数：等宽但不用大号，
   写死了宽度，好让一条很长的 id 也只占一行、只折一次。 */
.stack__run {
  margin: 0;
  font-family: var(--font-num);
  font-size: var(--t-small);
  color: var(--ink-body);
  word-break: break-all;
  line-height: 1.5;
}

.stack__note {
  margin: var(--s2) 0 0;
  font-size: var(--t-micro);
  color: var(--ink-faint);
}

.panel__hint {
  font-family: var(--font-num);
}

/* 左栏：图的高度由 script 里的 chartHeight 回填，面板跟着内容走。
   不写 align-items 之外的东西——面板自身不需要再撑开。 */
.panel--chart {
  display: flex;
  flex-direction: column;
}

/* 两栏的高由 chartHeight 配平，这里不再让面板自己撑——
   面板一旦 stretch 就会反过来影响右栏量出来的数。 */
.panel--chart > .panel__body--chart {
  display: flex;
  flex-direction: column;
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
  padding: var(--s3) 0;
  border-top: 1px solid var(--line-faint);
}

/* 类别名和计数一起提到 --t-title（17px）：这两行是右栏唯一的文字内容，
   右栏宽 393px，一条 `dt` 加一条 `dd` 要占满，两行都是 13px 时
   整个面板读起来是「一片小字」。计数是这屏要读的东西，不是脚注。 */
.ratio__item dt {
  display: flex;
  align-items: center;
  font-size: var(--t-title);
  color: var(--ink-body);
}

/* 色点跟着放大：.dot 是 6px，配 13px 的字刚好；配 17px 的字就像一粒灰。
   颜色语义不变——烟仍是中性深灰、火仍是 --alarm，
   在这一档尺寸下靠面积（明火段更长）也能分开。 */
.ratio__item .dot {
  width: 9px;
  height: 9px;
  margin-right: var(--s2);
}

.ratio__item dd {
  margin: 0;
  display: flex;
  align-items: baseline;
  gap: var(--s3);
  font-size: var(--t-title);
}

/* 百分比是次要读数：压暗并降一档，避免和计数抢注意力。
   只降字号不降对比度——它和计数并排，差太多会被读成另一个量。 */
.ratio__pct {
  min-width: 3ch;
  text-align: right;
  font-size: var(--t-small);
  color: var(--ink-faint);
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