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
      component: () => import('@/views/EventsView.vue'),
      meta: { title: '告警事件' },
    },
    {
      path: '/monitor',
      name: 'monitor',
      component: () => import('@/views/MonitorView.vue'),
      meta: { title: '实时监控' },
    },
    {
      path: '/reports',
      name: 'reports',
      component: () => import('@/views/ReportView.vue'),
      meta: { title: '分析报告' },
    },
    {
      path: '/models',
      name: 'models',
      component: () => import('@/views/ModelsView.vue'),
      meta: { title: '模型库' },
    },
    {
      path: '/bigscreen',
      name: 'bigscreen',
      component: () => import('@/views/BigScreenView.vue'),
      // 大屏是给投影/汇报看的：脱掉顶栏与风险尺，整页就是内容
      meta: { title: '月度火情统计大屏', fullscreen: true },
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

export default router
