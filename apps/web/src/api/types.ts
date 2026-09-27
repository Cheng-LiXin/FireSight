/** 与后端 fire_sight/api/schemas.py 一一对应。改动需两侧同步。 */

export interface GpuStatus {
  available: boolean
  name?: string | null
  memory_total_mb?: number | null
  memory_used_mb?: number | null
  utilization_pct?: number | null
  reason?: string | null
}

export interface GpuJobStatus {
  holder: string | null
  waiting: string[]
  busy: boolean
}

export interface WorkspaceStatus {
  root: string
  data_dir: string
  models_dir: string
  events_db: string
  events_db_exists: boolean
}

export interface ClassInfo {
  id: number
  name: string
  name_cn: string
}

export interface SystemStatus {
  workspace: WorkspaceStatus
  python_version: string
  gpu: GpuStatus
  gpu_job: GpuJobStatus
  trained_model_count: number
  active_model: string | null
  event_count: number
  classes: ClassInfo[]
}

export interface AlertSummary {
  total: number
  pending: number
  false_positive_rate: number
}

export interface DailyPoint {
  date: string
  count: number
  smoke: number
  fire: number
  avg_conf: number
}

export type RiskLevel = 'normal' | 'watch' | 'warn' | 'alert'

export interface RiskReading {
  score: number
  level: RiskLevel
  alerts_24h: number
  peak_conf_24h: number
  basis: string
}

export interface ModelMetrics {
  run_id: string | null
  exists: boolean
  metrics: Record<string, string>
}

export interface Overview {
  alerts: AlertSummary
  risk: RiskReading
  class_ratio: Record<string, number>
  daily: DailyPoint[]
  model: ModelMetrics
}

/** 类别名 → 中文名。唯一真相在后端 class_map，这里只做显示映射的兜底。 */
export const CLASS_CN: Record<string, string> = {
  smoke: '烟雾',
  fire: '明火',
}

// --------------------------------------------------------------------------
// 模型
// --------------------------------------------------------------------------

export interface ModelInfo {
  run_id: string
  /** 指标缺失时为 null——「没测过」和「测得 0 分」不是一回事，不要显示 0 */
  mAP50: number | null
  mAP50_95: number | null
  precision: number | null
  recall: number | null
  /** 训练配置里的轮数；归档没记时为 null */
  epochs: number | null
  size_mb: number | null
  dataset: string | null
  created: string | null
  is_active: boolean
}

/** 当前权重的解析结果。source 说明「为什么是它」，note 是给人看的一句解释。 */
export interface ActiveModel {
  run_id: string | null
  pinned: string | null
  source: 'pinned' | 'fallback-newest' | 'fallback-missing' | 'none'
  note: string
}

// --------------------------------------------------------------------------
// 多权重对比评估
// --------------------------------------------------------------------------

/** 一个权重在对比里的结果。键集合固定，失败时 error 非空、指标为 null。 */
export interface EvalModelResult {
  run_id: string
  mAP50: number | null
  mAP50_95: number | null
  precision: number | null
  recall: number | null
  per_class: Record<string, number>
  speed_ms: Record<string, number>
  error: string | null
}

export interface EvalJobInfo {
  id: string
  run_ids: string[]
  dataset: string | null
  imgsz: number
  split: string
  status: 'running' | 'done' | 'failed'
  started_at: string | null
  finished_at: string | null
  /** 已出结果的模型数 / 总数——界面据此画「正在评估 2/3」 */
  done: number
  total: number
  results: EvalModelResult[]
  error: string | null
}

export interface EvalRequest {
  run_ids: string[]
  imgsz?: number
  dataset?: string
  device?: string
}

// --------------------------------------------------------------------------
// 识别检测
// --------------------------------------------------------------------------

export interface Detection {
  cls_id: number
  name: string
  name_cn: string
  conf: number
  xyxy: number[]
}

export interface InferResult {
  detections: Detection[]
  count: number
  classes: string[]
  max_conf: number
  elapsed_ms: number
  width: number
  height: number
  weights: string
  device: string
  annotated: string | null
}

export interface SourceOption {
  key: string
  dataset: string
  split: string
  path: string
  image_count: number
}

export interface BatchRow {
  file: string
  count: number
  classes: string[]
  max_conf: number
  elapsed_ms: number
}

export interface BatchResult {
  rows: BatchRow[]
  files: number
  hit_files: number
  total_dets: number
  avg_ms: number
  class_totals: Record<string, number>
  weights: string
  device: string
}

// --------------------------------------------------------------------------
// 训练中心
// --------------------------------------------------------------------------

export interface TrainRun {
  run_id: string
  dataset: string | null
  model: string | null
  epochs_done: number
  archived: boolean
  mAP50: number | null
  mAP50_95: number | null
  precision: number | null
  recall: number | null
  artifact_count: number
  created: string | null
}

/** 曲线序列可含 null：那一轮该列缺失，前端应断开折线而非画到 0。 */
export type CurveSeries = (number | null)[]

export interface CurvesResponse {
  run_id: string
  epochs: number[]
  losses: Record<string, CurveSeries>
  metrics: Record<string, CurveSeries>
  lr: Record<string, CurveSeries>
  total_epochs: number
}

export interface ArtifactInfo {
  name: string
  kind: 'confusion' | 'curves' | 'pr' | 'labels' | 'batch' | 'other'
  size: number
}

export type JobStatus = 'running' | 'stopping' | 'done' | 'failed' | 'stopped'

// --------------------------------------------------------------------------
// 告警事件
// --------------------------------------------------------------------------

/** 一条告警事件。labels 后端已拆成数组；片段地址由 eventClipUrl 拼，不在这里。 */
export interface AlertEventInfo {
  event_id: string
  /** ISO 字符串，形如 2026-09-27T15:03:07 */
  ts: string
  frame_index: number | null
  labels: string[]
  conf: number
  location: string
  message: string
  has_clip: boolean
  is_false_positive: boolean
}

/** 分页结果。total 是**当前筛选下**的总数，不是全库总数。 */
export interface EventPage {
  items: AlertEventInfo[]
  total: number
  limit: number
  offset: number
}

export type EventStatusFilter = 'all' | 'pending' | 'false_positive'

/** 用 type 而不是 interface：interface 没有隐式索引签名，
 *  不能赋给 client.ts 里 qs() 的 Record<string, ...> 形参。 */
export type EventQuery = {
  keyword?: string
  label?: string
  status?: EventStatusFilter
  start?: string
  end?: string
  limit?: number
  offset?: number
}

export interface TrainJob {
  id: string
  dataset: string
  epochs: number
  status: JobStatus
  started_at: string
  finished_at: string | null
  /** 收到停止请求的时刻。非 null 表示这次训练是被主动停止的。 */
  stop_requested_at: string | null
  run_id: string | null
  error: string | null
  returncode: number | null
  config: Record<string, unknown>
  event_count: number
}

/** SSE 事件。type 决定哪些字段有值，其余为 undefined。 */
export interface TrainStreamEvent {
  type: 'start' | 'epoch' | 'log' | 'done' | 'error' | 'exit' | 'close'
  seq?: number
  epoch?: number
  epochs?: number
  metrics?: Record<string, number>
  losses?: Record<string, number>
  text?: string
  message?: string
  run_id?: string
  code?: number
  status?: JobStatus
}
