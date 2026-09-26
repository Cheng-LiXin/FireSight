"""识别服务：单图/批量，供识别页调用。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np


class InferService:
    def __init__(self, weights: str | Path, conf: float = 0.25, device: str = "0") -> None:
        self.weights = str(weights)
        self.conf = conf
        self.device = device
        self._model = None

    def _load(self):
        if self._model is None:
            from ultralytics import YOLO

            self._model = YOLO(self.weights)
        return self._model

    def infer_array(self, image_bgr: np.ndarray) -> Dict[str, Any]:
        model = self._load()
        from fire_sight.scene.class_map import display_name
        t0 = time.perf_counter()
        results = model.predict(
            source=image_bgr, conf=self.conf, device=self.device, verbose=False
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
        annotated = r.plot()
        return {
            "detections": dets,
            "annotated": annotated,
            "elapsed_ms": round(elapsed_ms, 2),
            "count": len(dets),
            "classes": sorted({d["name"] for d in dets}),
        }

    def infer_image_path(self, path: Path | str) -> Dict[str, Any]:
        import cv2

        img = cv2.imread(str(path))
        if img is None:
            raise FileNotFoundError(f"无法读图: {path}")
        out = self.infer_array(img)
        out["path"] = str(path)
        return out

    def infer_batch(self, directory: Path | str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        directory = Path(directory)
        exts = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
        paths = sorted(p for p in directory.iterdir() if p.suffix.lower() in exts)
        if limit:
            paths = paths[:limit]
        return [self.infer_image_path(p) for p in paths]
