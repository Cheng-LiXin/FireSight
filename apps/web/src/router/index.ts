import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'dashboard',
      component: () => import('@/views/DashboardView.vue'),
      meta: { title: '仪表盘' },
    },
    {
      path: '/detect',
      name: 'detect',
      component: () => import('@/views/DetectView.vue'),
      meta: { title: '识别检测' },
    },
    {
      path: '/training',
      name: 'training',
      component: () => import('@/views/TrainingView.vue'),
      meta: { title: '训练中心' },
    },
    {
      path: '/events',
      name: 'events',
      component: () => import('@/views/PendingView.vue'),
      meta: { title: '告警事件', plan: '事件筛选分页、片段回放、标记误报、导出 CSV' },
    },
    {
      path: '/models',
      name: 'models',
      component: () => import('@/views/PendingView.vue'),
      meta: { title: '模型库', plan: '归档权重列表与指标、设为当前、下载、多权重对比评估' },
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

export default router
