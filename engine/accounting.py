"""Account balance transitions for finalized trading days."""

from engine.risk import update_eod_floor


def apply_daily_result(
    current_balance: float,
    peak_balance: float,
    max_loss_limit: float,
    daily_pnl: float,
    drawdown_amount: float,
    lock_threshold: float,
) -> dict[str, float]:
    """Apply finalized daily P&L and move an EOD trailing floor once."""
    closing_balance = current_balance + daily_pnl
    new_peak, new_floor = update_eod_floor(
        current_balance=closing_balance,
        peak_balance=peak_balance,
        max_loss_limit=max_loss_limit,
        drawdown_amount=drawdown_amount,
        lock_threshold=lock_threshold,
    )
    return {
        "balance": closing_balance,
        "peak_balance": new_peak,
        "max_loss_limit": new_floor,
    }