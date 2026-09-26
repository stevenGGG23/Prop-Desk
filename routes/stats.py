from collections import defaultdict

from flask import Blueprint, render_template
from flask_login import login_required, current_user

from models import Account, Trade, Phase

bp = Blueprint("stats", __name__)

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


@bp.route("/stats")
@login_required
def stats():
    accounts = (Account.query
                .filter_by(user_id=current_user.id)
                .all())

    all_trades = []
    for acct in accounts:
        trades = acct.trades.all()
        for t in trades:
            all_trades.append({"trade": t, "account": acct})

    total_trades = len(all_trades)
    wins = [x for x in all_trades if float(x["trade"].pnl) > 0]
    losses = [x for x in all_trades if float(x["trade"].pnl) < 0]
    total_win_rate = (len(wins) / total_trades * 100) if total_trades else None

    # Per-account win rate
    per_account = []
    for acct in accounts:
        trades = acct.trades.all()
        n = len(trades)
        w = sum(1 for t in trades if float(t.pnl) > 0)
        per_account.append({
            "account": acct,
            "total": n,
            "wins": w,
            "losses": n - w,
            "win_rate": (w / n * 100) if n else None,
        })

    # Win rate by weekday (0=Monday)
    weekday_data = defaultdict(lambda: {"wins": 0, "total": 0})
    for x in all_trades:
        trade = x["trade"]
        dt = trade.opened_at
        wd = dt.weekday()  # 0=Monday
        if wd < 5:  # weekdays only
            weekday_data[wd]["total"] += 1
            if float(trade.pnl) > 0:
                weekday_data[wd]["wins"] += 1

    weekday_stats = []
    for i in range(5):
        d = weekday_data[i]
        n = d["total"]
        w = d["wins"]
        weekday_stats.append({
            "day": WEEKDAYS[i],
            "total": n,
            "wins": w,
            "win_rate": (w / n * 100) if n else None,
            "sample_adequate": n >= 20,
        })

    # Slippage tracker: signal price vs fill price
    slippage_data = []
    for x in all_trades:
        trade = x["trade"]
        if trade.signal_price and trade.fill_price:
            entry_slip = float(trade.fill_price) - float(trade.signal_price)
            slippage_data.append({
                "trade_id": trade.id,
                "date": trade.opened_at.date(),
                "direction": trade.direction,
                "entry_slip": entry_slip,
                "account": x["account"].nickname,
            })

    avg_entry_slippage = (
        sum(s["entry_slip"] for s in slippage_data) / len(slippage_data)
        if slippage_data else None
    )

    # Total pnl
    total_pnl = sum(float(x["trade"].pnl) for x in all_trades)
    avg_win = (sum(float(x["trade"].pnl) for x in wins) / len(wins)) if wins else None
    avg_loss = (sum(float(x["trade"].pnl) for x in losses) / len(losses)) if losses else None

    return render_template(
        "stats.html",
        total_trades=total_trades,
        total_win_rate=total_win_rate,
        total_pnl=total_pnl,
        per_account=per_account,
        weekday_stats=weekday_stats,
        slippage_data=slippage_data,
        avg_entry_slippage=avg_entry_slippage,
        avg_win=avg_win,
        avg_loss=avg_loss,
    )
