import math

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from engine.funded import simulate_funded_monthly, withdrawal_discipline_curve, sample_equity_paths
from models import Account

bp = Blueprint("projections", __name__)


@bp.route("/projections", methods=["GET", "POST"])
@login_required
def index():
    accounts = (Account.query.filter_by(user_id=current_user.id)
                .order_by(Account.nickname).all())
    eligible = [account for account in accounts if account.distribution_id]
    selected_id = request.values.get("account_id", type=int)
    selected = next((account for account in eligible if account.id == selected_id), None)
    if selected is None and eligible:
        selected = eligible[0]

    values = {}
    result = None
    curve = []
    equity_paths = []
    if selected:
        floor = float(selected.max_loss_limit)
        values = {
            "account_id": selected.id,
            "risk": float(selected.current_risk) if selected.current_risk else 500,
            "payout_cap": 2000,
            "profit_split": 0.9,
            "min_balance": floor + float(selected.drawdown_amount),
            "payout_frequency_days": 5,
            "qualifying_day_min": 0,
        }

    if request.method == "POST":
        try:
            account_id = int(request.form["account_id"])
            selected = next(account for account in eligible if account.id == account_id)
            values = {
                "account_id": selected.id,
                "risk": float(request.form["risk"]),
                "payout_cap": float(request.form["payout_cap"]),
                "profit_split": float(request.form["profit_split"]),
                "min_balance": float(request.form["min_balance"]),
                "payout_frequency_days": int(request.form["payout_frequency_days"]),
                "qualifying_day_min": float(request.form.get("qualifying_day_min") or 0),
            }
        except (KeyError, ValueError, StopIteration):
            flash("Choose an eligible account and enter valid projection assumptions.", "error")
            return redirect(url_for("projections.index"))

        floor = float(selected.max_loss_limit)
        numeric_values = [values[key] for key in
                  ("risk", "payout_cap", "profit_split", "min_balance", "qualifying_day_min")]
        if (not all(math.isfinite(value) for value in numeric_values)
            or values["risk"] <= 0 or values["payout_cap"] <= 0
                or not 0 < values["profit_split"] <= 1
                or values["min_balance"] < floor
                or values["payout_frequency_days"] not in (1, 5, 10, 15)
                or values["qualifying_day_min"] < 0):
            flash("Risk, payout cap, and withdrawal balance must be positive; split must be at most 100%.", "error")
            return redirect(url_for("projections.index", account_id=selected.id))

        distribution = selected.distribution
        if (not distribution
            or not any(value > 0 for value in distribution.win_multiples)
            or not any(value < 0 for value in distribution.loss_multiples)):
            flash("The selected account needs a distribution with wins and losses.", "error")
            return redirect(url_for("projections.index", account_id=selected.id))

        common = {
            "current_balance": float(selected.current_balance),
            "max_loss_limit": floor,
            "drawdown_amount": float(selected.drawdown_amount),
            "lock_threshold": float(selected.lock_threshold),
            "payout_cap": values["payout_cap"],
            "profit_split": values["profit_split"],
            "min_balance_to_withdraw": values["min_balance"],
            "win_multiples": distribution.win_multiples,
            "loss_multiples": distribution.loss_multiples,
            "risk": values["risk"],
            "base_risk": float(distribution.base_risk),
            "signal_frequency": float(distribution.signal_frequency),
            "payout_frequency_days": values["payout_frequency_days"],
            "qualifying_day_min": values["qualifying_day_min"],
        }
        result = simulate_funded_monthly(**common, n_runs=2500)
        equity_paths = sample_equity_paths(
            current_balance=float(selected.current_balance),
            max_loss_limit=float(selected.max_loss_limit),
            drawdown_amount=float(selected.drawdown_amount),
            lock_threshold=float(selected.lock_threshold),
            win_multiples=distribution.win_multiples,
            loss_multiples=distribution.loss_multiples,
            risk=values["risk"],
            base_risk=float(distribution.base_risk),
            signal_frequency=float(distribution.signal_frequency),
            n_paths=30,
            n_days=90,
        )
        cushion_step = max(float(selected.drawdown_amount) / 10, 1)
        curve = withdrawal_discipline_curve(
            **{key: value for key, value in common.items() if key != "min_balance_to_withdraw"},
            min_balance_to_withdraw=values["min_balance"],
            cushion_values=[index * cushion_step for index in range(11)],
            n_runs=500,
        )

    return render_template("projections.html", accounts=eligible, selected=selected,
                           values=values, result=result, curve=curve,
                           equity_paths=equity_paths)