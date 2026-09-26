"""evaluation: 模型评估服务(fs-val)。"""

from fire_sight_platform.evaluation.service import evaluate_weights, compare_runs

__all__ = ["evaluate_weights", "compare_runs"]
