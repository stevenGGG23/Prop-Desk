"""
Monte Carlo simulation engine for prop firm eval accounts.

Key design decisions enforced here (see README for rationale):
- Wins are sampled from win_multiples; not all wins clear remaining target
- Contract caps reject signals rather than reduce size
- Consistency rules gate the pass even after target is met
- Daily loss limits cap a day's loss (soft) or end an account (hard)
- Signal frequency: 68% of weekdays → calendar days ≠ trade days
- Portfolio simulation uses a SHARED trade sequence across all accounts
  (they all run the same signal)
"""

from __future__ import annotations

import hashlib
import json
import math
from functools import lru_cache
from typing import Optional

import numpy as np

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

N_RUNS = 20_000          # bootstrap iterations
MAX_TRADES = 2_000       # safety cap per run


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _account_state_hash(account_dict: dict) -> str:
    """Deterministic hash of the fields that affect simulation output."""
    key_fields = {
        "current_balance": float(account_dict["current_balance"]),
        "max_loss_limit": float(account_dict["max_loss_limit"]),
        "peak_balance": float(account_dict["peak_balance"]),
        "drawdown_amount": float(account_dict["drawdown_amount"]),
        "lock_threshold": float(account_dict["lock_threshold"]),
        "profit_target": float(account_dict.get("profit_target") or 0),
        "daily_loss_limit": float(account_dict.get("daily_loss_limit") or 0),
        "dll_is_hard": bool(account_dict.get("dll_is_hard", False)),
        "consistency_pct": float(account_dict.get("consistency_pct") or 0),
        "contract_cap": int(account_dict.get("contract_cap") or 999),
        "starting_balance": float(account_dict["starting_balance"]),
    }
    return hashlib.sha256(json.dumps(key_fields, sort_keys=True).encode()).hexdigest()[:16]


def _qty_from_risk(risk: float, base_risk: float, base_qty: int) -> int:
    """Estimate order size by scaling from the known base-risk quantity."""
    if base_risk <= 0 or base_qty <= 0:
        return 1
    return max(1, round(base_qty * risk / base_risk))


# ---------------------------------------------------------------------------
# Single-account simulation
# ---------------------------------------------------------------------------

def simulate_account(
    current_balance: float,
    max_loss_limit: float,
    peak_balance: float,
    drawdown_amount: float,
    lock_threshold: float,
    profit_target: float,
    risk: float,
    win_multiples: list[float],
    loss_multiples: list[float],
    starting_balance: float,
    drawdown_type: str = "EOD_TRAILING",
    daily_loss_limit: Optional[float] = None,
    dll_is_hard: bool = False,
    consistency_pct: Optional[float] = None,
    contract_cap: Optional[int] = None,
    base_risk: float = 1000.0,
    base_qty: int = 1,
    signal_frequency: float = 0.68,
    n_runs: int = N_RUNS,
    rng: Optional[np.random.Generator] = None,
) -> dict:
    """
    Run a Monte Carlo simulation for a single eval account.

    Returns a dict of statistics.
    """
    if rng is None:
        rng = np.random.default_rng()

    wins = np.array([m for m in win_multiples if m > 0], dtype=float)
    losses = np.array([m for m in loss_multiples if m < 0], dtype=float)

    if len(wins) == 0 or len(losses) == 0:
        return {"error": "Distribution must contain both wins and losses."}

    win_prob = len(wins) / (len(wins) + len(losses))

    passed_runs = 0
    breached_runs = 0
    rejected_total = 0
    total_trades_to_pass = []
    total_calendar_days = []

    for _ in range(n_runs):
        bal = current_balance
        peak = peak_balance
        floor = max_loss_limit
        profit = bal - starting_balance

        best_day_profit = 0.0
        trades_taken = 0
        rejected = 0
        passed = False
        breached = False

        # Consistency: track per-day profit for the consistency check
        day_profits: list[float] = []

        day = 0
        trade_day_pnl = 0.0
        dll_hit_today = False

        for t in range(MAX_TRADES):
            # New day: ~68% chance of a signal
            is_signal_day = rng.random() < signal_frequency
            day += 1

            if not is_signal_day:
                # EOD floor update even on no-trade days
                if drawdown_type == "EOD_TRAILING" and floor < lock_threshold:
                    if bal > peak:
                        peak = bal
                    candidate = peak - drawdown_amount
                    floor = min(candidate, lock_threshold)
                continue

            # Check contract cap rejection
            qty = _qty_from_risk(risk, base_risk, base_qty)
            if contract_cap and qty > contract_cap:
                rejected += 1
                # EOD floor update
                if drawdown_type == "EOD_TRAILING" and floor < lock_threshold:
                    if bal > peak:
                        peak = bal
                    candidate = peak - drawdown_amount
                    floor = min(candidate, lock_threshold)
                continue

            # Daily loss limit reset
            if dll_hit_today:
                dll_hit_today = False
                trade_day_pnl = 0.0

            # Sample outcome
            is_win = rng.random() < win_prob
            if is_win:
                multiple = rng.choice(wins)
            else:
                multiple = rng.choice(losses)

            trade_pnl = risk * multiple
            bal += trade_pnl
            trade_day_pnl += trade_pnl
            trades_taken += 1

            if trade_pnl > 0:
                day_profits.append(trade_pnl)
                if trade_pnl > best_day_profit:
                    best_day_profit = trade_pnl

            # Check daily loss limit
            if daily_loss_limit and trade_day_pnl < -daily_loss_limit:
                if dll_is_hard:
                    breached = True
                    break
                # Soft: stop trading for the day
                dll_hit_today = True

            # EOD floor update
            if drawdown_type == "EOD_TRAILING" and floor < lock_threshold:
                if bal > peak:
                    peak = bal
                candidate = peak - drawdown_amount
                floor = min(candidate, lock_threshold)
                trade_day_pnl = 0.0
                dll_hit_today = False

            # Breach check
            if bal <= floor:
                breached = True
                break

            # Pass check
            current_profit = bal - starting_balance
            if profit_target and current_profit >= profit_target:
                # Consistency check
                if consistency_pct and day_profits:
                    total_profit = sum(day_profits)
                    if total_profit > 0 and best_day_profit / total_profit > consistency_pct:
                        # Keep trading until consistency clears
                        continue
                passed = True
                break

        if passed:
            passed_runs += 1
            total_trades_to_pass.append(trades_taken)
            total_calendar_days.append(day)
        elif breached:
            breached_runs += 1

        rejected_total += rejected

    pass_pct = passed_runs / n_runs
    breach_pct = breached_runs / n_runs
    inconclusive_pct = 1.0 - pass_pct - breach_pct

    result = {
        "pass_pct": round(pass_pct * 100, 1),
        "breach_pct": round(breach_pct * 100, 1),
        "inconclusive_pct": round(inconclusive_pct * 100, 1),
        "avg_rejection_rate": round(rejected_total / n_runs, 2),
        "n_runs": n_runs,
    }

    if total_trades_to_pass:
        arr = np.array(total_trades_to_pass)
        result["median_trades"] = int(np.median(arr))
        result["p75_trades"] = int(np.percentile(arr, 75))

    if total_calendar_days:
        cd = np.array(total_calendar_days)
        result["median_calendar_days"] = int(np.median(cd))
        result["p75_calendar_days"] = int(np.percentile(cd, 75))

    return result


# ---------------------------------------------------------------------------
# Risk ladder simulation
# ---------------------------------------------------------------------------

def simulate_risk_ladder(
    account_dict: dict,
    distribution_dict: dict,
    risk_values: list[float],
    n_runs: int = N_RUNS,
) -> list[dict]:
    """
    Run simulate_account for each candidate risk value.

    account_dict: dict representation of an Account model
    distribution_dict: dict representation of a Distribution model
    risk_values: list of risk amounts to simulate
    """
    rng = np.random.default_rng()

    results = []
    for risk in risk_values:
        sim = simulate_account(
            current_balance=float(account_dict["current_balance"]),
            max_loss_limit=float(account_dict["max_loss_limit"]),
            peak_balance=float(account_dict["peak_balance"]),
            drawdown_amount=float(account_dict["drawdown_amount"]),
            lock_threshold=float(account_dict["lock_threshold"]),
            profit_target=float(account_dict.get("profit_target") or 0),
            risk=risk,
            win_multiples=distribution_dict["win_multiples"],
            loss_multiples=distribution_dict["loss_multiples"],
            starting_balance=float(account_dict["starting_balance"]),
            drawdown_type=account_dict.get("drawdown_type", "EOD_TRAILING"),
            daily_loss_limit=float(account_dict["daily_loss_limit"]) if account_dict.get("daily_loss_limit") else None,
            dll_is_hard=bool(account_dict.get("dll_is_hard", False)),
            consistency_pct=float(account_dict["consistency_pct"]) if account_dict.get("consistency_pct") else None,
            contract_cap=int(account_dict["contract_cap"]) if account_dict.get("contract_cap") else None,
            base_risk=float(distribution_dict["base_risk"]),
            signal_frequency=float(distribution_dict.get("signal_frequency", 0.68)),
            n_runs=n_runs,
            rng=rng,
        )
        sim["risk"] = risk
        results.append(sim)

    return results


# ---------------------------------------------------------------------------
# Portfolio joint simulation (shared trade sequence)
# ---------------------------------------------------------------------------

def simulate_portfolio(
    accounts: list[dict],
    distribution_dict: dict,
    n_runs: int = N_RUNS,
) -> dict:
    """
    Simulate all accounts simultaneously using a SINGLE shared trade sequence.

    This correctly captures correlation risk: all accounts run the same signal,
    so they win and lose together. Independent draws would massively understate
    joint-wipeout probability.

    Returns overall pass/breach stats plus joint-wipeout probability.
    """
    if not accounts:
        return {}

    rng = np.random.default_rng()
    win_multiples = np.array(distribution_dict["win_multiples"], dtype=float)
    loss_multiples = np.array(distribution_dict["loss_multiples"], dtype=float)
    win_prob = len([m for m in win_multiples if m > 0]) / len(np.concatenate([win_multiples, loss_multiples]))
    signal_frequency = float(distribution_dict.get("signal_frequency", 0.68))

    all_passed = 0
    joint_breach = 0
    any_breach = 0

    per_account_pass = [0] * len(accounts)
    per_account_breach = [0] * len(accounts)

    for _ in range(n_runs):
        account_states = []
        for a in accounts:
            account_states.append({
                "bal": float(a["current_balance"]),
                "peak": float(a["peak_balance"]),
                "floor": float(a["max_loss_limit"]),
                "passed": False,
                "breached": False,
                "profit_target": float(a.get("profit_target") or 0),
                "drawdown_amount": float(a["drawdown_amount"]),
                "lock_threshold": float(a["lock_threshold"]),
                "starting_balance": float(a["starting_balance"]),
                "risk": float(a.get("current_risk") or 500),
                "contract_cap": int(a["contract_cap"]) if a.get("contract_cap") else 999,
            })

        for t in range(MAX_TRADES):
            # Shared signal: same day across all accounts
            is_signal_day = rng.random() < signal_frequency
            is_win = rng.random() < win_prob

            if is_win:
                multiple = rng.choice(win_multiples)
            else:
                multiple = rng.choice(loss_multiples)

            all_done = all(s["passed"] or s["breached"] for s in account_states)
            if all_done:
                break

            for s in account_states:
                if s["passed"] or s["breached"]:
                    continue

                if is_signal_day and s["risk"] > 0:
                    trade_pnl = s["risk"] * multiple
                    s["bal"] += trade_pnl

                # EOD floor update
                if s["floor"] < s["lock_threshold"]:
                    if s["bal"] > s["peak"]:
                        s["peak"] = s["bal"]
                    candidate = s["peak"] - s["drawdown_amount"]
                    s["floor"] = min(candidate, s["lock_threshold"])

                # Breach check
                if s["bal"] <= s["floor"]:
                    s["breached"] = True
                    continue

                # Pass check
                profit = s["bal"] - s["starting_balance"]
                if s["profit_target"] and profit >= s["profit_target"]:
                    s["passed"] = True

        for i, s in enumerate(account_states):
            if s["passed"]:
                per_account_pass[i] += 1
            if s["breached"]:
                per_account_breach[i] += 1

        run_all_passed = all(s["passed"] for s in account_states)
        run_any_breach = any(s["breached"] for s in account_states)
        run_joint_breach = all(s["breached"] for s in account_states)

        if run_all_passed:
            all_passed += 1
        if run_any_breach:
            any_breach += 1
        if run_joint_breach:
            joint_breach += 1

    return {
        "all_passed_pct": round(all_passed / n_runs * 100, 1),
        "any_breach_pct": round(any_breach / n_runs * 100, 1),
        "joint_breach_pct": round(joint_breach / n_runs * 100, 1),
        "per_account": [
            {
                "pass_pct": round(per_account_pass[i] / n_runs * 100, 1),
                "breach_pct": round(per_account_breach[i] / n_runs * 100, 1),
            }
            for i in range(len(accounts))
        ],
        "n_runs": n_runs,
    }
