import calendar as calendar_module
from collections import defaultdict
from datetime import date, datetime

from flask import Blueprint, render_template, request
from flask_login import current_user, login_required

from models import Account, DailyResult, Trade

bp = Blueprint("calendar", __name__)


@bp.route("/calendar")
@login_required
def month_view():
    today = date.today()
    month_value = request.args.get("month", "")
    try:
        month_start = datetime.strptime(month_value, "%Y-%m").date().replace(day=1)
    except ValueError:
        month_start = today.replace(day=1)

    selected_account_id = request.args.get("account_id", 0, type=int)
    accounts = (Account.query.filter_by(user_id=current_user.id)
                .order_by(Account.nickname).all())
    account_ids = {account.id for account in accounts}
    if selected_account_id not in account_ids:
        selected_account_id = 0

    account_query = Account.query.filter_by(user_id=current_user.id)
    if selected_account_id:
        account_query = account_query.filter_by(id=selected_account_id)
    selected_accounts = account_query.all()
    selected_ids = [account.id for account in selected_accounts]

    daily_results = (DailyResult.query.filter(DailyResult.account_id.in_(selected_ids)).all()
                     if selected_ids else [])
    results_by_account_date = {(result.account_id, result.trade_date): result for result in daily_results}
    day_totals = defaultdict(lambda: {"pnl": 0.0, "trades": 0, "accounts": set()})
    trades = (Trade.query.filter(Trade.account_id.in_(selected_ids)).all()
              if selected_ids else [])
    trades_by_account_date = defaultdict(int)
    for trade in trades:
        trades_by_account_date[(trade.account_id, (trade.closed_at or trade.opened_at).date())] += 1

    first_day = month_start
    last_day = date(month_start.year, month_start.month,
                    calendar_module.monthrange(month_start.year, month_start.month)[1])
    for result in daily_results:
        if first_day <= result.trade_date <= last_day:
            day_totals[result.trade_date]["pnl"] += float(result.pnl)
            day_totals[result.trade_date]["accounts"].add(result.account_id)
            if result.source == "TRADES":
                day_totals[result.trade_date]["trades"] += trades_by_account_date[(result.account_id, result.trade_date)]

    for trade in trades:
        trade_day = (trade.closed_at or trade.opened_at).date()
        if not first_day <= trade_day <= last_day:
            continue
        if (trade.account_id, trade_day) in results_by_account_date:
            continue
        day_totals[trade_day]["pnl"] += float(trade.pnl)
        day_totals[trade_day]["trades"] += 1
        day_totals[trade_day]["accounts"].add(trade.account_id)

    weeks = calendar_module.Calendar(firstweekday=0).monthdatescalendar(
        month_start.year, month_start.month)
    previous_month = date(month_start.year - (month_start.month == 1),
                          12 if month_start.month == 1 else month_start.month - 1, 1)
    next_month = date(month_start.year + (month_start.month == 12),
                      1 if month_start.month == 12 else month_start.month + 1, 1)
    totals = {"pnl": sum(item["pnl"] for item in day_totals.values()),
              "trades": sum(item["trades"] for item in day_totals.values()),
              "days": len(day_totals)}
    return render_template(
        "calendar.html", month=month_start, weeks=weeks, day_totals=day_totals,
        previous_month=previous_month, next_month=next_month, accounts=accounts,
        selected_account_id=selected_account_id, totals=totals, today=today,
    )