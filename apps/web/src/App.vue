<script setup lang="ts">
/** 应用外壳：顶栏 + 风险尺 + 内容区。
 *
 * 风险尺放在 shell 而不是仪表盘页，因为它是全站的状态读数——
 * 无论切到哪个模块，值班人都应该看得到当前风险。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'

import RiskRuler from '@/components/RiskRuler.vue'
import { useConsoleStore } from '@/stores/console'

const store = useConsoleStore()

const NAV = [
  { to: '/', label: '仪表盘' },
  { to: '/detect', label: '识别检测' },
  { to: '/training', label: '训练中心' },
  { to: '/events', label: '告警事件' },
  { to: '/models', label: '模型库' },
]

const clock = ref('')
let timer: number | undefined

function tick() {
  clock.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
}

onMounted(() => {
  store.load()
  tick()
  timer = window.setInterval(tick, 1000)
})

onUnmounted(() => {
  if (timer) window.clearInterval(timer)
})

/** 顶栏右侧只放一件事：当前占着显存的任务。 */
const gpuText = computed(() => {
  const gpu = store.status?.gpu
  if (!gpu) return null
  if (!gpu.available) return 'GPU 未就绪'
  return `${gpu.name?.replace('NVIDIA GeForce ', '') ?? 'GPU'}`
})

const jobText = computed(() => {
  const job = store.status?.gpu_job
  if (!job?.busy) return null
  return job.holder === 'train' ? '训练占用中' : '推理占用中'
})
</script>

<template>
  <div class="shell">
    <header class="topbar">
      <RouterLink to="/" class="brand">
        <span class="brand__mark">Fire</span><span class="brand__rest">Sight</span>
      </RouterLink>

      <nav class="nav">
        <RouterLink v-for="item in NAV" :key="item.to" :to="item.to" class="nav__link">
          {{ item.label }}
        </RouterLink>
      </nav>

      <div class="env">
        <span v-if="jobText" class="env__job">{{ jobText }}</span>
        <span v-if="gpuText" class="env__gpu">{{ gpuText }}</span>
        <span class="env__clock fs-num">{{ clock }}</span>
      </div>
    </header>

    <RiskRuler :risk="store.overview?.risk ?? null" />

    <main class="content">
      <div v-if="store.error" class="alert" role="alert">
        <span>{{ store.error }}</span>
        <button type="button" class="alert__retry" @click="store.load()">重试</button>
      </div>
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.shell {
  min-height: 100%;
  display: flex;
  flex-direction: column;
}

/* --- 顶栏 ------------------------------------------------------------- */

.topbar {
  display: flex;
  align-items: center;
  gap: var(--sp-6);
  height: var(--shell-h);
  padding: 0 var(--sp-5);
  background: var(--ash);
  border-bottom: var(--hairline);
}

.brand {
  font-size: var(--fs-md);
  font-weight: 700;
  letter-spacing: 0.02em;
  color: var(--bone);
  text-decoration: none;
  white-space: nowrap;
}

/* 品牌唯一的色点：品牌的 "Fire" 用明火家族中段色 */
.brand__mark {
  color: var(--fire-2);
}

.nav {
  display: flex;
  gap: var(--sp-1);
}

.nav__link {
  padding: var(--sp-2) var(--sp-3);
  font-size: var(--fs-base);
  color: var(--dross);
  text-decoration: none;
  border-bottom: 2px solid transparent;
  transition: color 150ms ease;
}

.nav__link:hover {
  color: var(--bone);
}

.nav__link.router-link-exact-active {
  color: var(--bone);
  border-bottom-color: var(--fire-2);
}

.env {
  display: flex;
  align-items: center;
  gap: var(--sp-4);
  margin-left: auto;
  font-size: var(--fs-sm);
  color: var(--dross);
}

.env__job {
  color: var(--fire-3);
}

.env__clock {
  color: var(--bone);
}

/* --- 内容区 ----------------------------------------------------------- */

.content {
  flex: 1;
  padding: var(--sp-5);
}

.alert {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-4);
  margin-bottom: var(--sp-5);
  padding: var(--sp-3) var(--sp-4);
  font-size: var(--fs-sm);
  color: var(--bone);
  background: var(--ash);
  border: var(--hairline);
  border-left: 3px solid var(--fire-2);
}

.alert__retry {
  padding: var(--sp-1) var(--sp-3);
  font: inherit;
  font-size: var(--fs-sm);
  color: var(--bone);
  background: transparent;
  border: 1px solid var(--cinder);
  border-radius: var(--radius);
  cursor: pointer;
}

.alert__retry:hover {
  border-color: var(--dross);
}

/* --- 窄屏：导航换行，顶栏让高 ------------------------------------------ */

@media (max-width: 860px) {
  .topbar {
    height: auto;
    flex-wrap: wrap;
    gap: var(--sp-2) var(--sp-4);
    padding: var(--sp-2) var(--sp-4);
  }

  .env {
    font-size: var(--fs-xs);
  }

  .content {
    padding: var(--sp-4);
  }
}
</style>
