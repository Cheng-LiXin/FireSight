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
      component: () => import('@/views/PendingView.vue'),
      meta: { title: '识别检测', plan: '上传图像识别、原图与结果对比、检测框列表、目录批量推理' },
    },
    {
      path: '/training',
      name: 'training',
      component: () => import('@/views/PendingView.vue'),
      meta: { title: '训练中心', plan: '历史 run 指标对比、训练曲线、网页启动训练与实时日志流' },
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
