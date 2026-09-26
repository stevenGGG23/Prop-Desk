"""
Room and risk ladder calculations.

All functions operate on plain numbers (float/int) so they can be called
outside of a request context (e.g., from the Monte Carlo engine or tests).
"""

from __future__ import annotations
import math
from typing import Optional


FRICTION_DEFAULT = 25.0  # USD per trade: commission + slippage estimate


def room(current_balance: float, max_loss_limit: float) -> float:
    """Dollars between current balance and the max loss floor."""
    return current_balance - max_loss_limit


def losses_survivable(current_balance: float, max_loss_limit: float,
                      risk_per_trade: float, friction: float = FRICTION_DEFAULT) -> int:
    """
    How many full losses (at the given risk setting, plus friction) the
    account can absorb before hitting the floor.
    """
    r = room(current_balance, max_loss_limit)
    cost_per_loss = risk_per_trade + friction
    if cost_per_loss <= 0:
        return 0
    return math.floor(r / cost_per_loss)


def max_risk_for_n_losses(current_balance: float, max_loss_limit: float,
                           n: int, friction: float = FRICTION_DEFAULT) -> float:
    """
    The largest risk setting that still leaves room for n losses.
    Returns 0 if there is not enough room even after removing friction.
    """
    r = room(current_balance, max_loss_limit)
    result = (r / n) - friction
    return max(result, 0.0)


def risk_ladder(current_balance: float, max_loss_limit: float,
                friction: float = FRICTION_DEFAULT,
                loss_counts: tuple = (2, 3, 4, 5, 6)) -> list[dict]:
    """
    Return a list of dicts, one per loss count, each showing:
      - n: the loss count
      - max_risk: the maximum risk setting that survives n losses
    """
    result = []
    for n in loss_counts:
        result.append({
            "n": n,
            "max_risk": max_risk_for_n_losses(current_balance, max_loss_limit, n, friction),
        })
    return result


def update_eod_floor(current_balance: float, peak_balance: float,
                     max_loss_limit: float, drawdown_amount: float,
                     lock_threshold: float) -> tuple[float, float]:
    """
    Apply end-of-day trailing floor logic.

    Returns (new_peak_balance, new_max_loss_limit).

    Once the floor reaches lock_threshold it never moves again.
    """
    if float(max_loss_limit) >= float(lock_threshold):
        # Floor already locked — nothing changes
        return peak_balance, max_loss_limit

    if current_balance > peak_balance:
        peak_balance = current_balance
        candidate = peak_balance - drawdown_amount
        max_loss_limit = min(candidate, lock_threshold)

    return peak_balance, max_loss_limit


def check_risk_warning(current_risk: float, current_balance: float,
                       max_loss_limit: float, warning_n: int = 3,
                       friction: float = FRICTION_DEFAULT) -> bool:
    """
    Return True if current_risk exceeds the threshold for warning_n survivable losses.
    """
    threshold = max_risk_for_n_losses(current_balance, max_loss_limit, warning_n, friction)
    return current_risk > threshold
