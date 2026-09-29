<script setup lang="ts">
/** KPI 读数。数值用比例字形——等宽只用在需要纵向对齐的地方，
 *  大号独立数字用等宽会让它显得松散。 */
withDefaults(
  defineProps<{
    label: string
    value: string | number
    unit?: string
    /** 语义色：只在读数真的表示「好/坏」时使用，不用来装饰 */
    tone?: 'default' | 'fire' | 'muted'
  }>(),
  { unit: '', tone: 'default' },
)
</script>

<template>
  <div class="tile" :class="`tile--${tone}`">
    <div class="tile__label">{{ label }}</div>
    <div class="tile__value">
      <transition name="num" mode="out-in">
        <!-- :key 让数值变化时走一次淡入，而不是原地跳变 -->
        <span :key="String(value)" class="tile__num"
          >{{ value }}<span v-if="unit" class="tile__unit">{{ unit }}</span></span
        >
      </transition>
    </div>
  </div>
</template>

<style scoped>
.tile {
  padding: var(--sp-3) var(--sp-4);
  background: var(--ash);
  border: var(--hairline);
  border-radius: var(--radius);
  box-shadow: var(--shadow-1);
  transition: box-shadow var(--dur-2) var(--ease), transform var(--dur-2) var(--ease);
}

.tile:hover {
  box-shadow: var(--shadow-2);
  transform: translateY(-2px);
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

/* 数值淡入：进场 250ms 从下方 4px 升起，退场直接交棒（out-in） */
.num-enter-active {
  transition: opacity var(--dur-2) var(--ease), transform var(--dur-2) var(--ease);
}

.num-leave-active {
  transition: opacity var(--dur-1) var(--ease);
}

.num-enter-from {
  opacity: 0;
  transform: translateY(4px);
}

.num-leave-to {
  opacity: 0;
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
