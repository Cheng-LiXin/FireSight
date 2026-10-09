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

// --------------------------------------------------------------------------
// 月度统计（大屏）
// --------------------------------------------------------------------------

export interface MonthBucket {
  month: string
  count: number
  fire: number
  smoke: number
  false_positives: number
  avg_conf: number
}

export interface MonthDetail {
  month: string
  total: number
  fire: number
  smoke: number
  false_positives: number
  pending: number
  false_positive_rate: number
  avg_conf: number
  /** 24 个小时桶，下标即小时 */
  hourly: number[]
  /** 较上月环比（%）；上月为 0 时是 null——不是 0，是无从比较 */
  mom_pct: number | null
}

export interface MonthlyStats {
  months: MonthBucket[]
  detail: MonthDetail
}

// --------------------------------------------------------------------------
// 实时监控
// --------------------------------------------------------------------------

export type MonitorStatusKind =
  | 'idle'
  | 'connecting'
  | 'running'
  | 'stopping'
  | 'stopped'
  | 'ended'
  | 'failed'

export interface MonitorStatus {
  status: MonitorStatusKind
  source: string
  camera_name: string
  started_at: string | null
  finished_at: string | null
  frames: number
  alerts: number
  reconnects: number
  error: string | null
  // 人工暂停播报（后端 TTS 单例上的状态，每次 status 现读）
  tts_paused?: boolean
}

export interface MonitorStartParams {
  source: string
  camera_name?: string
  conf?: number
  iou?: number
  device?: string
  enable_tts?: boolean
  clip_before_sec?: number
  clip_after_sec?: number
}

/** 视频源下拉里的一项。key 原样交给 MonitorStart.source，界面不拼装地址。 */
export interface MonitorSourceOption {
  key: string
  label: string
  /** camera / stream / file——只为显示分组用 */
  kind: string
  /** 分辨率、帧率或完整地址，作为标签旁的补充 */
  detail: string
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
  /** 用旧数据（processed 的 8:1:1 随机划分）训的。
   *  新 val 里有一部分图当初进过旧 train，拿它评旧归档会虚高，列表要标出来。 */
  legacy: boolean
}

/** 当前权重的解析结果。source 说明「为什么是它」，note 是给人看的一句解释。 */
export interface ActiveModel {
  run_id: string | null
  pinned: string | null
  source: 'pinned' | 'fallback-newest' | 'fallback-missing' | 'none'
  note: string
  /** 自动换成别的归档了（没指定过时「取最新」会这样）。用户自己设的不算。 */
  switched: boolean
  switched_from: string | null
}

/** 删除归档的结果。
 *
 * `removed_archive` / `removed_run` 分别如实回报两类数据有没有真的被搬走；
 * `note` 是给人看的一句话（搬了什么、当前权重是否跟着换了）。
 */
export interface DeleteResult {
  run_id: string
  removed_archive: boolean
  removed_run: boolean
  trash_dir: string | null
  /** 删除之后的当前权重。删掉的恰好是它时，这里就是回落结果 */
  active: ActiveModel | null
  note: string
}

/** 可选的预训练模型。present=false 表示本机没有权重，选了会联网下载。 */
export interface ModelOption {
  stem: string
  /** 传给后端的值，如 yolo11s.pt */
  name: string
  present: boolean
  size_mb: number | null
  /** false 表示这个规模没有实测显存数据，上限是按 yolo11m 估的 */
  measured: boolean
  /** 相对 yolo11n 的显存开销倍数 */
  factor: number
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
  /** 在哪个划分上评估。val 用来选模型；test 只留给最终汇报，两者的数不可混着比。 */
  split?: EvalSplit
  device?: string
}

/** 允许评估的划分。train 故意不在里面——在训过的数据上报指标没有意义。 */
export type EvalSplit = 'val' | 'test'

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
  /** 训练几何，取自现场 args.yaml。不同 batch / imgsz 的结果不可直接比。 */
  batch: number | null
  imgsz: number | null
  epochs_done: number
  archived: boolean
  mAP50: number | null
  mAP50_95: number | null
  precision: number | null
  recall: number | null
  artifact_count: number
  created: string | null
  /** 能否从这个现场断点续训。跑完的训练会被 strip_optimizer 剥掉优化器状态。 */
  resumable: boolean
  /** 不能续训的原因（可续时为空串）。直接显示，不猜。 */
  resume_note: string
  /** 检查点里记录的已完成 / 总轮数，用于说明「从第 N+1 轮继续」 */
  resume_done: number | null
  resume_total: number | null
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
  /** 处置状态。后端保证是下面四个值之一，不会是空串。 */
  review_status: EventReviewStatus
}

/** 处置状态。四态互斥，合起来等于全库。
 *
 *  为什么不是原来的两态：is_false_positive 只回答「是不是误报」，回答不了
 *  「看没看过、处理了没」。值班的人要能区分「刚响还没看」和「确认过了但
 *  现场还没处理完」。
 *
 *  is_false_positive 与它并存而非替代：前者是统计口径（误报率、月度图表），
 *  后者是界面口径。后端保证两者永远同步，这里不要自己推导。 */
export type EventReviewStatus = 'pending' | 'confirmed' | 'false_positive' | 'resolved'

/** 状态变更接口的返回值。后端写完后回读，两列一起给。 */
export interface EventStatusResult {
  event_id: string
  review_status: EventReviewStatus
  is_false_positive: boolean
}

/** 分页结果。total 是**当前筛选下**的总数，不是全库总数。 */
export interface EventPage {
  items: AlertEventInfo[]
  total: number
  limit: number
  offset: number
}

export type EventStatusFilter = 'all' | EventReviewStatus

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

// --------------------------------------------------------------------------
// 火情分析报告
// --------------------------------------------------------------------------

export type ReportJobStatus = 'running' | 'done' | 'failed'

export interface ReportJob {
  id: string
  status: ReportJobStatus
  /** 离散阶段标签：聚合数据 / 模型生成中 / 归档中。不做假百分比 */
  stage: string
  start: string
  end: string
  error: string | null
  report_id: string | null
  created_at: string | null
}

export interface ReportListItem {
  id: string
  title: string
  start: string
  end: string
  model: string
  created_at: string | null
}

export interface ReportDetail {
  meta: Record<string, unknown>
  markdown: string
}
