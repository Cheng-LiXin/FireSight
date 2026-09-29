<script setup lang="ts">
/** 火情分析报告：选时间段 → 生成 → Markdown 展示 / 下载 / 历史回看。
 *
 * 生成是后台任务，这里只做两秒一次的轮询——进度粒度是「一次生成」，
 * 不必上 SSE（与 eval 任务同结论）。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
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

/** 显式 async:false：marked 的重载在无参时返回 string | Promise<string>，
 *  直接 as string 会把 Promise 渲染成 "[object Promise]"。 */
const html = computed(() =>
  detail.value ? marked.parse(detail.value.markdown, { async: false }) : '',
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
    timer = window.setInterval(async () => {
      try {
        job.value = await api.reportJob(jobId)
        if (job.value.status !== 'running') {
          stopPolling()
          busy.value = false
          if (job.value.status === 'done' && job.value.report_id) {
            detail.value = await api.report(job.value.report_id)
            await refreshHistory()
          } else if (job.value.status === 'failed') {
            error.value = job.value.error || '生成失败'
          }
        }
      } catch (e) {
        stopPolling()
        busy.value = false
        error.value = e instanceof Error ? e.message : String(e)
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

    <section class="fs-viewfinder panel">
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
          <!-- role="button"：e2e 按按钮角色取它；href 仍交给浏览器原生下载 -->
          <a
            v-if="detail"
            role="button"
            class="fs-btn-ghost btn-link"
            :href="api.reportDownloadUrl(String(detail.meta.id))"
          >
            下载 .md
          </a>
        </div>
      </div>
    </section>

    <section v-if="detail" class="fs-viewfinder panel">
      <h2 class="fs-viewfinder__title">{{ String(detail.meta.title || '报告') }}</h2>
      <!-- eslint-disable-next-line vue/no-v-html —— 内容是本系统自己生成的 Markdown -->
      <article class="md" v-html="html"></article>
    </section>

    <section v-else class="fs-empty">
      选好时间段点「生成报告」。生成的报告会归档，可在下方列表回看。
    </section>

    <section class="fs-viewfinder panel">
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
.report {
  display: flex;
  flex-direction: column;
  gap: var(--sp-5);
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

.form {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: var(--sp-5);
}

.actions {
  display: flex;
  gap: var(--sp-3);
  margin-left: auto;
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

.history__item {
  display: flex;
  align-items: baseline;
  gap: var(--sp-4);
  padding: var(--sp-2) 0;
  border-bottom: var(--hairline);
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
  margin-left: auto;
  font-size: var(--fs-xs);
  color: var(--dross);
}

.history__empty {
  padding: var(--sp-3) 0;
  font-size: var(--fs-sm);
  color: var(--dross);
}
</style>
