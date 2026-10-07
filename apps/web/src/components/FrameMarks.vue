<script setup lang="ts">
/**
 * 取景器刻度角 —— FireSight 的全站视觉签名。
 *
 * 形状取自相机取景器：四角齐全的 L 形角标，沿每条边再补一小段刻度线。
 * 早期版本只有左上、右下两角，更接近「装饰」；补齐四角并加刻度之后，
 * 它才真的读作取景器 —— 而图像、曲线、画面本来就是这个项目里被
 * 「取景」的东西。
 *
 * 为什么不用边框代替：容器已经有一圈 1px 边框了，再描一圈是两种语言打架。
 * 角标只在四角出现，是「标记」而不是「容器」。
 *
 * 纯装饰层：aria-hidden + pointer-events:none，绝不吃鼠标事件。
 */
</script>

<template>
  <span class="fs-frame" aria-hidden="true">
    <i class="fs-frame__mark fs-frame__mark--tl" />
    <i class="fs-frame__mark fs-frame__mark--tr" />
    <i class="fs-frame__mark fs-frame__mark--bl" />
    <i class="fs-frame__mark fs-frame__mark--br" />
  </span>
</template>

<style scoped>
.fs-frame {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

/* 角标线宽 --frame-mark、长度 14px、内收 6px 落在 12px 圆角弧线内。
   --frame-ink 是本组件自己的颜色变量：边框和刻度线共用它，改颜色只改一处，
   hover 点亮也只需重设这一个变量。 */
.fs-frame__mark {
  --frame-ink: var(--ink-4);

  position: absolute;
  width: 14px;
  height: 14px;
  border-color: var(--frame-ink);
  border-style: solid;
  transition: border-color var(--dur-2) var(--ease);
}

.fs-frame__mark--tl {
  top: 6px;
  left: 6px;
  border-width: var(--frame-mark) 0 0 var(--frame-mark);
}

.fs-frame__mark--tr {
  top: 6px;
  right: 6px;
  border-width: var(--frame-mark) var(--frame-mark) 0 0;
}

.fs-frame__mark--bl {
  bottom: 6px;
  left: 6px;
  border-width: 0 0 var(--frame-mark) var(--frame-mark);
}

.fs-frame__mark--br {
  bottom: 6px;
  right: 6px;
  border-width: 0 var(--frame-mark) var(--frame-mark) 0;
}

/* 沿边刻度线：紧挨角标外侧，短到几乎看不见。
   它们的作用不是装饰，是让「这四条边是取景器的取景框」这句话有了第二层证据——
   真正的取景器上就是这种沿边小刻度。四根线都放在角标之外且不与角标相接，
   免得糊成一条 L。 */
.fs-frame__mark::after {
  content: '';
  position: absolute;
  background: var(--frame-ink);
  transition: background-color var(--dur-2) var(--ease);
}

.fs-frame__mark--tl::after,
.fs-frame__mark--tr::after {
  top: calc(var(--frame-mark) / 2);
  height: 1px;
  width: var(--frame-tick);
}

.fs-frame__mark--bl::after,
.fs-frame__mark--br::after {
  bottom: calc(var(--frame-mark) / 2);
  height: 1px;
  width: var(--frame-tick);
}

.fs-frame__mark--tl::after,
.fs-frame__mark--bl::after {
  left: calc(14px + var(--frame-mark));
}

.fs-frame__mark--tr::after,
.fs-frame__mark--br::after {
  right: calc(14px + var(--frame-mark));
}

/* hover / 聚焦时点亮：静止态是灰的，被看一眼的时候才变近黑。
   选择器带 :global(.is-hot) 是为了让调用方能用「当前正看着这一块」这类
   状态类点亮角标，而不必为了一个颜色再造一个组件。 */
:global(.fs-viewfinder:hover) > .fs-frame .fs-frame__mark,
:global(.is-hot) > .fs-frame .fs-frame__mark {
  --frame-ink: var(--accent);
}

@media (prefers-reduced-motion: reduce) {
  .fs-frame__mark {
    transition: none;
  }
}
</style>
