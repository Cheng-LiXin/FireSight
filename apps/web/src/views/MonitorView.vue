<script setup lang="ts">
/** 实时监控：一路 RTSP 常驻推理 + 标注画面推流。
 *
 * 推理、事件、片段、TTS 全走既有流水线，这里只负责启停与看画面。
 * 显存与训练互斥：训练优先，起训练时后端会自动停掉监控。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'

import { api } from '@/api/client'
import type { MonitorStatus } from '@/api/types'
import FrameMarks from '@/components/FrameMarks.vue'

const status = ref<MonitorStatus | null>(null)
const error = ref('')
const busy = ref(false)

const form = ref({
  source: '',
  camera_name: '',
  conf: 0.25,
  device: '0',
  enable_tts: true,
})

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
      source: form.value.source.trim(),
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
      <h1 class="head__title">实时监控</h1>
      <p class="head__note">
        一路 RTSP 常驻推理：告警进事件库、片段留前后各 30 秒、TTS 播报位置。与训练互斥，训练优先。
      </p>
    </div>

    <div v-if="shownError" class="fs-note-error" role="alert">
      {{ shownError }}
    </div>

    <!-- 启动表单 -->
    <section class="fs-viewfinder panel--params">
      <FrameMarks />
      <h2 class="fs-viewfinder__title">启动参数</h2>
      <div class="form">
        <div class="field field--stack field--wide">
          <label class="field__label" for="mon-source">视频源</label>
          <input
            id="mon-source"
            v-model="form.source"
            class="field__control"
            placeholder="rtsp://… 或本地视频/图片目录"
            :disabled="active"
          />
        </div>
        <div class="field field--stack">
          <label class="field__label" for="mon-loc">位置名</label>
          <input
            id="mon-loc"
            v-model="form.camera_name"
            class="field__control"
            placeholder="如：东门"
            :disabled="active"
          />
          <p class="field__hint">进事件与语音播报：「检测到明火，位置：东门」</p>
        </div>
        <div class="field field--stack field--narrow">
          <label class="field__label" for="mon-conf">
            置信度阈值 <b>{{ form.conf.toFixed(2) }}</b>
          </label>
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
        </div>
        <div class="field field--stack field--narrow">
          <label class="field__label" for="mon-device">设备</label>
          <select id="mon-device" v-model="form.device" class="field__control" :disabled="active">
            <option value="0">GPU (0)</option>
            <option value="cpu">CPU</option>
          </select>
        </div>
        <div class="field field--stack field--narrow">
          <label class="field__label" for="mon-tts">语音播报</label>
          <select id="mon-tts" v-model="form.enable_tts" class="field__control" :disabled="active">
            <option :value="true">开</option>
            <option :value="false">关</option>
          </select>
        </div>

        <div class="actions">
          <button
            v-if="active"
            type="button"
            class="fs-btn-ghost"
            :disabled="busy"
            :data-testid="ttsPaused ? 'mon-tts-resume' : 'mon-tts-pause'"
            :title="ttsPaused ? '恢复语音播报' : '暂停语音播报（告警与片段照常留存）'"
            @click="toggleTtsPaused"
          >
            {{ ttsPaused ? '恢复播报' : '暂停播报' }}
          </button>
          <button v-if="!active" type="button" class="fs-btn-primary" :disabled="busy || !form.source.trim()" @click="start">
            开始监控
          </button>
          <button v-else type="button" class="fs-btn-ghost" :disabled="busy" @click="stop">
            停止监控
          </button>
        </div>
      </div>
    </section>

    <!-- 状态条贴在画面正上方：这页的判断依据是「现在到底在不在看」，
         状态和画面必须同时落在一眼里，不必先读参数再往下找画面 -->
    <div class="live">
      <section class="status" aria-label="监控状态">
        <span class="status__item">
          状态
          <b>{{ status ? (STATUS_CN[status.status] ?? status.status) : '—' }}</b>
        </span>
        <span class="status__item">已处理帧 <b class="fs-num">{{ status?.frames ?? 0 }}</b></span>
        <span class="status__item">断线重连 <b class="fs-num">{{ status?.reconnects ?? 0 }}</b></span>
        <span v-if="active" class="status__item">
          语音 <b :data-testid="'mon-tts-state'">{{ ttsPaused ? '已暂停' : '播报中' }}</b>
        </span>
        <span v-if="status?.camera_name" class="status__item">位置 {{ status.camera_name }}</span>
      </section>

      <!-- 画面 -->
      <section class="fs-viewfinder">
        <FrameMarks />
        <h2 class="fs-viewfinder__title">画面</h2>
        <div class="stage">
          <img v-if="streamUrl && hasFrames" class="stage__img" :src="streamUrl" alt="监控画面" />
          <p v-else-if="active" class="stage__hint">正在接入画面…</p>
          <p v-else class="stage__hint">未在监控。填好视频源后点「开始监控」。</p>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
/* 参数在左、画面在右。启动参数是低频动作——填一次就好；
   画面是这页的高频对象，要一直占着主视区。三块全宽纵排的话，
   看画面得先滚过整张参数表，每次看一眼都要滚一次。 */
.monitor {
  display: grid;
  grid-template-columns: minmax(0, 320px) minmax(0, 1fr);
  grid-template-areas: 'head head' 'note note' 'params live';
  align-content: start;
  gap: var(--sp-5);
}

.head {
  grid-area: head;
}

.monitor > .fs-note-error {
  grid-area: note;
}

.panel--params {
  grid-area: params;
}

/* 窄屏折回纵排：参数表横过来会把视频源输入框挤成一条缝 */
@media (max-width: 1100px) {
  .monitor {
    grid-template-columns: minmax(0, 1fr);
    grid-template-areas: 'head' 'note' 'params' 'live';
  }
}

.live {
  grid-area: live;
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
  min-width: 0;
}

.head__title {
  margin: 0;
  font-size: var(--fs-xl);
  font-weight: 500;
  color: var(--bone);
}

.head__note {
  margin: var(--sp-2) 0 0;
  font-size: var(--fs-sm);
  color: var(--dross);
}

/* 左栏里的参数不再横铺：320px 宽放五个控件，横排会挤成两行错位。
   .field--stack 负责把每个控件的 180px 最小宽也一并放开。 */
.form {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: var(--sp-4);
}

.actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-3);
}

.status {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-5);
  font-size: var(--fs-sm);
  color: var(--dross);
}

.status__item b {
  margin-left: var(--sp-1);
  font-weight: 500;
  color: var(--bone);
}

.stage {
  min-height: 320px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--soot);
  border: 1px dashed var(--ink-4);
  border-radius: var(--radius-sm);
  overflow: hidden;
}

.stage__img {
  display: block;
  max-width: 100%;
  max-height: 70vh;
}

.stage__hint {
  margin: 0;
  font-size: var(--fs-sm);
  color: var(--dross);
}
</style>
