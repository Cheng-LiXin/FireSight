<script setup lang="ts">
/** 未建模块的占位。空态应当给出方向，而不是一句「暂无数据」。 */
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const title = computed(() => String(route.meta.title ?? ''))
const plan = computed(() => String(route.meta.plan ?? ''))
</script>

<template>
  <section class="pending">
    <h1 class="pending__title">{{ title }}</h1>
    <p class="pending__plan">{{ plan }}</p>
    <p class="pending__note">此模块尚未实现。当前可用的只有仪表盘。</p>
  </section>
</template>

<style scoped>
.pending {
  max-width: 60ch;
  padding: var(--sp-6) 0;
}

.pending__title {
  margin: 0 0 var(--sp-3);
  font-size: var(--fs-xl);
  font-weight: 500;
}

.pending__plan {
  margin: 0 0 var(--sp-5);
  color: var(--dross);
}

.pending__note {
  margin: 0;
  font-size: var(--fs-sm);
  color: var(--dross);
  padding-left: var(--sp-3);
  border-left: 2px solid var(--cinder);
}
</style>
