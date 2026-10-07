<script setup lang="ts">
/** 应用外壳：左侧图标轨 + 顶部状态区 + 风险尺 + 内容区。
 *
 * 风险尺放在 shell 而不是仪表盘页，因为它是全站的状态读数——
 * 无论切到哪个模块，值班人都应该看得到当前风险。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import AppRail from '@/components/AppRail.vue'
import RiskRuler from '@/components/RiskRuler.vue'
import { useConsoleStore } from '@/stores/console'

const store = useConsoleStore()
const route = useRoute()

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
    <AppRail />

    <div class="shell__main">
      <header class="topbar">
        <!-- 模块名是「我在哪」的定位标签，不是文档标题：各页自己已经有一个
             <h1>，这里再用 <h1> 会让一页有两个主标题（读屏软件与
             getByRole('heading') 都会撞车）。所以是 p，读屏仍会念出来。 -->
        <p class="topbar__where">{{ route.meta.title ?? 'FireSight' }}</p>

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
  </div>
</template>

<style scoped>
/* 壳是「轨 + 主区」两列：轨道宽度由 AppRail 自己声明，这里只负责留位。
   主区是一个纵向 flex 容器，撑满剩余高度，所以风险尺和状态区各自 stick
   的时候下面滚动的是内容而不是彼此。 */
.shell {
  display: flex;
  min-height: 100%;
}

.shell__main {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
}

/* --- 顶部状态区 --------------------------------------------------------- */

/* 顶栏现在只回答两个问题：我在哪个模块、机器此刻什么状态。
   模块名从 route.meta.title 取，省掉一份要在导航和路由两处同步的常量。
   它不是标题元素——各页自己已经有一个 <h1>，这里再用一个 <h1> 会让一页
   有两个主标题。用 <p> 则读屏软件仍会把它当普通文本念出来，不丢信息。 */
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-6);
  height: var(--shell-h);
  padding: 0 var(--sp-5);
  background: var(--ash);
  border-bottom: var(--hairline);
  position: sticky;
  top: 0;
  z-index: 10;
}

.topbar__where {
  margin: 0;
  font-size: var(--fs-sm);
  font-weight: 500;
  color: var(--dross);
  letter-spacing: 0.04em;
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

/* --- 窄屏：状态区换行，让高 ---------------------------------------------- */

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
