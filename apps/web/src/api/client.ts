/** HTTP 客户端。开发时经 Vite 代理到 fs-serve，故一律用相对路径。 */

import type { Overview, SystemStatus } from './types'

const BASE = '/api'

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    })
  } catch {
    // 后端没起来是这个项目最常见的失败，给一句能直接照做的提示
    throw new ApiError('无法连接后端服务，请确认已运行 fs-serve', 0)
  }

  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (body?.detail) detail = String(body.detail)
    } catch {
      /* 响应不是 JSON，保留状态行即可 */
    }
    throw new ApiError(detail, res.status)
  }

  return (await res.json()) as T
}

export const api = {
  health: () => request<{ status: string; spa_built: boolean }>('/health'),
  systemStatus: () => request<SystemStatus>('/system/status'),
  overview: (days = 30) => request<Overview>(`/overview?days=${days}`),
}
