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
import FrameMarks from '@/components/FrameMarks.vue'

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
 *  直接 as string 会把 Promise 渲染成 "[object Promise]"。 */
const html = computed(() =>
  detail.value ? DOMPurify.sanitize(marked.parse(detail.value.markdown, { async: false })) : '',
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
      <h1 class="head__title">分析报告</h1>
      <p class="head__note">
        基于选定时间段的告警数据生成火情分析与防火建议，生成后归档可回看。
      </p>
    </div>

    <div v-if="error" class="fs-note-error" role="alert">
      <p class="err__msg">{{ error }}</p>
      <p v-if="needsChannelConfig" class="err__hint">
        配置模型通道：环境变量 FIRESIGHT_LLM_PROVIDER（deepseek / siliconflow / claude /
        custom）、FIRESIGHT_LLM_API_KEY，可选 FIRESIGHT_LLM_BASE_URL 与 FIRESIGHT_LLM_MODEL，
        改完重启 fs-serve。
      </p>
    </div>

    <section class="fs-viewfinder gen">
      <FrameMarks />
      <h2 class="fs-viewfinder__title">生成</h2>
      <div class="form">
        <div class="field field--narrow">
          <label class="field__label" for="rp-start">起始日期</label>
          <input id="rp-start" v-model="range.start" type="date" class="field__control" :disabled="running" />
        </div>
        <div class="field field--narrow">
          <label class="field__label" for="rp-end">结束日期</label>
          <input id="rp-end" v-model="range.end" type="date" class="field__control" :disabled="running" />
        </div>
        <div class="actions">
          <button type="button" class="fs-btn-primary" :disabled="busy || running" @click="generate">
            {{ running ? (job?.stage || '生成中…') : '生成报告' }}
          </button>
          <!-- 下载是导航，角色就是 link：与事件导出、权重下载的 <a> 保持一致 -->
          <a
            v-if="detail"
            class="fs-btn-ghost btn-link"
            :href="api.reportDownloadUrl(String(detail.meta.id))"
          >
            下载 .md
          </a>
        </div>
      </div>
    </section>

    <section v-if="detail" class="fs-viewfinder doc">
      <FrameMarks />
      <h2 class="fs-viewfinder__title">{{ String(detail.meta.title || '报告') }}</h2>
      <!-- eslint-disable-next-line vue/no-v-html —— 模型输出按不可信内容处理：
           marked 会原样放行原始 HTML，进 v-html 前先过 DOMPurify 消毒 -->
      <article class="md" v-html="html"></article>
    </section>

    <section v-else class="fs-empty doc">
      选好时间段点「生成报告」。生成的报告会归档，可在右侧列表回看。
    </section>

    <section class="fs-viewfinder history-panel">
      <FrameMarks />
      <h2 class="fs-viewfinder__title">历史报告</h2>
      <ul class="history">
        <li v-for="r in history" :key="r.id" class="history__item">
          <button type="button" class="history__btn" @click="openReport(r.id)">{{ r.title }}</button>
          <span class="history__meta">{{ r.created_at }} · {{ r.model }}</span>
        </li>
        <li v-if="!history.length" class="history__empty">还没有归档报告</li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
/* 生成参数是低频动作（挑个区间、等它跑完），翻历史报告是高频入口。
   所以生成参数与历史报告并排占上方一格，正文独占下一行通栏——
   报告是要读的字，通栏才不至于每行都短得要换行。窄屏折回纵排。 */
.report {
  display: grid;
  grid-template-columns: minmax(0, 300px) minmax(0, 1fr);
  grid-template-areas: 'head head' 'note note' 'gen history' 'doc doc';
  align-content: start;
  gap: var(--sp-5);
}

.head {
  grid-area: head;
}

.report > .fs-note-error {
  grid-area: note;
}

.gen {
  grid-area: gen;
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
    grid-template-areas: 'head' 'note' 'gen' 'doc' 'history';
  }
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

.err__msg {
  margin: 0;
}

.err__hint {
  margin: var(--sp-2) 0 0;
  font-size: var(--fs-xs);
  color: var(--dross);
}

/* 左格只有 300px，两个日期控件横排会挤在一起 */
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

.btn-link {
  display: inline-flex;
  align-items: center;
  text-decoration: none;
}

.md {
  font-size: var(--fs-sm);
  line-height: 1.8;
  color: var(--bone);
}

.md :deep(h2) {
  margin: var(--sp-5) 0 var(--sp-2);
  font-size: var(--fs-md);
  font-weight: 500;
  color: var(--bone);
}

.history {
  margin: 0;
  padding: 0;
  list-style: none;
}

/* 归档条目按账册行读：标题与时间各占一行，时间退到 --fs-xs。
   窄栏里挤成一行的话，标题会被时间戳顶掉尾巴。 */
.history__item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--sp-3) 0;
  border-bottom: var(--hairline);
}

.history__item:last-child {
  border-bottom: 0;
}

.history__btn {
  padding: 0;
  font: inherit;
  font-size: var(--fs-sm);
  color: var(--bone);
  background: transparent;
  border: 0;
  cursor: pointer;
  text-align: left;
}

.history__btn:hover {
  color: var(--accent);
}

.history__meta {
  font-size: var(--fs-xs);
  color: var(--dross);
}

.history__empty {
  padding: var(--sp-3) 0;
  font-size: var(--fs-sm);
  color: var(--dross);
}
</style>
