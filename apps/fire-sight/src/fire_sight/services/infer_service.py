"""识别服务：单图 / 批量。

模型按权重懒加载并缓存在实例上；调用方应复用实例（见 api/deps.get_infer_service），
否则每次请求都会重新读一遍权重，单图推理会慢到不可接受。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from fire_sight_platform.common.constants import IMAGE_EXTENSIONS

DEFAULT_CONF = 0.25
DEFAULT_IOU = 0.6


class InferService:
    def __init__(
        self,
        weights: str | Path,
        conf: float = DEFAULT_CONF,
        device: str = "0",
        iou: float = DEFAULT_IOU,
    ) -> None:
        self.weights = str(weights)
        self.conf = conf
        self.iou = iou
        self.device = device
        self._model = None

    def _load(self):
        if self._model is None:
            from ultralytics import YOLO

            self._model = YOLO(self.weights)
        return self._model

    def infer_array(
        self,
        image_bgr: np.ndarray,
        *,
        conf: Optional[float] = None,
        iou: Optional[float] = None,
        plot: bool = True,
    ) -> Dict[str, Any]:
        """对单张 BGR 图像推理。

        plot=False 跳过标注图渲染——批量场景只要统计数字，渲染是纯浪费。
        此时 annotated 为 None。
        """
        model = self._load()
        from fire_sight.scene.class_map import display_name

        t0 = time.perf_counter()
        results = model.predict(
            source=image_bgr,
            conf=self.conf if conf is None else conf,
            iou=self.iou if iou is None else iou,
            device=self.device,
            verbose=False,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000

        r = results[0]
        dets: List[Dict[str, Any]] = []
        if r.boxes is not None and len(r.boxes) > 0:
            for b in r.boxes:
                cls_id = int(b.cls[0])
                dets.append(
                    {
                        "cls_id": cls_id,
                        "name": display_name(cls_id),
                        "conf": float(b.conf[0]),
                        "xyxy": [float(x) for x in b.xyxy[0].tolist()],
                    }
                )

        dets.sort(key=lambda d: d["conf"], reverse=True)
        return {
            "detections": dets,
            "annotated": r.plot() if plot else None,
            "elapsed_ms": round(elapsed_ms, 2),
            "count": len(dets),
            "classes": sorted({d["name"] for d in dets}),
            "max_conf": round(max((d["conf"] for d in dets), default=0.0), 4),
        }

    def infer_image_path(
        self, path: Path | str, *, conf: Optional[float] = None,
        iou: Optional[float] = None, plot: bool = True,
    ) -> Dict[str, Any]:
        import cv2

        img = cv2.imread(str(path))
        if img is None:
            raise ValueError(f"无法解码图像: {path}")
        out = self.infer_array(img, conf=conf, iou=iou, plot=plot)
        out["path"] = str(path)
        return out

    def infer_batch(
        self,
        directory: Path | str,
        limit: Optional[int] = None,
        *,
        conf: Optional[float] = None,
        iou: Optional[float] = None,
        plot: bool = False,
    ) -> List[Dict[str, Any]]:
        directory = Path(directory)
        if not directory.is_dir():
            raise ValueError(f"目录不存在: {directory}")
        paths = sorted(
            p for p in directory.iterdir()
            if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
        )
        if limit:
            paths = paths[:limit]
        return [
            self.infer_image_path(p, conf=conf, iou=iou, plot=plot) for p in paths
        ]
