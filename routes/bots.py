from datetime import datetime, timezone

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from app import db
from models import Account, Bot, BotEvent

bp = Blueprint("bots", __name__)
EVENT_TYPES = ["SIGNAL", "ORDER", "FILL", "REJECTION", "ERROR", "NOTE"]


@bp.route("/bots", methods=["GET", "POST"])
@login_required
def index():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        version = request.form.get("version", "1").strip() or "1"
        if not name:
            flash("Enter a bot or strategy name.", "error")
        else:
            bot = Bot(
                user_id=current_user.id,
                name=name,
                version=version,
                source=request.form.get("source", "").strip() or None,
                notes=request.form.get("notes", "").strip() or None,
            )
            try:
                db.session.add(bot)
                db.session.flush()
                db.session.add(BotEvent(
                    bot_id=bot.id,
                    event_type="NOTE",
                    message="Bot version registered.",
                ))
                db.session.commit()
                flash("Bot version added.", "success")
                return redirect(url_for("bots.index"))
            except IntegrityError:
                db.session.rollback()
                flash("That bot name and version already exist.", "error")

    bots = Bot.query.filter_by(user_id=current_user.id).order_by(Bot.name, Bot.version.desc()).all()
    accounts = Account.query.filter_by(user_id=current_user.id).order_by(Account.nickname).all()
    bot_rows = [{
        "bot": bot,
        "events": bot.events.order_by(BotEvent.event_at.desc()).limit(12).all(),
        "trade_count": bot.trades.count(),
    } for bot in bots]
    return render_template("bots.html", bot_rows=bot_rows, accounts=accounts,
                           event_types=EVENT_TYPES)


@bp.route("/bots/<int:bot_id>/events", methods=["POST"])
@login_required
def add_event(bot_id):
    bot = Bot.query.filter_by(id=bot_id, user_id=current_user.id).first_or_404()
    event_type = request.form.get("event_type", "NOTE").upper()
    message = request.form.get("message", "").strip()
    if event_type not in EVENT_TYPES or not message:
        flash("Choose an event type and enter a message.", "error")
        return redirect(url_for("bots.index"))

    account_id = request.form.get("account_id", type=int)
    if account_id and not Account.query.filter_by(id=account_id, user_id=current_user.id).first():
        flash("Select one of your accounts.", "error")
        return redirect(url_for("bots.index"))

    event_at_text = request.form.get("event_at", "").strip()
    try:
        event_at = datetime.fromisoformat(event_at_text) if event_at_text else datetime.now(timezone.utc)
    except ValueError:
        flash("Enter a valid event date and time.", "error")
        return redirect(url_for("bots.index"))
    if event_at.tzinfo is None:
        event_at = event_at.replace(tzinfo=timezone.utc)

    db.session.add(BotEvent(
        bot_id=bot.id,
        account_id=account_id,
        event_type=event_type,
        message=message,
        event_at=event_at,
    ))
    db.session.commit()
    flash("Bot event recorded.", "success")
    return redirect(url_for("bots.index"))