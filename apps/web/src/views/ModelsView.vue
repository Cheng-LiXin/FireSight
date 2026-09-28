<script setup lang="ts">
/** 模型库：归档列表、设为当前、下载权重、多权重对比评估。
 *
 * 「当前权重」以前等于「最新归档」，于是一次实验跑完就会悄悄顶掉线上模型——
 * 2026-09-27 那次 30 轮、imgsz=512 的实验（mAP50 0.7432）就这样把 0.7675 顶掉了，
 * 页面上没有任何提示。现在由用户显式指定，并且把「为什么是它」显示出来。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { api, ApiError } from '@/api/client'
import type { ActiveModel, EvalJobInfo, ModelInfo } from '@/api/types'

const rows = ref<ModelInfo[]>([])
const active = ref<ActiveModel | null>(null)
const busy = ref(false)
/** 首轮取数是否已完成。E2E 需要它：挂载后、请求返回前页面正是空态，
 *  只看「账册或空态」会被那个瞬时状态骗过（告警事件页踩过一次，这里差点又踩）。 */
const loaded = ref(false)
const error = ref<string | null>(null)

/** 勾选参与对比的归档。至少两个才有意义。 */
const picked = ref<string[]>([])
const evalImgsz = ref(640)
const evalDevice = ref('0')
const job = ref<EvalJobInfo | null>(null)
const starting = ref(false)

let timer: ReturnType<typeof setInterval> | undefined

const pickedRows = computed(() => rows.value.filter((r) => picked.value.includes(r.run_id)))
const canCompare = computed(() => picked.value.length >= 2 && job.value?.status !== 'running')

function describe(e: unknown): string {
  return e instanceof ApiError ? e.message : '操作失败'
}

function fix(v: number | null, digits = 4): string {
  return v === null || v === undefined ? '—' : v.toFixed(digits)
}

/** 2026-09-27T17:49:22 -> 2026-09-27 17:49 */
function stamp(iso: string | null): string {
  return iso ? iso.replace('T', ' ').slice(0, 16) : '—'
}

/** 权重文件名（run_id 可能含斜杠，取最后一段更像文件名） */
function shortName(runId: string): string {
  const parts = runId.split('/')
  return parts.length > 1 ? `${parts[0]}/${parts[parts.length - 1]}` : runId
}

// --------------------------------------------------------------------------
// 加载
// --------------------------------------------------------------------------

async function load(): Promise<void> {
  busy.value = true
  error.value = null
  try {
    const [list, act] = await Promise.all([api.models(), api.activeModel()])
    rows.value = list
    active.value = act
    // 归档被删掉后，勾选里可能留着已不存在的 run_id
    picked.value = picked.value.filter((id) => list.some((r) => r.run_id === id))
  } catch (e) {
    error.value = describe(e)
  } finally {
    busy.value = false
    loaded.value = true
  }
}

async function setActive(runId: string): Promise<void> {
  error.value = null
  try {
    active.value = await api.setActiveModel(runId)
    await load()
  } catch (e) {
    error.value = describe(e)
  }
}

async function clearActive(): Promise<void> {
  error.value = null
  try {
    active.value = await api.clearActiveModel()
    await load()
  } catch (e) {
    error.value = describe(e)
  }
}

function toggle(runId: string): void {
  picked.value = picked.value.includes(runId)
    ? picked.value.filter((id) => id !== runId)
    : [...picked.value, runId]
}

// --------------------------------------------------------------------------
// 对比评估（后台任务 + 轮询）
// --------------------------------------------------------------------------

function stopPolling(): void {
  if (timer) {
    clearInterval(timer)
    timer = undefined
  }
}

async function poll(id: string): Promise<void> {
  try {
    const next = await api.evalJob(id)
    job.value = next
    if (next.status !== 'running') stopPolling()
  } catch (e) {
    error.value = describe(e)
    stopPolling()
  }
}

async function startCompare(): Promise<void> {
  if (!canCompare.value) return
  starting.value = true
  error.value = null
  try {
    const started = await api.startEval({
      run_ids: picked.value,
      imgsz: evalImgsz.value,
      device: evalDevice.value,
    })
    job.value = started
    stopPolling()
    // 评估粒度是「逐个模型」，两秒问一次足够，也不必占一条长连接
    timer = setInterval(() => void poll(started.id), 2000)
  } catch (e) {
    error.value = describe(e)
  } finally {
    starting.value = false
  }
}

// --------------------------------------------------------------------------
// 删除（移进回收目录）
// --------------------------------------------------------------------------

/** 待确认删除的归档；null 表示没有弹窗。 */
const pending = ref<ModelInfo | null>(null)
/** 是否连同训练现场一起删。归档与现场是两份数据，默认只动归档。 */
const withRun = ref(false)
const removing = ref(false)
/** 上一次删除的回执。删完必须说清「搬走了什么、当前权重变成了谁」。 */
const receipt = ref<string | null>(null)

function askDelete(run: ModelInfo): void {
  pending.value = run
  withRun.value = false
  receipt.value = null
  error.value = null
}

function cancelDelete(): void {
  pending.value = null
  withRun.value = false
}

async function confirmDelete(): Promise<void> {
  const target = pending.value
  if (!target) return
  removing.value = true
  error.value = null
  try {
    const res = await api.deleteModel(target.run_id, withRun.value)
    receipt.value = res.note || '已移入回收目录'
    pending.value = null
    withRun.value = false
    await load()
    // 删掉的可能正是正在看的那一次对比的参评权重，结果表留着会指向不存在的模型
    if (job.value && (job.value.run_ids ?? []).includes(target.run_id)) {
      job.value = null
      stopPolling()
    }
  } catch (e) {
    error.value = describe(e)
  } finally {
    removing.value = false
  }
}

/** 某一列的最佳值。忽略失败行与缺失值——「没测出来」不参与评比。 */
function bestOf(key: 'mAP50' | 'mAP50_95' | 'precision' | 'recall'): number | null {
  const values = (job.value?.results ?? [])
    .filter((r) => !r.error && r[key] !== null)
    .map((r) => r[key] as number)
  return values.length ? Math.max(...values) : null
}

function isBest(r: { [k: string]: unknown; error: string | null }, key: string): boolean {
  const best = bestOf(key as 'mAP50')
  return !r.error && best !== null && r[key] === best
}

onMounted(async () => {
  await load()
  // 服务重启过就把上次的任务表接回来，免得以为结果没了
  try {
    const jobs = await api.evalJobs()
    const last = jobs[0]
    if (last) {
      job.value = last
      if (last.status === 'running') timer = setInterval(() => void poll(last.id), 2000)
    }
  } catch {
    /* 拿不到历史任务不影响主流程 */
  }
})

onBeforeUnmount(stopPolling)
</script>

<template>
  <div class="models" :data-loaded="loaded">
    <p v-if="error" class="fs-note-error" role="alert">{{ error }}</p>

    <!-- 当前权重 -->
    <section class="fs-block">
      <div class="fs-block__head">
        <h2 class="fs-block__title">当前权重</h2>
        <button type="button" class="fs-btn-ghost" :disabled="busy" @click="load">
          {{ busy ? '加载中…' : '刷新' }}
        </button>
      </div>

      <p v-if="!rows.length" class="fs-empty">
        还没有归档训练。去<RouterLink to="/training">训练中心</RouterLink>跑一次，结束后会自动归档。
      </p>

      <template v-else>
        <p class="now">
          <span class="now__id mono">{{ active?.run_id ?? '—' }}</span>
          <span v-if="active?.source === 'pinned'" class="tag tag--ok">已指定</span>
          <span v-else-if="active?.source === 'fallback-newest'" class="tag">取最新</span>
          <span v-else-if="active?.source === 'fallback-missing'" class="tag tag--warn">指定已失效</span>
          <button
            v-if="active?.pinned"
            type="button"
            class="fs-btn-ghost fs-btn-ghost--tiny"
            @click="clearActive"
          >
            清除指定
          </button>
        </p>
        <!-- 「为什么是它」必须说出来：静默回落会让人以为设置丢了 -->
        <p class="now__note">{{ active?.note }}</p>
      </template>
    </section>

    <!-- 归档列表 -->
    <section v-if="rows.length" class="fs-block">
      <div class="fs-block__head">
        <h2 class="fs-block__title">
          归档 <span class="fs-num">{{ rows.length }}</span> 份
        </h2>
        <span class="hint">勾选两份以上可比对（已选 <span class="fs-num">{{ picked.length }}</span>）</span>
      </div>

      <table class="fs-ledger" data-table="archives">
        <thead>
          <tr>
            <th class="is-pick"></th>
            <th>归档</th>
            <th>数据集</th>
            <th class="is-num">轮数</th>
            <th class="is-num">mAP@50</th>
            <th class="is-num">mAP@50-95</th>
            <th class="is-num">精确率</th>
            <th class="is-num">召回率</th>
            <th class="is-num">体积</th>
            <th>归档时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="r in rows"
            :key="r.run_id"
            :data-run-id="r.run_id"
            :class="{ 'is-active': r.is_active }"
          >
            <td class="is-pick">
              <input
                type="checkbox"
                :checked="picked.includes(r.run_id)"
                :aria-label="`选择 ${r.run_id}`"
                @change="toggle(r.run_id)"
              />
            </td>
            <td class="mono run">
              {{ shortName(r.run_id) }}
              <span v-if="r.is_active" class="tag tag--ok">当前</span>
            </td>
            <td>{{ r.dataset ?? '—' }}</td>
            <td class="is-num">{{ r.epochs ?? '—' }}</td>
            <td class="is-num strong">{{ fix(r.mAP50) }}</td>
            <td class="is-num">{{ fix(r.mAP50_95) }}</td>
            <td class="is-num">{{ fix(r.precision, 3) }}</td>
            <td class="is-num">{{ fix(r.recall, 3) }}</td>
            <td class="is-num">{{ r.size_mb === null ? '—' : `${r.size_mb} MB` }}</td>
            <td class="mono">{{ stamp(r.created) }}</td>
            <td class="acts">
              <button
                type="button"
                class="fs-btn-ghost fs-btn-ghost--tiny"
                :disabled="r.is_active"
                @click="setActive(r.run_id)"
              >
                设为当前
              </button>
              <a class="fs-btn-ghost fs-btn-ghost--tiny" :href="api.modelDownloadUrl(r.run_id)">
                下载
              </a>
              <button
                type="button"
                class="fs-btn-ghost fs-btn-ghost--tiny is-danger"
                @click="askDelete(r)"
              >
                删除
              </button>
            </td>
          </tr>
        </tbody>
      </table>

      <!-- 删除确认：必须说清「删的是什么、还剩什么、能不能捞回来」。
           权重是几十分钟到两小时的产物，误点一次的代价太大。 -->
      <div v-if="pending" class="confirm" role="dialog" aria-label="确认删除">
        <p class="confirm__title">
          删除归档 <span class="mono">{{ shortName(pending.run_id) }}</span>？
        </p>
        <p class="confirm__body">
          归档权重与训练现场是两份数据，可以分开处置。
          <strong>归档</strong>是能部署的 <span class="mono">best.pt</span>，
          <strong>训练现场</strong>是曲线、图件与检查点。
        </p>
        <label class="confirm__pick">
          <input v-model="withRun" type="checkbox" />
          连同训练现场一起删除（含曲线与检查点，删了就再也续不了训）
        </label>
        <p class="confirm__body">
          两者都会<strong>移进回收目录</strong>（<span class="mono">models/.trash</span>），
          页面上不再出现、磁盘也不会立即释放，删错了可以手工捞回来。
        </p>
        <p v-if="pending.is_active" class="confirm__warn">
          这是当前权重，删除后会自动回落到最新的归档。
        </p>
        <div class="confirm__acts">
          <button type="button" class="fs-btn-ghost" :disabled="removing" @click="cancelDelete">
            取消
          </button>
          <button
            type="button"
            class="fs-btn-primary is-danger"
            :disabled="removing"
            @click="confirmDelete"
          >
            {{ removing ? '正在删除…' : '确认删除' }}
          </button>
        </div>
      </div>

      <p v-if="receipt" class="receipt" role="status">{{ receipt }}</p>
    </section>

    <!-- 对比评估 -->
    <section v-if="rows.length" class="fs-block">
      <div class="fs-block__head">
        <h2 class="fs-block__title">多权重对比评估</h2>
      </div>

      <div class="bar">
        <p class="bar__picked">
          <template v-if="pickedRows.length">
            <span v-for="r in pickedRows" :key="r.run_id" class="chip mono">{{ shortName(r.run_id) }}</span>
          </template>
          <template v-else>从上面的表里勾选两份以上归档</template>
        </p>

        <label class="field field--narrow">
          <span class="field__label">imgsz</span>
          <input v-model.number="evalImgsz" class="field__control" type="number" min="32" max="2048" />
        </label>
        <label class="field field--narrow">
          <span class="field__label">设备</span>
          <select v-model="evalDevice" class="field__control">
            <option value="0">GPU (0)</option>
            <option value="cpu">CPU</option>
          </select>
        </label>

        <div class="bar__actions">
          <button
            type="button"
            class="fs-btn-primary"
            :disabled="!canCompare || starting"
            @click="startCompare"
          >
            {{ starting ? '正在发起…' : '开始对比' }}
          </button>
        </div>
      </div>

      <p class="note">
        同一份 val 集上逐个评估，阈值口径与归档 metrics.txt 一致（conf=0.001 / iou=0.7，
        mAP 计算口径），所以**模型之间**可比。<br />
        但评估用的 imgsz 可能与训练时不同（这里统一用上面的值），因此绝对数值会和归档里
        训练期验证的数字略有差异——对比看的是同一条起跑线上的排序，不是复现归档的数。
        选 CPU 会非常慢，val 集有上千张图。
      </p>

      <!-- 进度 -->
      <div v-if="job" class="job">
        <p class="job__line">
          <template v-if="job.status === 'running'">
            正在评估 <span class="fs-num">{{ job.done }}</span> / <span class="fs-num">{{ job.total }}</span> …
          </template>
          <template v-else-if="job.status === 'done'">评估完成。</template>
          <template v-else>评估失败。</template>
          <span class="job__meta mono">
            {{ job.dataset ?? '—' }} · {{ job.split }} · imgsz={{ job.imgsz }} ·
            {{ stamp(job.started_at) }}
          </span>
        </p>
        <p v-if="job.status === 'running'" class="job__warn">
          评估占着 GPU，这期间发起训练会返回 409。
        </p>
        <p v-if="job.error" class="fs-note-error" role="alert">{{ job.error }}</p>

        <table v-if="job.results.length" class="fs-ledger" data-table="eval">
          <thead>
            <tr>
              <th>模型</th>
              <th class="is-num">mAP@50</th>
              <th class="is-num">mAP@50-95</th>
              <th class="is-num">精确率</th>
              <th class="is-num">召回率</th>
              <th class="is-num">推理 ms</th>
              <th>烟雾 / 明火 mAP@50-95</th>
              <th>备注</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in job.results" :key="r.run_id" :data-eval-id="r.run_id">
              <td class="mono run">{{ shortName(r.run_id) }}</td>
              <td class="is-num" :class="{ strong: isBest(r, 'mAP50') }">
                {{ fix(r.mAP50) }}<span v-if="isBest(r, 'mAP50')" class="tag tag--best">最佳</span>
              </td>
              <td class="is-num" :class="{ strong: isBest(r, 'mAP50_95') }">
                {{ fix(r.mAP50_95) }}
              </td>
              <td class="is-num" :class="{ strong: isBest(r, 'precision') }">
                {{ fix(r.precision, 3) }}
              </td>
              <td class="is-num" :class="{ strong: isBest(r, 'recall') }">
                {{ fix(r.recall, 3) }}
              </td>
              <td class="is-num">{{ fix(r.speed_ms?.inference ?? null, 1) }}</td>
              <td class="is-num">
                <template v-if="r.per_class && Object.keys(r.per_class).length">
                  {{ fix(r.per_class.smoke ?? null, 3) }} / {{ fix(r.per_class.fire ?? null, 3) }}
                </template>
                <template v-else>—</template>
              </td>
              <td class="note-cell">{{ r.error ?? '' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<style scoped>
.models {
  display: flex;
  flex-direction: column;
  gap: var(--sp-5);
}

.mono {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}

.run {
  white-space: nowrap;
}

.strong {
  font-weight: 600;
}

.hint,
.note {
  font-size: var(--fs-xs);
  color: var(--dross);
}

.note {
  margin: var(--sp-3) 0 0;
  padding-left: var(--sp-3);
  border-left: 2px solid var(--cinder);
}

/* --- 当前权重 --------------------------------------------------------- */

.now {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  margin: 0;
}

.now__id {
  font-size: var(--fs-lg);
}

.now__note {
  margin: var(--sp-2) 0 0;
  font-size: var(--fs-xs);
  color: var(--dross);
}

/* --- 表格 ------------------------------------------------------------- */

.is-pick {
  width: 28px;
}

/* 当前权重那一行：它决定了识别页用哪份权重，值得一眼看到 */
.fs-ledger tbody tr.is-active td {
  background: var(--highlight);
}

.acts {
  display: flex;
  gap: var(--sp-2);
  white-space: nowrap;
}

.acts a {
  display: inline-flex;
  align-items: center;
  text-decoration: none;
}

/* 删除是破坏性操作，但它是可恢复的（移进回收目录、不是抹掉），所以只把文字
   染成告警色、不填成实心红按钮——实心红会让它比「设为当前」更抢眼，而后者
   才是这个页面最常做的事。 */
.is-danger {
  color: var(--alarm);
}

.fs-btn-primary.is-danger {
  color: var(--soot);
  background: var(--alarm);
  border-color: var(--alarm);
}

/* --- 删除确认 --------------------------------------------------------- */

.confirm {
  margin-top: var(--sp-3);
  padding: var(--sp-3);
  background: var(--highlight);
  border: 1px solid var(--alarm);
  border-radius: var(--radius);
}

.confirm__title {
  margin: 0 0 var(--sp-2);
  font-weight: 600;
  color: var(--bone);
}

.confirm__body {
  margin: 0 0 var(--sp-2);
  font-size: var(--fs-xs);
  line-height: 1.6;
  color: var(--dross);
}

.confirm__pick {
  display: flex;
  gap: var(--sp-2);
  align-items: flex-start;
  margin: 0 0 var(--sp-2);
  font-size: var(--fs-xs);
  line-height: 1.6;
  color: var(--bone);
  cursor: pointer;
}

.confirm__warn {
  margin: 0 0 var(--sp-2);
  font-size: var(--fs-xs);
  color: var(--alarm);
}

.confirm__acts {
  display: flex;
  gap: var(--sp-2);
  margin-top: var(--sp-3);
}

.receipt {
  margin: var(--sp-3) 0 0;
  font-size: var(--fs-xs);
  line-height: 1.6;
  color: var(--bone);
  border-left: 2px solid var(--cinder);
  padding-left: var(--sp-3);
}

.tag {
  display: inline-block;
  margin-left: var(--sp-2);
  padding: 1px var(--sp-2);
  font-size: var(--fs-xs);
  color: var(--dross);
  border: 1px solid var(--cinder);
}

.tag--ok {
  color: var(--bone);
}

/* 指定的归档失效了：这是需要人去处理的状态，用告警红 */
.tag--warn {
  color: var(--alarm);
  border-color: var(--alarm);
}

.tag--best {
  font-weight: 400;
  color: var(--bone);
}

/* --- 对比 ------------------------------------------------------------- */

.bar {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: var(--sp-4);
  padding: var(--sp-3) var(--sp-4);
  background: var(--ash);
  border: var(--hairline);
}

.bar__picked {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-2);
  margin: 0;
  font-size: var(--fs-sm);
  color: var(--dross);
}

.bar__actions {
  margin-left: auto;
}

.chip {
  padding: 1px var(--sp-2);
  font-size: var(--fs-xs);
  color: var(--bone);
  border: 1px solid var(--cinder);
}

.job {
  margin-top: var(--sp-4);
}

.job__line {
  display: flex;
  align-items: baseline;
  gap: var(--sp-3);
  margin: 0 0 var(--sp-2);
}

.job__meta {
  font-size: var(--fs-xs);
  color: var(--dross);
}

.job__warn {
  margin: 0 0 var(--sp-3);
  font-size: var(--fs-xs);
  color: var(--dross);
}

.note-cell {
  max-width: 240px;
  font-size: var(--fs-xs);
  color: var(--dross);
}
</style>
