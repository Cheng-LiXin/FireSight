<script setup lang="ts">
/** 训练中心：历史记录、训练曲线、现场图件。
 *
 * 本次交付「回看」一半；网页启动训练与实时日志流是下一半。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessageBox } from 'element-plus'

import { api, ApiError, type TrainJobRequest, type VramLimits } from '@/api/client'
import { trainingLossPanels, trainingMetricOption } from '@/charts/options'
import EChart from '@/components/EChart.vue'
import type {
  ArtifactInfo,
  CurveSeries,
  CurvesResponse,
  ModelOption,
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
/** 正在发起续训的那个 run_id（空串表示没有）。防连点，也用来禁用按钮。 */
const resuming = ref('')

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

/** 可选的预训练模型。本机没权重的那几个也列出来，标「需下载」——
 *  藏起来用户会以为是系统缺陷，列出来至少能自己判断要不要联网抓一个。 */
const modelOptions = ref<ModelOption[]>([])
const chosenModel = computed(
  () => modelOptions.value.find((m) => m.name === form.value.model) ?? null,
)

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

/** 本次任务落到哪个现场。子进程**开训前**就把 run_id 报上来了，所以被暂停的任务
 *  也拿得到——只等 done 事件的话，一按暂停界面就再也说不出该续哪个现场。 */
const liveRunId = computed(() => job.value?.run_id ?? '')

/** 刚收尾、正在重读训练记录。这期间「能不能续」的判据还是旧列表，按钮先按住。 */
const refreshing = ref(false)

/** 上一次训练是不是「暂停」或「崩溃」收的尾。跑完的现场优化器已被剥离，不给按钮。 */
const settledJob = computed(() => {
  const r = job.value
  return r && (r.status === 'stopped' || r.status === 'failed') ? r : null
})

/** 上一次训练在训练记录里的那一条。没有条目＝这个现场还没形成（一次 results.csv
 *  都没写过），也就是「第 1 轮都没跑完」。 */
const settleTarget = computed(() => {
  const id = settledJob.value?.run_id
  return id ? (runs.value.find((r) => r.run_id === id) ?? null) : null
})

/** 上一次训练能不能接着练，以及按不下去时怎么解释。
 *
 *  判据分两处，缺一不可：`job.run_id` 说「是哪个现场」，训练记录里的 `resumable`
 *  说「检查点里的优化器还在不在」——后者只有后端知道。
 *
 *  `tail` 进状态条（短），`reason` 挂在按钮上（整句）。 */
const settleAdvice = computed<{ ok: boolean; tail: string; reason: string } | null>(() => {
  const r = settledJob.value
  if (!r) return null
  // 两种收尾的文案分开：崩溃的人想知道「现场还能不能用」，暂停的人想知道「东西还在不在」。
  const okTail = r.status === 'failed' ? '现场可续' : '现场保留，可继续训练'
  if (refreshing.value) {
    return { ok: false, tail: '正在读取现场…', reason: '正在读取现场记录…' }
  }
  if (!r.run_id) {
    return { ok: false, tail: '没有留下现场', reason: '训练还没开跑就结束了，没有留下现场' }
  }
  const target = settleTarget.value
  if (!target) {
    return {
      ok: false,
      tail: '第 1 轮未跑完，没有留下检查点',
      reason: '第 1 轮未跑完，没有留下检查点',
    }
  }
  if (!target.resumable) {
    const why = target.resume_note || '该现场不可续训'
    return { ok: false, tail: why, reason: why }
  }
  return { ok: true, tail: okTail, reason: '' }
})

/** 按钮区「继续训练」：与训练记录里那一条走同一条路（同一个确认框、同一个载荷）。 */
function resumeSettled(): void {
  const target = settleTarget.value
  if (target) void resumeRun(target)
}

/** 任务收尾后重读训练记录。「能不能续」只有后端读得出（要看优化器状态），
 *  列表刷新完之前不给按钮——否则会让人对着旧判据点一下撞 400。 */
async function refreshRuns(): Promise<void> {
  refreshing.value = true
  try {
    await loadRuns()
  } finally {
    refreshing.value = false
  }
}

/** 显存预算：超上限的组合提交必被后端拒，不如在表单上先说清楚。 */
const limits = ref<VramLimits | null>(null)
const overBudget = computed(() => {
  const l = limits.value
  if (!l?.checked || l.max_batch == null) return false
  return form.value.batch > l.max_batch
})

// imgsz / device / model 各触发一次请求，返回顺序不保证。没有这个序号，
// 慢的那次会覆盖快的结果——实测切到 CPU 后，提示仍显示 GPU 的上限，
// 而 overBudget 会据此禁掉一个 CPU 下完全合法的配置。
let limitsSeq = 0
let limitsTimer: ReturnType<typeof setTimeout> | undefined

async function loadLimits(): Promise<void> {
  const issued = ++limitsSeq
  try {
    const next = await api.trainLimits(form.value.imgsz, form.value.device, form.value.model)
    if (issued !== limitsSeq) return
    limits.value = next
  } catch {
    // 拿不到上限不该挡住训练，后端仍会兜底校验
    if (issued === limitsSeq) limits.value = null
  }
}

// model 是自由文本输入，逐字符请求会打出一串请求；统一延后一拍。
// 序号守卫仍然保留，处理「后发先至」。
function scheduleLimits(): void {
  if (limitsTimer) clearTimeout(limitsTimer)
  limitsTimer = setTimeout(() => void loadLimits(), 300)
}

watch(() => [form.value.imgsz, form.value.device, form.value.model], scheduleLimits)

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
    case 'run':
      // 开训前就有值：暂停时没有 done 事件，这个字段就是「继续训练」的全部依据。
      if (data.run_id && job.value) job.value = { ...job.value, run_id: data.run_id }
      break
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
  for (const kind of ['start', 'run', 'epoch', 'log', 'done', 'error', 'exit'] as const) {
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
    void refreshRuns()
  })
  // 连接异常时浏览器会自动带 Last-Event-ID 重连，服务端据此续传，不必在这里处理
}

/** 起任务之后的共用接线：记下 job 并接上 SSE。 */
function beginJob(started: TrainJob): void {
  job.value = started
  connect(started.id)
}

function resetLive(): void {
  job.value = null
  liveEpochs.value = []
  liveLosses.value = {}
  liveMetrics.value = {}
  logs.value = []
  logsOpen.value = true
}

async function startTraining(): Promise<void> {
  error.value = null
  resetLive()
  try {
    beginJob(await api.startTrainJob(form.value))
  } catch (e) {
    error.value = describe(e)
  }
}

/** 从某个现场断点续训。
 *
 * 只给 run_id：几何、总轮数、学习率轨迹都由后端从检查点里取。这是 ultralytics 的
 * 语义——它用 checkpoint 的 train_args 重建配置，界面上那些字段本来就不生效，
 * 所以这里不假装能改它们。
 */
async function resumeRun(run: TrainRun): Promise<void> {
  const done = run.resume_done ?? 0
  const total = run.resume_total ?? 0
  // 原生 window.confirm 换掉：它渲染成浏览器自己的控件，深色系统主题下会
  // 直接顶穿整站的浅灰无彩调子，而且没法把「几何不可改」这条约束排在正文里。
  // 用户点「取消」是正常路径而不是错误，所以 catch 掉取消这一支。
  try {
    await ElMessageBox.confirm(
      `从第 ${done + 1} 轮继续，共 ${total} 轮。\n几何与总轮数取自检查点，不能在这里改。`,
      '断点续训',
      { confirmButtonText: '继续训练', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  error.value = null
  resetLive()
  resuming.value = run.run_id
  try {
    beginJob(
      await api.startTrainJob({
        dataset: run.dataset ?? '',
        resume_run_id: run.run_id,
      }),
    )
  } catch (e) {
    error.value = describe(e)
  } finally {
    resuming.value = ''
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

async function loadModels(): Promise<void> {
  try {
    modelOptions.value = await api.trainModels()
    // 默认值改成**本机已有的第一个**，而不是写死 yolo11n：本机有哪些权重是
    // 运行时事实（受管目录与仓库根都算），写死一个名字会在它缺失时先卡下载。
    if (!modelOptions.value.some((m) => m.name === form.value.model)) {
      const first = modelOptions.value.find((m) => m.present)
      if (first) form.value.model = first.name
    }
  } catch {
    /* 清单拿不到不影响看历史；模型字段退回自由文本的旧行为 */
  }
}

onMounted(async () => {
  void loadModels()
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

// 定时器与在途请求都要在卸载时收掉。原先只 disconnect() 了 SSE，
// `limitsTimer` 还挂在事件循环上，300ms 后仍会发一次请求并写到已卸载的组件上
// ——这是全仓唯一的定时器泄漏（2026-10-09 代码审查 R16）。
// 序号自增让在途的那次 loadLimits 回来时直接作废，不再写 limits。
onBeforeUnmount(() => {
  disconnect()
  if (limitsTimer) {
    clearTimeout(limitsTimer)
    limitsTimer = undefined
  }
  limitsSeq++
})
</script>

<template>
  <div class="train">
    <p v-if="error" class="fs-note-error" role="alert">{{ error }}</p>

    <div class="head">
      <div>
        <h1 class="head__title">训练中心</h1>
        <p class="head__sub">
          启动参数、实时曲线、现场图件与训练记录在这一页。批量和 imgsz 定不下来就先别开训——
          下面那条显存提示是按 batch × imgsz² 推出来的上限，超了后端会直接拒。
        </p>
      </div>
      <div class="head__actions">
        <button type="button" class="btn btn--ghost" @click="launcherOpen = !launcherOpen">
          {{ launcherOpen ? '收起启动参数' : '展开启动参数' }}
        </button>
      </div>
    </div>

    <!-- 启动 -->
    <section v-if="launcherOpen" class="tray" aria-label="启动参数">
      <div class="tray__grid tray__grid--seven">
        <label class="knob">
          <span class="knob__label">数据集</span>
          <select v-model="form.dataset" class="field__control">
            <option v-if="!datasets.length" value="">无可训练的数据集</option>
            <option v-for="d in datasets" :key="d" :value="d">{{ d }}</option>
          </select>
        </label>
        <label class="knob">
          <span class="knob__label">模型</span>
          <select v-model="form.model" class="field__control">
            <option v-for="m in modelOptions" :key="m.stem" :value="m.name">
              {{ m.name }}{{ m.present ? '' : '（需下载）' }}
            </option>
            <!-- 清单拿不到时保留当前值，不让表单变成空下拉 -->
            <option v-if="!modelOptions.length" :value="form.model">{{ form.model }}</option>
          </select>
        </label>
        <label class="knob">
          <span class="knob__label">轮数</span>
          <input v-model.number="form.epochs" class="field__control" type="number" min="1" />
        </label>
        <label class="knob">
          <span class="knob__label">批量</span>
          <input
            v-model.number="form.batch"
            class="field__control"
            :class="{ 'is-over': overBudget }"
            type="number"
            min="1"
            :aria-invalid="overBudget"
          />
        </label>
        <label class="knob">
          <span class="knob__label">imgsz</span>
          <input v-model.number="form.imgsz" class="field__control" type="number" min="32" />
        </label>
        <label class="knob">
          <span class="knob__label">子集比例</span>
          <input
            v-model.number="form.fraction"
            class="field__control"
            type="number"
            min="0.05"
            max="1"
            step="0.05"
          />
        </label>
        <label class="knob">
          <span class="knob__label">设备</span>
          <select v-model="form.device" class="field__control">
            <option value="0">GPU (0)</option>
            <option value="cpu">CPU</option>
          </select>
        </label>
      </div>

      <!-- 模型说明与显存上限各占一整行，不塞进「模型」格子里。
           模型说明在这个宽度下要折三行（规模特点 + 本机有无 + 估算依据），
           塞进格子会把那一项顶得比同排高一截，顶边底边怎么调都齐不了。
           这是量出来的结论，不是估的：视口 1244px 时该段文字高 72px。 -->
      <p v-if="chosenModel" class="model-note">
        {{ chosenModel.note }}
        <template v-if="!chosenModel.present">
          本机没有这个权重，选中后第一次训练会联网下载。
        </template>
        <template v-else-if="chosenModel.size_mb != null">
          本机已有，{{ chosenModel.size_mb.toFixed(1) }} MB。
        </template>
        <template v-if="!chosenModel.measured">
          显存开销无实测数据，按 yolo11m 估算，见下方上限提示。
        </template>
      </p>

      <!-- 显存上限。等 ultralytics 自己报 CUDA OOM，模型已经加载、时间已经烧掉 -->
      <p
        v-if="limits?.checked && limits.max_batch != null"
        class="vram"
        :class="{ 'vram--over': overBudget }"
        :title="`按 batch × imgsz² × 模型规模因子(${limits.factor}) 推算，标定自 ${limits.reference}`"
        role="status"
      >
        <template v-if="overBudget">
          本机 <span class="fs-num">{{ limits.total_mb }}</span> MB 显存，
          {{ limits.model }} 在 imgsz=<span class="fs-num">{{ form.imgsz }}</span> 时批量最多
          <span class="fs-num">{{ limits.max_batch }}</span>；当前
          <span class="fs-num">{{ form.batch }}</span> 会超预算，后端会拒绝。
        </template>
        <template v-else>
          本机 <span class="fs-num">{{ limits.total_mb }}</span> MB 显存，
          {{ limits.model }} 在 imgsz=<span class="fs-num">{{ form.imgsz }}</span> 时批量上限
          <span class="fs-num">{{ limits.max_batch }}</span>。
        </template>
        <!-- 估出来的上限要说出来，否则用户会以为它也实测过 -->
        <template v-if="limits.model_measured === false">
          <br /><span class="vram__caveat"
            >该模型规模没有实测数据，上限按 yolo11m 估算——参数更多时请再留余量。</span
          >
        </template>
      </p>

      <!-- 开始 / 暂停 / 继续成组：这三件事是同一件事的三个阶段，放同一行，
           用户不用在「下面的训练记录表」里找下一步。

           后端那个接口其实是**结束训练进程树**，不是真挂起。但现场（last.pt 带
           优化器状态）留得住，效果就是暂停，所以按钮就叫暂停——接口路径没改，
           改它要动文档和 e2e，收益为零。 -->
      <div class="tray__actions">
        <button
          type="button"
          class="btn btn--primary"
          :disabled="active || !form.dataset || overBudget"
          @click="startTraining"
        >
          {{ stopping ? '正在暂停…' : running ? '训练进行中…' : '开始训练' }}
        </button>
        <button
          v-if="active"
          type="button"
          class="btn btn--ghost"
          :disabled="stopping"
          @click="stopTraining"
        >
          {{ stopping ? '暂停中…' : '暂停训练' }}
        </button>
        <!-- 崩溃（failed）和暂停（stopped）都留得住现场，所以都给这个按钮。
             不能续时**保留按钮但禁用它**，原因写在下面那句状态里——藏起来的话
             用户看到的是「上次崩了，然后什么都没有」。 -->
        <button
          v-if="settledJob"
          type="button"
          class="btn btn--ghost"
          :disabled="!settleAdvice?.ok"
          :title="settleAdvice?.reason || ''"
          @click="resumeSettled"
        >
          继续训练
        </button>
      </div>

      <p v-if="job" class="status" :class="`status--${job.status}`">
        任务 <span class="mono">{{ job.id }}</span> ·
        <template v-if="running">
          训练中 <span class="fs-num">{{ liveEpochs.length }}</span> / {{ job.epochs }} 轮<template
            v-if="lastEpoch !== null"
          >
            · 最新 mAP@50 <span class="fs-num">{{ lastEpoch.toFixed(3) }}</span></template
          ><template v-if="liveRunId">
            · 现场 <span class="mono">{{ liveRunId }}</span></template
          >
        </template>
        <template v-else-if="stopping">
          正在暂停…（结束训练进程树，几秒内释放 GPU）
        </template>
        <template v-else-if="job.status === 'done'">
          已完成 · 归档为 <span class="mono">{{ job.run_id }}</span>
        </template>
        <template v-else-if="job.status === 'failed'">
          失败 · {{ job.error ?? `退出码 ${job.returncode}` }}
          <template v-if="settleAdvice"> · {{ settleAdvice.tail }}</template>
        </template>
        <!-- 暂停留下的现场和崩溃留下的一模一样（last.pt 都带优化器状态），
             所以两种收尾给同一句话，差别只在下一句能不能续。 -->
        <template v-else>
          已暂停 · {{ settleAdvice?.tail ?? '现场保留' }}
          <template v-if="settleTarget && settleAdvice?.ok">
            · 现场 <span class="mono">{{ settleTarget.run_id }}</span>
          </template>
        </template>
      </p>
    </section>

    <!-- 实时曲线与日志 -->
    <template v-if="liveEpochs.length || logs.length">
      <div v-if="liveLossPanels.length" class="charts charts--3">
        <section v-for="p in liveLossPanels" :key="p.title" class="panel">
          <div class="panel__head">
            <h2 class="panel__title">{{ p.title }}</h2>
          </div>
          <div class="panel__body panel__body--chart">
            <EChart :option="p.option" :height="200" />
          </div>
        </section>
      </div>

      <section v-if="liveMetricOption" class="panel">
        <div class="panel__head">
          <h2 class="panel__title">实时验证指标</h2>
        </div>
        <div class="panel__body panel__body--chart">
          <EChart :option="liveMetricOption" :height="240" />
        </div>
      </section>

      <section class="panel">
        <div class="panel__head">
          <h2 class="panel__title">训练日志（最近 {{ logs.length }} 行）</h2>
          <button type="button" class="btn btn--ghost" @click="logsOpen = !logsOpen">
            {{ logsOpen ? '收起' : '展开' }}
          </button>
        </div>
        <div v-if="logsOpen" class="panel__body">
          <pre class="logs">{{ logs.join('\n') }}</pre>
        </div>
      </section>
    </template>

    <section class="panel">
      <div class="panel__head">
        <h2 class="panel__title">训练记录 <span class="num">{{ runs.length }}</span> 条</h2>
        <span class="panel__hint">点运行号看那一次的曲线与图件</span>
      </div>
      <div class="panel__body panel__body--flush">
        <table v-if="runs.length" class="fs-ledger">
          <thead>
            <tr>
              <th>运行</th>
              <th>数据集</th>
              <th class="is-num">轮数</th>
              <th class="is-num">batch</th>
              <th class="is-num">imgsz</th>
              <th class="is-num">mAP@50</th>
              <th>归档</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="r in runs"
              :key="r.run_id"
              :data-run-id="r.run_id"
              :class="{ 'is-selected': r.run_id === selectedId, 'is-live': r.run_id === liveRunId }"
            >
              <td>
                <!-- 用真的 button 而不是给 tr 挂 click：键盘可达 -->
                <button type="button" class="runlink" @click="selectedId = r.run_id">
                  <span class="mono">{{ r.run_id }}</span>
                </button>
              </td>
              <td>{{ r.dataset ?? '—' }}</td>
              <td class="is-num">{{ r.epochs_done }}</td>
              <td class="is-num">{{ r.batch ?? '—' }}</td>
              <td class="is-num">{{ r.imgsz ?? '—' }}</td>
              <td class="is-num">{{ fix(r.mAP50) }}</td>
              <td>
                <span v-if="r.archived" class="tag tag--ok">已归档</span>
                <span v-else class="tag">现场</span>
              </td>
              <td>
                <!-- 本次任务的那一行要说清「它就是你刚起的那个」：正在跑时按钮必须
                     按死，否则点下去只会撞 409（GPU 还被自己占着）。 -->
                <template v-if="r.run_id === liveRunId && active">
                  <span class="tag tag--live">本次任务{{ running ? '进行中' : '收尾中' }}</span>
                </template>
                <template v-else-if="r.resumable">
                  <button
                    type="button"
                    class="fs-btn-ghost fs-btn-ghost--tiny"
                    :disabled="active || resuming === r.run_id"
                    :title="`已完成 ${r.resume_done}/${r.resume_total} 轮，从第 ${(r.resume_done ?? 0) + 1} 轮继续`"
                    @click="resumeRun(r)"
                  >
                    {{ resuming === r.run_id ? '发起中…' : '继续训练' }}
                  </button>
                </template>
                <!-- 说不清为什么不能续，用户就会以为是按钮坏了 -->
                <span v-else class="why" :title="r.resume_note">不可续训</span>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-else class="empty empty--inset">
          尚无训练记录。命令行执行 fs-train 后，曲线会出现在这里。
        </p>
      </div>
    </section>

    <template v-if="selected">
      <div v-if="lossPanels.length" class="charts charts--3">
        <section v-for="p in lossPanels" :key="p.title" class="panel">
          <div class="panel__head">
            <h2 class="panel__title">{{ p.title }}</h2>
          </div>
          <div class="panel__body panel__body--chart">
            <EChart :option="p.option" :height="200" />
          </div>
        </section>
      </div>
      <p v-else class="empty empty--inset">
        该次训练没有可用的 results.csv，或其中不含损失列。
      </p>

      <div class="work">
        <section class="panel">
          <div class="panel__head">
            <h2 class="panel__title">验证指标</h2>
          </div>
          <div class="panel__body panel__body--chart">
            <EChart v-if="metricOption" :option="metricOption" :height="300" />
          </div>
        </section>

        <aside class="side">
          <div v-for="row in readouts" :key="row.label" class="readout">
            <div class="readout__label">{{ row.label }}</div>
            <div class="readout__value">{{ row.value }}</div>
          </div>
          <p v-if="selected && !selected.archived" class="side__note">
            该次训练未归档，因此没有可比的最终指标——曲线仍来自现场记录。
          </p>
        </aside>
      </div>

      <section v-for="group in grouped" :key="group.label" class="panel">
        <div class="panel__head">
          <h2 class="panel__title">{{ group.label }}</h2>
        </div>
        <div class="panel__body">
          <div class="gallery">
            <figure v-for="a in group.items" :key="a.name" class="shot">
              <a :href="api.trainArtifactUrl(selectedId, a.name)" target="_blank" rel="noopener">
                <img :src="api.trainArtifactUrl(selectedId, a.name)" :alt="a.name" loading="lazy" />
              </a>
              <figcaption>{{ a.name }}</figcaption>
            </figure>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.train {
  display: flex;
  flex-direction: column;
  gap: var(--s5);
}

/* 七个启动控件一行放得下，且顺序固定：数据集→模型→三个几何量→子集→设备。
   设备必须留在最后，因为它是「换一个硬件」而不是「调一个数字」，
   放在中间会让人以为它和 imgsz 是同一类。 */
.tray__grid--seven {
  grid-template-columns:
    minmax(190px, 1.3fr) minmax(210px, 1.5fr) repeat(3, minmax(96px, 0.7fr))
    minmax(110px, 0.8fr) minmax(120px, 0.8fr);
}

@media (max-width: 1240px) {
  .tray__grid--seven {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}

/* 验证指标独占主区，右栏是四条读数。给读数 200px：它的内容都是
   「标签 + 一个数」，再宽就只是把白摊开，不会更好读。 */
.work {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 200px;
  gap: var(--s5);
  align-items: start;
}

/* 小倍数：每种损失一张，各自自适应量程 */
.side {
  display: flex;
  flex-direction: column;
  gap: var(--s3);
}

.side__note {
  margin: 0;
  padding-left: var(--s3);
  border-left: 2px solid var(--line);
  font-size: var(--t-small);
  line-height: 1.6;
  color: var(--ink-mute);
}

/* --- 运行列表 --------------------------------------------------------- */

/* 表格里的 run_id 入口。它是「一行里的一个跳转目标」，不是一组互斥选项，
   所以不能套全局那个胶囊 .pick——那会让二十几行表格变成二十几个按钮。
   这里给的是文字按钮的配方：零内边距、无背景，靠冷色和手型表明可点。 */
.runlink {
  padding: 0;
  font: inherit;
  color: var(--ink);
  background: none;
  border: 0;
  cursor: pointer;
  text-align: left;
}

.runlink:hover {
  color: var(--signal-deep);
}

/* 「不可续训」：把鼠标放上去能看到原因（例如优化器状态已被剥离）。
   不给它按钮样式——它不是操作，是一句说明。 */
.why {
  font-size: var(--t-micro);
  color: var(--ink-faint);
  cursor: help;
}

/* --- 启动托盘内的说明条 ------------------------------------------------- */

/* 显存提示：常态安静，超预算才转明火色——那时它需要人改一个数字。
   独占一行，不挤在输入框之间。 */
.vram {
  margin: var(--s4) 0 0;
  padding-left: var(--s3);
  font-size: var(--t-small);
  line-height: 1.6;
  color: var(--ink-mute);
  border-left: 2px solid var(--line-strong);
}

.vram--over {
  color: var(--alarm);
  border-left-color: var(--alarm);
}

/* 「这个上限是估的」——压低一档，不跟主句抢，但必须看得见 */
.vram__caveat {
  color: var(--ink-faint);
}

/* 超预算的输入框描边用告警色，和左边那条竖线是同一个信号：
   要改的正是 batch 这个数字，边框得先让人看见哪里不对。 */
.field__control.is-over {
  border-color: var(--alarm);
}

/* 模型说明条：整行下方单独一条，宽度按整行算，所以三行文字在这里只占两行左右，
   不再把某个格子顶高。 */
.model-note {
  margin: var(--s4) 0 0;
  font-size: var(--t-small);
  line-height: 1.55;
  color: var(--ink-mute);
}

/* 状态条：一块浅底的小条，四种状态靠左边一道竖线区分。
   failed 用明火色——它确实需要人去看一眼。 */
.status {
  margin: var(--s4) 0 0;
  padding: var(--s2) var(--s3);
  font-size: var(--t-body);
  color: var(--ink-body);
  background: var(--surface-sunk);
  border-radius: var(--r-sm);
}

.status--running {
  color: var(--ink);
  border-left: 2px solid var(--signal);
  padding-left: calc(var(--s3) - 2px);
}

/* 已完成：中性。绿色在这个界面里留给「已被人工判定无误报」这类事实，
   训练完成既不是火情也不是判定结论，用冷色竖线即可。 */
.status--done {
  color: var(--ink);
  border-left: 2px solid var(--line-strong);
  padding-left: calc(var(--s3) - 2px);
}

.status--failed {
  color: var(--ink);
  border-left: 2px solid var(--alarm);
  padding-left: calc(var(--s3) - 2px);
}

/* 停止中：不是失败也不是完成，压成中性色，别让人以为出错了 */
.status--stopping {
  color: var(--ink-faint);
  border-left: 2px solid var(--line-strong);
  padding-left: calc(var(--s3) - 2px);
}

/* 已暂停：和 stopping 同样是中性色，但竖线画成虚线——实线在这套界面里
   读作「终局」，而暂停的现场还等着人接着往下跑。 */
.status--stopped {
  color: var(--ink-body);
  border-left: 2px dashed var(--line-strong);
  padding-left: calc(var(--s3) - 2px);
}

.logs {
  max-height: 320px;
  margin: 0;
  padding: var(--s3);
  overflow: auto;
  font-family: var(--font-num);
  font-size: var(--t-micro);
  line-height: 1.5;
  color: var(--ink-body);
  background: var(--surface-sunk);
  border: 1px solid var(--line-faint);
  border-radius: var(--r-sm);
  white-space: pre-wrap;
  word-break: break-all;
}

.mono {
  font-family: var(--font-num);
  font-size: var(--t-micro);
  word-break: break-all;
}

/* 选中行：左侧一道冷色竖线，比整行染色安静，也不会和「待处置」那类
   真正的状态色撞车。 */
.fs-ledger tbody tr.is-selected td {
  background: var(--surface-sunk);
}

.fs-ledger tbody tr.is-selected td:first-child {
  box-shadow: inset 2px 0 0 var(--signal);
}

/* 本次任务的现场行：正在写盘的那一行。和「选中」用同一道竖线——两者都是
   「看这里」，不该给两个颜色；不同的是这一行的动作列按死为标签，点不动。 */
.fs-ledger tbody tr.is-live td:first-child {
  box-shadow: inset 2px 0 0 var(--signal);
}

.tag {
  display: inline-flex;
  align-items: center;
  padding: 2px var(--s3);
  font-size: var(--t-micro);
  font-weight: var(--w-medium);
  line-height: 1.5;
  color: var(--ink-body);
  background: var(--surface-sunk);
  border-radius: var(--r-pill);
  white-space: nowrap;
}

/* 已归档是中性状态，不是火情——所以不染任何彩色，只用一圈内描边把它
   和「现场」区分开。明火色在这套界面里表示风险与严重度，
   不该被状态标签借用。 */
.tag--ok {
  color: var(--ink);
  background: var(--surface-sunk);
  box-shadow: inset 0 0 0 1px var(--line-strong);
}

/* 「本次任务」：表格里那一行就是当前这个任务。它不是一个可点的动作，
   是一个指认——所以给冷色竖边，不做成按钮的样子。 */
.tag--live {
  color: var(--signal-deep);
  background: var(--surface-sunk);
  box-shadow: inset 0 0 0 1px var(--signal);
}

/* --- 图件 ------------------------------------------------------------- */

.gallery {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: var(--s4);
}

.shot {
  margin: 0;
}

.shot img {
  display: block;
  width: 100%;
  height: auto;
  background: var(--surface-sunk);
  border: 1px solid var(--line-faint);
  border-radius: var(--r-sm);
}

.shot figcaption {
  margin-top: var(--s2);
  font-family: var(--font-num);
  font-size: var(--t-micro);
  color: var(--ink-mute);
  word-break: break-all;
}

@media (max-width: 1100px) {
  .work {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
