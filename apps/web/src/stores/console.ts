/** 控制台共享状态：系统状态与仪表盘聚合。
 *
 * 顶栏和风险尺在 shell 层，仪表盘页也要用同一份数据，
 * 因此放在 store 里只拉一次，避免同屏多请求。
 */

import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api, ApiError } from '@/api/client'
import type { Overview, SystemStatus } from '@/api/types'

export const useConsoleStore = defineStore('console', () => {
  const status = ref<SystemStatus | null>(null)
  const overview = ref<Overview | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function load(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const [s, o] = await Promise.all([api.systemStatus(), api.overview()])
      status.value = s
      overview.value = o
    } catch (e) {
      error.value = e instanceof ApiError ? e.message : '加载失败'
    } finally {
      loading.value = false
    }
  }

  return { status, overview, loading, error, load }
})
