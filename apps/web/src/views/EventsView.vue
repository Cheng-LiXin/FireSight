<script setup lang="ts">
/** 告警事件：筛选分页、片段回放与下载、四态处置、导出 CSV。
 *
 * 筛选在**后端**做（SQL）。服务层曾经是「取前 N 条再在 Python 里过滤」，那样第 2 页
 * 会重复或漏记录、总数也算不出来，筛稀有类别时还可能明明有匹配却返回空。
 *
 * 类别中文名从 `/api/system/status` 取，不在前端再抄一份契约。
 *
 * 处置状态是四态（待处置 / 已确认 / 误报 / 已处置）。原来是「待复核 / 已标误报」
 * 两态，只答得「是不是误报」；值班的人还要能区分「刚响还没看」和「确认过了但
 * 现场还没处理完」。状态与误报位由后端保证同步，这里不自己推导。
 */
import { computed, onMounted, ref, watch } from 'vue'

import { api, ApiError } from '@/api/client'
import type { AlertEventInfo, EventReviewStatus, EventStatusFilter } from '@/api/types'
import FrameMarks from '@/components/FrameMarks.vue'

const items = ref<AlertEventInfo[]>([])
const total = ref(0)
/** 全库事件数（不带筛选）。用来区分「一条都没有」和「当前筛选没匹配」 */
const libraryCount = ref(0)
const labelCn = ref<Record<string, string>>({})
const busy = ref(false)
/** 首轮取数是否已完成。E2E 需要它：挂载后、请求返回前页面正好是空态，
 *  只看「账册或空态」会被那个瞬时状态骗过。 */
const loaded = ref(false)
const error = ref<string | null>(null)

const filters = ref({
  keyword: '',
  label: '',
  status: 'all' as EventStatusFilter,
  start: '',
  end: '',
})
const pageSize = ref(20)
const offset = ref(0)

/** 正在回放的事件；null 表示回放区收起 */
const playing = ref<AlertEventInfo | null>(null)
/** 浏览器解不开这个片段。历史片段里有 mp4v 编码的，<video> 会报
 *  DEMUXER_ERROR_NO_SUPPORTED_STREAMS——此时要给出去路，而不是一块黑屏。 */
const clipFailed = ref(false)

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))
const pageNo = computed(() => Math.floor(offset.value / pageSize.value) + 1)
const filtered = computed(
  () =>
    Boolean(filters.value.keyword || filters.value.label || filters.value.start ||
      filters.value.end || filters.value.status !== 'all'),
)

// --------------------------------------------------------------------------
// 处置状态
// --------------------------------------------------------------------------

/** 四个状态的文案与配色。
 *
 * 配色是刻意的，不是装饰：
 * - 待处置 深墨（--ink-1）：醒目，因为它是**唯一还要人动手的**状态；
 * - 已确认 告警红（--alarm）：全站唯一彩色额度，给「确实有事、已认定」的那一条；
 * - 误报 浅灰（--ink-3）：已处理完，不该再抢注意力；
 * - 已处置 中灰（--ink-2）：看过、且收尾了。
 * 红只给「confirmed」而不是给所有非误报状态，否则误报和已处置也会染红，
 * 彩色额度就废了。 */
const STATUS_META: Record<EventReviewStatus, { label: string; cls: string }> = {
  pending: { label: '待处置', cls: 'tag--todo' },
  confirmed: { label: '已确认', cls: 'tag--alarm' },
  false_positive: { label: '误报', cls: 'tag--done' },
  resolved: { label: '已处置', cls: 'tag--done' },
}

const STATUS_ORDER: EventReviewStatus[] = ['pending', 'confirmed', 'false_positive', 'resolved']

function statusMeta(s: string): { label: string; cls: string } {
  // 后端保证是合法值；这里的兜底让「意外的空串」显示成可读的待处置，
  // 而不是渲染出一个空白标签。
  return STATUS_META[s as EventReviewStatus] ?? STATUS_META.pending
}

function describe(e: unknown): string {
  return e instanceof ApiError ? e.message : '加载失败'
}

function cn(name: string): string {
  return labelCn.value[name] ?? name
}

/** 2026-09-27T15:03:07 -> 2026-09-27 15:03:07（等宽字下更好读，也省掉时区歧义） */
function stamp(ts: string): string {
  return (ts || '').replace('T', ' ')
}

async function load(): Promise<void> {
  busy.value = true
  error.value = null
  try {
    const base = { ...filters.value, limit: pageSize.value }
    let page = await api.events({ ...base, offset: offset.value })
    // 处置状态变化会让当前筛选下的「总数」变小，当前 offset 可能因此越界。
    // 夹回最后一页重取一次——否则用户看到一张空表，会以为数据没了。
    if (page.items.length === 0 && page.total > 0 && offset.value > 0) {
      offset.value = Math.max(0, (Math.ceil(page.total / pageSize.value) - 1) * pageSize.value)
      page = await api.events({ ...base, offset: offset.value })
    }
    items.value = page.items
    total.value = page.total
  } catch (e) {
    error.value = describe(e)
    items.value = []
    total.value = 0
  } finally {
    busy.value = false
    loaded.value = true
  }
}

/** 翻页。offset 不挂 watcher——否则筛选重置 offset 时会连带触发两次请求。 */
function goTo(next: number): void {
  offset.value = Math.max(0, next)
  void load()
}

function resetFilters(): void {
  filters.value = { keyword: '', label: '', status: 'all', start: '', end: '' }
}

/** 正在改状态的事件号。用来只给这一行按钮上锁——
 *  全表 busy 会让 20 行一起变灰，点第二个也看不出自己点的是哪条。 */
const savingId = ref('')

async function markFalsePositive(row: AlertEventInfo): Promise<void> {
  await setStatus(row, 'false_positive')
}

async function setStatus(row: AlertEventInfo, next: EventReviewStatus): Promise<void> {
  if (savingId.value) return
  savingId.value = row.event_id
  try {
    await api.eventSetStatus(row.event_id, next)
    // 整表重载而不是就地改：处置状态决定这条记录在四个状态筛选下的归属，
    // 就地改会让当前列表与状态自相矛盾——筛「待处置」时刚标完的那条还留在页上。
    await load()
  } catch (e) {
    error.value = describe(e)
  } finally {
    savingId.value = ''
  }
}

function toggleClip(row: AlertEventInfo): void {
  const same = playing.value?.event_id === row.event_id
  playing.value = same ? null : row
  clipFailed.value = false
}

watch(filters, () => {
  offset.value = 0
  void load()
}, { deep: true })
watch(pageSize, () => {
  offset.value = 0
  void load()
})

onMounted(async () => {
  // 类别中文名与总数一起拿；拿不到不影响列表本身
  try {
    const st = await api.systemStatus()
    labelCn.value = Object.fromEntries(st.classes.map((c) => [c.name, c.name_cn]))
    libraryCount.value = st.event_count
  } catch {
    /* 标签退化成英文契约名，仍然可用 */
  }
  await load()
})
</script>

<template>
  <div class="events" :data-loaded="loaded">
    <p v-if="error" class="fs-note-error" role="alert">{{ error }}</p>

    <!-- 片段回放：与识别页共用「取景窗」容器语言 -->
    <section v-if="playing" class="fs-viewfinder">
      <FrameMarks />
      <div class="fs-block__head">
        <h2 class="fs-viewfinder__title">
          片段回放 · <span class="mono">{{ playing.event_id }}</span>
        </h2>
        <button type="button" class="fs-btn-ghost" @click="playing = null">关闭</button>
      </div>
      <video
        :src="api.eventClipUrl(playing.event_id)"
        class="clip"
        controls
        autoplay
        muted
        playsinline
        @error="clipFailed = true"
      ></video>
      <p v-if="clipFailed" class="clip__note clip__note--bad">
        这个片段浏览器解不开（历史片段里有 mp4v 编码，浏览器只认 H.264 / VP8 / VP9 / AV1）。
        <a :href="api.eventClipDownloadUrl(playing.event_id)" download>下载后用本地播放器打开</a>。
        新写出的片段已改用可播放编码。
      </p>
      <p v-else class="clip__note">
        告警前后各若干帧，25 fps 写出。片段未留存的事件没有回放按钮。
      </p>
    </section>

    <!-- 筛选 -->
    <section class="fs-block">
      <div class="fs-block__head">
        <h2 class="fs-block__title">筛选</h2>
        <button type="button" class="fs-btn-ghost" :disabled="busy" @click="load()">
          {{ busy ? '加载中…' : '刷新' }}
        </button>
      </div>

      <div class="bar fs-tray">
        <label class="field">
          <span class="field__label">关键词</span>
          <input
            v-model.trim="filters.keyword"
            class="field__control"
            placeholder="消息 / 位置 / 事件号"
          />
        </label>
        <label class="field field--narrow">
          <span class="field__label">类别</span>
          <select v-model="filters.label" class="field__control">
            <option value="">全部</option>
            <option v-for="(c, n) in labelCn" :key="n" :value="n">{{ c }}</option>
          </select>
        </label>
        <label class="field field--narrow">
          <span class="field__label">状态</span>
          <select v-model="filters.status" class="field__control">
            <option value="all">全部</option>
            <option v-for="s in STATUS_ORDER" :key="s" :value="s">
              {{ statusMeta(s).label }}
            </option>
          </select>
        </label>
        <label class="field field--narrow">
          <span class="field__label">起始日期</span>
          <input v-model="filters.start" class="field__control" type="date" />
        </label>
        <label class="field field--narrow">
          <span class="field__label">结束日期</span>
          <input v-model="filters.end" class="field__control" type="date" />
        </label>
        <label class="field field--narrow">
          <span class="field__label">每页</span>
          <select v-model.number="pageSize" class="field__control">
            <option :value="20">20</option>
            <option :value="50">50</option>
            <option :value="100">100</option>
          </select>
        </label>

        <div class="bar__actions">
          <button type="button" class="fs-btn-ghost" :disabled="!filtered" @click="resetFilters">
            清空条件
          </button>
          <a class="fs-btn-primary" :href="api.eventsExportUrl(filters)">导出 CSV</a>
        </div>
      </div>
    </section>

    <!-- 列表 -->
    <section class="fs-block">
      <div class="fs-block__head">
        <h2 class="fs-block__title">
          事件 <span class="fs-num">{{ total }}</span> 条<template v-if="filtered">
            （已筛选，全库 <span class="fs-num">{{ libraryCount }}</span> 条）</template
          >
        </h2>
        <span v-if="total" class="pager__info">
          第 <span class="fs-num">{{ pageNo }}</span> / <span class="fs-num">{{ pageCount }}</span> 页
        </span>
      </div>

      <!-- fs-ledger--lg：事件表字号 16px，比检测页的账册大一档。
           这张表是值班的人盯着最久的一屏，13px 看久了费眼；
           做成修饰类而不是改全局 .fs-ledger，是为了不连带放大检测框账册。 -->
      <table v-if="items.length" class="fs-ledger fs-ledger--lg">
        <thead>
          <tr>
            <th>时间</th>
            <th>类别</th>
            <th class="is-num">置信度</th>
            <th>消息</th>
            <th>片段</th>
            <th>状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="r in items"
            :key="r.event_id"
            :data-event-id="r.event_id"
            :class="{ 'is-fp': r.is_false_positive, 'is-playing': playing?.event_id === r.event_id }"
          >
            <td class="mono">{{ stamp(r.ts) }}</td>
            <td>
              <span
                v-for="lab in r.labels"
                :key="lab"
                class="tag"
                :class="lab === 'fire' ? 'tag--alarm' : 'tag--calm'"
              >{{ cn(lab) }}</span>
            </td>
            <td class="is-num">{{ r.conf.toFixed(3) }}</td>
            <td class="msg">{{ r.message }}</td>
            <td>
              <div v-if="r.has_clip" class="acts">
                <button
                  type="button"
                  class="fs-btn-ghost fs-btn-ghost--tiny"
                  :data-testid="`clip-${r.event_id}`"
                  @click="toggleClip(r)"
                >
                  {{ playing?.event_id === r.event_id ? '收起' : '回放' }}
                </button>
                <!-- 下载走浏览器原生下载，不走 JS 搬几十 MB 视频 -->
                <a
                  class="fs-btn-ghost fs-btn-ghost--tiny"
                  :href="api.eventClipDownloadUrl(r.event_id)"
                  :data-testid="`clip-dl-${r.event_id}`"
                  >下载</a
                >
              </div>
              <span v-else class="muted">未留存</span>
            </td>
            <td>
              <span class="tag" :class="statusMeta(r.review_status).cls">{{
                statusMeta(r.review_status).label
              }}</span>
            </td>
            <td>
              <div class="acts">
                <!-- 按当前状态条件渲染，而不是把动作都摆出来再 disable：
                     6 个按钮全列着再灰掉 4 个，比只给 2 个可用动作更难读，
                     也更容易让人以为界面坏了。 -->
                <button
                  v-if="r.review_status !== 'confirmed' && r.review_status !== 'resolved'"
                  type="button"
                  class="fs-btn-ghost fs-btn-ghost--tiny"
                  :data-testid="`confirm-${r.event_id}`"
                  :disabled="savingId === r.event_id"
                  @click="setStatus(r, 'confirmed')"
                >
                  确认
                </button>
                <button
                  v-if="r.review_status === 'confirmed'"
                  type="button"
                  class="fs-btn-ghost fs-btn-ghost--tiny"
                  :data-testid="`resolve-${r.event_id}`"
                  :disabled="savingId === r.event_id"
                  @click="setStatus(r, 'resolved')"
                >
                  标记已处置
                </button>
                <button
                  v-if="r.review_status !== 'false_positive'"
                  type="button"
                  class="fs-btn-ghost fs-btn-ghost--tiny"
                  :data-testid="`fp-${r.event_id}`"
                  :disabled="savingId === r.event_id"
                  @click="markFalsePositive(r)"
                >
                  标为误报
                </button>
                <button
                  v-if="r.review_status === 'false_positive'"
                  type="button"
                  class="fs-btn-ghost fs-btn-ghost--tiny"
                  :data-testid="`unfp-${r.event_id}`"
                  :disabled="savingId === r.event_id"
                  @click="setStatus(r, 'pending')"
                >
                  撤销误报
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>

      <p v-else-if="!busy" class="fs-empty">
        <template v-if="libraryCount === 0">
          还没有任何告警记录。跑一次推理（<code>fs-infer</code>）后事件会出现在这里。
        </template>
        <template v-else>当前条件下没有匹配的事件。</template>
      </p>

      <div v-if="total > pageSize" class="pager">
        <button
          type="button"
          class="fs-btn-ghost"
          :disabled="offset === 0 || busy"
          @click="goTo(offset - pageSize)"
        >
          上一页
        </button>
        <button
          type="button"
          class="fs-btn-ghost"
          :disabled="offset + pageSize >= total || busy"
          @click="goTo(offset + pageSize)"
        >
          下一页
        </button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.events {
  display: flex;
  flex-direction: column;
  gap: var(--sp-5);
}

/* --- 筛选栏 ----------------------------------------------------------- */

.bar {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: var(--sp-4);
}

.bar__actions {
  display: flex;
  gap: var(--sp-3);
  margin-left: auto;
}

/* 导出是链接而不是按钮：交给浏览器原生下载，前端不解析 CSV */
.bar__actions a {
  display: inline-flex;
  align-items: center;
  text-decoration: none;
}

/* --- 列表 ------------------------------------------------------------- */

/* 事件表放大一档：13px -> 16px。只给这张表加，不动全局 .fs-ledger，
   否则检测页的检测框账册会一起被放大——那张表列多，一放大就横向溢出。 */
.fs-ledger--lg {
  font-size: var(--fs-md);
}

.fs-ledger--lg th,
.fs-ledger--lg td {
  padding: var(--sp-3) var(--sp-4);
}

/* 表头跟不上正文放大：14px 表头配 16px 正文会显得表头更弱 */
.fs-ledger--lg th {
  font-size: var(--fs-sm);
}

.msg {
  max-width: 380px;
}

/* 操作列的按钮组。换行而不是挤成一行：动作多了以后挤在一起，
   值班的人要在几个 4px 宽的按钮里找对那个。 */
.acts {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-1);
  align-items: center;
}

.acts a.fs-btn-ghost {
  text-decoration: none;
}

.mono {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}

.muted {
  color: var(--dross);
}

.tag {
  display: inline-block;
  margin-right: var(--sp-2);
  padding: 1px var(--sp-2);
  font-size: var(--fs-xs);
  color: var(--dross);
  border: 1px solid var(--cinder);
  border-radius: var(--radius-pill);
  white-space: nowrap;
}

/* 明火用告警红——这是全界面唯一允许的彩色，也正是它该出现的地方 */
.tag--alarm {
  color: var(--alarm);
  border-color: var(--alarm);
}

.tag--calm {
  color: var(--bone);
}

/* 待处置：还没人动手，所以要醒目（深墨边框 + 深墨字）。 */
.tag--todo {
  color: var(--ink-0);
  border-color: var(--ink-1);
  font-weight: 500;
}

/* 误报 / 已处置：都已经被处理过，压成灰色，不该再抢值班人的注意力。 */
.tag--done {
  color: var(--ink-3);
  border-color: var(--cinder);
}

/* 已标误报的行压暗：它已经复核过了，不该和待办的抢注意力 */
.fs-ledger tbody tr.is-fp td {
  color: var(--dross);
}

/* 放大后的按钮也跟着长一档，24px 在 16px 字号下显得挤 */
.fs-ledger--lg .fs-btn-ghost--tiny {
  height: 28px;
  font-size: var(--fs-sm);
}

.fs-ledger tbody tr.is-playing td {
  background: var(--highlight);
}

/* --- 分页 ------------------------------------------------------------- */

.pager {
  display: flex;
  justify-content: center;
  gap: var(--sp-3);
  margin-top: var(--sp-4);
}

.pager__info {
  font-size: var(--fs-xs);
  color: var(--dross);
}

/* --- 回放 ------------------------------------------------------------- */

.clip {
  width: 100%;
  max-height: 460px;
  background: #000;
  border: var(--hairline);
  border-radius: var(--radius);
  overflow: hidden;
}

.clip__note {
  margin: var(--sp-2) 0 0;
  padding-left: var(--sp-3);
  font-size: var(--fs-xs);
  color: var(--dross);
  border-left: 2px solid var(--cinder);
}

/* 解不开的片段：用告警红——这确实需要人处理一下 */
.clip__note--bad {
  color: var(--bone);
  border-left-color: var(--alarm);
}

.clip__note--bad a {
  color: var(--alarm);
}
</style>
