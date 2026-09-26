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
      {{ value }}<span v-if="unit" class="tile__unit">{{ unit }}</span>
    </div>
  </div>
</template>

<style scoped>
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
  font-weight: 500;
  line-height: 1.2;
  color: var(--bone);
}

.tile__unit {
  margin-left: 2px;
  font-size: var(--fs-sm);
  color: var(--dross);
}

.tile--fire .tile__value {
  color: var(--fire-3);
}

.tile--muted .tile__value {
  color: var(--dross);
}
</style>
