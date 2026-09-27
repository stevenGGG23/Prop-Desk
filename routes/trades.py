from flask import Blueprint, render_template, request
from flask_login import current_user, login_required

from app import db
from models import Account, Trade

bp = Blueprint("trades", __name__)


@bp.route("/trades")
@login_required
def trades_list():
    accounts = Account.query.filter_by(user_id=current_user.id).order_by(Account.nickname).all()
    account_id = request.args.get("account_id", type=int)
    selected = next((a for a in accounts if a.id == account_id), None)

    if selected:
        trades = (Trade.query
                  .filter_by(account_id=selected.id)
                  .order_by(Trade.opened_at.desc())
                  .all())
    else:
        trades = (Trade.query
                  .join(Account)
                  .filter(Account.user_id == current_user.id)
                  .order_by(Trade.opened_at.desc())
                  .all())

    return render_template("trades.html", trades=trades, accounts=accounts, selected=selected)
