"""划分策略公共件:比例校验、三方计数、带种子洗牌。"""

from __future__ import annotations

import random
from typing import Dict, List, Sequence, Tuple

from fire_sight_platform.common.constants import RATE_EPSILON


def validate_rates(train: float, val: float, test: float) -> None:
    if any(r < 0 for r in (train, val, test)):
        raise ValueError(f"划分比例不可为负: {train, val, test}")
    if abs(train + val + test - 1.0) > RATE_EPSILON:
        raise ValueError(f"划分比例之和须为 1: {train}+{val}+{test}")


def three_way_counts(n: int, train: float, val: float, test: float) -> Tuple[int, int, int]:
    n_train = int(n * train)
    n_val = int(n * val)
    n_test = n - n_train - n_val
    return n_train, n_val, n_test


def seeded_shuffled(items: Sequence[str], seed: int) -> List[str]:
    rng = random.Random(seed)
    out = list(items)
    rng.shuffle(out)
    return out
