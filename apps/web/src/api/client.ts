/** HTTP 客户端。开发时经 Vite 代理到 fs-serve，故一律用相对路径。 */

import type {
  ActiveModel,
  ArtifactInfo,
  BatchResult,
  CurvesResponse,
  DeleteResult,
  EvalJobInfo,
  EvalRequest,
  EventPage,
  EventQuery,
  InferResult,
  ModelInfo,
  ModelOption,
  Overview,
  SourceOption,
  SystemStatus,
  TrainJob,
  TrainRun,
} from './types'

const BASE = '/api'

/** 把查询对象拼成 query string，跳过空值——空参数不该出现在 URL 里。 */
function qs(params: Record<string, string | number | undefined>): string {
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === '') continue
    sp.set(k, String(v))
  }
  const s = sp.toString()
  return s ? `?${s}` : ''
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

/** 统一解析错误体，把后端的可读中文说明透传给界面。 */
async function unwrap<T>(res: Response): Promise<T> {
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
  return unwrap<T>(res)
}

/** multipart 上传：绝不能手写 Content-Type，浏览器要自己填 boundary。 */
async function postForm<T>(path: string, form: FormData): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, { method: 'POST', body: form })
  } catch {
    throw new ApiError('无法连接后端服务，请确认已运行 fs-serve', 0)
  }
  return unwrap<T>(res)
}

export interface InferParams {
  weights: string
  conf: number
  iou: number
  device: string
}

export const api = {
  health: () => request<{ status: string; spa_built: boolean }>('/health'),
  systemStatus: () => request<SystemStatus>('/system/status'),
  overview: (days = 30) => request<Overview>(`/overview?days=${days}`),
  models: () => request<ModelInfo[]>('/models'),
  activeModel: () => request<ActiveModel>('/models/active'),

  /** 设为当前权重。run_id 含斜杠，走 body 而不是路径段。 */
  setActiveModel: (runId: string) =>
    request<ActiveModel>('/models/active', {
      method: 'POST',
      body: JSON.stringify({ run_id: runId }),
    }),

  /** 清除指定，回到「取最新归档」。 */
  clearActiveModel: () => request<ActiveModel>('/models/active', { method: 'DELETE' }),

  /** 权重下载交给浏览器原生下载（带 Content-Disposition）。 */
  modelDownloadUrl: (runId: string) =>
    `${BASE}/models/download?run_id=${encodeURIComponent(runId)}`,

  /** 删除归档（移进回收目录，可恢复）。
   *
   * DELETE 带 body：run_id 里含斜杠、走路径会被路由切碎，而 with_run 这种
   * 结构化意图查询参数又表达不清。后端与浏览器都支持，就这么发。
   *
   * with_run 决定是否连同训练现场（曲线、图件、检查点）一起搬走——归档与现场
   * 是两份数据，由用户逐次勾选，不替他绑死。
   */
  deleteModel: (runId: string, withRun = false) =>
    request<DeleteResult>('/models', {
      method: 'DELETE',
      body: JSON.stringify({ run_id: runId, with_run: withRun }),
    }),

  startEval: (payload: EvalRequest) =>
    request<EvalJobInfo>('/eval', { method: 'POST', body: JSON.stringify(payload) }),
  evalJobs: () => request<EvalJobInfo[]>('/eval/jobs'),
  evalJob: (id: string) => request<EvalJobInfo>(`/eval/jobs/${encodeURIComponent(id)}`),

  sources: () => request<SourceOption[]>('/infer/sources'),

  inferImage(file: File, params: InferParams): Promise<InferResult> {
    const form = new FormData()
    form.append('file', file)
    form.append('weights', params.weights)
    form.append('conf', String(params.conf))
    form.append('iou', String(params.iou))
    form.append('device', params.device)
    return postForm<InferResult>('/infer/image', form)
  },

  inferBatch(
    sourceKey: string,
    params: InferParams,
    limit: number,
  ): Promise<BatchResult> {
    return request<BatchResult>('/infer/batch', {
      method: 'POST',
      body: JSON.stringify({ source_key: sourceKey, limit, ...params }),
    })
  },

  // ------------------------------------------------------------------
  // 告警事件
  // ------------------------------------------------------------------

  events: (query: EventQuery = {}) => request<EventPage>(`/events${qs(query)}`),

  markEventFalsePositive: (id: string) =>
    request<{ event_id: string; is_false_positive: boolean }>(
      `/events/${encodeURIComponent(id)}/false-positive`,
      { method: 'POST' },
    ),

  /** 片段直接给 URL 让 <video> 去取，不必把几十 MB 视频走一遍 JS。 */
  eventClipUrl: (id: string) => `${BASE}/events/${encodeURIComponent(id)}/clip`,

  /** 导出交给浏览器原生下载（带 Content-Disposition），前端不解析 CSV。 */
  eventsExportUrl: (query: EventQuery = {}) => `${BASE}/events/export${qs(query)}`,

  // run_id 里含斜杠，走查询参数而不是路径段——否则路由会把它切碎
  trainRuns: () => request<TrainRun[]>('/train/runs'),  trainCurves: (runId: string) =>
    request<CurvesResponse>(`/train/curves?run_id=${encodeURIComponent(runId)}`),
  trainArtifacts: (runId: string) =>
    request<ArtifactInfo[]>(`/train/artifacts?run_id=${encodeURIComponent(runId)}`),

  /** 图件直接给 URL 让 <img> 去取，不必绕一圈 base64。 */
  trainArtifactUrl: (runId: string, name: string) =>
    `${BASE}/train/artifact?run_id=${encodeURIComponent(runId)}&name=${encodeURIComponent(name)}`,

  startTrainJob(payload: TrainJobRequest | TrainResumeRequest): Promise<TrainJob> {
    return request<TrainJob>('/train/jobs', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },

  /** 本机在给定 imgsz 与模型下的 batch 上限。提前提示，用户不必提交一次才拿到 400。
   *  必须带模型：不同规模的激活开销差到 7 倍，只按 imgsz 算会放行超预算的 batch。 */
  trainLimits: (imgsz: number, device: string, model: string) =>
    request<VramLimits>(
      `/train/limits?imgsz=${imgsz}&device=${encodeURIComponent(device)}&model=${encodeURIComponent(model)}`,
    ),

  trainJobs: () => request<TrainJob[]>('/train/jobs'),

  stopTrainJob: (id: string) =>
    request<{ stopped: boolean }>(`/train/jobs/${encodeURIComponent(id)}/stop`, {
      method: 'POST',
    }),

  /** SSE 地址。since 用于断线续传，带上已收到的最后一个 seq。 */
  trainJobStreamUrl: (id: string, since = 0) =>
    `${BASE}/train/jobs/${encodeURIComponent(id)}/stream?since=${since}`,

  /** 可选的预训练模型，附本机是否已有权重。
   *
   * 之前「模型」是自由文本输入框，用户得自己记得 `yolo11s.pt` 的拼写，写错了
   * 要等训练起起来才报「找不到权重」。这里把清单一次给全。
   */
  trainModels: () => request<ModelOption[]>('/train/models'),
}

export interface TrainJobRequest {
  dataset: string
  model: string
  epochs: number
  batch: number
  imgsz: number
  device: string
  fraction: number
  patience: number
  workers: number
  cache: boolean
  seed: number
  /** 非空时从该现场断点续训。其余字段基本不生效——后端取检查点里的几何与 epochs。 */
  resume_run_id?: string
}

/**
 * 断点续训的请求体：**只需要现场与（可选）设备**。
 *
 * 单独一个类型而不是把 TrainJobRequest 的字段都标成可选：续训时几何、总轮数、
 * 学习率轨迹都取自检查点，界面上的那些输入本来就不生效。留成必填会让人以为
 * 「填了就能改」，标成可选又会削弱正常训练那条路径的类型约束。
 */
export interface TrainResumeRequest {
  dataset: string
  resume_run_id: string
  device?: string
}

/** 显存预算。checked=false 表示没做限制（CPU 或探测不到显卡）。 */
export interface VramLimits {
  checked: boolean
  reason?: string
  total_mb?: number
  px_budget?: number
  max_batch?: number | null
  reference?: string
  /** 参与预算的模型主名，如 yolo11n */
  model?: string
  /** 该模型规模相对 yolo11n 的显存开销倍数 */
  factor?: number
  /** false 表示这个模型规模没有实测依据，上限是按 yolo11m 估的 */
  model_measured?: boolean
}
