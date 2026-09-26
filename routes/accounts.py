import json
from datetime import datetime, timezone

from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user

from app import db
from models import (Account, Trade, Payout, Firm, Distribution, ActivityLog,
                    Phase, DrawdownType, ActivityKind)
from engine.risk import (room, losses_survivable, risk_ladder,
                          update_eod_floor, check_risk_warning)

bp = Blueprint("accounts", __name__)


def _log(kind, message, account=None, payload=None):
    entry = ActivityLog(
        user_id=current_user.id,
        account_id=account.id if account else None,
        kind=kind,
        message=message,
        payload_json=json.dumps(payload) if payload else None,
    )
    db.session.add(entry)


def _apply_eod_floor(account):
    new_peak, new_floor = update_eod_floor(
        current_balance=float(account.current_balance),
        peak_balance=float(account.peak_balance),
        max_loss_limit=float(account.max_loss_limit),
        drawdown_amount=float(account.drawdown_amount),
        lock_threshold=float(account.lock_threshold),
    )
    account.peak_balance = new_peak
    account.max_loss_limit = new_floor


def _check_phase_after_trade(account):
    bal = float(account.current_balance)
    floor = float(account.max_loss_limit)
    target = float(account.profit_target) if account.profit_target else None
    starting = float(account.starting_balance)

    if account.phase == Phase.EVAL:
        if bal <= floor:
            account.phase = Phase.BREACHED
            account.closed_at = datetime.now(timezone.utc)
            _log(ActivityKind.BREACH,
                 "{} breached — balance {:.2f} hit floor {:.2f}".format(
                     account.nickname, bal, floor),
                 account=account)
        elif target and (bal - starting) >= target:
            account.phase = Phase.PASSED
            account.closed_at = datetime.now(timezone.utc)
            _log(ActivityKind.PHASE_CHANGE,
                 "{} passed eval — profit {:.2f}".format(account.nickname, bal - starting),
                 account=account)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@bp.route("/")
@login_required
def dashboard():
    accounts = (Account.query
                .filter_by(user_id=current_user.id)
                .order_by(Account.opened_at.desc())
                .all())

    cards = []
    for acct in accounts:
        r = room(float(acct.current_balance), float(acct.max_loss_limit))
        risk = float(acct.current_risk) if acct.current_risk else None
        survivable = (losses_survivable(float(acct.current_balance),
                                        float(acct.max_loss_limit), risk)
                      if risk else None)
        ladder = risk_ladder(float(acct.current_balance), float(acct.max_loss_limit))
        warning = (check_risk_warning(risk, float(acct.current_balance),
                                       float(acct.max_loss_limit)) if risk else False)

        trade_count = acct.trades.count()
        win_count = acct.trades.filter(Trade.pnl > 0).count()
        win_rate = (win_count / trade_count * 100) if trade_count else None

        cards.append({
            "account": acct,
            "room": r,
            "losses_survivable": survivable,
            "ladder": ladder,
            "risk_warning": warning,
            "trade_count": trade_count,
            "win_rate": win_rate,
        })

    firms = Firm.query.order_by(Firm.name).all()
    return render_template("dashboard.html", cards=cards, firms=firms)


# ---------------------------------------------------------------------------
# New Account
# ---------------------------------------------------------------------------

@bp.route("/accounts/new", methods=["GET", "POST"])
@login_required
def new_account():
    firms = Firm.query.order_by(Firm.name).all()
    distributions = Distribution.query.order_by(Distribution.name).all()

    if request.method == "POST":
        f = request.form
        firm_id = int(f["firm_id"])
        firm = Firm.query.get_or_404(firm_id)

        try:
            starting = float(f["starting_balance"])
            drawdown = float(f["drawdown_amount"])
            lock = float(f.get("lock_threshold") or starting)
            profit_target = float(f["profit_target"]) if f.get("profit_target") else None
            daily_ll = float(f["daily_loss_limit"]) if f.get("daily_loss_limit") else None
            consistency = float(f["consistency_pct"]) if f.get("consistency_pct") else None
            cap = int(f["contract_cap"]) if f.get("contract_cap") else None
            cost = float(f.get("cost_paid") or 0)
            current_risk = float(f["current_risk"]) if f.get("current_risk") else None
            dist_id = int(f["distribution_id"]) if f.get("distribution_id") else None
            current_bal = float(f.get("current_balance") or starting)
        except (ValueError, KeyError) as e:
            flash("Invalid input: {}".format(e), "error")
            return render_template("accounts/new.html", firms=firms, distributions=distributions)

        initial_floor = starting - drawdown
        nickname = f.get("nickname", "").strip() or "{} {}k".format(firm.name, int(starting / 1000))
        acct = Account(
            user_id=current_user.id,
            firm_id=firm_id,
            nickname=nickname,
            external_id=f.get("external_id", "").strip() or None,
            phase=Phase.EVAL,
            starting_balance=starting,
            current_balance=current_bal,
            peak_balance=current_bal,
            max_loss_limit=initial_floor,
            drawdown_amount=drawdown,
            drawdown_type=DrawdownType[f.get("drawdown_type", "EOD_TRAILING")],
            lock_threshold=lock,
            profit_target=profit_target,
            daily_loss_limit=daily_ll,
            dll_is_hard=bool(f.get("dll_is_hard")),
            consistency_pct=consistency,
            contract_cap=cap,
            cost_paid=cost,
            current_risk=current_risk,
            distribution_id=dist_id,
        )
        db.session.add(acct)
        db.session.flush()
        _log(ActivityKind.NOTE, "Account {} created".format(acct.nickname), account=acct,
             payload={"firm": firm.name, "starting_balance": starting})
        db.session.commit()
        flash("Account '{}' created.".format(acct.nickname), "success")
        return redirect(url_for("accounts.account_detail", account_id=acct.id))

    return render_template("accounts/new.html", firms=firms, distributions=distributions)


# ---------------------------------------------------------------------------
# Account Detail
# ---------------------------------------------------------------------------

@bp.route("/accounts/<int:account_id>")
@login_required
def account_detail(account_id):
    acct = Account.query.filter_by(id=account_id, user_id=current_user.id).first_or_404()
    trades = acct.trades.order_by(Trade.opened_at.desc()).all()
    payouts = acct.payouts.order_by(Payout.requested_at.desc()).all()

    r = room(float(acct.current_balance), float(acct.max_loss_limit))
    risk = float(acct.current_risk) if acct.current_risk else None
    survivable = (losses_survivable(float(acct.current_balance),
                                    float(acct.max_loss_limit), risk)
                  if risk else None)
    ladder = risk_ladder(float(acct.current_balance), float(acct.max_loss_limit))
    warning = (check_risk_warning(risk, float(acct.current_balance),
                                   float(acct.max_loss_limit)) if risk else False)

    # Replay trades oldest-first to build equity curve
    equity_points = []
    floor_points = []
    bal = float(acct.starting_balance)
    pk = bal
    fl = bal - float(acct.drawdown_amount)
    lock = float(acct.lock_threshold)
    equity_points.append({"t": acct.opened_at.isoformat(), "v": bal})
    floor_points.append({"t": acct.opened_at.isoformat(), "v": fl})

    for trade in reversed(list(trades)):
        bal += float(trade.pnl)
        if fl < lock:
            if bal > pk:
                pk = bal
            candidate = pk - float(acct.drawdown_amount)
            fl = min(candidate, lock)
        ts = (trade.closed_at or trade.opened_at).isoformat()
        equity_points.append({"t": ts, "v": round(bal, 2)})
        floor_points.append({"t": ts, "v": round(fl, 2)})

    trade_count = len(trades)
    win_count = sum(1 for t in trades if float(t.pnl) > 0)
    win_rate = (win_count / trade_count * 100) if trade_count else None

    return render_template(
        "accounts/detail.html",
        account=acct,
        trades=trades,
        payouts=payouts,
        room=r,
        losses_survivable=survivable,
        ladder=ladder,
        risk_warning=warning,
        equity_points=equity_points,
        floor_points=floor_points,
        trade_count=trade_count,
        win_rate=win_rate,
    )


# ---------------------------------------------------------------------------
# Trade entry
# ---------------------------------------------------------------------------

@bp.route("/accounts/<int:account_id>/trades", methods=["POST"])
@login_required
def add_trade(account_id):
    acct = Account.query.filter_by(id=account_id, user_id=current_user.id).first_or_404()

    if acct.phase == Phase.BREACHED:
        flash("Cannot add trades to a breached account.", "error")
        return redirect(url_for("accounts.account_detail", account_id=account_id))

    f = request.form
    try:
        pnl = float(f["pnl"])
        opened_at_str = f.get("opened_at", "")
        opened_at = (datetime.fromisoformat(opened_at_str)
                     if opened_at_str else datetime.now(timezone.utc))
        r_multiple = float(f["r_multiple"]) if f.get("r_multiple") else None
        direction = f.get("direction", "").upper() or None
        qty = int(f["quantity"]) if f.get("quantity") else None
        signal_price = float(f["signal_price"]) if f.get("signal_price") else None
        fill_price = float(f["fill_price"]) if f.get("fill_price") else None
    except ValueError as e:
        flash("Invalid input: {}".format(e), "error")
        return redirect(url_for("accounts.account_detail", account_id=account_id))

    trade = Trade(
        account_id=acct.id,
        opened_at=opened_at,
        direction=direction,
        signal_price=signal_price,
        fill_price=fill_price,
        quantity=qty,
        pnl=pnl,
        r_multiple=r_multiple,
    )
    db.session.add(trade)
    acct.current_balance = float(acct.current_balance) + pnl
    _apply_eod_floor(acct)
    _check_phase_after_trade(acct)

    _log(ActivityKind.TRADE,
         "Trade: {} ${:+.2f} -> balance ${:.2f}".format(
             "WIN" if pnl > 0 else "LOSS", pnl, float(acct.current_balance)),
         account=acct,
         payload={"pnl": pnl, "balance_after": float(acct.current_balance),
                  "floor_after": float(acct.max_loss_limit)})

    db.session.commit()
    flash("Trade logged: ${:+.2f}".format(pnl), "success" if pnl > 0 else "error")
    return redirect(url_for("accounts.account_detail", account_id=account_id))


# ---------------------------------------------------------------------------
# Payout entry
# ---------------------------------------------------------------------------

@bp.route("/accounts/<int:account_id>/payouts", methods=["POST"])
@login_required
def add_payout(account_id):
    acct = Account.query.filter_by(id=account_id, user_id=current_user.id).first_or_404()
    f = request.form

    try:
        gross = float(f["gross"])
        split = float(f.get("profit_split") or 0.9)
        net = round(gross * split, 2)
        balance_after = float(acct.current_balance) - gross
    except ValueError as e:
        flash("Invalid input: {}".format(e), "error")
        return redirect(url_for("accounts.account_detail", account_id=account_id))

    payout = Payout(account_id=acct.id, gross=gross, net=net, balance_after=balance_after)
    db.session.add(payout)
    acct.current_balance = balance_after

    _log(ActivityKind.PAYOUT,
         "Payout: gross ${:.2f} / net ${:.2f}, balance -> ${:.2f}".format(
             gross, net, balance_after),
         account=acct,
         payload={"gross": gross, "net": net, "balance_after": balance_after})

    db.session.commit()
    flash("Payout of ${:.2f} recorded.".format(gross), "success")
    return redirect(url_for("accounts.account_detail", account_id=account_id))


# ---------------------------------------------------------------------------
# Risk setting update
# ---------------------------------------------------------------------------

@bp.route("/accounts/<int:account_id>/risk", methods=["POST"])
@login_required
def update_risk(account_id):
    acct = Account.query.filter_by(id=account_id, user_id=current_user.id).first_or_404()
    try:
        new_risk = float(request.form["risk"])
    except ValueError:
        flash("Invalid risk value.", "error")
        return redirect(url_for("accounts.account_detail", account_id=account_id))

    old_risk = float(acct.current_risk) if acct.current_risk else None
    acct.current_risk = new_risk

    _log(ActivityKind.SETTING_CHANGE,
         "Risk setting changed: ${} -> ${}".format(old_risk, new_risk),
         account=acct,
         payload={"old_risk": old_risk, "new_risk": new_risk})

    db.session.commit()
    flash("Risk setting updated to ${:.0f}.".format(new_risk), "success")
    return redirect(url_for("accounts.account_detail", account_id=account_id))
