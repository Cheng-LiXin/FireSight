"""数据流水线编排:检查原料 → 转换 → 报告 → 划分 → 物化 → yaml。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence

from fire_sight_platform.common.constants import (
    DEFAULT_RANDOM_STATE,
    DEFAULT_SPLIT_STRATEGY,
    DEFAULT_TRAIN_RATE,
    DEFAULT_VAL_RATE,
)
from fire_sight_platform.common import paths as paths_mod
from fire_sight_platform.common.performance_utils import time_it
from fire_sight_platform.data_pipeline.convert.service import convert_data_to_yolo
from fire_sight_platform.data_pipeline.report import analyze_balance
from fire_sight_platform.data_pipeline.split.materializer import SplitOutputDirs, SplitSourceDirs, materialize
from fire_sight_platform.data_pipeline.split.split_service import split_dataset
from fire_sight_platform.data_pipeline.split.yaml_writer import write_dataset_yaml

logger = logging.getLogger(__name__)


@dataclass
class TransformResult:
    dataset: str
    processed_root: Path
    yaml_path: Path
    split_counts: Dict[str, int]
    class_counts: Dict[str, int]
    total_boxes: int


class DatasetPipeline:
    """把 raw/<dataset> 变成 processed/<dataset> 可训练成品。"""

    def __init__(
        self,
        dataset: str,
        source_format: str = "yolo",
        class_names: Optional[Sequence[str]] = None,
        train_rate: float = DEFAULT_TRAIN_RATE,
        val_rate: float = DEFAULT_VAL_RATE,
        test_rate: Optional[float] = None,
        seed: int = DEFAULT_RANDOM_STATE,
        split_strategy: str = DEFAULT_SPLIT_STRATEGY,
        raw_root: Optional[Path] = None,
        processed_root: Optional[Path] = None,
    ) -> None:
        self.dataset = dataset
        self.source_format = source_format
        self.train_rate = train_rate
        self.val_rate = val_rate
        self.test_rate = (
            test_rate if test_rate is not None else round(1.0 - train_rate - val_rate, 6)
        )
        self.seed = seed
        self.split_strategy = split_strategy
        self.raw_root = Path(raw_root or (paths_mod.RAW_DATA_DIR / dataset))
        self.out_root = Path(processed_root or (paths_mod.PROCESSED_DATA_DIR / dataset))
        self.class_names = list(class_names) if class_names else ["smoke", "fire"]
        self.class_to_id = {name: i for i, name in enumerate(self.class_names)}

    def _resolve_source_annotation_dir(self) -> Path:
        candidates = [self.raw_root / "annotations", self.raw_root / "labels", self.raw_root]
        patterns = {
            "pascal_voc": ("*.xml",),
            "coco": ("*.json",),
            "yolo": ("*.txt",),
        }.get(self.source_format, ("*.txt",))
        for c in candidates:
            if not c.is_dir():
                continue
            for pat in patterns:
                if any(c.glob(pat)):
                    return c
        raise FileNotFoundError(f"{self.raw_root} 下找不到 {self.source_format} 标注文件")

    @time_it(iterations=1, name="数据流水线", logger_instance=logger)
    def run(self) -> TransformResult:
        images_src = self.raw_root / "images"
        if not images_src.is_dir():
            raise FileNotFoundError(f"缺少图像目录: {images_src}")
        ann_src = self._resolve_source_annotation_dir()

        stage = self.out_root / "_stage" / "labels"
        if stage.exists():
            import shutil

            shutil.rmtree(stage)
        stage.mkdir(parents=True, exist_ok=True)

        n_ann, n_box = convert_data_to_yolo(
            self.source_format, ann_src, images_src, stage, self.class_to_id
        )
        logger.info(f"转换完成: {n_ann} 份标注, {n_box} 个框")

        balance = analyze_balance(stage, self.class_names)
        logger.info("\n" + balance.format_table())
        rare = balance.rare_classes()
        if rare:
            logger.warning(f"稀有类别: {rare}")

        stems = sorted({p.stem for p in images_src.iterdir() if p.is_file()})
        labels_by_stem = {
            p.stem: [ln.split()[0] for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
            for p in stage.glob("*.txt")
        }
        splits = split_dataset(
            self.split_strategy,
            stems,
            self.train_rate,
            self.val_rate,
            self.test_rate,
            self.seed,
            labels_by_stem=labels_by_stem,
        )

        counts = materialize(
            splits,
            SplitSourceDirs(images=images_src, labels=stage),
            SplitOutputDirs(root=self.out_root),
        )
        logger.info(f"物化完成: {counts}")

        yaml_path = write_dataset_yaml(
            self.out_root / "data.yaml",
            dataset_name=self.dataset,
            processed_root=self.out_root,
            names={i: n for i, n in enumerate(self.class_names)},
            meta={
                "source_format": self.source_format,
                "seed": self.seed,
                "train_rate": self.train_rate,
                "val_rate": self.val_rate,
                "test_rate": self.test_rate,
                "split_strategy": self.split_strategy,
                "split_counts": counts,
            },
        )

        stats_path = self.out_root / "transform_stats.json"
        stats = {
            "dataset": self.dataset,
            "source_format": self.source_format,
            "annotations": n_ann,
            "boxes": n_box,
            "class_counts": {s.name: s.count for s in balance.stats},
            "split_counts": counts,
            "seed": self.seed,
        }
        stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

        return TransformResult(
            dataset=self.dataset,
            processed_root=self.out_root,
            yaml_path=yaml_path,
            split_counts=counts,
            class_counts={s.name: s.count for s in balance.stats},
            total_boxes=n_box,
        )


def run_transform(
    dataset: str,
    source_format: str = "yolo",
    class_names: Optional[Sequence[str]] = None,
    seed: int = DEFAULT_RANDOM_STATE,
    train_rate: float = DEFAULT_TRAIN_RATE,
    val_rate: float = DEFAULT_VAL_RATE,
    test_rate: Optional[float] = None,
    split_strategy: str = DEFAULT_SPLIT_STRATEGY,
) -> TransformResult:
    pipeline = DatasetPipeline(
        dataset=dataset,
        source_format=source_format,
        class_names=class_names,
        seed=seed,
        train_rate=train_rate,
        val_rate=val_rate,
        test_rate=test_rate,
        split_strategy=split_strategy,
    )
    return pipeline.run()
