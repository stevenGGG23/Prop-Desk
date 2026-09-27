import csv
import io
import json
import hashlib
import math
import time
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify, current_app, render_template
from flask_login import login_required, current_user
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from app import db
from models import (Account, Bot, BotEvent, DailyResult, Distribution, Trade,
                    Phase, ActivityLog, ActivityKind, DrawdownType, WebhookReceiver)
from engine.montecarlo import simulate_risk_ladder, simulate_portfolio
from engine.risk import max_risk_for_n_losses

bp = Blueprint("api", __name__, url_prefix="/api")

# Simple in-memory cache: {cache_key: (result, expires_at)}
_cache: dict = {}
CACHE_TTL = 900  # 15 minutes


def _cache_get(key):
    entry = _cache.get(key)
    if entry and time.time() < entry[1]:
        return entry[0]
    return None


def _cache_set(key, value):
    _cache[key] = (value, time.time() + CACHE_TTL)


def _account_to_dict(acct: Account) -> dict:
    return {
        "id": acct.id,
        "current_balance": float(acct.current_balance),
        "max_loss_limit": float(acct.max_loss_limit),
        "peak_balance": float(acct.peak_balance),
        "drawdown_amount": float(acct.drawdown_amount),
        "lock_threshold": float(acct.lock_threshold),
        "profit_target": float(acct.profit_target) if acct.profit_target else None,
        "daily_loss_limit": float(acct.daily_loss_limit) if acct.daily_loss_limit else None,
        "dll_is_hard": acct.dll_is_hard,
        "consistency_pct": float(acct.consistency_pct) if acct.consistency_pct else None,
        "contract_cap": acct.contract_cap,
        "starting_balance": float(acct.starting_balance),
        "drawdown_type": acct.drawdown_type.value,
        "current_risk": float(acct.current_risk) if acct.current_risk else None,
    }


def _dist_to_dict(dist: Distribution) -> dict:
    return {
        "id": dist.id,
        "win_multiples": dist.win_multiples,
        "loss_multiples": dist.loss_multiples,
        "base_risk": float(dist.base_risk),
        "signal_frequency": float(dist.signal_frequency),
    }


# ---------------------------------------------------------------------------
# Simulate: risk ladder for one account
# ---------------------------------------------------------------------------

@bp.route("/simulate")
@login_required
def simulate():
    account_id = request.args.get("account_id", type=int)
    risk_values_raw = request.args.getlist("risk[]")

    if not account_id or not risk_values_raw:
        return jsonify({"error": "account_id and risk[] required"}), 400

    try:
        risk_values = [float(r) for r in risk_values_raw]
    except ValueError:
        return jsonify({"error": "risk values must be numeric"}), 400

    acct = Account.query.filter_by(id=account_id, user_id=current_user.id).first()
    if not acct:
        return jsonify({"error": "Account not found"}), 404

    if not acct.distribution_id:
        return jsonify({"error": "No distribution linked to this account"}), 422

    dist = Distribution.query.get(acct.distribution_id)
    acct_dict = _account_to_dict(acct)
    dist_dict = _dist_to_dict(dist)

    # Cache key
    state_hash = hashlib.sha256(
        json.dumps({**acct_dict, "risk_values": sorted(risk_values)}, sort_keys=True).encode()
    ).hexdigest()[:16]
    cache_key = "sim_{}".format(state_hash)

    cached = _cache_get(cache_key)
    if cached:
        return jsonify(cached)

    results = simulate_risk_ladder(acct_dict, dist_dict, risk_values, n_runs=5000)
    _cache_set(cache_key, results)
    return jsonify(results)


# ---------------------------------------------------------------------------
# Portfolio risk: joint simulation across all accounts
# ---------------------------------------------------------------------------

@bp.route("/portfolio-risk")
@login_required
def portfolio_risk():
    accounts = (Account.query
                .filter_by(user_id=current_user.id)
                .filter(Account.phase.in_([Phase.EVAL, Phase.FUNDED]))
                .all())

    if not accounts:
        return jsonify({"error": "No active accounts"}), 404

    # Use first account's distribution (all run same signal)
    dist = None
    for acct in accounts:
        if acct.distribution_id:
            dist = Distribution.query.get(acct.distribution_id)
            break

    if not dist:
        return jsonify({"error": "No distribution found"}), 422

    acct_dicts = [_account_to_dict(a) for a in accounts]
    dist_dict = _dist_to_dict(dist)

    cache_key = "port_{}".format(
        hashlib.sha256(json.dumps([a["id"] for a in acct_dicts]).encode()).hexdigest()[:16]
    )
    cached = _cache_get(cache_key)
    if cached:
        return jsonify(cached)

    result = simulate_portfolio(acct_dicts, dist_dict, n_runs=5000)
    _cache_set(cache_key, result)
    return jsonify(result)


# ---------------------------------------------------------------------------
# CSV import from TradingView strategy export
# ---------------------------------------------------------------------------

CSV_MAX_BYTES = 10 * 1024 * 1024


def _normalize_header(value):
    return "".join(character.lower() for character in (value or "") if character.isalnum())


def _csv_value(row, selected, aliases):
    fields = list(row.keys())
    if selected:
        selected_key = _normalize_header(selected)
        for field in fields:
            if _normalize_header(field) == selected_key:
                return row.get(field, "")
    alias_keys = {_normalize_header(alias) for alias in aliases}
    for field in fields:
        if _normalize_header(field) in alias_keys:
            return row.get(field, "")
    return ""


def _parse_csv_datetime(value):
    value = (value or "").strip()
    if not value:
        raise ValueError("date/time is missing")
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = None
        for pattern in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %I:%M:%S %p",
                        "%m/%d/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y",
                        "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(value, pattern)
                break
            except ValueError:
                continue
        if parsed is None:
            raise ValueError("date/time format is not recognized")
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _parse_csv_money(value):
    text = (value or "").strip().replace("$", "").replace(",", "")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    amount = float(text)
    if not math.isfinite(amount):
        raise ValueError("P&L must be a finite number")
    return amount


def _parse_trade_csv(content, account, bot, form):
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        raise ValueError("The CSV file has no header row.")

    alias_map = {
        "pnl": ("Profit", "PnL", "Net Profit", "Profit/Loss", "Realized PnL", "Net P&L", "Net PnL USD", "P&L", "Amount"),
        "date": ("Date/Time", "Date and time", "Date Time", "Date", "Entry Time", "Open Time", "Timestamp", "Time"),
        "external_id": ("Trade ID", "Trade number", "Execution ID", "External ID", "ID"),
        "direction": ("Direction", "Side", "Type"),
        "signal_name": ("Signal", "Strategy", "Signal Name"),
        "quantity": ("Contracts", "Qty", "Quantity", "Size", "Size (qty)"),
        "signal_price": ("Signal Price", "Expected Price"),
        "fill_price": ("Fill Price", "Entry Price", "Average Price", "Price USD"),
        "exit_signal_price": ("Exit Signal Price",),
        "exit_fill_price": ("Exit Fill Price", "Exit Price", "Price USD"),
    }
    parsed_rows = []
    errors = []
    duplicate_count = 0
    batch_hashes = set()
    existing_hashes = {
        trade.import_hash for trade in account.trades.filter(Trade.import_hash.isnot(None)).all()
    }
    selected = {key: form.get(key + "_column", "") for key in alias_map}
    raw_rows = list(enumerate(reader, start=2))
    entry_rows = {}
    type_column_present = any(_normalize_header(header) == "type" for header in reader.fieldnames)
    for row_number, row in raw_rows:
        trade_type = _csv_value(row, selected["direction"], alias_map["direction"]).strip().lower()
        external_id = _csv_value(row, selected["external_id"], alias_map["external_id"]).strip()
        if type_column_present and trade_type.startswith("entry ") and external_id:
            entry_rows[external_id] = (row_number, row)

    for row_number, row in raw_rows:
        try:
            trade_type = _csv_value(row, selected["direction"], alias_map["direction"]).strip()
            normalized_type = trade_type.lower()
            external_id = _csv_value(row, selected["external_id"], alias_map["external_id"]).strip()
            paired_entry = entry_rows.get(external_id) if external_id else None
            if type_column_present and normalized_type.startswith("entry "):
                continue
            if type_column_present and "entry " in normalized_type:
                continue

            def value_for(field, row_data):
                return _csv_value(row_data, selected[field], alias_map[field])

            pnl = _parse_csv_money(_csv_value(row, selected["pnl"], alias_map["pnl"]))
            entry_row = paired_entry[1] if paired_entry else row
            opened_at = _parse_csv_datetime(value_for("date", entry_row))
            closed_at = _parse_csv_datetime(value_for("date", row))
            direction_value = (value_for("direction", entry_row) or trade_type).strip().upper()
            direction = ("LONG" if "LONG" in direction_value or direction_value == "BUY"
                         else "SHORT" if "SHORT" in direction_value or direction_value == "SELL"
                         else direction_value)
            quantity_value = value_for("quantity", entry_row).strip()
            quantity = int(float(quantity_value)) if quantity_value else None

            optional = {}
            for field in ("signal_price", "fill_price", "exit_signal_price", "exit_fill_price"):
                source_row = entry_row if field in ("signal_price", "fill_price") else row
                value = value_for(field, source_row).strip()
                optional[field] = float(value.replace(",", "")) if value else None
                if optional[field] is not None and not math.isfinite(optional[field]):
                    raise ValueError("{} must be a finite number".format(field.replace("_", " ")))

            fingerprint = ("{}:{}:external:{}:{}".format(
                               account.id, bot.id if bot else "", external_id,
                               opened_at.isoformat()) if external_id else
                           "\x1f".join((
                               str(account.id), str(bot.id if bot else ""),
                               opened_at.isoformat(), "{:.2f}".format(pnl), direction,
                               str(quantity or ""), "{:.4f}".format(optional["fill_price"] or 0),
                               str(row_number),
                           )))
            import_hash = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
            if import_hash in existing_hashes or import_hash in batch_hashes:
                duplicate_count += 1
                continue
            batch_hashes.add(import_hash)
            parsed_rows.append({
                "row": row_number, "pnl": pnl, "opened_at": opened_at,
                "closed_at": closed_at,
                "external_id": external_id or None, "direction": direction or None,
                "quantity": quantity, "signal_name": value_for("signal_name", entry_row).strip() or None,
                "import_hash": import_hash, **optional,
            })
        except (TypeError, ValueError) as error:
            errors.append({"row": row_number, "error": str(error)})

    return parsed_rows, errors, reader.fieldnames, duplicate_count


@bp.route("/import-csv", methods=["POST"])
@login_required
def import_csv():
    account_id = request.form.get("account_id", type=int)
    if not account_id:
        return jsonify({"error": "account_id required"}), 400

    acct = Account.query.filter_by(id=account_id, user_id=current_user.id).first()
    if not acct:
        return jsonify({"error": "Account not found"}), 404
    if acct.phase in (Phase.BREACHED, Phase.PASSED):
        return jsonify({"error": "Cannot import new fills into a closed account."}), 409

    bot_id = request.form.get("bot_id", type=int)
    bot = None
    if bot_id:
        bot = Bot.query.filter_by(id=bot_id, user_id=current_user.id, active=True).first()
        if not bot:
            return jsonify({"error": "Bot not found"}), 404

    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "Choose a CSV file."}), 400
    content_bytes = file.read(CSV_MAX_BYTES + 1)
    if len(content_bytes) > CSV_MAX_BYTES:
        return jsonify({"error": "CSV files must be 10 MB or smaller."}), 413
    content = content_bytes.decode("utf-8-sig", errors="replace")

    try:
        parsed_rows, errors, columns, duplicate_count = _parse_trade_csv(content, acct, bot, request.form)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    result = {
        "preview": request.form.get("confirm_import") != "1",
        "count": len(parsed_rows),
        "duplicates": duplicate_count,
        "net_pnl": round(sum(row["pnl"] for row in parsed_rows), 2),
        "errors": errors,
        "columns": columns,
        "sample": [{"row": row["row"], "date": row["opened_at"].isoformat(),
                    "pnl": row["pnl"], "direction": row["direction"],
                    "quantity": row["quantity"]} for row in parsed_rows[:8]],
        "new_balance": float(acct.current_balance),
        "projected_balance": round(
            float(acct.current_balance) + sum(row["pnl"] for row in parsed_rows), 2),
    }
    if request.form.get("confirm_import") != "1":
        return jsonify(result)
    if errors:
        return jsonify({"error": "Fix the invalid rows before importing.", **result}), 422
    if not parsed_rows:
        return jsonify({"error": "No new trades to import. The file may already be imported.", **result}), 400

    finalized_dates = {result.trade_date for result in acct.daily_results.all()}
    latest_closed_date = max(finalized_dates) if finalized_dates else None
    existing_trades = acct.trades.all()
    existing_open_dates = {
        (trade.closed_at or trade.opened_at).date()
        for trade in existing_trades
        if (trade.closed_at or trade.opened_at).date() not in finalized_dates
    }
    latest_payout_date = max((payout.requested_at.date() for payout in acct.payouts.all()), default=None)
    if latest_payout_date and any(day <= latest_payout_date for day in existing_open_dates):
        return jsonify({"error": "Close open trade days before the latest payout before importing more fills."}), 409
    existing_open_trade_pnl = sum(
        float(trade.pnl) for trade in existing_trades
        if (trade.closed_at or trade.opened_at).date() not in finalized_dates
    )
    opening_balance = float(acct.current_balance) - existing_open_trade_pnl
    for row in parsed_rows:
        trade_date = row["opened_at"].date()
        if (trade_date in finalized_dates
            or (latest_closed_date and trade_date < latest_closed_date)
            or (latest_payout_date and trade_date <= latest_payout_date)):
            return jsonify({"error": "Import contains a finalized or out-of-order trading date: {}.".format(trade_date)}), 409

    for row in parsed_rows:
        db.session.add(Trade(
            account_id=acct.id,
            bot_id=bot.id if bot else None,
            import_hash=row["import_hash"],
            opened_at=row["opened_at"],
            closed_at=row["closed_at"],
            direction=row["direction"],
            signal_name=row["signal_name"],
            quantity=row["quantity"],
            signal_price=row["signal_price"],
            fill_price=row["fill_price"],
            exit_signal_price=row["exit_signal_price"],
            exit_fill_price=row["exit_fill_price"],
            pnl=row["pnl"],
        ))
    db.session.flush()

    imported_dates = sorted({row["opened_at"].date() for row in parsed_rows})
    all_trades = acct.trades.all()
    open_dates = sorted({
        (trade.closed_at or trade.opened_at).date()
        for trade in all_trades
        if (trade.closed_at or trade.opened_at).date() not in finalized_dates
    })
    open_trade_pnl = {
        day: sum(float(trade.pnl) for trade in all_trades
                 if (trade.closed_at or trade.opened_at).date() == day)
        for day in open_dates
    }
    acct.current_balance = opening_balance

    from routes.accounts import _apply_daily_close, _check_phase_after_trade, _log

    finalize_days = request.form.get("finalize_days") == "1"
    if finalize_days:
        for day in open_dates:
            day_pnl = open_trade_pnl[day]
            result_row = DailyResult(
                account_id=acct.id,
                bot_id=bot.id if bot else None,
                trade_date=day,
                pnl=day_pnl,
                source="TRADES",
                balance_before=acct.current_balance,
                peak_before=acct.peak_balance,
                floor_before=acct.max_loss_limit,
                best_day_before=acct.best_day_so_far,
                phase_before=acct.phase.name,
                closed_at_before=acct.closed_at,
            )
            db.session.add(result_row)
            acct.current_balance = float(acct.current_balance) + day_pnl
            _apply_daily_close(acct, 0)
            acct.best_day_so_far = max(float(acct.best_day_so_far), day_pnl)
    else:
        acct.current_balance = opening_balance + sum(open_trade_pnl.values())

    if imported_dates:
        _log(ActivityKind.NOTE,
             "CSV import: {} new trade(s) across {} day(s) for {}".format(
                 len(parsed_rows), len(imported_dates), bot.name if bot else acct.nickname),
             account=acct,
             payload={"bot_id": bot.id if bot else None,
                      "trade_count": len(parsed_rows),
                      "trade_dates": [day.isoformat() for day in imported_dates],
                      "finalized": finalize_days})
        if bot:
            for row in parsed_rows:
                description = "{} {} ${:+.2f}".format(
                    row["direction"] or "Fill", row["signal_name"] or "",
                    row["pnl"]).strip()
                db.session.add(BotEvent(
                    bot_id=bot.id,
                    account_id=acct.id,
                    event_type="FILL",
                    message="Imported {} on {}.".format(description, row["closed_at"].date()),
                    event_at=row["closed_at"],
                    source_key=row["import_hash"],
                ))

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Some fills were imported by another request. Preview the CSV again."}), 409
    result.update({"preview": False, "imported": len(parsed_rows),
                   "closed_days": len(open_dates) if finalize_days else 0,
                   "new_balance": float(acct.current_balance),
                   "projected_balance": float(acct.current_balance)})
    return jsonify(result)


@bp.route("/imports")
@login_required
def imports_page():
    accounts = Account.query.filter_by(user_id=current_user.id).order_by(Account.nickname).all()
    bots = Bot.query.filter_by(user_id=current_user.id, active=True).order_by(Bot.name).all()
    distributions = Distribution.query.filter(
        or_(Distribution.user_id.is_(None), Distribution.user_id == current_user.id)
    ).order_by(Distribution.name).all()
    return render_template("imports.html", accounts=accounts, bots=bots,
                           distributions=distributions)


@bp.route("/import-distribution", methods=["POST"])
@login_required
def import_distribution():
    account_id = request.form.get("account_id", type=int)
    account = Account.query.filter_by(id=account_id, user_id=current_user.id).first()
    if not account:
        return jsonify({"error": "Select one of your accounts."}), 400

    bot_id = request.form.get("bot_id", type=int)
    bot = None
    if bot_id:
        bot = Bot.query.filter_by(id=bot_id, user_id=current_user.id, active=True).first()
        if not bot:
            return jsonify({"error": "Bot not found."}), 404

    try:
        name = request.form.get("name", "").strip()
        base_risk = float(request.form.get("base_risk", ""))
        signal_frequency = float(request.form.get("signal_frequency", "0.68"))
    except ValueError:
        return jsonify({"error": "Enter a name, base risk, and signal frequency."}), 400
    if (not name or len(name) > 128 or not math.isfinite(base_risk)
            or not math.isfinite(signal_frequency) or base_risk <= 0
            or not 0 < signal_frequency <= 1):
        return jsonify({"error": "Name is required; base risk must be positive and signal frequency must be 0–1."}), 400

    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "Choose a backtest CSV file."}), 400
    content_bytes = file.read(CSV_MAX_BYTES + 1)
    if len(content_bytes) > CSV_MAX_BYTES:
        return jsonify({"error": "CSV files must be 10 MB or smaller."}), 413

    reader = csv.DictReader(io.StringIO(content_bytes.decode("utf-8-sig", errors="replace")))
    if not reader.fieldnames:
        return jsonify({"error": "The CSV file has no header row."}), 400
    pnl_column = request.form.get("pnl_column", "")
    wins, losses, errors = [], [], []
    sample = []
    type_column_present = any(_normalize_header(header) == "type" for header in reader.fieldnames)
    for row_number, row in enumerate(reader, start=2):
        if not any((value or "").strip() for value in row.values()):
            continue
        trade_type = _csv_value(row, "", ("Type",)).strip().lower()
        if type_column_present and trade_type.startswith("entry "):
            continue
        try:
            pnl = _parse_csv_money(_csv_value(
                row, pnl_column,
                ("Profit", "PnL", "Net Profit", "Profit/Loss", "Net P&L", "Net PnL USD", "P&L", "Amount"),
            ))
            if pnl > 0:
                wins.append(round(pnl / base_risk, 4))
            elif pnl < 0:
                losses.append(round(pnl / base_risk, 4))
            if len(sample) < 8:
                sample.append({"row": row_number, "pnl": pnl})
        except (TypeError, ValueError) as error:
            errors.append({"row": row_number, "error": str(error)})

    preview = request.form.get("confirm_import") != "1"
    result = {"preview": preview, "count": len(wins) + len(losses),
              "wins": len(wins), "losses": len(losses), "errors": errors,
              "sample": sample}
    if preview:
        return jsonify(result)
    if errors:
        return jsonify({"error": "Fix the invalid rows before importing.", **result}), 422
    if not wins or not losses:
        return jsonify({"error": "The backtest must contain at least one winning and one losing trade.", **result}), 422

    distribution = Distribution(
        user_id=current_user.id,
        bot_id=bot.id if bot else None,
        name=name,
        source=file.filename,
        base_risk=base_risk,
        signal_frequency=signal_frequency,
    )
    distribution.win_multiples = wins
    distribution.loss_multiples = losses
    db.session.add(distribution)
    db.session.flush()
    previous_distribution_id = account.distribution_id
    account.distribution_id = distribution.id
    entry = ActivityLog(
        user_id=current_user.id,
        account_id=account.id,
        kind=ActivityKind.NOTE,
        message="Backtest distribution {} imported and linked.".format(name),
        payload_json=json.dumps({
            "distribution_id": distribution.id,
            "previous_distribution_id": previous_distribution_id,
            "win_count": len(wins),
            "loss_count": len(losses),
            "base_risk": base_risk,
        }),
    )
    db.session.add(entry)
    if bot:
        db.session.add(BotEvent(
            bot_id=bot.id,
            account_id=account.id,
            event_type="NOTE",
            message="Backtest distribution {} imported ({} wins, {} losses).".format(
                name, len(wins), len(losses)),
        ))
    db.session.commit()
    result.update({"preview": False, "distribution": name,
                   "win_rate": round(len(wins) / (len(wins) + len(losses)) * 100, 1)})
    return jsonify(result)


# ---------------------------------------------------------------------------
# Webhook receiver — TradersPost / any source POSTs fill data here
# ---------------------------------------------------------------------------

@bp.route("/webhook/<token>", methods=["POST"])
def receive_webhook(token):
    """Public endpoint: no login required — token is the credential."""
    receiver = WebhookReceiver.query.filter_by(token=token, active=True).first()
    if not receiver:
        return jsonify({"error": "not found"}), 404

    data = request.get_json(silent=True) or {}
    receiver.last_received_at = datetime.now(timezone.utc)
    receiver.last_payload_json = json.dumps(data)

    acct = Account.query.get(receiver.account_id)

    # Try to extract P&L from the payload
    # NOTE: Standard TradersPost entry signals {"ticker":"MNQ","action":"buy"}
    # do NOT include P&L — that field only appears in close/fill notifications.
    # Without P&L we log the signal as a NOTE so you can see activity, but
    # the balance is not changed.
    pnl = None
    for key in ("pnl", "profit", "net_profit", "net_pnl", "realizedPnl", "realized_pnl"):
        if key in data:
            try:
                pnl = float(data[key])
                break
            except (TypeError, ValueError):
                pass

    ticker = data.get("ticker", "")
    action = (data.get("action", "") or "").upper()
    has_pnl = pnl is not None and math.isfinite(pnl)

    if has_pnl:
        msg = "Webhook fill from {}: {} {} pnl={:+.2f}".format(
            receiver.source, action, ticker, pnl)
    else:
        msg = "Webhook signal from {}: {} {} (no P&L in payload — balance unchanged)".format(
            receiver.source, action, ticker)

    entry = ActivityLog(
        user_id=acct.user_id,
        account_id=acct.id,
        kind=ActivityKind.NOTE,
        message=msg,
        payload_json=json.dumps({"token_prefix": token[:8], "pnl": pnl, "raw": data}),
    )
    db.session.add(entry)

    # Only update balance if the payload actually contains a realized P&L
    if has_pnl and acct.phase not in (Phase.BREACHED, Phase.PASSED):
        trade = Trade(
            account_id=acct.id,
            opened_at=datetime.now(timezone.utc),
            pnl=pnl,
            direction=action[:5] or None,
            quantity=data.get("quantity") or data.get("contracts") or None,
            fill_price=data.get("price") or data.get("fill_price") or None,
            signal_name="webhook:{}".format(receiver.source),
        )
        db.session.add(trade)
        acct.current_balance = float(acct.current_balance) + pnl

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "db error"}), 500

    return jsonify({"ok": True, "pnl_recorded": pnl, "signal": action or None})


# ---------------------------------------------------------------------------
# Master inbound webhook — one URL for all accounts, route by account name
# ---------------------------------------------------------------------------

@bp.route("/inbound/<token>", methods=["POST"])
def inbound_webhook(token):
    """One webhook URL per user.  Route trades to the right account using
    the "account" field in the payload (matched against Account.nickname or
    Account.external_id, case-insensitive).

    Minimum payload:
        {"account": "Growth 150k", "action": "buy", "ticker": "MNQ"}

    With P&L (updates balance + logs trade):
        {"account": "Growth 150k", "action": "buy", "ticker": "MNQ",
         "price": 21500.25, "quantity": 5, "pnl": 900.00}

    TradingView strategy alert JSON to paste in TV:
        {
          "account": "Growth 150k",
          "ticker": "{{ticker}}",
          "action": "{{strategy.order.action}}",
          "sentiment": "{{strategy.market_position}}",
          "price": {{close}},
          "pnl": {{strategy.netprofit}}
        }
    """
    from models import User
    user = User.query.filter_by(inbound_token=token).first()
    if not user:
        return jsonify({"error": "invalid token"}), 404

    data = request.get_json(silent=True) or {}

    # ── Resolve account ──────────────────────────────────────────────────────
    account_name = (data.get("account") or data.get("account_id") or "").strip()
    if not account_name:
        return jsonify({"error": "payload must include 'account' field with the account nickname"}), 422

    acct = (Account.query
            .filter_by(user_id=user.id)
            .filter(
                db.or_(
                    db.func.lower(Account.nickname) == account_name.lower(),
                    Account.external_id == account_name,
                )
            )
            .first())
    if not acct:
        return jsonify({"error": "no account matched '{}'. Check the nickname in Prop Desk.".format(account_name)}), 404

    # ── Parse payload ────────────────────────────────────────────────────────
    # Direction: TradersPost uses action = buy/sell; sentiment = long/short/flat
    action_raw = (data.get("action") or "").lower()
    sentiment_raw = (data.get("sentiment") or "").lower()
    if action_raw in ("buy", "buy_long", "long") or sentiment_raw in ("long",):
        direction = "LONG"
    elif action_raw in ("sell", "sell_short", "short", "exit_long") or sentiment_raw in ("short",):
        direction = "SHORT"
    else:
        direction = None

    ticker = data.get("ticker", "")

    fill_price = None
    for key in ("price", "fill_price", "close"):
        if key in data:
            try:
                fill_price = float(data[key])
                break
            except (TypeError, ValueError):
                pass

    quantity = None
    for key in ("quantity", "contracts", "qty", "size"):
        if key in data:
            try:
                quantity = int(float(data[key]))
                break
            except (TypeError, ValueError):
                pass

    pnl = None
    for key in ("pnl", "profit", "net_pnl", "net_profit", "realizedPnl", "realized_pnl"):
        if key in data:
            try:
                pnl = float(data[key])
                break
            except (TypeError, ValueError):
                pass

    # Optional: caller can send current balance directly to sync the account
    new_balance = None
    for key in ("balance", "account_balance", "current_balance"):
        if key in data:
            try:
                new_balance = float(data[key])
                break
            except (TypeError, ValueError):
                pass

    has_pnl = pnl is not None and math.isfinite(pnl)

    # ── Log + update ─────────────────────────────────────────────────────────
    if has_pnl:
        msg = "Inbound webhook: {} {} {}  pnl={:+.2f}".format(
            direction or action_raw.upper(), ticker, acct.nickname, pnl)
    else:
        msg = "Inbound webhook: {} {} {}  (signal only — no P&L)".format(
            direction or action_raw.upper(), ticker, acct.nickname)

    entry = ActivityLog(
        user_id=user.id,
        account_id=acct.id,
        kind=ActivityKind.NOTE,
        message=msg,
        payload_json=json.dumps({"raw": data}),
    )
    db.session.add(entry)

    if has_pnl and acct.phase not in (Phase.BREACHED, Phase.PASSED):
        trade = Trade(
            account_id=acct.id,
            opened_at=datetime.now(timezone.utc),
            pnl=pnl,
            direction=direction,
            quantity=quantity,
            fill_price=fill_price,
            signal_name="inbound:{}".format(ticker or "unknown"),
        )
        db.session.add(trade)
        if new_balance is not None:
            acct.current_balance = new_balance
        else:
            acct.current_balance = float(acct.current_balance) + pnl

    elif new_balance is not None and acct.phase not in (Phase.BREACHED, Phase.PASSED):
        # Balance sync without explicit P&L
        acct.current_balance = new_balance

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return jsonify({"error": "db error"}), 500

    # Forward to TradersPost (or any configured webhook URL) after recording
    if acct.forward_url:
        try:
            import requests as _req
            _req.post(acct.forward_url, json=data, timeout=5)
        except Exception:
            pass

    return jsonify({
        "ok": True,
        "account": acct.nickname,
        "direction": direction,
        "pnl_recorded": pnl,
        "balance": float(acct.current_balance),
    })


# ---------------------------------------------------------------------------
# Daily email report  (called by scheduler — no request context needed)
# ---------------------------------------------------------------------------

def send_daily_report_email(flask_app):
    """Build and send the daily summary email to stevengobran@gmail.com.

    Designed to be called from a background scheduler thread.
    Uses flask_app.app_context() so no request context is required.
    """
    import logging
    import smtplib
    from email.mime.text import MIMEText

    logger = logging.getLogger(__name__)

    with flask_app.app_context():
        gmail_password = (flask_app.config.get("GMAIL_APP_PASSWORD", "") or "").replace(" ", "")
        if not gmail_password:
            logger.warning("Daily report skipped: GMAIL_APP_PASSWORD not set.")
            return

        gmail_address = "stevengobran@gmail.com"

        accounts = (Account.query
                    .order_by(Account.opened_at.desc())
                    .all())

        from engine.risk import room as calc_room, losses_survivable, check_risk_warning
        from routes.accounts import _consistency_flags, _days_to_target

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        lines = [
            "PROP DESK DAILY REPORT — {}".format(today),
            "=" * 60,
            "",
        ]

        active_count = 0
        for acct in accounts:
            if acct.phase in (Phase.BREACHED, Phase.PASSED):
                continue
            active_count += 1
            bal = float(acct.current_balance)
            start = float(acct.starting_balance)
            mll = float(acct.max_loss_limit)
            profit = bal - start
            r = calc_room(bal, mll)
            risk = float(acct.current_risk) if acct.current_risk else None
            survivable = losses_survivable(bal, mll, risk) if risk else None
            warn = check_risk_warning(risk, bal, mll) if risk else False
            cons = _consistency_flags(acct)
            days = _days_to_target(acct)

            lines.append("▸ {} — {}  [{}]".format(acct.nickname, acct.firm.name, acct.phase.value))
            lines.append("  Balance : ${:,.2f}   Profit: ${:+,.2f}".format(bal, profit))
            lines.append("  Floor   : ${:,.2f}   Room:   ${:,.0f}{}".format(
                mll, r, "  *** LOCKED ***" if acct.floor_is_locked else ""))
            if risk:
                lines.append("  Risk    : ${:,.0f}   Losses left: {}{}".format(
                    risk, survivable or "—", "  *** WARNING ***" if warn else ""))
            if acct.profit_target:
                pct_done = max(0, min(profit / float(acct.profit_target) * 100, 100))
                lines.append("  Target  : ${:,.0f}   Progress: {:.0f}%".format(
                    float(acct.profit_target), pct_done))
            if days and days != 0:
                lines.append("  Est. days to target: ~{} trading (~{} calendar)  (${:,.0f} remaining)".format(
                    days["trading"], days["calendar"], days["remaining"]))
            if cons:
                if not cons["passing"]:
                    lines.append("  !! CONSISTENCY FAILING: {:.1f}% ratio vs {:.0f}% limit".format(
                        cons["current_ratio"] or 0, cons["pct_label"]))
                    lines.append("     Need ${:,.0f} more profit before you can pass.".format(
                        cons["extra_needed"] or 0))
                elif cons.get("max_next_win"):
                    lines.append("  Consistency OK — stay under ${:,.0f} on any single day.".format(
                        cons["max_next_win"]))
            lines.append("")

        if active_count == 0:
            lines.append("No active accounts.")
            lines.append("")

        lines.append("-" * 60)
        lines.append("Sent by Prop Desk  |  {}".format(
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")))

        body = "\n".join(lines)
        subject = "Prop Desk Report — {}".format(
            datetime.now(timezone.utc).strftime("%b %d, %Y"))

        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = gmail_address
        msg["To"] = gmail_address

        try:
            try:
                with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
                    server.login(gmail_address, gmail_password)
                    server.send_message(msg)
            except (smtplib.SMTPException, OSError):
                with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as server:
                    server.ehlo()
                    server.starttls()
                    server.login(gmail_address, gmail_password)
                    server.send_message(msg)
            logger.info("Daily report sent to %s", gmail_address)
        except Exception as exc:
            logger.error("Daily report Gmail send failed: %s", exc)
