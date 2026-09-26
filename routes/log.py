from flask import Blueprint, render_template, request
from flask_login import login_required, current_user

from models import ActivityLog, Account, ActivityKind

bp = Blueprint("log", __name__)


@bp.route("/log")
@login_required
def activity_log():
    page = request.args.get("page", 1, type=int)
    kind_filter = request.args.get("kind", "")
    account_filter = request.args.get("account_id", 0, type=int)

    query = (ActivityLog.query
             .filter_by(user_id=current_user.id)
             .order_by(ActivityLog.created_at.desc()))

    if kind_filter:
        try:
            query = query.filter(ActivityLog.kind == ActivityKind[kind_filter])
        except KeyError:
            pass

    if account_filter:
        query = query.filter(ActivityLog.account_id == account_filter)

    entries = query.paginate(page=page, per_page=50, error_out=False)

    accounts = (Account.query
                .filter_by(user_id=current_user.id)
                .order_by(Account.nickname)
                .all())

    kinds = [k.value for k in ActivityKind]

    return render_template("log.html", entries=entries, accounts=accounts,
                           kinds=kinds, kind_filter=kind_filter,
                           account_filter=account_filter)
