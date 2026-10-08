<script setup lang="ts">
/** KPI 读数。数值用比例字形——等宽只用在需要纵向对齐的地方，
 *  大号独立数字用等宽会让它显得松散。
 *
 *  形状语言取「读数条」而不是卡片：一行 KPI 曾经做成四张带边框、
 *  圆角、悬停上浮的小卡片，四个一模一样的盒子并排会把四个数字的权重
 *  拉平——而这四个数里只有一个（待复核）真的需要被看见。改成一组共用
 *  顶部分隔线的读数条之后，数字之间靠基线和留白分节奏，不必靠盒子。 */
import CountUp from '@/components/CountUp.vue'

const props = withDefaults(
  defineProps<{
    label: string
    /** 传数字走滚动动画；传字符串（例如已经是「12.3%」的成品文本）原样显示。 */
    value: string | number
    unit?: string
    /** 语义色：只在读数真的表示「好/坏」时使用，不用来装饰 */
    tone?: 'default' | 'fire' | 'muted'
    /** 小数位。value 是数字时生效；字符串一律原样显示，不在这里改精度。 */
    digits?: number
  }>(),
  { unit: '', tone: 'default', digits: 0 },
)

/** 只有真正的数值才滚。字符串是上游已经格式化好的文本（带单位、带千分位），
 *  重新解析再滚一遍反而会把不该动的地方动起来。 */
const isNumber = (): boolean => typeof props.value === 'number'
</script>

<template>
  <div class="tile fs-readout" :class="`tile--${tone}`">
    <div class="tile__label fs-readout__label">{{ label }}</div>
    <div class="tile__value fs-readout__value">
      <span class="tile__num">
        <CountUp v-if="isNumber()" :value="value as number" :digits="digits" />
        <template v-else>{{ value }}</template>
        <span v-if="unit" class="tile__unit">{{ unit }}</span>
      </span>
    </div>
  </div>
</template>

<style scoped>
/* 无边框、无圆角、无阴影：分隔交给 .fs-readout 的顶线（来自 base.css）。
   刻度角同理不挂——它属于被观察对象（图像/曲线/画面），KPI 不是被观察的东西。 */
.tile {
  transition: opacity var(--dur-2) var(--ease);
}

.tile:hover {
  opacity: 0.72;
}

.tile__label {
  font-size: var(--fs-xs);
  color: var(--dross);
}

.tile__value {
  margin-top: var(--sp-1);
  font-size: var(--fs-xl);
  font-weight: 500;
  line-height: 1.2;
  color: var(--bone);
}

.tile__num {
  display: inline-block;
}

.tile__unit {
  margin-left: 2px;
  font-size: var(--fs-sm);
  color: var(--dross);
}

/* 「这个读数表示有火/有告警」——正是 --alarm 的合法用途 */
.tile--fire .tile__value {
  color: var(--alarm);
}

.tile--muted .tile__value {
  color: var(--dross);
}
</style>
