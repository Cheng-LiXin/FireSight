<script setup lang="ts">
/** 识别检测。
 *
 * 这是 FireSight 最该拿得出手的一页，所以它不做「双栏两张图」——
 * 把图放到版面的主位，右边只留读数与账本。图是主角。
 *
 * 参数（权重、置信度、IoU、设备）仍然横贯在页面最上方，作用于下方全部内容：
 * 不把筛选器塞进图区，那样会让「当前用的是哪套参数」变得不确定。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { api, ApiError, type InferParams } from '@/api/client'
import { CLASS_CN } from '@/api/types'
import type { BatchResult, InferResult, ModelInfo, SourceOption } from '@/api/types'

import CountUp from '@/components/CountUp.vue'
import DetectionOverlay from '@/components/DetectionOverlay.vue'

const models = ref<ModelInfo[]>([])
const sources = ref<SourceOption[]>([])

/** 划分的中文名。数据集名本身可能已含 "train"（如 D-Fire/train），
 *  用中文能避免 "D-Fire/train / train" 这种读不通的重复。 */
const SPLIT_CN: Record<string, string> = {
  train: '训练集',
  val: '验证集',
  test: '测试集',
}

const weights = ref('')
const conf = ref(0.25)
const iou = ref(0.6)
const device = ref('0')
const mode = ref<'single' | 'batch'>('single')

const file = ref<File | null>(null)
const previewUrl = ref('')
const result = ref<InferResult | null>(null)

/** 主位在两种渲染之间切换：默认是前端自己画的 SVG 叠加层（可联动、
 *  标签是中文），另一种是后端烧好像素的 annotated 位图（可与前端
 *  的画法逐像素对照，也保留 ultralytics 原始输出的样子）。 */
const renderMode = ref<'overlay' | 'backend'>('overlay')

/** 当前被指到的框序号。账册行与叠加框共用它：指针在任一侧，
 *  另一侧就点亮同一个框。它是全局的一份状态而不是组件内的，
 *  因为「联动」这件事本身就跨了两个 DOM 子树。 */
const hotBox = ref<number | null>(null)

/** 入表门槛。低于它的框仍然画在图上，账本里也不隐藏，只压暗——
 *  后端已经按 conf 参数筛过一道了，前端再藏一层等于凭空丢信息。
 *  复核时最该看的恰恰是那些低分的框。 */
const LEDGER_AT = 0.7

const sourceKey = ref('')
const limit = ref(20)
const batch = ref<BatchResult | null>(null)

const busy = ref(false)
const dragging = ref(false)
const error = ref<string | null>(null)
/** 权重列表要等 /api/models 回来才知道，期间不能开跑。 */
const modelsLoading = ref(true)
/** 权重就绪前收到的文件先记在这里，就绪后补跑——丢弃用户的投递更糟。 */
const pendingRun = ref(false)

const noModels = computed(() => !modelsLoading.value && models.value.length === 0)

const params = computed<InferParams>(() => ({
  weights: weights.value,
  conf: conf.value,
  iou: iou.value,
  device: device.value,
}))

function describe(e: unknown): string {
  return e instanceof ApiError ? e.message : '操作失败'
}

onMounted(async () => {
  try {
    const [m, s] = await Promise.all([api.models(), api.sources()])
    models.value = m
    sources.value = s
    weights.value = (m.find((x) => x.is_active) ?? m[0])?.run_id ?? ''
    sourceKey.value = s[0]?.key ?? ''
  } catch (e) {
    error.value = describe(e)
  } finally {
    modelsLoading.value = false
  }
})

onBeforeUnmount(() => {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
})

function pickFile(f: File): void {
  if (noModels.value) {
    error.value = '没有可用的模型，请先运行 fs-train 训练一次'
    return
  }
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  file.value = f
  previewUrl.value = URL.createObjectURL(f)
  result.value = null
  // 换图必须清掉高亮：旧的框序号已经指向另一张图上的另一批框
  hotBox.value = null
  if (modelsLoading.value) {
    // 权重还没到，先挂起；下面 watch 会在就绪后补跑
    pendingRun.value = true
    return
  }
  void runSingle()
}

// 权重就绪后补跑挂起的那次——比直接丢弃用户的投递好
watch([weights, modelsLoading], () => {
  if (pendingRun.value && weights.value && !modelsLoading.value) {
    pendingRun.value = false
    void runSingle()
  }
})

function onDrop(e: DragEvent): void {
  dragging.value = false
  const f = e.dataTransfer?.files?.[0]
  if (f) pickFile(f)
}

const DROP_HINT = computed(() => {
  if (modelsLoading.value) return '正在加载模型列表…'
  if (noModels.value) return '没有可用的模型'
  return '把图像拖到这里，或点击选择'
})

const DROP_SUB = computed(() =>
  noModels.value ? '请先运行 fs-train 训练一次' : '支持 JPG / PNG / BMP / WebP',
)

function onPick(e: Event): void {
  const f = (e.target as HTMLInputElement).files?.[0]
  if (f) pickFile(f)
}

async function runSingle(): Promise<void> {
  if (!file.value) return
  // 不能在这里静默 return：权重还没到时什么都不做，用户看到的是「图传上去了
  // 但永远没结果、也没有报错」。必须说明为什么没跑。
  if (!weights.value) {
    error.value = modelsLoading.value
      ? '模型列表尚未加载完成，请稍候重试'
      : '没有可用的模型，请先运行 fs-train 训练一次'
    return
  }
  busy.value = true
  error.value = null
  try {
    result.value = await api.inferImage(file.value, params.value)
  } catch (e) {
    error.value = describe(e)
    result.value = null
  } finally {
    busy.value = false
  }
}

async function runBatch(): Promise<void> {
  if (!sourceKey.value || !weights.value) return
  busy.value = true
  error.value = null
  try {
    batch.value = await api.inferBatch(sourceKey.value, params.value, limit.value)
  } catch (e) {
    error.value = describe(e)
    batch.value = null
  } finally {
    busy.value = false
  }
}

/** 数值固定到 3 位：后端回传的是原始浮点串，界面不该显示 16 位小数。 */
function fix(v: number | null | undefined, digits = 3): string {
  return v === null || v === undefined ? '—' : v.toFixed(digits)
}

/** 账本列出全部检出，索引保持 detections 里的原序号 i——
 *  联动靠它对着图上的框，所以不能重新编号。 */
const ledger = computed(() =>
  (result.value?.detections ?? []).map((d, i) => ({ d, i })),
)

const weakCount = computed(() => ledger.value.filter(({ d }) => d.conf < LEDGER_AT).length)
</script>

<template>
  <div class="page">
    <header class="head">
      <div>
        <h1 class="head__title">识别检测</h1>
        <p class="head__sub">
          选一张图，看检测框落在哪里。框与账本双向联动——账本行亮起，图上对应的框也亮起。
        </p>
      </div>

      <div class="picks" role="radiogroup" aria-label="输入方式">
        <button
          type="button"
          class="pick"
          :class="{ 'is-on': mode === 'single' }"
          role="radio"
          :aria-checked="mode === 'single'"
          @click="mode = 'single'"
        >
          单张图像
        </button>
        <button
          type="button"
          class="pick"
          :class="{ 'is-on': mode === 'batch' }"
          role="radio"
          :aria-checked="mode === 'batch'"
          @click="mode = 'batch'"
        >
          目录批量
        </button>
      </div>
    </header>

    <p v-if="error" class="fs-note-error" role="alert">{{ error }}</p>

    <!-- 参数托盘：横贯页面，作用于下方全部内容。
         四个控件是一组「这次推理的条件」，所以摆在一起，而不是散进图区。 -->
    <section class="knobs" aria-label="推理参数">
      <label class="knob">
        <span class="knob__label">权重</span>
        <select v-model="weights" class="field__control" :disabled="modelsLoading || noModels">
          <option v-if="!models.length" value="">无已归档模型</option>
          <option v-for="m in models" :key="m.run_id" :value="m.run_id">
            {{ m.run_id }}
          </option>
        </select>
      </label>

      <div class="knob">
        <label class="knob__label" for="conf">
          置信度阈值 <b class="num">{{ conf.toFixed(2) }}</b>
        </label>
        <input
          id="conf"
          v-model.number="conf"
          type="range"
          min="0.05"
          max="0.95"
          step="0.05"
          :style="{ '--fill': `${((conf - 0.05) / 0.9) * 100}%` }"
        />
      </div>

      <div class="knob">
        <label class="knob__label" for="iou">
          IoU 阈值 <b class="num">{{ iou.toFixed(2) }}</b>
        </label>
        <input
          id="iou"
          v-model.number="iou"
          type="range"
          min="0.1"
          max="0.95"
          step="0.05"
          :style="{ '--fill': `${((iou - 0.1) / 0.85) * 100}%` }"
        />
      </div>

      <label class="knob">
        <span class="knob__label">设备</span>
        <select v-model="device" class="field__control">
          <option value="0">GPU (0)</option>
          <option value="cpu">CPU</option>
        </select>
      </label>
    </section>

    <!-- 单张：图占主位，右栏只放读数与账本 -->
    <template v-if="mode === 'single'">
      <div class="work">
        <section class="stage">
          <div class="stage__bar">
            <h2 class="stage__title">检测结果</h2>

            <div class="stage__meta">
              <span v-if="result" class="num">{{ result.width }} × {{ result.height }}</span>

              <!-- 切换器只作用于主位这一块，所以摆在它的标题行里 -->
              <span class="picks" role="radiogroup" aria-label="结果渲染方式">
                <button
                  type="button"
                  class="pick pick--sm"
                  :class="{ 'is-on': renderMode === 'overlay' }"
                  role="radio"
                  :aria-checked="renderMode === 'overlay'"
                  @click="renderMode = 'overlay'"
                >
                  叠加图
                </button>
                <button
                  type="button"
                  class="pick pick--sm"
                  :class="{ 'is-on': renderMode === 'backend' }"
                  role="radio"
                  :aria-checked="renderMode === 'backend'"
                  @click="renderMode = 'backend'"
                >
                  后端标注图
                </button>
              </span>
            </div>
          </div>

          <!-- 空态：还没投图 -->
          <div
            v-if="!file"
            class="dropzone"
            :class="{ 'is-over': dragging, 'is-blocked': noModels }"
            @dragover.prevent="dragging = !noModels"
            @dragleave="dragging = false"
            @drop.prevent="onDrop"
          >
            <label class="dropzone__inner">
              <input type="file" accept="image/*" hidden :disabled="noModels" @change="onPick" />
              <span class="dropzone__hint">{{ DROP_HINT }}</span>
              <span class="dropzone__sub">{{ DROP_SUB }}</span>
            </label>
          </div>

          <div v-else class="stage__body">
            <!-- 有检出就画叠加层，哪怕一个框都没有也画：
                 空图与「还没推理」是两回事，后者才有占位文案。 -->
            <DetectionOverlay
              v-if="result && renderMode === 'overlay'"
              class="pair__item"
              :image-src="previewUrl"
              :width="result.width"
              :height="result.height"
              :detections="result.detections"
              :hot="hotBox"
              @hover="hotBox = $event"
            />
            <img
              v-else-if="result"
              class="pair__item"
              :src="result.annotated ?? ''"
              alt="标注后的检测结果"
            />
            <div v-else class="stage__pending">{{ busy ? '推理中…' : '未检出或尚未推理' }}</div>

            <p class="stage__foot">
              框是浏览器按坐标即时画的，不是后端烧进位图的。缩放窗口时框会跟着图一起变。
            </p>

            <div class="stage__actions">
              <label class="btn btn--ghost">
                <input type="file" accept="image/*" hidden @change="onPick" />
                换一张
              </label>
              <button type="button" class="btn btn--primary" :disabled="busy" @click="runSingle">
                {{ busy ? '推理中…' : '重新推理' }}
              </button>
            </div>
          </div>
        </section>

        <aside class="side">
          <section class="readouts">
            <div class="readout">
              <span class="readout__label">检出目标</span>
              <span class="readout__value">
                <CountUp :value="result ? result.count : '—'" />
                <span class="readout__unit">个</span>
              </span>
            </div>
            <div class="readout">
              <span class="readout__label">最高置信度</span>
              <!-- digits 必须给。CountUp 默认 0 位小数，会把 0.991 四舍五入成
                   1——那不是「最高置信度」，那是在说有一成把握以外的东西。 -->
              <span class="readout__value">
                <CountUp :value="result ? result.max_conf : '—'" :digits="3" />
              </span>
            </div>
            <div class="readout">
              <span class="readout__label">耗时</span>
              <span class="readout__value">
                <CountUp :value="result ? result.elapsed_ms : '—'" />
                <span class="readout__unit">ms</span>
              </span>
            </div>
          </section>

          <section class="ledger">
            <div class="panel__head">
              <h2 class="panel__title">检测框</h2>
              <span class="panel__hint">
                <template v-if="weakCount > 0">
                  低于 {{ LEDGER_AT.toFixed(2) }} 的 {{ weakCount }} 个已压暗
                </template>
                <template v-else>按置信度从高到低</template>
              </span>
            </div>

            <!-- 表格直接坐在面板里，末行的圆角由 .sheet--flush 兜住 -->
            <table v-if="result?.detections.length" class="sheet sheet--flush fs-ledger">
              <thead>
                <tr>
                  <th>类别</th>
                  <th class="is-num">置信度</th>
                  <th class="is-num">x1</th>
                  <th class="is-num">y1</th>
                  <th class="is-num">x2</th>
                  <th class="is-num">y2</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="{ d, i } in ledger"
                  :key="i"
                  :class="{ 'is-weak': d.conf < LEDGER_AT, 'is-hot': hotBox === i }"
                  @mouseenter="hotBox = i"
                  @mouseleave="hotBox = null"
                >
                  <td>
                    <i class="dot" :class="`dot--${d.name}`" />{{ d.name_cn }}
                  </td>
                  <td class="is-num">{{ fix(d.conf) }}</td>
                  <td class="is-num">{{ d.xyxy[0].toFixed(0) }}</td>
                  <td class="is-num">{{ d.xyxy[1].toFixed(0) }}</td>
                  <td class="is-num">{{ d.xyxy[2].toFixed(0) }}</td>
                  <td class="is-num">{{ d.xyxy[3].toFixed(0) }}</td>
                </tr>
              </tbody>
            </table>

            <p v-else class="empty">
              {{ result ? '本帧未检出明火或烟雾。' : '上传图像后，检测框会列在这里。' }}
            </p>
          </section>

          <p class="side__note">
            阈值调低可提高召回、增加误报。明火为弱类，召回偏低是可预期的。
          </p>
        </aside>
      </div>
    </template>

    <!-- 批量 -->
    <template v-else>
      <section class="knobs" aria-label="批量参数">
        <label class="knob">
          <span class="knob__label">来源</span>
          <select v-model="sourceKey" class="field__control">
            <option v-if="!sources.length" value="">无可批量推理的目录</option>
            <option v-for="s in sources" :key="s.key" :value="s.key">
              {{ s.dataset }} · {{ SPLIT_CN[s.split] ?? s.split }}（{{
                s.image_count.toLocaleString()
              }}
              张）
            </option>
          </select>
        </label>

        <label class="knob">
          <span class="knob__label">最多处理</span>
          <input v-model.number="limit" class="field__control" type="number" min="1" max="300" />
        </label>

        <button type="button" class="btn btn--primary" :disabled="busy || !sourceKey" @click="runBatch">
          {{ busy ? '推理中…' : '开始批量识别' }}
        </button>
      </section>

      <template v-if="batch">
        <section class="readouts">
          <div class="readout">
            <span class="readout__label">处理张数</span>
            <span class="readout__value"><CountUp :value="batch.files" /></span>
          </div>
          <div class="readout">
            <span class="readout__label">有检出</span>
            <span class="readout__value">
              <CountUp :value="batch.hit_files" />
              <span class="readout__unit">张</span>
            </span>
          </div>
          <div class="readout">
            <span class="readout__label">目标总数</span>
            <span class="readout__value"><CountUp :value="batch.total_dets" /></span>
          </div>
          <div class="readout">
            <span class="readout__label">平均耗时</span>
            <span class="readout__value">
              <CountUp :value="batch.avg_ms" :digits="1" />
              <span class="readout__unit">ms</span>
            </span>
          </div>
        </section>

        <section class="panel">
          <div class="panel__head">
            <h2 class="panel__title">逐图结果</h2>
          </div>
          <div class="fs-scroll">
            <table class="sheet fs-ledger">
              <thead>
                <tr>
                  <th>文件</th>
                  <th>类别</th>
                  <th class="is-num">目标数</th>
                  <th class="is-num">最高置信度</th>
                  <th class="is-num">耗时 ms</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="r in batch.rows" :key="r.file">
                  <td class="mono">{{ r.file }}</td>
                  <td>
                    <template v-if="r.classes.length">
                      <i
                        v-for="c in r.classes"
                        :key="c"
                        class="dot"
                        :class="`dot--${c}`"
                        :title="CLASS_CN[c] ?? c"
                      />{{ r.classes.map((c) => CLASS_CN[c] ?? c).join(' / ') }}
                    </template>
                    <span v-else class="none-inline">—</span>
                  </td>
                  <td class="is-num">{{ r.count }}</td>
                  <td class="is-num">{{ fix(r.max_conf) }}</td>
                  <td class="is-num">{{ r.elapsed_ms.toFixed(1) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      </template>

      <p v-else class="empty">选择来源后开始批量识别，逐图结果会列在这里。</p>
    </template>
  </div>
</template>

<style scoped>
.page {
  max-width: 1360px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--s5);
}

/* --- 参数托盘 ------------------------------------------------------------
   四个控件构成「这次推理的条件」，所以摆在一起。
   托盘不挂取景框的刻度角：刻度角标记「正在被观察的对象」，
   而这里是用户要操作的输入控件，语义不同。 */
.knobs {
  display: grid;
  grid-template-columns: minmax(200px, 1.4fr) repeat(2, minmax(160px, 1fr)) minmax(140px, 0.8fr);
  gap: var(--s5);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  padding: var(--s4) var(--s5) var(--s5);
  box-shadow: var(--lift-1);
}

.knob {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--s2);
}

.knob__label {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--s2);
  font-size: var(--t-small);
  color: var(--ink-body);
}

.knob__label b {
  font-family: var(--font-num);
  color: var(--signal-deep);
  font-variant-numeric: tabular-nums;
}

/* --- 工作区：图占大头，右栏固定宽 ---------------------------------------
   不写 align-items:start（默认就是 stretch）：两栏并排时右栏比画面矮一大截，
   下沿不齐看着像没做完——实测画面 704px、右栏 542px，差 162px。 */
.work {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 380px;
  gap: var(--s5);
}

.stage {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  overflow: hidden;
  box-shadow: var(--lift-1);
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.stage__bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--s4);
  padding: var(--s4) var(--s5);
  border-bottom: 1px solid var(--line-faint);
}

.stage__title {
  font-size: var(--t-title);
  font-weight: var(--w-bold);
  color: var(--ink);
  letter-spacing: var(--track-tight);
}

.stage__meta {
  display: flex;
  align-items: center;
  gap: var(--s3);
  font-size: var(--t-micro);
  color: var(--ink-faint);
}

.pick--sm {
  padding: 4px var(--s3);
  font-size: var(--t-micro);
}

.stage__body {
  display: flex;
  flex-direction: column;
}

/* 主位的图。类名沿用 .pair__item：它历史上是「对比区里的一个图格」，
   而 detect.spec 里那条「两张图不得拉伸变形」的回归仍在盯着这个类。
   形态从两栏并排改成单栏占主位，但断言的意图原样保留。 */
.stage__body > .pair__item {
  display: block;
  width: 100%;
}

.stage__foot {
  margin: 0;
  padding: var(--s3) var(--s5);
  border-top: 1px solid var(--line-faint);
  font-size: var(--t-micro);
  color: var(--ink-faint);
  background: var(--surface-sunk);
}

.stage__actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--s2);
  padding: var(--s3) var(--s5) var(--s4);
}

.stage__pending {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 220px;
  font-size: var(--t-small);
  color: var(--ink-faint);
  background: var(--surface-sunk);
}

/* --- 投递区 -------------------------------------------------------------- */
.dropzone {
  border: 1px dashed var(--line-strong);
  border-radius: 0;
  transition: border-color var(--dur-1) var(--ease-out),
    background-color var(--dur-1) var(--ease-out);
}

.dropzone.is-over {
  border-color: var(--signal);
  background: var(--signal-soft);
}

/* 权重列表没就绪或压根没有模型时，明确压暗并说明原因——
   可点却什么都不发生的控件比不可点更糟。 */
.dropzone.is-blocked {
  opacity: 0.55;
}

.dropzone__inner {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--s2);
  min-height: 300px;
  cursor: pointer;
  text-align: center;
}

.dropzone.is-blocked .dropzone__inner {
  cursor: not-allowed;
}

.dropzone__inner:hover .dropzone__hint {
  color: var(--signal-deep);
}

.dropzone__hint {
  font-size: var(--t-body);
  color: var(--ink);
  transition: color var(--dur-1) var(--ease-out);
}

.dropzone__sub {
  font-size: var(--t-micro);
  color: var(--ink-faint);
}

/* --- 右栏 ---------------------------------------------------------------- */
.side {
  display: flex;
  flex-direction: column;
  gap: var(--s5);
  min-width: 0;
}

/* 右栏里的读数是纵排，不套 .readouts 的「每条成卡」配方——
   同一页里两种读数容器并排会互相削弱。
   而且 380px 要塞三条，三等分后每条只剩 120px，「最高置信度」会折行、
   单位「ms」会被挤到第二行。改成标签在左、数字在右的一行式，
   数字右对齐后纵列成一条基线，扫读反而更快。 */
/* 上下留白交给第一行和最后一行自己带（下面 .side .readout 的 padding），
   容器本身不留——否则容器 8px + 首行 12px = 20px，末行 16px，
   上沿 28px、下沿 35px，肉眼能看出头轻脚重。 */
.side .readouts {
  display: flex;
  flex-direction: column;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  padding: 0;
  box-shadow: var(--lift-1);
}

/* 真的把「每条成卡」拆掉，而不是只改 padding。
   base.css:355-364 给 .readouts > .readout 写了白底 + 边框 + 圆角 + 阴影，
   这套配方是给横排的（容器没有盒子，卡片自己就是盒子）；
   竖排的这里容器已经是盒子了，卡片再来一层就变成「盒中盒」——
   实测文字到内框只剩 1px，看着像文字贴在虚框上。
   所以清成透明无边，只留行间的发丝线，纵列靠线分区而不是靠框。 */
.side .readouts > .readout {
  background: transparent;
  border: 0;
  border-radius: 0;
  box-shadow: none;
}

/* 标签提到 --t-small：它是这一行里唯一的说明文字，
   用 --t-micro（11px）在 380px 的窄栏里几乎读不下去。 */
.side .readout__label {
  font-size: var(--t-small);
}

.side .readout {
  display: grid;
  grid-template-columns: 1fr auto;
  /* 居中而不是基线对齐：数字是 2.25rem 而标签是 0.6875rem，
     按基线对齐会把标签压到数字下沿，看上去像掉下去一格。 */
  align-items: center;
  gap: var(--s3);
  /* 上下 --s4：容器已经把容器 padding 归零了，留白全靠每行自己带，
     这样第一行和最后一行的留白不受「行数」影响。 */
  padding: var(--s4) var(--s5);
}

/* 行间的发丝线。上下内边距在这里已经足够，再加粗会变成双线。 */
.side .readout + .readout {
  border-top: var(--hairline);
}

/* 数字和它的小单位之间不能断行：「22」和「ms」分成两行会读成两个数 */
.side .readout__value {
  white-space: nowrap;
}

.ledger {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  overflow: hidden;
  box-shadow: var(--lift-1);
}

/* 账册行被指到时点亮：与图上的框共用同一个 hotBox 状态。
   只靠底色会和普通的 hover 行混在一起，所以再加一道左侧标记。 */
.fs-ledger tbody tr.is-hot td {
  background: var(--signal-soft);
  box-shadow: inset 2px 0 0 var(--signal);
}

/* 低于入表门槛的行压暗但仍可点：它们不是被丢掉，是被降了一档权重。
   复核时最该看的恰恰是这些框。 */
.fs-ledger tbody tr.is-weak td {
  color: var(--ink-faint);
}

/* 两栏等高之后右栏会剩下一截空白。账本不吃这份空白：它 overflow:hidden，
   让它 flex:1 的话检出多的时候末行会被裁掉而不是把栏目拉高（检测框没有限行）。
   空白留在说明之前——说明钉在栏目底部，读数、账本、说明三段的间距自然拉开，
   下沿也跟着画面对齐了。 */
.side__note {
  margin: auto 0 0;
  padding-left: var(--s3);
  border-left: 2px solid var(--line-strong);
  font-size: var(--t-micro);
  line-height: 1.6;
  color: var(--ink-faint);
}

/* --- 杂项 --------------------------------------------------------------- */
.mono {
  font-family: var(--font-num);
  font-size: var(--t-micro);
}

.none-inline {
  color: var(--ink-faint);
}

@media (max-width: 1100px) {
  .work {
    grid-template-columns: minmax(0, 1fr);
  }

  .knobs {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 700px) {
  .head,
  .stage__bar {
    flex-direction: column;
    align-items: flex-start;
  }

  /* 批量模式的读数是网格，要折列；右栏那组已经是 flex 列，不再叠这条 */
  .work > .readouts,
  .knobs {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>