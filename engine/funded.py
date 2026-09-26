"""
Funded phase payout projections.

The key output is the withdrawal discipline curve:
monthly income and breach probability as a function of how much
cushion you leave in the account after each withdrawal.
"""

from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Optional
import numpy as np

N_RUNS = 10_000
MAX_DAYS = 365


def simulate_funded_monthly(
    current_balance: float,
    max_loss_limit: float,
    drawdown_amount: float,
    lock_threshold: float,
    payout_cap: float,
    profit_split: float,
    min_balance_to_withdraw: float,
    win_multiples: list[float],
    loss_multiples: list[float],
    risk: float,
    base_risk: float = 1000.0,
    signal_frequency: float = 0.68,
    payout_frequency_days: int = 1,
    qualifying_day_min: float = 0.0,
    n_runs: int = N_RUNS,
    rng: Optional[np.random.Generator] = None,
) -> dict:
    """
    Simulate a funded account for 90 days (one quarter) and estimate
    monthly income.
    """
    if rng is None:
        rng = np.random.default_rng()

    wins = np.array([m for m in win_multiples if m > 0], dtype=float)
    losses = np.array([m for m in loss_multiples if m < 0], dtype=float)
    if not wins.size or not losses.size:
        raise ValueError("The distribution must include at least one win and one loss.")
    if n_runs < 1 or payout_frequency_days < 1:
        raise ValueError("Simulation runs and payout interval must be positive.")
    if (not 0 <= signal_frequency <= 1 or risk <= 0 or payout_cap <= 0
            or not 0 < profit_split <= 1):
        raise ValueError("Signal frequency, risk, payout cap, or profit split is invalid.")
    win_prob = len(wins) / (len(wins) + len(losses))

    total_payouts = []
    breach_count = 0
    simulation_start = datetime.now(timezone.utc).date()

    for _ in range(n_runs):
        bal = current_balance
        peak = current_balance
        floor = max_loss_limit
        payout_total = 0.0
        breached = False
        days_since_payout = 0

        for day in range(90):
            is_weekday = (simulation_start + timedelta(days=day)).weekday() < 5
            is_signal_day = is_weekday and rng.random() < signal_frequency
            trade_pnl = 0.0
            if is_signal_day:
                is_win = rng.random() < win_prob
                if is_win:
                    multiple = rng.choice(wins)
                else:
                    multiple = rng.choice(losses)
                trade_pnl = risk * multiple
                bal += trade_pnl

            # EOD floor update
            if floor < lock_threshold:
                if bal > peak:
                    peak = bal
                candidate = peak - drawdown_amount
                floor = min(candidate, lock_threshold)

            if bal <= floor:
                breached = True
                break

            # Payout check
            days_since_payout += 1
            day_profit = trade_pnl if is_signal_day else 0.0

            if (days_since_payout >= payout_frequency_days and
                    bal > min_balance_to_withdraw and
                    day_profit >= qualifying_day_min):
                available = bal - min_balance_to_withdraw
                if available > 0:
                    payout_gross = min(available, payout_cap) * profit_split
                    payout_total += payout_gross
                    bal -= min(available, payout_cap)
                    days_since_payout = 0

        total_payouts.append(payout_total)
        if breached:
            breach_count += 1

    payouts_arr = np.array(total_payouts)

    return {
        "median_90d_payout": round(float(np.median(payouts_arr)), 2),
        "median_monthly": round(float(np.median(payouts_arr)) / 3, 2),
        "p25_monthly": round(float(np.percentile(payouts_arr, 25)) / 3, 2),
        "p75_monthly": round(float(np.percentile(payouts_arr, 75)) / 3, 2),
        "breach_pct": round(breach_count / n_runs * 100, 1),
        "n_runs": n_runs,
    }


def withdrawal_discipline_curve(
    current_balance: float,
    max_loss_limit: float,
    drawdown_amount: float,
    lock_threshold: float,
    payout_cap: float,
    profit_split: float,
    win_multiples: list[float],
    loss_multiples: list[float],
    risk: float,
    cushion_values: Optional[list[float]] = None,
    signal_frequency: float = 0.68,
    min_balance_to_withdraw: Optional[float] = None,
    payout_frequency_days: int = 1,
    qualifying_day_min: float = 0.0,
    n_runs: int = 5_000,
) -> list[dict]:
    """
    Return a list of {cushion, monthly_income, breach_pct} dicts.

    cushion: how much extra (beyond firm's floor) the trader leaves in the account.
    Higher cushion = lower breach risk = lower monthly income.
    """
    if cushion_values is None:
        step = drawdown_amount * 0.1
        cushion_values = [i * step for i in range(11)]  # 0x to 1x drawdown

    base_min_balance = max(
        float(max_loss_limit),
        float(min_balance_to_withdraw) if min_balance_to_withdraw is not None else float(max_loss_limit),
    )
    rng = np.random.default_rng()
    results = []

    for cushion in cushion_values:
        min_bal = base_min_balance + cushion
        sim = simulate_funded_monthly(
            current_balance=current_balance,
            max_loss_limit=max_loss_limit,
            drawdown_amount=drawdown_amount,
            lock_threshold=lock_threshold,
            payout_cap=payout_cap,
            profit_split=profit_split,
            min_balance_to_withdraw=min_bal,
            win_multiples=win_multiples,
            loss_multiples=loss_multiples,
            risk=risk,
            signal_frequency=signal_frequency,
            payout_frequency_days=payout_frequency_days,
            qualifying_day_min=qualifying_day_min,
            n_runs=n_runs,
            rng=rng,
        )
        results.append({
            "cushion": round(min_bal - float(max_loss_limit), 0),
            "min_balance": round(min_bal, 0),
            "monthly_income": sim["median_monthly"],
            "breach_pct": sim["breach_pct"],
        })

    return results
