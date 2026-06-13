from __future__ import annotations

import random

import torch


def set_deterministic(seed: int) -> None:
    """Set deterministic CPU execution knobs used by the Phase 4 harness."""

    random.seed(seed)
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True)
    if torch.backends.cudnn.is_available():
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False
