"""API 响应模型。字段名即前端契约，改动需同步前端类型定义。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class GpuStatus(BaseModel):
    available: bool = False
    name: Optional[str] = None
    memory_total_mb: Optional[int] = None
    memory_used_mb: Optional[int] = None
    utilization_pct: Optional[int] = None
    reason: Optional[str] = None


class GpuJobStatus(BaseModel):
    """GPU 任务锁：holder 非空即表示有任务占用显存。"""

    holder: Optional[str] = None
    waiting: List[str] = Field(default_factory=list)
    busy: bool = False


class WorkspaceStatus(BaseModel):
    root: str
    data_dir: str
    models_dir: str
    events_db: str
    events_db_exists: bool


class ClassInfo(BaseModel):
    """类别契约：id 即 class_id，禁止在别处硬编码顺序。"""

    id: int
    name: str
    name_cn: str


class SystemStatus(BaseModel):
    workspace: WorkspaceStatus
    python_version: str
    gpu: GpuStatus
    gpu_job: GpuJobStatus
    trained_model_count: int
    active_model: Optional[str] = None
    event_count: int = 0
    classes: List[ClassInfo] = Field(default_factory=list)


class AlertSummary(BaseModel):
    total: int = 0
    pending: int = 0
    false_positive_rate: float = 0.0


class DailyPoint(BaseModel):
    date: str
    count: int = 0
    smoke: int = 0
    fire: int = 0
    avg_conf: float = 0.0


class RiskReading(BaseModel):
    """风险尺读数：驱动前端顶部的风险刻度尺。

    score 由近 24 小时告警数与最高置信度合成，取值 0..1；
    level 为离散档位，前端只用它选色，数值以 score 为准。
    """

    score: float = 0.0
    level: str = "normal"
    alerts_24h: int = 0
    peak_conf_24h: float = 0.0
    basis: str = ""


class ModelMetrics(BaseModel):
    run_id: Optional[str] = None
    exists: bool = False
    metrics: Dict[str, Any] = Field(default_factory=dict)


class Overview(BaseModel):
    alerts: AlertSummary
    risk: RiskReading
    class_ratio: Dict[str, int] = Field(default_factory=dict)
    daily: List[DailyPoint] = Field(default_factory=list)
    model: ModelMetrics


# --------------------------------------------------------------------------
# 月度统计（大屏）
# --------------------------------------------------------------------------


class MonthBucket(BaseModel):
    month: str
    count: int = 0
    fire: int = 0
    smoke: int = 0
    false_positives: int = 0
    avg_conf: float = 0.0


class MonthDetail(BaseModel):
    month: str
    total: int = 0
    fire: int = 0
    smoke: int = 0
    false_positives: int = 0
    pending: int = 0
    false_positive_rate: float = 0.0
    avg_conf: float = 0.0
    """24 小时时段分布，下标即小时（0..23）。"""
    hourly: List[int] = Field(default_factory=list)
    """较上月环比（百分比）。上月为 0 时是 null——不是 100，是从 0 到 N 无从比较。"""
    mom_pct: Optional[float] = None


class MonthlyStats(BaseModel):
    months: List[MonthBucket] = Field(default_factory=list)
    detail: MonthDetail


# --------------------------------------------------------------------------
# 告警事件
# --------------------------------------------------------------------------


class AlertEventInfo(BaseModel):
    """一条告警事件。labels 已拆成数组，前端不必再切逗号。

    clip_path 不外泄：只给 has_clip，取片段走 `/api/events/{id}/clip`，
    否则这个字段就是一个能读出任意路径的窗口。
    """

    event_id: str
    ts: str
    frame_index: Optional[int] = None
    labels: List[str] = Field(default_factory=list)
    conf: float = 0.0
    location: str = ""
    message: str = ""
    has_clip: bool = False
    is_false_positive: bool = False


class EventPage(BaseModel):
    """分页结果。total 是**当前筛选下**的总数，不是全库总数——前端据此算页码。"""

    items: List[AlertEventInfo] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0


# --------------------------------------------------------------------------
# 模型
# --------------------------------------------------------------------------


class ModelInfo(BaseModel):
    """一次归档训练。指标缺失时相应字段为 None，前端显示「—」而非 0。"""

    run_id: str
    mAP50: Optional[float] = None
    mAP50_95: Optional[float] = None
    precision: Optional[float] = None
    recall: Optional[float] = None
    epochs: Optional[int] = None
    size_mb: Optional[float] = None
    dataset: Optional[str] = None
    created: Optional[str] = None
    is_active: bool = False
    #: 用旧数据（processed 的 8:1:1 随机划分）训的归档。新数据 val 里有一部分图
    #: 当初是训过的，拿旧归档在新 val 上评估会虚高，所以列表里要标出来。
    legacy: bool = False


class ActiveModel(BaseModel):
    """当前权重的解析结果。

    `source` 说明它怎么来的：`pinned`（用户指定的）/ `fallback-newest`（没指定过，
    取最新）/ `fallback-missing`（指定的已失效，暂用最新）/ `none`（没有归档）。

    `switched` / `switched_from` 说的是「自动换成别的了」：没指定过时「取最新」
    这条退路会让每个新归档悄悄顶掉线上模型，界面据此提醒一次。用户自己点
    「设为当前」不算切换。
    """

    run_id: Optional[str] = None
    pinned: Optional[str] = None
    source: str = "none"
    note: str = ""
    switched: bool = False
    switched_from: Optional[str] = None


class ActiveModelRequest(BaseModel):
    run_id: str = Field(min_length=1)


class DeleteRequest(BaseModel):
    """删除一个归档。两个开关分别对应两份**不同**的数据。

    默认只删归档权重；`with_run` 才连带删训练现场（曲线、图件、检查点）。
    绑死成「一起删」会让人为了清理权重而丢掉现场，反之亦然。
    """

    run_id: str = Field(min_length=1)
    #: 是否连同训练现场（runs/training 下的曲线与图件）一起移进回收目录
    with_run: bool = False


class DeleteResult(BaseModel):
    """删除结果。删除是**移到回收目录**，所以要把去处如实告诉用户。"""

    run_id: str
    removed_archive: bool = False
    removed_run: bool = False
    #: 回收目录（模型库里不再显示，但还在磁盘上）
    trash_dir: Optional[str] = None
    #: 删除后当前权重变成了什么——删掉的恰好是当前权重时必须说明
    active: Optional["ActiveModel"] = None
    #: 一句给人看的结果说明
    note: str = ""


class ModelOption(BaseModel):
    """一个可选的预训练模型（训练界面的「模型」下拉）。"""

    stem: str
    name: str
    #: 权重是否已在本机。False 时 ultralytics 会尝试联网下载，离线就会失败。
    present: bool
    size_mb: Optional[float] = None
    #: 显存规模因子是否有实测依据；False 表示按未知模型保守估
    measured: bool = False
    factor: float = 1.0
    note: str = ""


# --------------------------------------------------------------------------
# 多权重对比评估
# --------------------------------------------------------------------------


class EvalModelResult(BaseModel):
    """一个权重在对比里的评估结果。失败时 error 非空、指标为 None。"""

    run_id: str
    mAP50: Optional[float] = None
    mAP50_95: Optional[float] = None
    precision: Optional[float] = None
    recall: Optional[float] = None
    per_class: Dict[str, float] = Field(default_factory=dict)
    speed_ms: Dict[str, float] = Field(default_factory=dict)
    error: Optional[str] = None


class EvalJobInfo(BaseModel):
    """一次对比评估任务。同 SSE 一样，前端轮询它拿进度。

    `done` / `total` 让界面能画「正在评估 2/3」——逐个模型出结果，
    不必等到全部跑完才有反馈。
    """

    id: str
    run_ids: List[str] = Field(default_factory=list)
    dataset: Optional[str] = None
    imgsz: int = 640
    split: str = "val"
    status: str = "running"
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    done: int = 0
    total: int = 0
    results: List[EvalModelResult] = Field(default_factory=list)
    error: Optional[str] = None


class EvalRequest(BaseModel):
    """发起一次对比。run_ids 至少两个——单个权重用不到「对比」。

    `split` 选在哪个划分上评估。默认 `val`——它是平时选模型用的那一份；
    `test` 留给最终汇报，因为它从未参与任何决策（选模型、调参、比对照），
    在这上面出的数才是「没被挑过的」。写别的值由路由层拒绝并列出可选值。
    """

    run_ids: List[str] = Field(min_length=2, max_length=5)
    imgsz: int = Field(640, ge=32, le=2048)
    dataset: str = ""
    split: str = "val"
    device: str = "0"


# --------------------------------------------------------------------------
# 识别检测
# --------------------------------------------------------------------------


class Detection(BaseModel):
    """单个检测框。name 是契约名，name_cn 供界面显示。"""

    cls_id: int
    name: str
    name_cn: str
    conf: float
    xyxy: List[float]


class InferResult(BaseModel):
    detections: List[Detection] = Field(default_factory=list)
    count: int = 0
    classes: List[str] = Field(default_factory=list)
    max_conf: float = 0.0
    elapsed_ms: float = 0.0
    width: int = 0
    height: int = 0
    weights: str = ""
    device: str = "cpu"
    # 标注图的 data URL；批量场景为 None
    annotated: Optional[str] = None


class BatchRow(BaseModel):
    file: str
    count: int = 0
    classes: List[str] = Field(default_factory=list)
    max_conf: float = 0.0
    elapsed_ms: float = 0.0


class BatchResult(BaseModel):
    rows: List[BatchRow] = Field(default_factory=list)
    files: int = 0
    hit_files: int = 0
    total_dets: int = 0
    avg_ms: float = 0.0
    class_totals: Dict[str, int] = Field(default_factory=dict)
    weights: str = ""
    device: str = "cpu"


# --------------------------------------------------------------------------
# 训练中心
# --------------------------------------------------------------------------


class TrainRun(BaseModel):
    """一次训练的现场记录。archived 表示其结果已归档到 models/trained。"""

    run_id: str
    dataset: Optional[str] = None
    model: Optional[str] = None
    #: 训练几何，取自现场 args.yaml。同一个数据集在不同 batch / imgsz 下训出来的
    #: 结果不可直接比——列表里看得见才不用去翻 args.yaml。
    #: 早期现场可能没有 args.yaml（或其中缺这两个键），所以是 Optional。
    batch: Optional[int] = None
    imgsz: Optional[int] = None
    epochs_done: int = 0
    archived: bool = False
    mAP50: Optional[float] = None
    mAP50_95: Optional[float] = None
    precision: Optional[float] = None
    recall: Optional[float] = None
    artifact_count: int = 0
    created: Optional[str] = None
    #: 能否从这个现场断点续训。判据是 last.pt 里还有没有优化器状态——
    #: 跑完的训练会被 strip_optimizer 剥掉，从它「续」等于换个权重微调。
    resumable: bool = False
    #: 不能续训的原因（可续时为空串）。界面直接显示，不猜。
    resume_note: str = ""
    #: 检查点里记录的已完成 / 总轮数，供界面说明「从第 41 轮继续」
    resume_done: Optional[int] = None
    resume_total: Optional[int] = None


class CurvesResponse(BaseModel):
    """训练曲线。各序列等长于 epochs，缺失值以 null 表示断点。"""

    run_id: str
    epochs: List[int] = Field(default_factory=list)
    losses: Dict[str, List[Optional[float]]] = Field(default_factory=dict)
    metrics: Dict[str, List[Optional[float]]] = Field(default_factory=dict)
    lr: Dict[str, List[Optional[float]]] = Field(default_factory=dict)
    total_epochs: int = 0


class ArtifactInfo(BaseModel):
    """训练现场图件。kind 供前端分组，避免把一堆文件名平铺出来。"""

    name: str
    kind: str
    size: int


class SourceOption(BaseModel):
    """可批量推理的目录。

    key 由后端生成、前端只回传，不允许前端拼任意路径——
    免得接口变成一个能遍历工作区的文件浏览器。
    """

    key: str
    dataset: str
    split: str
    path: str
    image_count: int


# --------------------------------------------------------------------------
# 火情分析报告
# --------------------------------------------------------------------------


class ReportRequest(BaseModel):
    """一次生成请求。start/end 是日期（YYYY-MM-DD），含两端。

    pattern 卡在 schema 层：8 个字符的乱码（`abcdefgh`）不该一路走到聚合、
    被当成「所选时间段没有告警记录」拒绝——那是两回事。
    """

    start: str = Field(
        min_length=8,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="起始日期 YYYY-MM-DD",
    )
    end: str = Field(
        min_length=8,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="结束日期 YYYY-MM-DD",
    )


class ReportJobInfo(BaseModel):
    """一次生成任务。stage 是离散阶段标签——生成没有可信的百分比。"""

    id: str
    status: str = "running"
    stage: str = ""
    start: str = ""
    end: str = ""
    error: Optional[str] = None
    report_id: Optional[str] = None
    created_at: Optional[str] = None


class ReportListItem(BaseModel):
    id: str
    title: str = ""
    start: str = ""
    end: str = ""
    model: str = ""
    created_at: Optional[str] = None


class ReportDetail(BaseModel):
    meta: Dict[str, Any] = Field(default_factory=dict)
    markdown: str = ""
