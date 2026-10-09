<script setup lang="ts">
/** 实时监控：一路 RTSP 常驻推理 + 标注画面推流。
 *
 * 推理、事件、片段、TTS 全走既有流水线，这里只负责启停与看画面。
 * 显存与训练互斥：训练优先，起训练时后端会自动停掉监控。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'

import { api } from '@/api/client'
import type { MonitorSourceOption, MonitorStatus } from '@/api/types'

const status = ref<MonitorStatus | null>(null)
const error = ref('')
const busy = ref(false)

/** 下拉末尾的自定义项。设备序号 / rtsp 地址 / 路径都不会是这个串。 */
const CUSTOM_SOURCE = '__custom__'

const form = ref({
  source: '',
  camera_name: '',
  conf: 0.25,
  device: '0',
  enable_tts: true,
})

/** 后端给的视频源清单：现场固定设备 + 本机摄像头。 */
const sources = ref<MonitorSourceOption[]>([])
const sourcesError = ref('')
/** 自定义项下方的自由文本（本地视频、图片目录、现场临时开的流）。 */
const customSource = ref('')

const customSelected = computed(() => form.value.source === CUSTOM_SOURCE)

/** 真正要交给后端的源。自定义项取文本框，其余取下拉里的原串。 */
const sourceValue = computed(() =>
  customSelected.value ? customSource.value.trim() : form.value.source.trim(),
)

/** 当前选中项必须出现在下拉里：它可能是别的机器配的地址，也可能是探测失败时的手输值。 */
const sourceOptions = computed<MonitorSourceOption[]>(() => {
  const cur = form.value.source
  if (!cur || cur === CUSTOM_SOURCE || sources.value.some((o) => o.key === cur)) return sources.value
  return [{ key: cur, label: '当前选中', kind: 'stream', detail: cur }, ...sources.value]
})

/** 选中项的补充信息（分辨率 / 完整地址）。选项不在清单里时退回显示源本身。 */
const sourceDetail = computed(() => {
  if (customSelected.value) return ''
  return sources.value.find((o) => o.key === form.value.source)?.detail ?? ''
})

/** 选设备时顺手把位置名填上——现场给摄像头起的名字（东门）就是位置名，
    事件文案与播报里要的就是它。本机摄像头没有位置概念，不填。 */
function onSourceChange() {
  if (customSelected.value) return
  const opt = sources.value.find((o) => o.key === form.value.source)
  if (opt && opt.kind === 'stream' && !form.value.camera_name.trim()) {
    form.value.camera_name = opt.label
  }
}

async function loadSources() {
  try {
    sources.value = await api.monitorSources()
    sourcesError.value = ''
  } catch (e) {
    sourcesError.value = e instanceof Error ? e.message : String(e)
  }
}

const active = computed(() => {
  const s = status.value?.status
  return s === 'connecting' || s === 'running' || s === 'stopping'
})

/** 两类错误都要露出来：请求本身失败，或后台流水线报错（status.error）。 */
const shownError = computed(() => error.value || status.value?.error || '')

const hasFrames = computed(() => (status.value?.frames ?? 0) > 0)

/** 人工暂停播报（后端 TTS 单例上的状态；status 里每次现读）。 */
const ttsPaused = computed(() => status.value?.tts_paused === true)

/** 流地址挂在 img 上；只有在跑时才挂，停了就把连接放掉。 */
const streamUrl = computed(() => (active.value ? api.monitorStreamUrl() : ''))

const STATUS_CN: Record<string, string> = {
  idle: '未启动',
  connecting: '连接中',
  running: '监控中',
  stopping: '停止中',
  stopped: '已停止',
  ended: '源已结束',
  failed: '失败',
}

let timer: number | undefined
/** 停止确认期间的快轮询定时器；平时是 undefined（用 2s 的常驻轮询）。 */
let fastTimer: number | undefined
let fastTicks = 0

const STATUS_POLL_MS = 2000
/** 停止确认窗口：这段时间里改成快轮询，把「停止中」的滞留压到可忽略。 */
const STOP_FAST_POLL_MS = 300
const STOP_FAST_TICKS = 8

async function refresh() {
  try {
    status.value = await api.monitorStatus()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

/** 停止后快轮询几轮，确认状态已离开 stopping，再交还给常驻轮询。 */
function startFastPolling() {
  stopFastPolling()
  fastTicks = 0
  fastTimer = window.setInterval(async () => {
    fastTicks += 1
    await refresh()
    if (!status.value || status.value.status !== 'stopping' || fastTicks >= STOP_FAST_TICKS) {
      stopFastPolling()
    }
  }, STOP_FAST_POLL_MS)
}

function stopFastPolling() {
  if (fastTimer) {
    window.clearInterval(fastTimer)
    fastTimer = undefined
  }
}

async function start() {
  busy.value = true
  error.value = ''
  try {
    status.value = await api.monitorStart({
      source: sourceValue.value,
      camera_name: form.value.camera_name.trim(),
      conf: form.value.conf,
      device: form.value.device,
      enable_tts: form.value.enable_tts,
    })
  } catch (e) {
    // 409（训练占卡）与 400（没有权重）都要把后端原话显示出来
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
  }
}

async function stop() {
  busy.value = true
  error.value = ''
  try {
    // 后端现在会在返回前等到终态，但仍有「停止中」这条尾巴（例如正在等收流）。
    // 拿到响应后直接用它刷新；万一还停在 stopping，就切成快轮询确认，
    // 不让用户对着「停止中」等满一个 2 秒的常驻轮询周期。
    status.value = await api.monitorStop()
    if (status.value?.status === 'stopping') {
      startFastPolling()
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
  }
}

async function toggleTtsPaused() {
  busy.value = true
  error.value = ''
  try {
    const next = !ttsPaused.value
    // 后端回的是整份 status，直接换掉当前这份，暂停状态与监控状态天然一致
    status.value = await api.monitorSetTtsPaused(next)
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  refresh()
  loadSources()
  timer = window.setInterval(refresh, STATUS_POLL_MS)
})

onUnmounted(() => {
  if (timer) window.clearInterval(timer)
  stopFastPolling()
})
</script>

<template>
  <div class="monitor">
    <div class="head">
      <div>
        <h1 class="head__title">实时监控</h1>
        <p class="head__sub">
          一路 RTSP 常驻推理：告警进事件库、片段留前后各 30 秒、TTS 播报位置。与训练互斥，训练优先。
        </p>
      </div>
      <div class="head__actions">
        <button
          v-if="active"
          type="button"
          class="btn btn--ghost"
          :disabled="busy"
          :data-testid="ttsPaused ? 'mon-tts-resume' : 'mon-tts-pause'"
          :title="ttsPaused ? '恢复语音播报' : '暂停语音播报（告警与片段照常留存）'"
          @click="toggleTtsPaused"
        >
          {{ ttsPaused ? '恢复播报' : '暂停播报' }}
        </button>
        <button v-if="!active" type="button" class="btn btn--primary" :disabled="busy || !sourceValue" @click="start">
          开始监控
        </button>
        <button v-else type="button" class="btn btn--ghost" :disabled="busy" @click="stop">
          停止监控
        </button>
      </div>
    </div>

    <p v-if="shownError" class="fs-note-error" role="alert">
      {{ shownError }}
    </p>

    <!-- 参数在左、画面在右，两框等高。启动参数是低频动作——填一次就好；
         画面是这页的高频对象，要一直占着主视区。并排等高看的时候，
         「现在到底在不在看」和「我刚才填的源是什么」在同一眼里，
         视线也不用在两个高度不同的框之间来回跳。 -->
    <div class="monitor__grid">
      <section class="tray" aria-label="启动参数">
        <h2 class="tray__title">启动参数</h2>
        <div class="tray__grid">
          <label class="knob" for="mon-source">
            <span class="knob__label">视频源</span>
            <!-- 设备由后端给，前端不拼地址：能进这个下拉的串都是后端认得的源 -->
            <select
              id="mon-source"
              v-model="form.source"
              class="field__control"
              :disabled="active"
              :title="sourceDetail || '现场固定设备与本机摄像头'"
              @change="onSourceChange"
            >
              <option value="" disabled>选择摄像头或监控设备…</option>
              <option v-for="opt in sourceOptions" :key="opt.key" :value="opt.key">
                {{ opt.label }}
              </option>
              <option :value="CUSTOM_SOURCE">自定义…（地址或本地路径）</option>
            </select>
            <span v-if="sourceDetail" class="field__hint">{{ sourceDetail }}</span>
          </label>

          <label v-if="customSelected" class="knob" for="mon-source-custom">
            <span class="knob__label">地址或路径</span>
            <input
              id="mon-source-custom"
              v-model="customSource"
              class="field__control"
              placeholder="rtsp://… 或本地视频/图片目录"
              :disabled="active"
            />
          </label>

          <label class="knob" for="mon-loc">
            <span class="knob__label">位置名</span>
            <input
              id="mon-loc"
              v-model="form.camera_name"
              class="field__control"
              placeholder="如：东门"
              :disabled="active"
            />
          </label>
          <label class="knob" for="mon-conf">
            <span class="knob__label">置信度阈值 <b>{{ form.conf.toFixed(2) }}</b></span>
            <!-- 滑块横着铺满整行：太窄的话拇指会压在轨道两端，
                 0.05 到 0.95 这段精度根本点不准。左栏有 300px 出头，够按。 -->
            <input
              id="mon-conf"
              v-model.number="form.conf"
              type="range"
              min="0.05"
              max="0.95"
              step="0.05"
              :style="{ '--fill': `${((form.conf - 0.05) / 0.9) * 100}%` }"
              :disabled="active"
            />
          </label>
          <label class="knob" for="mon-device">
            <span class="knob__label">设备</span>
            <select id="mon-device" v-model="form.device" class="field__control" :disabled="active">
              <option value="0">GPU (0)</option>
              <option value="cpu">CPU</option>
            </select>
          </label>
          <label class="knob" for="mon-tts">
            <span class="knob__label">语音播报</span>
            <select id="mon-tts" v-model="form.enable_tts" class="field__control" :disabled="active">
              <option :value="true">开</option>
              <option :value="false">关</option>
            </select>
          </label>
        </div>

        <p v-if="sourcesError" class="fs-note-error">读不到设备清单：{{ sourcesError }}</p>
        <p class="tray__hint">
          位置名会进事件与语音播报：「检测到明火，位置：东门」。填一次就好，监控期间不能改。
        </p>
      </section>

      <!-- 状态条贴在画面正上方：这页的判断依据是「现在到底在不在看」，
           状态和画面必须同时落在一眼里，不必先读参数再往下找画面 -->
      <section class="panel">
        <div class="panel__head">
          <h2 class="panel__title">画面</h2>
          <!-- 这行是读数条不是面板：它和标题说的是同一件事（这一路在跑什么），
               所以并排而不是另起一行。数字用等宽，纵向扫的时候位置感才不会跳。 -->
          <div class="status" aria-label="监控状态">
            <span class="status__item">
              状态
              <b>{{ status ? (STATUS_CN[status.status] ?? status.status) : '—' }}</b>
            </span>
            <span class="status__item">已处理帧 <b class="num">{{ status?.frames ?? 0 }}</b></span>
            <span class="status__item">断线重连 <b class="num">{{ status?.reconnects ?? 0 }}</b></span>
            <span v-if="active" class="status__item">
              语音 <b :data-testid="'mon-tts-state'">{{ ttsPaused ? '已暂停' : '播报中' }}</b>
            </span>
            <span v-if="status?.camera_name" class="status__item">位置 {{ status.camera_name }}</span>
          </div>
        </div>

        <div class="panel__body panel__body--flush">
          <div class="stage">
            <img v-if="streamUrl && hasFrames" class="stage__img" :src="streamUrl" alt="监控画面" />
            <p v-else-if="active" class="stage__hint">正在接入画面…</p>
            <p v-else class="stage__hint">未在监控。选好视频源后点「开始监控」。</p>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.monitor {
  display: flex;
  flex-direction: column;
  gap: var(--s5);
}

/* 左参数框、右画面框，两框一样高。
   左栏定宽而不是按比例分：启动参数是五个定宽控件加两段文字，
   按 2.4fr : 1fr 分的话画面会被压到放不下 16:9 一屏，而画面才是这页的高频对象。
   stretch（默认值）让右框跟着左框长高，左框跟着画面长高——两框下沿始终对齐。 */
.monitor__grid {
  display: grid;
  grid-template-columns: minmax(0, 380px) minmax(0, 1fr);
  gap: var(--s5);
  align-items: stretch;
}

/* 左框内容偏少（参数就五格），跟着画面长高会空出一大片。
   让提示段落顶到框底，剩下的空白落在两段文字之间，而不是全堆在尾巴上。 */
.tray {
  display: flex;
  flex-direction: column;
}

/* 与右框的 .panel__title 同一档字。两框并排时标题高度不同，
   下面的内容起始线就对不齐——看着像没对齐，其实在读。 */
.tray__title {
  margin: 0 0 var(--s4);
  font-size: var(--t-title);
  font-weight: var(--w-bold);
  color: var(--ink);
  letter-spacing: var(--track-tight);
}

/* 单列一格：左栏只有 380px 出头，再分两列每个控件会被压到放不下地址。
   阈值滑块独占一行是硬要求（base.css:192-198 同一档约束），
   这里天然满足——它总是整行宽。 */
.tray__grid {
  grid-template-columns: minmax(0, 1fr);
}

/* 框被画面拉高时提示贴着底走，空白留在控件与提示之间。 */
.tray__hint {
  margin: var(--s4) 0 0;
  font-size: var(--t-small);
  color: var(--ink-mute);
}

.tray__grid + .fs-note-error {
  margin: var(--s4) 0 0;
}

/* --- 状态读数 ---------------------------------------------------------- */

/* 状态读数在右框标题栏里横排。右栏比通栏窄，两条以上就换行，
   所以尺寸降一档到 --t-micro：数字还是要能一眼扫到，
   但不能为了它把标题栏撑成两行高。 */
.status {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: var(--s2) var(--s4);
  margin: 0;
  font-size: var(--t-micro);
  color: var(--ink-mute);
}

.status__item b {
  margin-left: var(--s1);
  font-weight: var(--w-bold);
  color: var(--ink);
}

/* 画面面板的标题行只放标题：状态条挤在右边，
   标题就不该再占满整行，否则「画面」两个字会被推走。 */
.panel__head {
  align-items: center;
}

/* 面板与它下面的深底区都要能长大：网格把两框拉到一样高之后，
   多出来的高度得顺着「面板 → body → 画面」这三层传下去，
   否则等高只等在边框上，画面还是 340px，深色底与左边白框仍然错一截。 */
.panel {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.panel__body {
  flex: 1;
  display: flex;
}

@media (max-width: 1100px) {
  .monitor__grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .panel__head {
    flex-direction: column;
    align-items: flex-start;
    gap: var(--s2);
  }

  .status {
    justify-content: flex-start;
    /* 单栏时右框宽了，状态条不必再挤，降回常规一档 */
    font-size: var(--t-small);
  }
}

/* 画面区跟着框高走：min-height 兜住空态（没跑起来时那行提示不要贴在框顶），
   flex:1 让它吃掉右框多出来的高度——否则等高只等在框的边框上，
   深底画面还是只有 340px，两框看着仍然不齐。 */
.stage {
  min-height: 340px;
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--stage-void);
  border-radius: 0 0 var(--r-lg) var(--r-lg);
  overflow: hidden;
}

.stage__img {
  display: block;
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
}

.stage__hint {
  margin: 0;
  font-size: var(--t-small);
  color: #8a93a0;
}
</style>
