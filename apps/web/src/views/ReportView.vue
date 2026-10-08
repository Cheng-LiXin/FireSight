<script setup lang="ts">
/** 火情分析报告：选时间段 → 生成 → Markdown 展示 / 下载 / 历史回看。
 *
 * 生成是后台任务，这里只做两秒一次的轮询——进度粒度是「一次生成」，
 * 不必上 SSE（与 eval 任务同结论）。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import DOMPurify from 'dompurify'
import { marked } from 'marked'

import { api } from '@/api/client'
import type { ReportDetail, ReportJob, ReportListItem } from '@/api/types'

function thisMonthRange(): { start: string; end: string } {
  const d = new Date()
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const last = new Date(y, d.getMonth() + 1, 0).getDate()
  return { start: `${y}-${m}-01`, end: `${y}-${m}-${String(last).padStart(2, '0')}` }
}

const range = ref(thisMonthRange())
const job = ref<ReportJob | null>(null)
const detail = ref<ReportDetail | null>(null)
const history = ref<ReportListItem[]>([])
const error = ref('')
const busy = ref(false)

const running = computed(() => job.value?.status === 'running')

/** 报告正文是**模型输出**（还掺着事件的 location / message 原文），按不可信内容
 *  处理：marked 自 v4 起原样放行原始 HTML，必须过 DOMPurify 消毒再进 v-html。
 *  显式 async:false：marked 的重载在无参时返回 string | Promise<string>，
 *  直接 as string 会把 Promise 渲染成 "[object Promise]"。
 *  gfm:false：开了 gfm 之后 marked 会把正文里的「mAP@0.5」「recall@0.75」
 *  认成邮箱地址，自动包成 `<a href="mailto:...">`——报告里每一个指标名都会变成
 *  一个点下去触发邮件客户端的链接。指标名不是邮箱。 */
const html = computed(() =>
  detail.value
    ? DOMPurify.sanitize(
        marked.parse(detail.value.markdown, { async: false, gfm: false }),
      )
    : '',
)

/** 未配通道时补一段配置说明——只甩一个环境变量名，用户还是不知道从哪配起。 */
const needsChannelConfig = computed(() => error.value.includes('FIRESIGHT_LLM_API_KEY'))

let timer: number | undefined

/** 页面已卸载后就别再起轮询：POST 在卸载后才返回时，没人会再来 clearInterval。 */
let alive = true

function stopPolling() {
  if (timer !== undefined) {
    window.clearInterval(timer)
    timer = undefined
  }
}

async function refreshHistory() {
  try {
    history.value = await api.reports()
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

async function generate() {
  busy.value = true
  error.value = ''
  detail.value = null
  stopPolling()
  try {
    const created = await api.createReport(range.value.start, range.value.end)
    if (!alive) return
    job.value = created
    // 任务 id 抓成局部常量：轮询期间用户可能点开历史报告把 job 置空，
    // 下一轮不能再去读 job.value!.id
    const jobId = created.id
    let inFlight = false
    timer = window.setInterval(async () => {
      if (inFlight) return // 上一轮还没回来：轮询比 2 秒慢时不叠请求
      inFlight = true
      try {
        const next = await api.reportJob(jobId)
        if (!alive) return
        job.value = next
        if (next.status !== 'running') {
          stopPolling()
          busy.value = false
          if (next.status === 'done' && next.report_id) {
            const loaded = await api.report(next.report_id)
            if (!alive) return
            detail.value = loaded
            await refreshHistory()
          } else if (next.status === 'failed') {
            error.value = next.error || '生成失败'
          }
        }
      } catch (e) {
        // 一次轮询失败（网络抖动 / 后端重启）就停表，但不能把表单卡死在
        // running：清掉任务让按钮与日期立刻可用，错误走横幅，用户可直接重试
        stopPolling()
        busy.value = false
        job.value = null
        error.value = e instanceof Error ? e.message : String(e)
      } finally {
        inFlight = false
      }
    }, 2000)
  } catch (e) {
    // 400（无事件 / 未配通道）把后端原话显示出来
    error.value = e instanceof Error ? e.message : String(e)
    busy.value = false
  }
}

async function openReport(id: string) {
  error.value = ''
  try {
    detail.value = await api.report(id)
    job.value = null
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

onMounted(refreshHistory)
onUnmounted(() => {
  alive = false
  stopPolling()
})
</script>

<template>
  <div class="report">
    <div class="head">
      <div>
        <h1 class="head__title">分析报告</h1>
        <p class="head__sub">
          基于选定时间段的告警数据生成火情分析与防火建议，生成后归档可回看。
        </p>
      </div>
    </div>

    <div v-if="error" class="fs-note-error" role="alert">
      <p class="err__msg">{{ error }}</p>
      <p v-if="needsChannelConfig" class="err__hint">
        配置模型通道：环境变量 FIRESIGHT_LLM_PROVIDER（deepseek / siliconflow / claude /
        custom）、FIRESIGHT_LLM_API_KEY，可选 FIRESIGHT_LLM_BASE_URL 与 FIRESIGHT_LLM_MODEL，
        改完重启 fs-serve。
      </p>
    </div>

    <!-- 生成参数是低频动作（挑个区间、等它跑完），翻历史报告是高频入口。
         所以两者并排占上方一格，正文独占下一行通栏——报告是要读的字，
         通栏才不至于每行都短得要换行。窄屏折回纵排。 -->
    <section class="tray" aria-label="生成参数">
      <div class="tray__grid">
        <label class="knob" for="rp-start">
          <span class="knob__label">起始日期</span>
          <input id="rp-start" v-model="range.start" type="date" class="field__control" :disabled="running" />
        </label>
        <label class="knob" for="rp-end">
          <span class="knob__label">结束日期</span>
          <input id="rp-end" v-model="range.end" type="date" class="field__control" :disabled="running" />
        </label>
      </div>

      <div class="tray__actions">
        <button type="button" class="btn btn--primary" :disabled="busy || running" @click="generate">
          {{ running ? (job?.stage || '生成中…') : '生成报告' }}
        </button>
      </div>
    </section>

    <section class="panel history-panel">
      <div class="panel__head">
        <h2 class="panel__title">历史报告</h2>
        <span class="panel__hint">{{ history.length }} 份归档</span>
      </div>
      <div class="panel__body panel__body--flush">
        <ul class="history">
          <li v-for="r in history" :key="r.id" class="history__item">
            <button type="button" class="history__btn" @click="openReport(r.id)">{{ r.title }}</button>
            <span class="history__meta">{{ r.created_at }} · {{ r.model }}</span>
          </li>
          <li v-if="!history.length" class="history__empty">还没有归档报告</li>
        </ul>
      </div>
    </section>

    <!-- 正文面板的外壳不随有没有内容切换：有报告时是正文，
         没报告时是同一块面板里的一句提示。换外壳会让边框和高度跳一下，
         而用户点「生成」之后正好盯着这块地方。 -->
    <section class="panel doc">
      <div class="panel__head">
        <h2 class="panel__title">{{ detail ? String(detail.meta.title || '报告') : '报告正文' }}</h2>
        <div class="doc__acts">
          <a
            v-if="detail"
            class="btn btn--ghost btn-link"
            :href="api.reportDownloadUrl(String(detail.meta.id))"
          >
            下载 .md
          </a>
        </div>
      </div>
      <div class="panel__body">
        <!-- eslint-disable-next-line vue/no-v-html —— 模型输出按不可信内容处理：
             marked 会原样放行原始 HTML，进 v-html 前先过 DOMPurify 消毒 -->
        <article v-if="detail" class="md" v-html="html"></article>
        <p v-else class="empty">
          选好时间段点「生成报告」。生成的报告会归档，可在上面的列表回看。
        </p>
      </div>
    </section>
  </div>
</template>

<style scoped>
.report {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 320px);
  grid-template-areas: 'head head' 'note note' 'gen history' 'doc doc';
  align-content: start;
  gap: var(--s5);
}

.head {
  grid-area: head;
}

.report > .fs-note-error {
  grid-area: note;
}

.tray {
  grid-area: gen;
}

.tray__grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.history-panel {
  grid-area: history;
}

.doc {
  grid-area: doc;
}

@media (max-width: 1100px) {
  .report {
    grid-template-columns: minmax(0, 1fr);
    grid-template-areas: 'head' 'note' 'gen' 'history' 'doc';
  }
}

.err__msg {
  margin: 0;
}

.err__hint {
  margin: var(--s2) 0 0;
  font-size: var(--t-small);
  color: var(--ink-mute);
}

/* 正文比周边说明大一号、行距更松：这一块是拿来读的，
   不是拿来扫的。跟同页的表单控件用同一档字号会让整页只有一种节奏。 */
.md {
  max-width: 78ch;
  font-size: var(--t-body);
  line-height: 1.85;
  color: var(--ink);
}

.md :deep(h2) {
  margin: var(--s6) 0 var(--s3);
  font-size: 1.15rem;
  font-weight: var(--w-bold);
  color: var(--ink);
  letter-spacing: var(--track-tight);
}

.md :deep(h2:first-child) {
  margin-top: 0;
}

.md :deep(p),
.md :deep(ul),
.md :deep(ol) {
  margin: 0 0 var(--s3);
}

.md :deep(li) {
  margin-bottom: var(--s1);
}

.md :deep(code) {
  padding: 1px var(--s2);
  font-family: var(--font-num);
  font-size: 0.9em;
  background: var(--surface-sunk);
  border-radius: var(--r-sm);
}

.doc__acts {
  display: flex;
  gap: var(--s2);
}

.history {
  margin: 0;
  padding: 0;
  list-style: none;
}

/* 归档条目按账册行读：标题与时间各占一行，时间退到最小号。
   窄栏里挤成一行的话，标题会被时间戳顶掉尾巴。 */
.history__item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--s3) var(--s4);
  border-bottom: 1px solid var(--line-faint);
}

.history__item:last-child {
  border-bottom: 0;
}

.history__btn {
  padding: 0;
  font: inherit;
  font-size: var(--t-small);
  font-weight: var(--w-medium);
  color: var(--ink);
  background: transparent;
  border: 0;
  cursor: pointer;
  text-align: left;
}

.history__btn:hover {
  color: var(--signal-deep);
}

.history__meta {
  font-size: var(--t-micro);
  color: var(--ink-mute);
}

.history__empty {
  padding: var(--s4);
  font-size: var(--t-small);
  color: var(--ink-mute);
}
</style>
