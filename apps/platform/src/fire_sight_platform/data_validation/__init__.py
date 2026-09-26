"""data_validation - 只读数据质检。"""

from fire_sight_platform.data_validation.service import run_all_checks, validate_dataset

__all__ = ["validate_dataset", "run_all_checks"]
