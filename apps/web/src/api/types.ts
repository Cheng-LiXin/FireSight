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
  precision: number | null
  recall: number | null
  created: string | null
  is_active: boolean
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
