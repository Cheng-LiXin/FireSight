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
# 模型
# --------------------------------------------------------------------------


class ModelInfo(BaseModel):
    """一次归档训练。指标缺失时相应字段为 None，前端显示「—」而非 0。"""

    run_id: str
    mAP50: Optional[float] = None
    precision: Optional[float] = None
    recall: Optional[float] = None
    created: Optional[str] = None
    is_active: bool = False


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
