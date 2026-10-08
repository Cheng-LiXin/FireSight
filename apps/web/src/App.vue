<script setup lang="ts">
/** 应用外壳：顶部胶囊导航 + 内容区。
 *
 * 这里没有左侧图标轨了。新方案靠字号层级和数字排版立辨识度，图标轨是上一套
 * 视觉语言的产物——两样摆在一起会互相削弱：一边说「这是仪表盘」，一边说
 * 「这是一排小图标里的第几格」。
 *
 * 大屏页独占整屏：顶栏与导航都不参与。挂在 wallboard 上看的时候，
 * 导航栏与操作控件都是干扰。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'

import { useConsoleStore } from '@/stores/console'

const store = useConsoleStore()
const route = useRoute()

const clock = ref('')
let timer: number | undefined

function tick(): void {
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

/** 值守的三页每天都要开，资产类的四页偶尔进。八条平铺会让「现在在哪一组」
 *  看不出来，所以中间用一段留白分组，而不是靠竖线或底色。 */
const NAV_WATCH = [
  { to: '/', label: '值班总览' },
  { to: '/events', label: '告警事件' },
  { to: '/detect', label: '识别检测' },
  { to: '/monitor', label: '实时监控' },
]

const NAV_ASSET = [
  { to: '/models', label: '模型库' },
  { to: '/training', label: '训练中心' },
  { to: '/reports', label: '分析报告' },
]

/** 顶栏右侧只放一件事：当前占着显存的任务。机器其余状态由 /api/system/status 提供，
 *  但值班时真正要盯的是「有没有东西正在跑」。 */
const jobText = computed(() => {
  const job = store.status?.gpu_job
  if (!job?.busy) return null
  return job.holder === 'train' ? '训练占用中' : '推理占用中'
})

const isFullscreen = computed(() => route.meta.fullscreen === true)
</script>

<template>
  <div class="shell">
    <template v-if="isFullscreen">
      <main class="shell__bare">
        <RouterView v-slot="{ Component }">
          <transition name="page" mode="out-in">
            <component :is="Component" />
          </transition>
        </RouterView>
      </main>
    </template>

    <template v-else>
      <header class="shell__bar">
        <RouterLink to="/" class="brand" aria-label="FireSight">
          <span class="brand__mark" aria-hidden="true" />
          <span class="brand__name">FireSight</span>
        </RouterLink>

        <nav class="shell__nav" aria-label="主导航">
          <RouterLink
            v-for="item in NAV_WATCH"
            :key="item.to"
            :to="item.to"
            class="navlink"
            :class="{ 'is-on': route.path === item.to }"
          >
            {{ item.label }}
          </RouterLink>
          <span class="navlink__gap" aria-hidden="true" />
          <RouterLink
            v-for="item in NAV_ASSET"
            :key="item.to"
            :to="item.to"
            class="navlink"
            :class="{ 'is-on': route.path === item.to }"
          >
            {{ item.label }}
          </RouterLink>
        </nav>

        <div class="shell__meta">
          <RouterLink to="/bigscreen" class="navlink navlink--wide">月度大屏</RouterLink>
          <span v-if="jobText" class="shell__job">{{ jobText }}</span>
          <span class="shell__clock num">{{ clock }}</span>
        </div>
      </header>

      <main class="shell__main">
        <!-- 系统级错误在这一层报：它不属于任何一页，而属于「后端没通」这件事 -->
        <div v-if="store.error" class="shell__alert" role="alert">
          <span>{{ store.error }}</span>
          <button type="button" class="btn btn--ghost" @click="store.load()">重试</button>
        </div>

        <RouterView v-slot="{ Component }">
          <transition name="page" mode="out-in">
            <component :is="Component" />
          </transition>
        </RouterView>
      </main>
    </template>
  </div>
</template>

<style scoped>
.shell {
  min-height: 100%;
  display: flex;
  flex-direction: column;
}

/* 顶栏只有一条底线。没有盒子、没有背景色、没有叠影——页面已经是浅灰底，
   再加一层容器只会把它压平。 */
.shell__bar {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  align-items: center;
  gap: var(--s6);
  height: var(--shell-h);
  padding: 0 var(--s6);
  background: color-mix(in srgb, var(--canvas) 88%, transparent);
  backdrop-filter: blur(8px);
  border-bottom: 1px solid var(--line);
}

.brand {
  display: inline-flex;
  align-items: center;
  gap: var(--s2);
  text-decoration: none;
  color: var(--ink);
}

/* 标记是一枚取景框，不是一块实心方块 */
.brand__mark {
  position: relative;
  width: 18px;
  height: 18px;
  border: 1.5px solid var(--ink);
  border-radius: 5px;
  transition: border-radius var(--dur-2) var(--ease-back);
}

.brand:hover .brand__mark {
  border-radius: 9px;
}

.brand__mark::after {
  content: '';
  position: absolute;
  inset: 4px;
  background: var(--alarm);
  border-radius: 50%;
}

.brand__name {
  font-size: var(--t-title);
  font-weight: var(--w-bold);
  letter-spacing: var(--track-tight);
}

.shell__nav {
  display: flex;
  align-items: center;
  gap: var(--s1);
}

/* 组间留白比组内间距大：靠留白分组，不靠竖线或底色 */
.navlink__gap {
  width: var(--s5);
}

/* 导航走胶囊。选中态用冷色而不是橙红——橙红只留给「真的有火」。 */
.navlink {
  padding: 6px var(--s4);
  font-size: var(--t-body);
  color: var(--ink-mute);
  text-decoration: none;
  border-radius: var(--r-pill);
  transition: color var(--dur-1) var(--ease-out),
    background-color var(--dur-1) var(--ease-out),
    transform var(--dur-1) var(--ease-out);
}

.navlink:hover {
  color: var(--ink);
  background: var(--surface-sunk);
}

.navlink:active {
  transform: scale(0.96);
}

/* 选中态除了换底色，还把字重往上抬一档——层级不该只靠颜色区分 */
.navlink.is-on {
  color: #fff;
  background: var(--signal);
  font-weight: var(--w-medium);
  box-shadow: var(--lift-2);
}

.navlink--wide {
  border: 1px solid var(--line);
}

.navlink--wide:hover {
  border-color: var(--line-strong);
  background: var(--surface);
}

.shell__meta {
  display: flex;
  align-items: center;
  gap: var(--s4);
  margin-left: auto;
  font-size: var(--t-micro);
  color: var(--ink-faint);
}

.shell__job {
  color: var(--alarm);
}

.shell__clock {
  color: var(--ink-mute);
}

.shell__main {
  flex: 1;
  padding: var(--s6);
}

/* 系统级错误。它不是某页的错误，所以留在壳层而不是塞进 RouterView。 */
.shell__alert {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--s4);
  max-width: 1360px;
  margin: 0 auto var(--s5);
  padding: var(--s3) var(--s5);
  font-size: var(--t-small);
  color: var(--ink-body);
  background: var(--alarm-soft);
  border: 1px solid var(--alarm-line);
  border-left: 3px solid var(--alarm);
  border-radius: var(--r);
}

.shell__bare {
  flex: 1;
  min-height: 100%;
}

/* 页面切换：淡入并轻微上移。出场比进场快，因为等的人是想看新页的人。 */
.page-enter-active {
  transition: opacity var(--dur-2) var(--ease-out),
    transform var(--dur-2) var(--ease-out);
}

.page-leave-active {
  transition: opacity var(--dur-1) var(--ease-out);
}

.page-enter-from {
  opacity: 0;
  transform: translateY(6px);
}

.page-leave-to {
  opacity: 0;
}

@media (max-width: 1100px) {
  .shell__bar {
    height: auto;
    flex-wrap: wrap;
    gap: var(--s3) var(--s5);
    padding: var(--s3) var(--s5);
  }

  .shell__nav {
    order: 3;
    width: 100%;
    overflow-x: auto;
  }

  .shell__main {
    padding: var(--s5);
  }
}

@media (prefers-reduced-motion: reduce) {
  .page-enter-active,
  .page-leave-active,
  .navlink,
  .brand__mark {
    transition: none;
  }
}
</style>