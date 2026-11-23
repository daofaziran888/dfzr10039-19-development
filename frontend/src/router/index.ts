import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import RetryDispatchView from '@/views/RetryDispatchView.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    // 失败重试派工是解析日志的专用子页，必须放在通配模块路由之前
    { path: '/parse_log/retry', name: 'retry-dispatch', component: RetryDispatchView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
