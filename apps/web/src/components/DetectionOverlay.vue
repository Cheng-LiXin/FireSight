<script setup lang="ts">
/** 检测框叠加层 —— 把后端回传的 xyxy 画在原图上。
 *
 * 为什么要自己画，而不是直接用后端 annotated 那张位图：
 *   那张位图是 ultralytics 的默认渲染，框线粗、标签是英文 class 名，
 *   且一旦烧进像素就没法和页面里的其他元素联动——鼠标划过图像时，
 *   没有任何办法知道「用户指的是哪个框」。这里把框做成 DOM，
 *   账册行与框之间才能双向点亮。
 *
 * 坐标零换算：svg 的 viewBox 直接取原图像素尺寸，配合
 * preserveAspectRatio="none" 让 svg 铺满 <img> 的盒子，于是
 * xyxy 就是 svg 里的用户坐标。代价是若某天给 img 加了
 * object-fit: cover，这层会与图像错位——那同时也违反了 detect.spec
 * 里「图片不得裁切变形」的断言，两者会一起报警。
 *
 * 矩形画在 svg 里、标签是 HTML：svg 里的字号是用户坐标，会跟着图一起
 * 缩到看不清（1920px 宽的图显示成 600px 时，13px 只剩 4px）。矢量描边
 * 可以用 vector-effect: non-scaling-stroke 钉死在屏幕像素上，字号没有
 * 同样的开关，所以标签改用百分比定位 + 真实 CSS px 字号。
 */
import { computed } from 'vue'

import type { Detection } from '@/api/types'

const props = defineProps<{
  imageSrc: string
  width: number
  height: number
  detections: Detection[]
  /** 当前被指到的框序号；null 表示无人指。 */
  hot?: number | null
}>()

const emit = defineEmits<{ hover: [index: number | null] }>()

/** 类别色槽位与图表图例共用同一组：烟=深石板灰、火=告警红。
 *  叠加层是 DOM，看得到 CSS 变量，所以直接引 var() 即可。 */
function classColor(d: Detection): string {
  return d.name === 'fire' ? 'var(--cls-fire)' : 'var(--cls-smoke)'
}

/** 标签文字用 name_cn（后端已按映射表翻好），置信度固定 2 位。
 *  与下方账册的 3 位不同是刻意的：图上标签要的是一眼可辨，
 *  账册要的是可核对，两者的精度诉求不一样。 */
function labelOf(d: Detection): string {
  return `${d.name_cn} ${d.conf.toFixed(2)}`
}

/** 框的最小尺寸：极小的误检框画出来会退化成一点，
 *  给个下限保证它至少还是个能被看见、能被指到的矩形。 */
const MIN_SIDE = 3

/** 标签块高度（屏幕像素）。与 .overlay__tag 的 padding 一起构成它的高度。 */
const TAG_H = 20

interface Box {
  /** 矩形，左上角原点，单位=原图像素。 */
  x: number
  y: number
  w: number
  h: number
  /** 标签的定位，单位=画布百分比（百分比是为了跟着图等比缩放）。 */
  tagLeft: number
  tagTop: number
  text: string
  color: string
}

const boxes = computed<Box[]>(() =>
  props.detections.map((d) => {
    const [x1, y1, x2, y2] = d.xyxy
    // 标签挂在框左上角外侧：贴到框内侧会盖住框里最该看的像素，
    // 挂到另一侧又可能整个跑出画布，所以越界时压回画布内。
    const above = y1 - TAG_H - 2
    return {
      x: x1,
      y: y1,
      w: Math.max(x2 - x1, MIN_SIDE),
      h: Math.max(y2 - y1, MIN_SIDE),
      tagLeft: (x1 / props.width) * 100,
      tagTop: (above >= 0 ? above / props.height : 0) * 100,
      text: labelOf(d),
      color: classColor(d),
    }
  }),
)

const viewBox = computed(() => `0 0 ${props.width} ${props.height}`)

function hover(i: number | null): void {
  emit('hover', i)
}
</script>

<template>
  <figure class="overlay">
    <img :src="imageSrc" alt="上传的原图" />

    <svg
      class="overlay__svg"
      :viewBox="viewBox"
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      <g
        v-for="(b, i) in boxes"
        :key="i"
        class="overlay__box"
        :class="{ 'is-hot': hot === i, 'is-dim': hot !== null && hot !== i }"
        @mouseenter="hover(i)"
        @mouseleave="hover(null)"
      >
        <rect
          :x="b.x"
          :y="b.y"
          :width="b.w"
          :height="b.h"
          :stroke="b.color"
        />
      </g>
    </svg>

    <!-- 标签单独一层：与矩形同一个 hover 契约，这样「指着标签」和
         「指着框」在账册那边点亮的是同一行。 -->
    <span
      v-for="(b, i) in boxes"
      :key="i"
      class="overlay__tag"
      :class="{ 'is-hot': hot === i, 'is-dim': hot !== null && hot !== i }"
      :style="{ left: `${b.tagLeft}%`, top: `${b.tagTop}%`, background: b.color }"
      @mouseenter="hover(i)"
      @mouseleave="hover(null)"
    >
      {{ b.text }}
    </span>
  </figure>
</template>

<style scoped>
/* 容器尺寸由外部（.pair__item 里的栅格）决定，组件只负责铺满。
   图像、svg、标签三层同尺寸叠放，因此任何坐标都直接等于图像坐标。 */
.overlay {
  position: relative;
  margin: 0;
}

.overlay img {
  display: block;
  width: 100%;
  height: auto;
  background: var(--canvas);
  border: var(--hairline);
  border-radius: var(--r-sm);
}

.overlay__svg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  /* 命中区只给 rect，不给整块画布，否则 <svg> 会把下面整张图的
     hover 事件吃掉，账册行的高亮就再也点不亮了。 */
  pointer-events: none;
}

.overlay__box {
  pointer-events: auto;
}

.overlay__box rect {
  /* 命中区是整个矩形内部，不只是那条描边。fill:none 的图元按 SVG 规范
     不参与命中测试，那样只有两三像素宽的框线可指，指着框中间反而没反应。
     transparent 是个颜色而不是 none，所以照样不可见、但整块可指。 */
  fill: transparent;
  stroke-width: 2;
  /* 非缩放描边：viewBox 是原图像素、显示尺寸只有几百像素，
     不钉住的话 2px 会被缩放成不到 1px 的发丝。 */
  vector-effect: non-scaling-stroke;
  transition: stroke-width var(--dur-1) var(--ease), opacity var(--dur-1) var(--ease);
}

.overlay__box.is-hot rect {
  stroke-width: 4;
}

/* 被指到某一个时，其余退到 25%：一帧里十几个框全亮着等于没亮。 */
.overlay__box.is-dim,
.overlay__tag.is-dim {
  opacity: 0.25;
}

/* 类别色底 + 白字。白字压类别色在两个槽位上都 >=4.5:1，
   实测对比度见 tokens.css --cls-smoke 处注释。 */
.overlay__tag {
  position: absolute;
  height: 20px;
  padding: 1px 4px 0;
  font-family: var(--font-num);
  font-size: var(--t-micro);
  font-weight: 500;
  line-height: 18px;
  color: var(--surface);
  white-space: nowrap;
  pointer-events: auto;
  transition: opacity var(--dur-1) var(--ease);
}
</style>