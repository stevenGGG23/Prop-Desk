import csv
import io
import json
import hashlib
import time
from datetime import datetime, timezone
from functools import lru_cache

from flask import Blueprint, request, jsonify, current_app
from flask_login import login_required, current_user

from app import db
from models import Account, Distribution, Trade, Phase, ActivityLog, ActivityKind
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

@bp.route("/import-csv", methods=["POST"])
@login_required
def import_csv():
    account_id = request.form.get("account_id", type=int)
    if not account_id:
        return jsonify({"error": "account_id required"}), 400

    acct = Account.query.filter_by(id=account_id, user_id=current_user.id).first()
    if not acct:
        return jsonify({"error": "Account not found"}), 404

    file = request.files.get("file")
    if not file:
        return jsonify({"error": "No file uploaded"}), 400

    content = file.read().decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(content))

    imported = 0
    errors = []

    for i, row in enumerate(reader):
        try:
            # TradingView export columns (may vary)
            pnl = float(row.get("Profit", row.get("PnL", row.get("Net Profit", 0))))
            date_str = row.get("Date/Time", row.get("Date", ""))
            try:
                opened_at = datetime.fromisoformat(date_str.replace(" ", "T"))
            except Exception:
                opened_at = datetime.now(timezone.utc)

            trade = Trade(
                account_id=acct.id,
                opened_at=opened_at,
                pnl=pnl,
            )
            db.session.add(trade)
            acct.current_balance = float(acct.current_balance) + pnl
            imported += 1
        except Exception as e:
            errors.append({"row": i + 1, "error": str(e)})

    if imported > 0:
        entry = ActivityLog(
            user_id=current_user.id,
            account_id=acct.id,
            kind=ActivityKind.NOTE,
            message="CSV import: {} trades imported".format(imported),
        )
        db.session.add(entry)

    db.session.commit()

    return jsonify({
        "imported": imported,
        "errors": errors,
        "new_balance": float(acct.current_balance),
    })
