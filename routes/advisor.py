from flask import Blueprint, render_template
from flask_login import login_required, current_user

from models import Account, Trade, Phase
from engine.risk import (room, losses_survivable, risk_ladder,
                          max_risk_for_n_losses, check_risk_warning)

bp = Blueprint("advisor", __name__)


@bp.route("/advisor")
@login_required
def advisor():
    accounts = (Account.query
                .filter_by(user_id=current_user.id)
                .filter(Account.phase.in_([Phase.EVAL, Phase.FUNDED, Phase.LIVE]))
                .order_by(Account.opened_at.desc())
                .all())

    recommendations = []
    for acct in accounts:
        bal = float(acct.current_balance)
        floor = float(acct.max_loss_limit)
        r = room(bal, floor)
        risk = float(acct.current_risk) if acct.current_risk else None

        # Risk ladder: 2-6 losses
        ladder = risk_ladder(bal, floor)

        # Safe risk = 3-loss threshold
        safe_risk = max_risk_for_n_losses(bal, floor, 3)
        aggressive_risk = max_risk_for_n_losses(bal, floor, 2)
        conservative_risk = max_risk_for_n_losses(bal, floor, 5)

        warning = check_risk_warning(risk, bal, floor) if risk else False

        # Post-win / post-loss guidance
        # After a win: balance is higher, room is larger, can maintain same or step up
        # After a loss: balance is lower, must recompute
        if risk:
            win_pnl = risk * 2.2  # approximate win
            loss_pnl = -risk
            bal_after_win = bal + win_pnl
            bal_after_loss = bal + loss_pnl

            risk_after_win = max_risk_for_n_losses(bal_after_win, floor, 3)
            risk_after_loss = max_risk_for_n_losses(bal_after_loss, floor, 3)
        else:
            risk_after_win = None
            risk_after_loss = None

        # When floor locks: tell the user exactly what balance triggers the lock
        floor_lock_balance = float(acct.lock_threshold) + float(acct.drawdown_amount)
        floor_locked = acct.floor_is_locked

        recommendations.append({
            "account": acct,
            "room": r,
            "ladder": ladder,
            "safe_risk": safe_risk,
            "aggressive_risk": aggressive_risk,
            "conservative_risk": conservative_risk,
            "current_risk": risk,
            "risk_warning": warning,
            "risk_after_win": risk_after_win,
            "risk_after_loss": risk_after_loss,
            "floor_lock_balance": floor_lock_balance,
            "floor_locked": floor_locked,
        })

    return render_template("advisor.html", recommendations=recommendations)
