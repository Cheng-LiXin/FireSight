<script setup lang="ts">
/** 训练中心：历史记录、训练曲线、现场图件。
 *
 * 本次交付「回看」一半；网页启动训练与实时日志流是下一半。
 */
import { computed, onMounted, ref, watch } from 'vue'

import { api, ApiError } from '@/api/client'
import { trainingLossPanels, trainingMetricOption } from '@/charts/options'
import EChart from '@/components/EChart.vue'
import type { ArtifactInfo, CurvesResponse, TrainRun } from '@/api/types'

const runs = ref<TrainRun[]>([])
const selectedId = ref('')
const curves = ref<CurvesResponse | null>(null)
const artifacts = ref<ArtifactInfo[]>([])
const busy = ref(false)
const error = ref<string | null>(null)

/** 图件按用途分组，平铺一堆文件名读不出重点。 */
const ARTIFACT_GROUP: Record<string, string> = {
  confusion: '混淆矩阵',
  pr: 'PR / F1 曲线',
  curves: '训练汇总图',
  labels: '标签分布',
  batch: '批次样例',
}

const selected = computed(() => runs.value.find((r) => r.run_id === selectedId.value) ?? null)

const lossPanels = computed(() =>
  curves.value ? trainingLossPanels(curves.value.epochs, curves.value.losses) : [],
)
const metricOption = computed(() =>
  curves.value ? trainingMetricOption(curves.value.epochs, curves.value.metrics) : null,
)

const grouped = computed(() => {
  const out: { label: string; items: ArtifactInfo[] }[] = []
  for (const kind of ['confusion', 'pr', 'curves', 'labels', 'batch']) {
    const items = artifacts.value.filter((a) => a.kind === kind)
    if (items.length) out.push({ label: ARTIFACT_GROUP[kind] ?? kind, items })
  }
  const rest = artifacts.value.filter((a) => !(a.kind in ARTIFACT_GROUP))
  if (rest.length) out.push({ label: '其他', items: rest })
  return out
})

/** 最新一轮的指标来自归档；未归档的只能看曲线，没有可比的数字。 */
const readouts = computed(() => {
  const r = selected.value
  if (!r) return []
  return [
    { label: '轮数', value: String(r.epochs_done) },
    { label: 'mAP@50', value: fix(r.mAP50) },
    { label: 'mAP@50-95', value: fix(r.mAP50_95) },
    { label: '精确率 P', value: fix(r.precision) },
    { label: '召回率 R', value: fix(r.recall) },
  ]
})

function fix(v: number | null, digits = 3): string {
  return v === null || v === undefined ? '—' : v.toFixed(digits)
}

function describe(e: unknown): string {
  return e instanceof ApiError ? e.message : '加载失败'
}

async function loadRuns(): Promise<void> {
  busy.value = true
  error.value = null
  try {
    runs.value = await api.trainRuns()
    if (runs.value.length && !selectedId.value) selectedId.value = runs.value[0].run_id
  } catch (e) {
    error.value = describe(e)
  } finally {
    busy.value = false
  }
}

async function loadRun(id: string): Promise<void> {
  if (!id) return
  busy.value = true
  error.value = null
  try {
    const [c, a] = await Promise.all([api.trainCurves(id), api.trainArtifacts(id)])
    curves.value = c
    artifacts.value = a
  } catch (e) {
    error.value = describe(e)
    curves.value = null
    artifacts.value = []
  } finally {
    busy.value = false
  }
}

onMounted(loadRuns)
watch(selectedId, (id) => void loadRun(id))
</script>

<template>
  <div class="train">
    <p v-if="error" class="alert" role="alert">{{ error }}</p>

    <section class="block">
      <h2 class="block__title">训练记录</h2>
      <table v-if="runs.length" class="fs-ledger">
        <thead>
          <tr>
            <th>运行</th>
            <th>数据集</th>
            <th class="is-num">轮数</th>
            <th class="is-num">mAP@50</th>
            <th>归档</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="r in runs"
            :key="r.run_id"
            :class="{ 'is-selected': r.run_id === selectedId }"
          >
            <td>
              <!-- 用真的 button 而不是给 tr 挂 click：键盘可达 -->
              <button type="button" class="pick" @click="selectedId = r.run_id">
                <span class="mono">{{ r.run_id }}</span>
              </button>
            </td>
            <td>{{ r.dataset ?? '—' }}</td>
            <td class="is-num">{{ r.epochs_done }}</td>
            <td class="is-num">{{ fix(r.mAP50) }}</td>
            <td>
              <span v-if="r.archived" class="tag tag--ok">已归档</span>
              <span v-else class="tag">现场</span>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="none">
        尚无训练记录。命令行执行 fs-train 后，曲线会出现在这里。
      </p>
    </section>

    <template v-if="selected">
      <div v-if="lossPanels.length" class="panels">
        <section v-for="p in lossPanels" :key="p.title" class="fs-viewfinder">
          <h2 class="fs-viewfinder__title">{{ p.title }}</h2>
          <EChart :option="p.option" :height="200" />
        </section>
      </div>
      <p v-else class="none">
        该次训练没有可用的 results.csv，或其中不含损失列。
      </p>

      <div class="work">
        <section class="fs-viewfinder">
          <h2 class="fs-viewfinder__title">验证指标</h2>
          <EChart v-if="metricOption" :option="metricOption" :height="300" />
        </section>

        <aside class="side">
          <div v-for="row in readouts" :key="row.label" class="fs-readout">
            <div class="fs-readout__label">{{ row.label }}</div>
            <div class="fs-readout__value">{{ row.value }}</div>
          </div>
          <p v-if="selected && !selected.archived" class="side__note">
            该次训练未归档，因此没有可比的最终指标——曲线仍来自现场记录。
          </p>
        </aside>
      </div>

      <section v-for="group in grouped" :key="group.label" class="block">
        <h2 class="block__title">{{ group.label }}</h2>
        <div class="gallery">
          <figure v-for="a in group.items" :key="a.name" class="shot">
            <a :href="api.trainArtifactUrl(selectedId, a.name)" target="_blank" rel="noopener">
              <img :src="api.trainArtifactUrl(selectedId, a.name)" :alt="a.name" loading="lazy" />
            </a>
            <figcaption>{{ a.name }}</figcaption>
          </figure>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.train {
  display: flex;
  flex-direction: column;
  gap: var(--sp-5);
}

.alert {
  margin: 0;
  padding: var(--sp-3) var(--sp-4);
  font-size: var(--fs-sm);
  background: var(--ash);
  border: var(--hairline);
  border-left: 3px solid var(--fire-2);
}

.block {
  display: flex;
  flex-direction: column;
}

.block__title {
  margin: 0 0 var(--sp-3);
  font-size: var(--fs-sm);
  font-weight: 500;
  color: var(--dross);
}

.work {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 200px;
  gap: var(--sp-5);
  align-items: start;
}

/* 小倍数：每种损失一张，各自自适应量程 */
.panels {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--sp-4);
}

.side {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}

.side__note {
  margin: var(--sp-2) 0 0;
  padding-left: var(--sp-3);
  border-left: 2px solid var(--cinder);
  font-size: var(--fs-xs);
  line-height: 1.6;
  color: var(--dross);
}

/* --- 运行列表 --------------------------------------------------------- */

.pick {
  padding: 0;
  font: inherit;
  color: var(--bone);
  background: none;
  border: 0;
  cursor: pointer;
  text-align: left;
}

.pick:hover {
  color: var(--fire-3);
}

.mono {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  word-break: break-all;
}

/* 选中行：左侧一道明火色，比整行高亮更安静 */
.fs-ledger tbody tr.is-selected td {
  background: rgb(51 42 32 / 45%);
}

.fs-ledger tbody tr.is-selected td:first-child {
  box-shadow: inset 2px 0 0 var(--fire-2);
}

.tag {
  padding: 1px var(--sp-2);
  font-size: var(--fs-xs);
  color: var(--dross);
  border: 1px solid var(--cinder);
  border-radius: var(--radius);
}

/* 已归档是中性状态，不是火情——用亮一档的文字而不是明火色。
   明火色在这套界面里表示风险与严重度，不该被状态标签借用。 */
.tag--ok {
  color: var(--bone);
  border-color: var(--dross);
}

/* --- 图件 ------------------------------------------------------------- */

.gallery {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: var(--sp-4);
}

.shot {
  margin: 0;
}

.shot img {
  display: block;
  width: 100%;
  height: auto;
  background: var(--soot);
  border: var(--hairline);
}

.shot figcaption {
  margin-top: var(--sp-2);
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--dross);
  word-break: break-all;
}

.none {
  margin: 0;
  padding: var(--sp-4);
  font-size: var(--fs-sm);
  color: var(--dross);
  border: 1px dashed var(--cinder);
}

@media (max-width: 1100px) {
  .work {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
