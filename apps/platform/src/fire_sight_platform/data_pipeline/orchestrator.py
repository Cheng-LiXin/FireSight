"""数据流水线编排:检查原料 → 转换 → 报告 → 划分 → 物化 → yaml。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence

from fire_sight_platform.common.constants import (
    DEFAULT_FIRE_SMOKE_CLASSES,
    DEFAULT_RANDOM_STATE,
    DEFAULT_SPLIT_STRATEGY,
    DEFAULT_TRAIN_RATE,
    DEFAULT_VAL_RATE,
    IMAGE_EXTENSIONS,
    assert_canonical_class_order,
)
from fire_sight_platform.common import paths as paths_mod
from fire_sight_platform.common.naming import safe_child
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
        name: Optional[str] = None,
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

        # 输入与输出分开：dataset 定位 data/raw/ 下的原始数据（可能形如
        # "D-Fire/train"，带斜杠表示原始数据集已自行切分），name 是产物目录名，
        # 默认与 dataset 相同。想要一个不同的产物名时显式传 name，
        # 不必再靠改动 dataset 去迁就路径。
        #
        # 两者都来自命令行，所以都必须夹在各自的数据根内：下游 materialize()
        # 会 `shutil.rmtree(out_root/images/<split>)`，早先直接 `ROOT / name`
        # 拼接时 `--name ../../OUTSIDE` 会真的删掉工作区外的目录（已实测复现）。
        # 显式传入的 raw_root / processed_root 是调用方自己的选择（测试用临时
        # 目录），不在这里改写。
        self.name = name or dataset
        self.raw_root = (
            Path(raw_root)
            if raw_root is not None
            else safe_child(paths_mod.RAW_DATA_DIR, dataset)
        )
        self.out_root = (
            Path(processed_root)
            if processed_root is not None
            else safe_child(paths_mod.PROCESSED_DATA_DIR, self.name)
        )

        self.class_names = (
            list(class_names) if class_names else list(DEFAULT_FIRE_SMOKE_CLASSES)
        )
        # 顺序即 class_id，错序会静默调换明火与烟雾，故在入口直接拒绝
        assert_canonical_class_order(self.class_names)
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

        # 只收图片。不过滤的话 Windows 自带的 `Thumbs.db`（还有 macOS 的
        # `.DS_Store`）会被当成一个样本，物化时找不到对应图像，
        # `materializer._copy_pair` 抛 `FileNotFoundError: 缺少图像: Thumbs`
        # 直接中止整条流水线（2026-10-09 代码审查 R7）。
        stems = sorted(
            {
                p.stem
                for p in images_src.iterdir()
                if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
            }
        )
        if not stems:
            raise FileNotFoundError(
                f"{images_src} 下没有图片（后缀 {IMAGE_EXTENSIONS}）——"
                "确认原始数据的图像放在 images/ 里"
            )
        splits = split_dataset(
            self.split_strategy,
            stems,
            self.train_rate,
            self.val_rate,
            self.test_rate,
            self.seed,
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
    name: Optional[str] = None,
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
        name=name,
    )
    return pipeline.run()
