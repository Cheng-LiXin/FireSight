"""随机划分:种子固定即可复现。"""

from __future__ import annotations

from typing import Dict, List, Sequence

from fire_sight_platform.data_pipeline.split.registry import register_strategy
from fire_sight_platform.data_pipeline.split.strategies._common import seeded_shuffled, three_way_counts, validate_rates


@register_strategy("random")
def random_split(
    stems: Sequence[str],
    train_rate: float,
    val_rate: float,
    test_rate: float,
    seed: int,
) -> Dict[str, List[str]]:
    validate_rates(train_rate, val_rate, test_rate)
    ordered = seeded_shuffled(stems, seed)
    n_train, n_val, n_test = three_way_counts(len(ordered), train_rate, val_rate, test_rate)
    train = ordered[:n_train]
    val = ordered[n_train : n_train + n_val]
    test = ordered[n_train + n_val : n_train + n_val + n_test]
    return {"train": train, "val": val, "test": test}
