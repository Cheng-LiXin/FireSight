"""划分服务层:校验依赖 → 调策略函数。"""

from __future__ import annotations

from typing import Dict, List, Sequence

from fire_sight_platform.data_pipeline.split.registry import get_strategy


def split_dataset(
    strategy: str,
    stems: Sequence[str],
    train_rate: float,
    val_rate: float,
    test_rate: float,
    seed: int,
) -> Dict[str, List[str]]:
    entry = get_strategy(strategy)
    return entry.func(
        stems,
        train_rate=train_rate,
        val_rate=val_rate,
        test_rate=test_rate,
        seed=seed,
    )
