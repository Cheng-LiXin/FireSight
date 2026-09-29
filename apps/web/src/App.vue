<script setup lang="ts">
/** 应用外壳：顶栏 + 风险尺 + 内容区。
 *
 * 风险尺放在 shell 而不是仪表盘页，因为它是全站的状态读数——
 * 无论切到哪个模块，值班人都应该看得到当前风险。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import RiskRuler from '@/components/RiskRuler.vue'
import { useConsoleStore } from '@/stores/console'

const store = useConsoleStore()
const route = useRoute()

const NAV = [
  { to: '/', label: '仪表盘' },
  { to: '/detect', label: '识别检测' },
  { to: '/monitor', label: '实时监控' },
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

/** 大屏页要整页展示：顶栏、风险尺、内容留白都不要。 */
const isFullscreen = computed(() => route.meta.fullscreen === true)
</script>

<template>
  <RouterView v-if="isFullscreen" v-slot="{ Component }">
    <transition name="page" mode="out-in">
      <component :is="Component" />
    </transition>
  </RouterView>

  <div v-else class="shell">
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
        <RouterLink to="/bigscreen" class="env__link">月度大屏</RouterLink>
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
      <RouterView v-slot="{ Component }">
        <transition name="page" mode="out-in">
          <component :is="Component" />
        </transition>
      </RouterView>
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
  box-shadow: var(--shadow-1);
  position: sticky;
  top: 0;
  z-index: 10;
}

.brand {
  font-size: var(--fs-md);
  font-weight: 700;
  letter-spacing: 0.02em;
  color: var(--bone);
  text-decoration: none;
  white-space: nowrap;
}

/* 品牌色点：单色主题下用近黑，与正文同族、只靠粗细区分 */
.brand__mark {
  color: var(--accent);
}

.nav {
  display: flex;
  gap: var(--sp-1);
}

/* 胶囊导航：激活态是圆角背景而不是下划线，靠底色与位移表达「当前在这」 */
.nav__link {
  padding: var(--sp-2) var(--sp-3);
  font-size: var(--fs-base);
  color: var(--dross);
  text-decoration: none;
  border-radius: var(--radius-pill);
  transition: color var(--dur-1) var(--ease),
    background-color var(--dur-2) var(--ease);
}

.nav__link:hover {
  color: var(--bone);
  background: var(--highlight);
}

.nav__link.router-link-exact-active {
  color: var(--bone);
  font-weight: 500;
  background: var(--highlight);
}

.env {
  display: flex;
  align-items: center;
  gap: var(--sp-4);
  margin-left: auto;
  font-size: var(--fs-sm);
  color: var(--dross);
}

.env__link {
  padding: var(--sp-1) var(--sp-3);
  color: var(--dross);
  text-decoration: none;
  border: 1px solid var(--cinder);
  border-radius: var(--radius-pill);
  transition: color var(--dur-1) var(--ease), border-color var(--dur-1) var(--ease),
    background-color var(--dur-1) var(--ease);
}

.env__link:hover {
  color: var(--bone);
  border-color: var(--dross);
  background: var(--highlight);
}

.env__job {
  color: var(--accent);
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
  border-left: 3px solid var(--accent);
  border-radius: var(--radius-sm);
  box-shadow: var(--shadow-1);
}

.alert__retry {
  padding: var(--sp-1) var(--sp-3);
  font: inherit;
  font-size: var(--fs-sm);
  color: var(--bone);
  background: transparent;
  border: 1px solid var(--cinder);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: border-color var(--dur-1) var(--ease),
    background-color var(--dur-1) var(--ease);
}

.alert__retry:hover {
  border-color: var(--dross);
  background: var(--highlight);
}

/* --- 页面切换：淡入 + 轻微上移，出场上更快一点 ------------------------- */

.page-enter-active {
  transition: opacity var(--dur-2) var(--ease), transform var(--dur-2) var(--ease);
}

.page-leave-active {
  transition: opacity var(--dur-1) var(--ease), transform var(--dur-1) var(--ease);
}

.page-enter-from {
  opacity: 0;
  transform: translateY(8px);
}

.page-leave-to {
  opacity: 0;
  transform: translateY(-4px);
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
