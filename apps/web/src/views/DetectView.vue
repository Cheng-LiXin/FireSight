<script setup lang="ts">
/** 识别检测：单张上传与目录批量。
 *
 * 参数栏横贯在最上方，作用于下方全部内容——不把筛选器塞进取景窗里。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { api, ApiError, type InferParams } from '@/api/client'
import { CLASS_CN } from '@/api/types'
import type { BatchResult, InferResult, ModelInfo, SourceOption } from '@/api/types'

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

const sourceKey = ref('')
const limit = ref(20)
const batch = ref<BatchResult | null>(null)

const busy = ref(false)
const dragging = ref(false)
const error = ref<string | null>(null)

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
  }
})

onBeforeUnmount(() => {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
})

function pickFile(f: File): void {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  file.value = f
  previewUrl.value = URL.createObjectURL(f)
  result.value = null
  void runSingle()
}

function onDrop(e: DragEvent): void {
  dragging.value = false
  const f = e.dataTransfer?.files?.[0]
  if (f) pickFile(f)
}

function onPick(e: Event): void {
  const f = (e.target as HTMLInputElement).files?.[0]
  if (f) pickFile(f)
}

async function runSingle(): Promise<void> {
  if (!file.value || !weights.value) return
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
</script>

<template>
  <div class="detect">
    <p v-if="error" class="alert" role="alert">{{ error }}</p>

    <!-- 参数栏：作用于下方全部内容 -->
    <section class="params" aria-label="推理参数">
      <label class="field">
        <span class="field__label">权重</span>
        <select v-model="weights" class="field__control">
          <option v-if="!models.length" value="">无已归档模型</option>
          <option v-for="m in models" :key="m.run_id" :value="m.run_id">
            {{ m.run_id }}
          </option>
        </select>
      </label>

      <label class="field field--slider">
        <span class="field__label">置信度阈值 <b class="fs-num">{{ conf.toFixed(2) }}</b></span>
        <input
          v-model.number="conf"
          type="range"
          min="0.05"
          max="0.95"
          step="0.05"
          :style="{ '--fill': `${((conf - 0.05) / 0.9) * 100}%` }"
        />
      </label>

      <label class="field field--slider">
        <span class="field__label">IoU 阈值 <b class="fs-num">{{ iou.toFixed(2) }}</b></span>
        <input
          v-model.number="iou"
          type="range"
          min="0.1"
          max="0.95"
          step="0.05"
          :style="{ '--fill': `${((iou - 0.1) / 0.85) * 100}%` }"
        />
      </label>

      <label class="field">
        <span class="field__label">设备</span>
        <select v-model="device" class="field__control">
          <option value="0">GPU (0)</option>
          <option value="cpu">CPU</option>
        </select>
      </label>

      <div class="modes" role="radiogroup" aria-label="输入方式">
        <button
          type="button"
          class="modes__btn"
          :class="{ 'is-on': mode === 'single' }"
          role="radio"
          :aria-checked="mode === 'single'"
          @click="mode = 'single'"
        >
          单张图像
        </button>
        <button
          type="button"
          class="modes__btn"
          :class="{ 'is-on': mode === 'batch' }"
          role="radio"
          :aria-checked="mode === 'batch'"
          @click="mode = 'batch'"
        >
          目录批量
        </button>
      </div>
    </section>

    <!-- 单张 -->
    <template v-if="mode === 'single'">
      <div class="work">
        <section class="fs-viewfinder viewfinder">
          <h2 class="fs-viewfinder__title">取景窗</h2>

          <div
            v-if="!file"
            class="dropzone"
            :class="{ 'is-over': dragging }"
            @dragover.prevent="dragging = true"
            @dragleave="dragging = false"
            @drop.prevent="onDrop"
          >
            <label class="dropzone__inner">
              <input type="file" accept="image/*" hidden @change="onPick" />
              <span class="dropzone__hint">把图像拖到这里，或点击选择</span>
              <span class="dropzone__sub">支持 JPG / PNG / BMP / WebP</span>
            </label>
          </div>

          <div v-else class="pair">
            <figure class="pair__item">
              <figcaption>原图</figcaption>
              <img :src="previewUrl" alt="上传的原图" />
            </figure>
            <figure class="pair__item">
              <figcaption>检测结果</figcaption>
              <img v-if="result" :src="result.annotated ?? ''" alt="标注后的检测结果" />
              <div v-else class="pair__pending">{{ busy ? '推理中…' : '未检出或尚未推理' }}</div>
            </figure>
          </div>

          <div v-if="file" class="viewfinder__foot">
            <label class="relink">
              <input type="file" accept="image/*" hidden @change="onPick" />
              换一张
            </label>
            <button type="button" class="relink" :disabled="busy" @click="runSingle">
              {{ busy ? '推理中…' : '重新推理' }}
            </button>
          </div>
        </section>

        <aside class="side">
          <div class="fs-readout">
            <div class="fs-readout__label">目标数</div>
            <div class="fs-readout__value">{{ result?.count ?? '—' }}</div>
          </div>
          <div class="fs-readout">
            <div class="fs-readout__label">最高置信度</div>
            <div class="fs-readout__value">{{ result ? fix(result.max_conf) : '—' }}</div>
          </div>
          <div class="fs-readout">
            <div class="fs-readout__label">耗时 (ms)</div>
            <div class="fs-readout__value">{{ result ? result.elapsed_ms.toFixed(1) : '—' }}</div>
          </div>
          <div class="fs-readout">
            <div class="fs-readout__label">图像尺寸</div>
            <div class="fs-readout__value">
              {{ result ? `${result.width}×${result.height}` : '—' }}
            </div>
          </div>
          <div class="fs-readout">
            <div class="fs-readout__label">实际设备</div>
            <div class="fs-readout__value fs-readout__value--sm">{{ result?.device ?? '—' }}</div>
          </div>
          <p class="side__note">
            阈值调低可提高召回、增加误报。明火为弱类，召回偏低是可预期的。
          </p>
        </aside>
      </div>

      <section class="block">
        <h2 class="block__title">检测框</h2>
        <table v-if="result?.detections.length" class="fs-ledger">
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
            <tr v-for="(d, i) in result.detections" :key="i">
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
        <p v-else-if="result" class="none">本帧未检出明火或烟雾。</p>
        <p v-else class="none">上传图像后，检测框会列在这里。</p>
      </section>
    </template>

    <!-- 批量 -->
    <template v-else>
      <section class="batchbar">
        <label class="field">
          <span class="field__label">来源</span>
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

        <label class="field field--narrow">
          <span class="field__label">最多处理</span>
          <input v-model.number="limit" class="field__control" type="number" min="1" max="300" />
        </label>

        <button type="button" class="run" :disabled="busy || !sourceKey" @click="runBatch">
          {{ busy ? '推理中…' : '开始批量识别' }}
        </button>
      </section>

      <template v-if="batch">
        <section class="tiles">
          <div class="tile">
            <div class="tile__label">处理张数</div>
            <div class="tile__value">{{ batch.files }}</div>
          </div>
          <div class="tile">
            <div class="tile__label">有检出</div>
            <div class="tile__value" :class="{ 'tile__value--fire': batch.hit_files > 0 }">
              {{ batch.hit_files }}
            </div>
          </div>
          <div class="tile">
            <div class="tile__label">目标总数</div>
            <div class="tile__value">{{ batch.total_dets }}</div>
          </div>
          <div class="tile">
            <div class="tile__label">平均耗时 (ms)</div>
            <div class="tile__value">{{ batch.avg_ms.toFixed(1) }}</div>
          </div>
        </section>

        <section class="block">
          <h2 class="block__title">逐图结果</h2>
          <table class="fs-ledger">
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
        </section>
      </template>

      <p v-else class="none">选择来源后开始批量识别，逐图结果会列在这里。</p>
    </template>
  </div>
</template>

<style scoped>
.detect {
  display: flex;
  flex-direction: column;
  gap: var(--sp-5);
}

.alert {
  margin: 0;
  padding: var(--sp-3) var(--sp-4);
  font-size: var(--fs-sm);
  background: var(--ash);
  border: var(--hairline);
  border-left: 3px solid var(--fire-2);
}

/* --- 参数栏 ----------------------------------------------------------- */

.params,
.batchbar {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: var(--sp-5);
  padding: var(--sp-3) var(--sp-4);
  background: var(--ash);
  border: var(--hairline);
}

.field {
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
  min-width: 180px;
}

.field--slider {
  min-width: 200px;
}

.field--narrow {
  min-width: 110px;
}

.field__label {
  font-size: var(--fs-xs);
  color: var(--dross);
}

.field__label b {
  color: var(--bone);
  font-weight: 500;
}

.field__control {
  height: 32px;
  padding: 0 var(--sp-2);
  font-family: var(--font-sans);
  font-size: var(--fs-sm);
  color: var(--bone);
  background: var(--soot);
  border: 1px solid var(--cinder);
  border-radius: var(--radius);
}

input[type='range'].field__control,
input[type='range'] {
  height: 32px;
  padding: 0;
  background: transparent;
  border: 0;
  -webkit-appearance: none;
  appearance: none;
  cursor: pointer;
}

/* 默认滑块是浅色轨道，在这套暖褐底上过于抢眼。轨道分两段着色，
   已选区间用明火色——滑块停在哪儿一眼可见，不必去读旁边的数字。 */
input[type='range']::-webkit-slider-runnable-track {
  height: 4px;
  border-radius: 2px;
  background: linear-gradient(
    to right,
    var(--fire-2) var(--fill, 0%),
    var(--cinder) var(--fill, 0%)
  );
}

input[type='range']::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 12px;
  height: 12px;
  margin-top: -4px;
  border-radius: 50%;
  background: var(--fire-3);
}

.modes {
  display: flex;
  margin-left: auto;
  border: 1px solid var(--cinder);
  border-radius: var(--radius);
  overflow: hidden;
}

.modes__btn {
  padding: 0 var(--sp-4);
  height: 32px;
  font: inherit;
  font-size: var(--fs-sm);
  color: var(--dross);
  background: transparent;
  border: 0;
  cursor: pointer;
}

.modes__btn + .modes__btn {
  border-left: 1px solid var(--cinder);
}

.modes__btn.is-on {
  color: var(--bone);
  background: var(--cinder);
}

/* --- 作业区 ----------------------------------------------------------- */

.work {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 220px;
  gap: var(--sp-5);
  align-items: start;
}

.viewfinder {
  min-height: 320px;
}

/* 取景窗自带四角框标，拖放区若再描一圈亮边就有两套边框语言了。
   静止时安静，只在悬停/拖入时响应。 */
.dropzone {
  border: 1px dashed rgb(140 129 114 / 35%);
  transition: border-color 150ms ease, background-color 150ms ease;
}

.dropzone.is-over {
  border-color: var(--fire-3);
  background: rgb(255 154 60 / 6%);
}

.dropzone__inner {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--sp-2);
  min-height: 260px;
  cursor: pointer;
  text-align: center;
}

.dropzone__inner:hover .dropzone__hint {
  color: var(--fire-3);
}

.dropzone__hint {
  color: var(--bone);
  transition: color 150ms ease;
}

.dropzone__sub {
  font-size: var(--fs-xs);
  color: var(--dross);
}

.pair {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--sp-4);
}

.pair__item {
  margin: 0;
}

.pair__item figcaption {
  margin-bottom: var(--sp-2);
  font-size: var(--fs-xs);
  color: var(--dross);
}

.pair__item img {
  display: block;
  width: 100%;
  height: auto;
  background: var(--soot);
  border: var(--hairline);
}

.pair__pending {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 180px;
  font-size: var(--fs-sm);
  color: var(--dross);
  background: var(--soot);
  border: var(--hairline);
}

.viewfinder__foot {
  display: flex;
  gap: var(--sp-3);
  margin-top: var(--sp-4);
}

.relink {
  padding: var(--sp-1) var(--sp-3);
  font: inherit;
  font-size: var(--fs-sm);
  color: var(--bone);
  background: transparent;
  border: 1px solid var(--cinder);
  border-radius: var(--radius);
  cursor: pointer;
}

.relink:hover:not(:disabled) {
  border-color: var(--dross);
}

.relink:disabled {
  color: var(--dross);
  cursor: default;
}

/* --- 读数侧栏 --------------------------------------------------------- */

.side {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}

.fs-readout__value--sm {
  font-size: var(--fs-base);
  word-break: break-all;
}

.side__note {
  margin: var(--sp-2) 0 0;
  padding-left: var(--sp-3);
  border-left: 2px solid var(--cinder);
  font-size: var(--fs-xs);
  line-height: 1.6;
  color: var(--dross);
}

/* --- 块 --------------------------------------------------------------- */

.block {
  display: flex;
  flex-direction: column;
}

.block__title {
  margin: 0 0 var(--sp-3);
  font-size: var(--fs-sm);
  font-weight: 500;
  color: var(--dross);
}

.batchbar {
  align-items: flex-end;
}

.run {
  height: 32px;
  padding: 0 var(--sp-5);
  font: inherit;
  font-size: var(--fs-sm);
  font-weight: 500;
  color: #fff;
  background: var(--fire-2);
  border: 0;
  border-radius: var(--radius);
  cursor: pointer;
}

.run:disabled {
  background: var(--cinder);
  color: var(--dross);
  cursor: default;
}

.tiles {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--sp-4);
}

.tile {
  padding: var(--sp-3) var(--sp-4);
  background: var(--ash);
  border: var(--hairline);
}

.tile__label {
  font-size: var(--fs-xs);
  color: var(--dross);
}

.tile__value {
  margin-top: var(--sp-1);
  font-size: var(--fs-xl);
  line-height: 1.2;
  color: var(--bone);
}

.tile__value--fire {
  color: var(--fire-3);
}

/* --- 杂项 ------------------------------------------------------------- */

/* 类别点：与图表共用同一组类别色，图例语义全局一致 */
.dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  margin-right: var(--sp-2);
  border-radius: 1px;
  vertical-align: baseline;
}

.dot--smoke {
  background: var(--cat-smoke);
}

.dot--fire {
  background: var(--cat-fire);
}

.mono {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
}

.none {
  margin: 0;
  padding: var(--sp-4);
  font-size: var(--fs-sm);
  color: var(--dross);
  border: 1px dashed var(--cinder);
}

.none-inline {
  color: var(--dross);
}

@media (max-width: 1100px) {
  .work {
    grid-template-columns: minmax(0, 1fr);
  }

  .tiles {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .pair {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
