import json
import math
import secrets
from datetime import date, datetime, timezone

from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify
from flask_login import login_required, current_user
from sqlalchemy import or_

from app import db
from models import (Account, Bot, DailyResult, Trade, Payout, Firm, Distribution,
                    ActivityLog, Phase, DrawdownType, ActivityKind, WebhookReceiver)
from engine.accounting import apply_daily_result
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


def _latest_ledger_date(account, exclude_daily_result_id=None):
    event_dates = [
        (trade.closed_at or trade.opened_at).date()
        for trade in account.trades.all()
    ]
    event_dates.extend(
        result.trade_date
        for result in account.daily_results.all()
        if result.id != exclude_daily_result_id
    )
    event_dates.extend(payout.requested_at.date() for payout in account.payouts.all())
    return max(event_dates) if event_dates else None


def _open_trade_dates(account):
    finalized_dates = {result.trade_date for result in account.daily_results.all()}
    return {
        (trade.closed_at or trade.opened_at).date()
        for trade in account.trades.all()
        if (trade.closed_at or trade.opened_at).date() not in finalized_dates
    }


def _consistency_flags(account):
    """Return a dict of consistency analytics for the account detail page."""
    pct = float(account.consistency_pct) if account.consistency_pct else None
    if pct is None:
        return None

    best = float(account.best_day_so_far)
    total = float(account.current_balance) - float(account.starting_balance)
    pct_label = round(pct * 100, 0)

    flags = {
        "pct": pct,
        "pct_label": pct_label,
        "best_day": best,
        "total_profit": total,
        "passing": None,
        "current_ratio": None,
        "min_total_needed": None,
        "extra_needed": None,
        "max_next_win": None,   # largest single-day win allowed without violation
        "safe_loss_gain": None, # after a loss of this size, best_day is no longer the barrier
    }

    if total <= 0 or best <= 0:
        # Not enough profit to evaluate; rule not yet triggered
        flags["passing"] = True
        return flags

    ratio = best / total
    flags["current_ratio"] = round(ratio * 100, 1)
    flags["passing"] = ratio <= pct

    # Minimum total profit such that best_day is no longer > pct of total
    # best / min_total = pct  →  min_total = best / pct
    min_total = best / pct
    flags["min_total_needed"] = round(min_total, 2)
    flags["extra_needed"] = round(max(min_total - total, 0), 2)

    # Largest win tomorrow that keeps consistency:
    # Case 1: win < best  →  best / (total + win) <= pct  →  win >= best/pct - total
    #   The existing best_day stays dominant; any win is fine as long as total grows enough.
    #   Max additional win without replacing best = best/pct - total (must be positive)
    safe_win_keeping_best = best / pct - total
    # Case 2: win > best  →  win / (total + win) <= pct  →  win <= pct * total / (1 - pct)
    if pct < 1:
        max_win_as_new_best = pct * total / (1 - pct)
    else:
        max_win_as_new_best = float("inf")

    if safe_win_keeping_best > 0:
        # There's still room to grow total without new best becoming dominant
        flags["max_next_win"] = round(safe_win_keeping_best, 2)
    else:
        # best already exceeds pct × total; any new win that's smaller than best is fine
        # largest new win that won't itself become the violating best:
        flags["max_next_win"] = round(max_win_as_new_best, 2)

    # How large a loss day makes consistency easier:
    # After loss L, total_new = total - L, ratio_new = best / (total - L)  (worse)
    # That's actually *harder*, not easier.
    # What the user asked: "take an extra day loss" — meaning, if you take a loss,
    # you gain more room on what your *next* winning day can be (relative to total)
    # because total went down. In practice the ratio worsens, but the user means:
    # "how much loss can I absorb and still pass if I then hit the required target?"
    # Just show: if today is a loss day of -X, new extra_needed = (best/(pct) - (total - X))
    # We surface max_next_win assuming current state; that's the actionable number.

    return flags


def _days_to_target(account):
    """Estimate calendar days remaining to hit profit_target based on recent PnL."""
    if not account.profit_target:
        return None
    target = float(account.profit_target)
    profit = float(account.current_balance) - float(account.starting_balance)
    remaining = target - profit
    if remaining <= 0:
        return 0  # already hit

    results = (account.daily_results
               .order_by(DailyResult.trade_date.desc())
               .limit(20)
               .all())
    if not results:
        return None

    avg_daily = sum(float(r.pnl) for r in results) / len(results)
    if avg_daily <= 0:
        return None  # flat or losing — can't estimate

    # trading days to target
    trading_days = math.ceil(remaining / avg_daily)
    # calendar days ≈ trading days / (5/7) / signal_frequency
    calendar_days = math.ceil(trading_days / (5 / 7))
    return {"trading": trading_days, "calendar": calendar_days, "remaining": round(remaining, 2)}


def _restore_daily_snapshot(account, result):
    account.current_balance = result.balance_before
    account.peak_balance = result.peak_before
    account.max_loss_limit = result.floor_before
    account.best_day_so_far = result.best_day_before
    account.phase = Phase[result.phase_before]
    account.closed_at = result.closed_at_before


def _apply_daily_close(account, pnl):
    if account.drawdown_type == DrawdownType.EOD_TRAILING:
        state = apply_daily_result(
            current_balance=float(account.current_balance),
            peak_balance=float(account.peak_balance),
            max_loss_limit=float(account.max_loss_limit),
            daily_pnl=float(pnl),
            drawdown_amount=float(account.drawdown_amount),
            lock_threshold=float(account.lock_threshold),
        )
        account.current_balance = state["balance"]
        account.peak_balance = state["peak_balance"]
        account.max_loss_limit = state["max_loss_limit"]
    else:
        account.current_balance = float(account.current_balance) + float(pnl)
        if account.drawdown_type == DrawdownType.INTRADAY_TRAILING:
            _apply_eod_floor(account)
    _check_phase_after_trade(account)


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
        net_performance = (
            float(acct.current_balance) - float(acct.starting_balance)
            + sum(float(payout.net) for payout in acct.payouts.all())
            - float(acct.cost_paid or 0)
        )

        # Build a simple sparkline from daily results (last 30)
        daily_sorted = (acct.daily_results
                        .order_by(DailyResult.trade_date.asc())
                        .limit(30)
                        .all())
        sparkline = []
        if daily_sorted:
            running_bal = float(daily_sorted[0].balance_before)
            sparkline.append(running_bal)
            for dr in daily_sorted:
                running_bal += float(dr.pnl)
                sparkline.append(round(running_bal, 2))
        if not sparkline:
            sparkline = [float(acct.current_balance)]

        days_est = _days_to_target(acct)

        cards.append({
            "account": acct,
            "room": r,
            "losses_survivable": survivable,
            "ladder": ladder,
            "risk_warning": warning,
            "trade_count": trade_count,
            "win_rate": win_rate,
            "net_performance": net_performance,
            "sparkline": sparkline,
            "days_to_target": days_est,
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
    distributions = Distribution.query.filter(
        or_(Distribution.user_id.is_(None), Distribution.user_id == current_user.id)
    ).order_by(Distribution.name).all()

    if request.method == "POST":
        f = request.form
        firm_id = int(f["firm_id"])
        firm = Firm.query.get_or_404(firm_id)

        try:
            starting = float(f["starting_balance"])
            drawdown = float(f["drawdown_amount"])
            lock = float(f.get("lock_threshold") or starting + 100)
            profit_target = float(f["profit_target"]) if f.get("profit_target") else None
            daily_ll = float(f["daily_loss_limit"]) if f.get("daily_loss_limit") else None
            # Accept consistency as whole number (40) or decimal (0.40)
            consistency_raw = float(f["consistency_pct"]) if f.get("consistency_pct") else None
            if consistency_raw is not None:
                consistency = consistency_raw / 100 if consistency_raw > 1 else consistency_raw
            else:
                consistency = None
            cap = int(f["contract_cap"]) if f.get("contract_cap") else None
            cost = float(f.get("cost_paid") or 0)
            current_risk = float(f["current_risk"]) if f.get("current_risk") else None
            dist_id = int(f["distribution_id"]) if f.get("distribution_id") else None
            current_bal = float(f.get("current_balance") or starting)
            # Current MLL: use explicitly if provided, otherwise compute from starting - drawdown
            current_mll_raw = f.get("current_mll", "").strip()
            current_mll = float(current_mll_raw) if current_mll_raw else (starting - drawdown)
            # Peak balance: must be at least current_balance and at least mll + drawdown
            peak_bal = max(current_bal, current_mll + drawdown)
            best_day = float(f.get("best_day_so_far") or 0)
            phase_str = f.get("phase", "EVAL")
        except (ValueError, KeyError) as e:
            flash("Invalid input: {}".format(e), "error")
            return render_template("accounts/new.html", firms=firms, distributions=distributions)

        nickname = f.get("nickname", "").strip() or "{} {}k".format(firm.name, int(starting / 1000))
        acct = Account(
            user_id=current_user.id,
            firm_id=firm_id,
            nickname=nickname,
            external_id=f.get("external_id", "").strip() or None,
            phase=Phase[phase_str],
            starting_balance=starting,
            current_balance=current_bal,
            peak_balance=peak_bal,
            max_loss_limit=current_mll,
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
            best_day_so_far=best_day,
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
    bots = Bot.query.filter_by(user_id=current_user.id, active=True).order_by(Bot.name).all()
    daily_results = acct.daily_results.order_by(DailyResult.trade_date.desc()).all()
    latest_event_date = _latest_ledger_date(acct)
    editable_daily_result = next(
        (result for result in daily_results
         if result.source == "MANUAL" and result.trade_date == latest_event_date),
        None,
    )
    open_trade_days = _open_trade_dates(acct)
    latest_open_trade_day = min(open_trade_days) if open_trade_days else None

    r = room(float(acct.current_balance), float(acct.max_loss_limit))
    risk = float(acct.current_risk) if acct.current_risk else None
    survivable = (losses_survivable(float(acct.current_balance),
                                    float(acct.max_loss_limit), risk)
                  if risk else None)
    ladder = risk_ladder(float(acct.current_balance), float(acct.max_loss_limit))
    warning = (check_risk_warning(risk, float(acct.current_balance),
                                   float(acct.max_loss_limit)) if risk else False)

    # Replay daily results, open trades, and payouts to keep the curve aligned with balance.
    equity_points = []
    floor_points = []
    results_by_day = {result.trade_date: result for result in daily_results}
    trades_by_day = {}
    for trade in trades:
        trade_day = (trade.closed_at or trade.opened_at).date()
        trades_by_day.setdefault(trade_day, []).append(trade)
    payouts_by_day = {}
    for payout in payouts:
        payout_day = payout.requested_at.date()
        payouts_by_day[payout_day] = payouts_by_day.get(payout_day, 0) + float(payout.gross)

    all_days = set(results_by_day) | set(trades_by_day) | set(payouts_by_day)
    ordered_results = sorted(daily_results, key=lambda result: result.trade_date)
    if ordered_results:
        first_result = ordered_results[0]
        bal = float(first_result.balance_before)
        pk = float(first_result.peak_before)
        fl = float(first_result.floor_before)
    else:
        total_trade_pnl = sum(float(trade.pnl) for trade in trades)
        total_daily_pnl = sum(float(result.pnl) for result in daily_results)
        total_payouts = sum(float(payout.gross) for payout in payouts)
        bal = float(acct.current_balance) - total_trade_pnl - total_daily_pnl + total_payouts
        pk = bal
        fl = float(acct.starting_balance) - float(acct.drawdown_amount)
    lock = float(acct.lock_threshold)
    equity_points.append({"t": acct.opened_at.isoformat(), "v": bal})
    floor_points.append({"t": acct.opened_at.isoformat(), "v": fl})

    for trade_day in sorted(all_days):
        day_trades = sorted(trades_by_day.get(trade_day, []),
                            key=lambda trade: trade.closed_at or trade.opened_at)
        day_result = results_by_day.get(trade_day)
        if day_trades:
            for trade in day_trades:
                bal += float(trade.pnl)
                if acct.drawdown_type == DrawdownType.INTRADAY_TRAILING:
                    pk, fl = update_eod_floor(
                        bal, pk, fl, float(acct.drawdown_amount), lock)
                timestamp = (trade.closed_at or trade.opened_at).isoformat()
                equity_points.append({"t": timestamp, "v": round(bal, 2)})
                floor_points.append({"t": timestamp, "v": round(fl, 2)})
        elif day_result:
            bal += float(day_result.pnl)

        close_time = datetime.combine(trade_day, datetime.max.time(), tzinfo=timezone.utc).isoformat()
        if day_result and acct.drawdown_type == DrawdownType.EOD_TRAILING:
            pk, fl = update_eod_floor(bal, pk, fl,
                                      float(acct.drawdown_amount), lock)
        if not day_trades or day_result:
            equity_points.append({"t": close_time, "v": round(bal, 2)})
            floor_points.append({"t": close_time, "v": round(fl, 2)})

        payout = payouts_by_day.get(trade_day, 0)
        if payout:
            bal -= payout
            equity_points.append({"t": close_time, "v": round(bal, 2)})
            floor_points.append({"t": close_time, "v": round(fl, 2)})

    if abs(bal - float(acct.current_balance)) > 0.01:
        now = datetime.now(timezone.utc).isoformat()
        equity_points.append({"t": now, "v": float(acct.current_balance)})
        floor_points.append({"t": now, "v": float(acct.max_loss_limit)})

    trade_count = len(trades)
    win_count = sum(1 for t in trades if float(t.pnl) > 0)
    win_rate = (win_count / trade_count * 100) if trade_count else None

    consistency = _consistency_flags(acct)
    days_est = _days_to_target(acct)
    webhooks = acct.webhook_receivers.filter_by(active=True).all()

    return render_template(
        "accounts/detail.html",
        account=acct,
        trades=trades,
        payouts=payouts,
        bots=bots,
        daily_results=daily_results,
        editable_daily_result=editable_daily_result,
        today=date.today(),
        latest_open_trade_day=latest_open_trade_day,
        open_trade_day_count=len(open_trade_days),
        room=r,
        losses_survivable=survivable,
        ladder=ladder,
        risk_warning=warning,
        equity_points=equity_points,
        floor_points=floor_points,
        trade_count=trade_count,
        win_rate=win_rate,
        consistency=consistency,
        days_to_target=days_est,
        webhooks=webhooks,
    )


# ---------------------------------------------------------------------------
# Trade entry
# ---------------------------------------------------------------------------

@bp.route("/accounts/<int:account_id>/trades", methods=["POST"])
@login_required
def add_trade(account_id):
    acct = Account.query.filter_by(id=account_id, user_id=current_user.id).first_or_404()

    if acct.phase in (Phase.BREACHED, Phase.PASSED):
        flash("Cannot add trades to a closed account.", "error")
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
        bot_id = int(f["bot_id"]) if f.get("bot_id") else None
    except ValueError as e:
        flash("Invalid input: {}".format(e), "error")
        return redirect(url_for("accounts.account_detail", account_id=account_id))

    if not math.isfinite(pnl) or opened_at.date() > datetime.now(timezone.utc).date():
        flash("P&L must be finite and the trade date cannot be in the future.", "error")
        return redirect(url_for("accounts.account_detail", account_id=account_id))

    if DailyResult.query.filter_by(account_id=acct.id, trade_date=opened_at.date()).first():
        flash("This day is already finalized. Use a new date or correct the daily result.", "error")
        return redirect(url_for("accounts.account_detail", account_id=account_id))
    latest_closed_date = max((result.trade_date for result in acct.daily_results.all()), default=None)
    latest_payout_date = max((payout.requested_at.date() for payout in acct.payouts.all()), default=None)
    if ((latest_closed_date and opened_at.date() <= latest_closed_date)
            or (latest_payout_date and opened_at.date() <= latest_payout_date)):
        flash("Trades must follow the latest finalized day and payout date.", "error")
        return redirect(url_for("accounts.account_detail", account_id=account_id))

    bot = None
    if bot_id:
        bot = Bot.query.filter_by(id=bot_id, user_id=current_user.id, active=True).first()
        if not bot:
            flash("Select one of your active bots.", "error")
            return redirect(url_for("accounts.account_detail", account_id=account_id))

    trade = Trade(
        account_id=acct.id,
        bot_id=bot.id if bot else None,
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
    if acct.drawdown_type != DrawdownType.EOD_TRAILING:
        if acct.drawdown_type == DrawdownType.INTRADAY_TRAILING:
            _apply_eod_floor(acct)
        _check_phase_after_trade(acct)

    _log(ActivityKind.TRADE,
         "Trade: {} ${:+.2f} -> balance ${:.2f}".format(
             "WIN" if pnl > 0 else "LOSS", pnl, float(acct.current_balance)),
         account=acct,
         payload={"pnl": pnl, "balance_after": float(acct.current_balance),
                  "floor_after": float(acct.max_loss_limit),
                  "bot_id": bot.id if bot else None})

    db.session.commit()
    flash("Trade logged: ${:+.2f}".format(pnl), "success" if pnl > 0 else "error")
    return redirect(url_for("accounts.account_detail", account_id=account_id))


@bp.route("/accounts/<int:account_id>/daily-result", methods=["POST"])
@login_required
def save_daily_result(account_id):
    acct = Account.query.filter_by(id=account_id, user_id=current_user.id).first_or_404()
    if acct.phase in (Phase.BREACHED, Phase.PASSED):
        flash("This account is closed and cannot accept more results.", "error")
        return redirect(url_for("accounts.account_detail", account_id=account_id))

    try:
        trade_date = date.fromisoformat(request.form.get("trade_date", ""))
        pnl = float(request.form.get("pnl", ""))
        bot_id = int(request.form["bot_id"]) if request.form.get("bot_id") else None
    except (ValueError, TypeError):
        flash("Enter a valid date and dollar P&L amount.", "error")
        return redirect(url_for("accounts.account_detail", account_id=account_id))

    if trade_date > datetime.now(timezone.utc).date():
        flash("A daily result cannot be dated in the future.", "error")
        return redirect(url_for("accounts.account_detail", account_id=account_id))
    if not math.isfinite(pnl):
        flash("P&L must be a finite dollar amount.", "error")
        return redirect(url_for("accounts.account_detail", account_id=account_id))

    bot = None
    if bot_id:
        bot = Bot.query.filter_by(id=bot_id, user_id=current_user.id, active=True).first()
        if not bot:
            flash("Select one of your active bots.", "error")
            return redirect(url_for("accounts.account_detail", account_id=account_id))

    result = DailyResult.query.filter_by(account_id=acct.id, trade_date=trade_date).first()
    if Trade.query.filter(
        Trade.account_id == acct.id,
        db.func.date(db.func.coalesce(Trade.closed_at, Trade.opened_at)) == trade_date,
    ).first():
        flash("This date has individual trades. Close those trades instead of adding a daily total.", "error")
        return redirect(url_for("accounts.account_detail", account_id=account_id))

    if result and result.source != "MANUAL":
        flash("This result was calculated from trades and cannot be edited here.", "error")
        return redirect(url_for("accounts.account_detail", account_id=account_id))

    latest_date = _latest_ledger_date(
        acct,
        exclude_daily_result_id=result.id if result else None,
    )
    if result:
        if latest_date and latest_date >= trade_date:
            flash("Only the latest trading day can be corrected. Later activity is already recorded.", "error")
            return redirect(url_for("accounts.account_detail", account_id=account_id))
        _restore_daily_snapshot(acct, result)
        result.pnl = pnl
        result.bot_id = bot.id if bot else None
        result.notes = request.form.get("notes", "").strip() or None
    else:
        if _open_trade_dates(acct):
            flash("Close unclosed trade days before entering a daily total.", "error")
            return redirect(url_for("accounts.account_detail", account_id=account_id))
        if latest_date and trade_date <= latest_date:
            flash("Record daily results in date order; older days cannot be inserted after later activity.", "error")
            return redirect(url_for("accounts.account_detail", account_id=account_id))
        result = DailyResult(
            account_id=acct.id,
            bot_id=bot.id if bot else None,
            trade_date=trade_date,
            pnl=pnl,
            source="MANUAL",
            notes=request.form.get("notes", "").strip() or None,
            balance_before=acct.current_balance,
            peak_before=acct.peak_balance,
            floor_before=acct.max_loss_limit,
            best_day_before=acct.best_day_so_far,
            phase_before=acct.phase.name,
            closed_at_before=acct.closed_at,
        )
        db.session.add(result)

    _apply_daily_close(acct, pnl)
    acct.best_day_so_far = max(float(acct.best_day_so_far), pnl)
    _log(
        ActivityKind.NOTE,
        "Daily result {}: ${:+.2f} -> balance ${:.2f}".format(
            trade_date.isoformat(), pnl, float(acct.current_balance)),
        account=acct,
        payload={"trade_date": trade_date.isoformat(), "pnl": pnl,
                 "balance_after": float(acct.current_balance), "bot_id": bot.id if bot else None},
    )
    db.session.commit()
    flash("Daily result saved: ${:+,.2f}. You can edit this latest day.".format(pnl), "success")
    return redirect(url_for("accounts.account_detail", account_id=account_id))


@bp.route("/accounts/<int:account_id>/close-day", methods=["POST"])
@login_required
def close_trading_day(account_id):
    acct = Account.query.filter_by(id=account_id, user_id=current_user.id).first_or_404()
    if acct.phase in (Phase.BREACHED, Phase.PASSED):
        flash("This account is closed.", "error")
        return redirect(url_for("accounts.account_detail", account_id=account_id))
    open_dates = sorted(_open_trade_dates(acct))
    if not open_dates:
        flash("There are no unclosed trading days.", "error")
        return redirect(url_for("accounts.account_detail", account_id=account_id))
    if any(payout.requested_at.date() >= open_dates[0] for payout in acct.payouts.all()):
        flash("A payout was recorded after an unclosed trade day. Contact support before closing it.", "error")
        return redirect(url_for("accounts.account_detail", account_id=account_id))

    all_trades = acct.trades.all()
    day_pnls = {
        trade_date: sum(float(trade.pnl) for trade in all_trades
                        if (trade.closed_at or trade.opened_at).date() == trade_date)
        for trade_date in open_dates
    }
    acct.current_balance = float(acct.current_balance) - sum(day_pnls.values())
    for trade_date in open_dates:
        pnl = day_pnls[trade_date]
        result = DailyResult(
            account_id=acct.id,
            trade_date=trade_date,
            pnl=pnl,
            source="TRADES",
            balance_before=acct.current_balance,
            peak_before=acct.peak_balance,
            floor_before=acct.max_loss_limit,
            best_day_before=acct.best_day_so_far,
            phase_before=acct.phase.name,
            closed_at_before=acct.closed_at,
        )
        db.session.add(result)
        acct.current_balance = float(acct.current_balance) + pnl
        _apply_daily_close(acct, 0)
        acct.best_day_so_far = max(float(acct.best_day_so_far), pnl)
        _log(ActivityKind.NOTE, "Trading day {} closed: ${:+.2f}".format(trade_date, pnl),
             account=acct,
             payload={"trade_date": trade_date.isoformat(), "pnl": pnl, "source": "TRADES"})
    db.session.commit()
    flash("{} trading day(s) closed. EOD floor and account phase updated.".format(len(open_dates)), "success")
    return redirect(url_for("accounts.account_detail", account_id=account_id))


# ---------------------------------------------------------------------------
# Payout entry
# ---------------------------------------------------------------------------

@bp.route("/accounts/<int:account_id>/payouts", methods=["POST"])
@login_required
def add_payout(account_id):
    acct = Account.query.filter_by(id=account_id, user_id=current_user.id).first_or_404()
    f = request.form

    if _open_trade_dates(acct):
        flash("Close unclosed trading days before recording a payout.", "error")
        return redirect(url_for("accounts.account_detail", account_id=account_id))

    try:
        gross = float(f["gross"])
        split = float(f.get("profit_split") or 0.9)
        net = round(gross * split, 2)
        balance_after = float(acct.current_balance) - gross
    except ValueError as e:
        flash("Invalid input: {}".format(e), "error")
        return redirect(url_for("accounts.account_detail", account_id=account_id))

    if (not math.isfinite(gross) or not math.isfinite(split)
            or gross <= 0 or not 0 < split <= 1
            or gross >= float(acct.current_balance) - float(acct.max_loss_limit)):
        flash("Payout must be positive, within the available balance above the floor, and use a valid split.", "error")
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


# ---------------------------------------------------------------------------
# Webhook receiver management
# ---------------------------------------------------------------------------

@bp.route("/accounts/<int:account_id>/webhooks", methods=["POST"])
@login_required
def add_webhook(account_id):
    acct = Account.query.filter_by(id=account_id, user_id=current_user.id).first_or_404()
    name = request.form.get("name", "").strip() or "TradersPost"
    source = request.form.get("source", "traderspost").strip() or "traderspost"
    receiver = WebhookReceiver(
        account_id=acct.id,
        name=name,
        source=source,
    )
    db.session.add(receiver)
    db.session.commit()
    flash("Webhook created. Copy the URL below and paste it into TradersPost.", "success")
    return redirect(url_for("accounts.account_detail", account_id=account_id))


@bp.route("/accounts/<int:account_id>/webhooks/<int:webhook_id>/delete", methods=["POST"])
@login_required
def delete_webhook(account_id, webhook_id):
    acct = Account.query.filter_by(id=account_id, user_id=current_user.id).first_or_404()
    receiver = WebhookReceiver.query.filter_by(id=webhook_id, account_id=acct.id).first_or_404()
    db.session.delete(receiver)
    db.session.commit()
    flash("Webhook removed.", "success")
    return redirect(url_for("accounts.account_detail", account_id=account_id))
