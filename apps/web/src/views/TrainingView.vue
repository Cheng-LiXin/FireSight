<script setup lang="ts">
/** 训练中心：历史记录、训练曲线、现场图件。
 *
 * 本次交付「回看」一半；网页启动训练与实时日志流是下一半。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { api, ApiError, type TrainJobRequest, type VramLimits } from '@/api/client'
import { trainingLossPanels, trainingMetricOption } from '@/charts/options'
import EChart from '@/components/EChart.vue'
import type {
  ArtifactInfo,
  CurveSeries,
  CurvesResponse,
  SourceOption,
  TrainJob,
  TrainRun,
  TrainStreamEvent,
} from '@/api/types'

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

// --------------------------------------------------------------------------
// 启动训练与实时流
// --------------------------------------------------------------------------

const sources = ref<SourceOption[]>([])
// 默认展开：启动训练是这个页面最主要的事，藏起来等于没有
const launcherOpen = ref(true)
const job = ref<TrainJob | null>(null)
const liveEpochs = ref<number[]>([])
const liveLosses = ref<Record<string, CurveSeries>>({})
const liveMetrics = ref<Record<string, CurveSeries>>({})
const logs = ref<string[]>([])
const logsOpen = ref(false)

let source: EventSource | null = null

/** 数据集名去重——同一个数据集有 train/val/test 三个来源，训练只关心数据集本身。 */
const datasets = computed(() => [...new Set(sources.value.map((s) => s.dataset))])

const form = ref<TrainJobRequest>({
  dataset: '',
  model: 'yolo11n.pt',
  epochs: 10,
  batch: 8,
  imgsz: 480,
  device: '0',
  fraction: 1,
  patience: 10,
  workers: 4,
  cache: false,
  seed: 42,
})

const running = computed(() => job.value?.status === 'running')
const stopping = computed(() => job.value?.status === 'stopping')
/** 占着 GPU 的所有状态。stopping 时进程树还在收尾，同样不能再起新训练。 */
const active = computed(() => running.value || stopping.value)

/** 显存预算：超上限的组合提交必被后端拒，不如在表单上先说清楚。 */
const limits = ref<VramLimits | null>(null)
const overBudget = computed(() => {
  const l = limits.value
  if (!l?.checked || l.max_batch == null) return false
  return form.value.batch > l.max_batch
})

// imgsz 与 device 各触发一次请求，返回顺序不保证。没有这个序号，
// 慢的那次会覆盖快的结果——实测切到 CPU 后，提示仍显示 GPU 的上限，
// 而 overBudget 会据此禁掉一个 CPU 下完全合法的配置。
let limitsSeq = 0

async function loadLimits(): Promise<void> {
  const issued = ++limitsSeq
  try {
    const next = await api.trainLimits(form.value.imgsz, form.value.device)
    if (issued !== limitsSeq) return
    limits.value = next
  } catch {
    // 拿不到上限不该挡住训练，后端仍会兜底校验
    if (issued === limitsSeq) limits.value = null
  }
}

watch(() => [form.value.imgsz, form.value.device], loadLimits)

/** 实时曲线的事件结构与历史曲线同构，图表配置因此可以整套复用。 */
const liveLossPanels = computed(() =>
  liveEpochs.value.length ? trainingLossPanels(liveEpochs.value, liveLosses.value) : [],
)
const liveMetricOption = computed(() =>
  liveEpochs.value.length ? trainingMetricOption(liveEpochs.value, liveMetrics.value) : null,
)

const lastEpoch = computed(() => {
  const m = liveMetrics.value.mAP50
  if (!m || !m.length) return null
  const value = m[m.length - 1]
  return value === null || value === undefined ? null : value
})

function appendSeries(
  target: Record<string, CurveSeries>,
  incoming: Record<string, number> | undefined,
): void {
  if (!incoming) return
  for (const [key, value] of Object.entries(incoming)) {
    if (!target[key]) target[key] = []
    target[key].push(value)
  }
}

function applyEvent(data: TrainStreamEvent): void {
  switch (data.type) {
    case 'epoch':
      if (typeof data.epoch === 'number') liveEpochs.value.push(data.epoch)
      appendSeries(liveLosses.value, data.losses)
      appendSeries(liveMetrics.value, data.metrics)
      break
    case 'log':
      if (data.text) {
        logs.value.push(data.text)
        // 日志是无界的，留最近 400 行足够排查
        if (logs.value.length > 400) logs.value.splice(0, logs.value.length - 400)
      }
      break
    case 'close':
      job.value = job.value ? { ...job.value, ...data } : job.value
      break
    default:
      break
  }
}

function disconnect(): void {
  source?.close()
  source = null
}

function connect(id: string): void {
  disconnect()
  const es = new EventSource(api.trainJobStreamUrl(id))
  source = es
  for (const kind of ['start', 'epoch', 'log', 'done', 'error', 'exit'] as const) {
    es.addEventListener(kind, (e) => {
      try {
        applyEvent(JSON.parse((e as MessageEvent).data) as TrainStreamEvent)
      } catch {
        /* 单条事件坏掉不该中断整条流 */
      }
    })
  }
  es.addEventListener('close', (e) => {
    try {
      applyEvent(JSON.parse((e as MessageEvent).data) as TrainStreamEvent)
    } catch {
      /* 收尾事件的载荷解析失败也要收流 */
    }
    disconnect()
    void loadRuns()
  })
  // 连接异常时浏览器会自动带 Last-Event-ID 重连，服务端据此续传，不必在这里处理
}

async function startTraining(): Promise<void> {
  error.value = null
  job.value = null
  liveEpochs.value = []
  liveLosses.value = {}
  liveMetrics.value = {}
  logs.value = []
  logsOpen.value = true
  try {
    const started = await api.startTrainJob(form.value)
    job.value = started
    connect(started.id)
  } catch (e) {
    error.value = describe(e)
  }
}

async function stopTraining(): Promise<void> {
  if (!job.value) return
  try {
    await api.stopTrainJob(job.value.id)
    // 立刻反映停止意图，不等 SSE 回包：后端会先把状态挪到 stopping，
    // 界面若还显示「训练中」，用户会以为停止没生效而反复点击。
    job.value = { ...job.value, status: 'stopping' }
  } catch (e) {
    error.value = describe(e)
  }
}

onMounted(async () => {
  void loadLimits()
  try {
    sources.value = await api.sources()
    if (!form.value.dataset && sources.value.length) {
      form.value.dataset = sources.value[0].dataset
    }
  } catch {
    /* 数据集列表拿不到不影响看历史 */
  }
})

onBeforeUnmount(disconnect)
</script>

<template>
  <div class="train">
    <p v-if="error" class="fs-note-error" role="alert">{{ error }}</p>

    <!-- 启动 -->
    <section class="fs-block">
      <div class="fs-block__head">
        <h2 class="fs-block__title">启动训练</h2>
        <button type="button" class="fs-btn-ghost" @click="launcherOpen = !launcherOpen">
          {{ launcherOpen ? '收起' : '展开' }}
        </button>
      </div>

      <div v-if="launcherOpen" class="launcher">
        <label class="field">
          <span class="field__label">数据集</span>
          <select v-model="form.dataset" class="field__control">
            <option v-if="!datasets.length" value="">无可训练的数据集</option>
            <option v-for="d in datasets" :key="d" :value="d">{{ d }}</option>
          </select>
        </label>
        <label class="field">
          <span class="field__label">模型</span>
          <input v-model="form.model" class="field__control" />
        </label>
        <label class="field field--narrow">
          <span class="field__label">轮数</span>
          <input v-model.number="form.epochs" class="field__control" type="number" min="1" />
        </label>
        <label class="field field--narrow">
          <span class="field__label">批量</span>
          <input
            v-model.number="form.batch"
            class="field__control"
            :class="{ 'is-over': overBudget }"
            type="number"
            min="1"
            :aria-invalid="overBudget"
          />
        </label>
        <label class="field field--narrow">
          <span class="field__label">imgsz</span>
          <input v-model.number="form.imgsz" class="field__control" type="number" min="32" />
        </label>
        <label class="field field--narrow">
          <span class="field__label">子集比例</span>
          <input
            v-model.number="form.fraction"
            class="field__control"
            type="number"
            min="0.05"
            max="1"
            step="0.05"
          />
        </label>
        <label class="field field--narrow">
          <span class="field__label">设备</span>
          <select v-model="form.device" class="field__control">
            <option value="0">GPU (0)</option>
            <option value="cpu">CPU</option>
          </select>
        </label>

        <div class="actions">
          <button
            type="button"
            class="fs-btn-primary"
            :disabled="active || !form.dataset || overBudget"
            @click="startTraining"
          >
            {{ stopping ? '正在停止…' : running ? '训练进行中…' : '开始训练' }}
          </button>
          <button
            v-if="active"
            type="button"
            class="fs-btn-ghost"
            :disabled="stopping"
            @click="stopTraining"
          >
            {{ stopping ? '停止中…' : '停止' }}
          </button>
        </div>

        <!-- 显存上限。等 ultralytics 自己报 CUDA OOM，模型已经加载、时间已经烧掉 -->
        <p
          v-if="limits?.checked && limits.max_batch != null"
          class="vram"
          :class="{ 'vram--over': overBudget }"
          :title="`按 batch × imgsz² 的显存预算推算，标定自 ${limits.reference}`"
          role="status"
        >
          <template v-if="overBudget">
            本机 <span class="fs-num">{{ limits.total_mb }}</span> MB 显存，
            imgsz=<span class="fs-num">{{ form.imgsz }}</span> 时批量最多
            <span class="fs-num">{{ limits.max_batch }}</span>；当前
            <span class="fs-num">{{ form.batch }}</span> 会超预算，后端会拒绝。
          </template>
          <template v-else>
            本机 <span class="fs-num">{{ limits.total_mb }}</span> MB 显存，
            imgsz=<span class="fs-num">{{ form.imgsz }}</span> 时批量上限
            <span class="fs-num">{{ limits.max_batch }}</span>。
          </template>
        </p>
      </div>

      <p v-if="job" class="status" :class="`status--${job.status}`">
        任务 <span class="mono">{{ job.id }}</span> ·
        <template v-if="running">
          训练中 <span class="fs-num">{{ liveEpochs.length }}</span> / {{ job.epochs }} 轮<template
            v-if="lastEpoch !== null"
          >
            · 最新 mAP@50 <span class="fs-num">{{ lastEpoch.toFixed(3) }}</span></template
          >
        </template>
        <template v-else-if="stopping">
          正在停止…（结束训练进程树，几秒内释放 GPU）
        </template>
        <template v-else-if="job.status === 'done'">
          已完成 · 归档为 <span class="mono">{{ job.run_id }}</span>
        </template>
        <template v-else-if="job.status === 'failed'">
          失败 · {{ job.error ?? `退出码 ${job.returncode}` }}
        </template>
        <template v-else>已停止（未归档，现场记录仍在）</template>
      </p>
    </section>

    <!-- 实时曲线与日志 -->
    <template v-if="liveEpochs.length || logs.length">
      <div v-if="liveLossPanels.length" class="fs-panels">
        <section v-for="p in liveLossPanels" :key="p.title" class="fs-viewfinder">
          <h2 class="fs-viewfinder__title">{{ p.title }}</h2>
          <EChart :option="p.option" :height="200" />
        </section>
      </div>

      <section v-if="liveMetricOption" class="fs-viewfinder">
        <h2 class="fs-viewfinder__title">实时验证指标</h2>
        <EChart :option="liveMetricOption" :height="240" />
      </section>

      <section class="fs-block">
        <div class="fs-block__head">
          <h2 class="fs-block__title">训练日志（最近 {{ logs.length }} 行）</h2>
          <button type="button" class="fs-btn-ghost" @click="logsOpen = !logsOpen">
            {{ logsOpen ? '收起' : '展开' }}
          </button>
        </div>
        <pre v-if="logsOpen" class="logs">{{ logs.join('\n') }}</pre>
      </section>
    </template>

    <section class="fs-block">
      <h2 class="fs-block__title">训练记录</h2>
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
      <div v-if="lossPanels.length" class="fs-panels">
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

      <section v-for="group in grouped" :key="group.label" class="fs-block">
        <h2 class="fs-block__title">{{ group.label }}</h2>
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
.work {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 200px;
  gap: var(--sp-5);
  align-items: start;
}

/* 小倍数：每种损失一张，各自自适应量程 */
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
  color: var(--accent);
}

/* --- 启动表单 --------------------------------------------------------- */

.launcher {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: var(--sp-4);
  padding: var(--sp-3) var(--sp-4);
  margin-bottom: var(--sp-3);
  background: var(--ash);
  border: var(--hairline);
}

.actions {
  display: flex;
  gap: var(--sp-3);
  margin-left: auto;
}

/* 显存提示：常态安静，超预算才转明火色——那时它需要人改一个数字。
   flex-basis:100% 让它独占一行，不挤在输入框之间。 */
.vram {
  flex-basis: 100%;
  margin: 0;
  padding-left: var(--sp-3);
  font-size: var(--fs-xs);
  line-height: 1.6;
  color: var(--dross);
  border-left: 2px solid var(--cinder);
}

.vram--over {
  color: var(--accent);
  border-left-color: var(--accent);
}

.field__control.is-over {
  border-color: var(--accent);
}

/* 状态条：左侧一道色条表示状态。failed 用明火色——它确实需要人去看一眼；
   done 用冷色（完成、无需动作）；stopped 压暗。 */
.status {
  margin: 0;
  padding: var(--sp-2) var(--sp-3);
  font-size: var(--fs-sm);
  color: var(--dross);
  border-left: 2px solid var(--cinder);
}

.status--running {
  color: var(--bone);
  border-left-color: var(--accent);
}

.status--done {
  color: var(--bone);
  border-left-color: var(--cool);
}

.status--failed {
  color: var(--bone);
  border-left-color: var(--accent);
}

/* 停止中：不是失败也不是完成，压成中性色，别让人以为出错了 */
.status--stopping {
  color: var(--dross);
  border-left-color: var(--dross);
}

.logs {
  max-height: 320px;
  margin: 0;
  padding: var(--sp-3);
  overflow: auto;
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  line-height: 1.5;
  color: var(--dross);
  background: var(--soot);
  border: var(--hairline);
  white-space: pre-wrap;
  word-break: break-all;
}

.mono {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  word-break: break-all;
}

/* 选中行：左侧一道明火色，比整行高亮更安静 */
.fs-ledger tbody tr.is-selected td {
  background: var(--highlight);
}

.fs-ledger tbody tr.is-selected td:first-child {
  box-shadow: inset 2px 0 0 var(--accent);
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
