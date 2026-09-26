import enum
import json
from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from app import db


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class DrawdownType(enum.Enum):
    EOD_TRAILING = "EOD_TRAILING"
    INTRADAY_TRAILING = "INTRADAY_TRAILING"
    STATIC = "STATIC"


class Phase(enum.Enum):
    EVAL = "EVAL"
    FUNDED = "FUNDED"
    LIVE = "LIVE"
    BREACHED = "BREACHED"
    PASSED = "PASSED"


class PayoutFrequency(enum.Enum):
    DAILY = "DAILY"
    EVERY_N_DAYS = "EVERY_N_DAYS"
    N_QUALIFYING_DAYS = "N_QUALIFYING_DAYS"


class PayoutFormula(enum.Enum):
    FLAT_CAP = "FLAT_CAP"
    PCT_OF_PROFIT = "PCT_OF_PROFIT"
    MULTIPLE_OF_RECENT_GAIN = "MULTIPLE_OF_RECENT_GAIN"


class ActivityKind(enum.Enum):
    TRADE = "TRADE"
    SETTING_CHANGE = "SETTING_CHANGE"
    PAYOUT = "PAYOUT"
    PHASE_CHANGE = "PHASE_CHANGE"
    BREACH = "BREACH"
    NOTE = "NOTE"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    display_name = db.Column(db.String(128), nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    must_change_password = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    accounts = db.relationship("Account", back_populates="user", lazy="dynamic")
    activity_logs = db.relationship("ActivityLog", back_populates="user", lazy="dynamic")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f"<User {self.username}>"


class Firm(db.Model):
    __tablename__ = "firms"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), unique=True, nullable=False)
    slug = db.Column(db.String(64), unique=True, nullable=False)
    logo_filename = db.Column(db.String(128), nullable=True)
    default_rules_json = db.Column(db.Text, nullable=True)

    accounts = db.relationship("Account", back_populates="firm", lazy="dynamic")

    @property
    def default_rules(self):
        if self.default_rules_json:
            return json.loads(self.default_rules_json)
        return {}

    @default_rules.setter
    def default_rules(self, value):
        self.default_rules_json = json.dumps(value)

    def __repr__(self):
        return f"<Firm {self.name}>"


class Account(db.Model):
    __tablename__ = "accounts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    firm_id = db.Column(db.Integer, db.ForeignKey("firms.id"), nullable=False)
    nickname = db.Column(db.String(128), nullable=False)
    external_id = db.Column(db.String(128), nullable=True)
    phase = db.Column(db.Enum(Phase), default=Phase.EVAL, nullable=False)

    # Balance tracking
    starting_balance = db.Column(db.Numeric(12, 2), nullable=False)
    current_balance = db.Column(db.Numeric(12, 2), nullable=False)
    peak_balance = db.Column(db.Numeric(12, 2), nullable=False)

    # Risk parameters
    max_loss_limit = db.Column(db.Numeric(12, 2), nullable=False)
    drawdown_amount = db.Column(db.Numeric(12, 2), nullable=False)
    drawdown_type = db.Column(db.Enum(DrawdownType), default=DrawdownType.EOD_TRAILING, nullable=False)
    lock_threshold = db.Column(db.Numeric(12, 2), nullable=False)

    # Evaluation parameters
    profit_target = db.Column(db.Numeric(12, 2), nullable=True)
    daily_loss_limit = db.Column(db.Numeric(12, 2), nullable=True)
    dll_is_hard = db.Column(db.Boolean, default=False, nullable=False)
    consistency_pct = db.Column(db.Numeric(5, 4), nullable=True)
    contract_cap = db.Column(db.Integer, nullable=True)

    # Trading
    current_risk = db.Column(db.Numeric(12, 2), nullable=True)
    cost_paid = db.Column(db.Numeric(10, 2), default=0, nullable=False)

    # Distribution for simulation
    distribution_id = db.Column(db.Integer, db.ForeignKey("distributions.id"), nullable=True)

    opened_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    closed_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship("User", back_populates="accounts")
    firm = db.relationship("Firm", back_populates="accounts")
    trades = db.relationship("Trade", back_populates="account", lazy="dynamic",
                             order_by="Trade.opened_at.desc()")
    payouts = db.relationship("Payout", back_populates="account", lazy="dynamic")
    distribution = db.relationship("Distribution", foreign_keys=[distribution_id])
    activity_logs = db.relationship("ActivityLog", back_populates="account", lazy="dynamic")

    @property
    def room(self):
        return float(self.current_balance) - float(self.max_loss_limit)

    @property
    def progress_to_target(self):
        if not self.profit_target:
            return None
        profit = float(self.current_balance) - float(self.starting_balance)
        return min(profit / float(self.profit_target), 1.0)

    @property
    def floor_is_locked(self):
        return float(self.max_loss_limit) >= float(self.lock_threshold)

    def __repr__(self):
        return f"<Account {self.nickname} ({self.phase.value})>"


class Trade(db.Model):
    __tablename__ = "trades"

    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False, index=True)
    opened_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    closed_at = db.Column(db.DateTime, nullable=True)
    direction = db.Column(db.String(8), nullable=True)  # LONG / SHORT

    signal_price = db.Column(db.Numeric(12, 4), nullable=True)
    fill_price = db.Column(db.Numeric(12, 4), nullable=True)
    exit_signal_price = db.Column(db.Numeric(12, 4), nullable=True)
    exit_fill_price = db.Column(db.Numeric(12, 4), nullable=True)

    quantity = db.Column(db.Integer, nullable=True)
    pnl = db.Column(db.Numeric(12, 2), nullable=False)
    r_multiple = db.Column(db.Numeric(8, 4), nullable=True)

    was_rejected = db.Column(db.Boolean, default=False, nullable=False)
    rejection_reason = db.Column(db.String(256), nullable=True)

    account = db.relationship("Account", back_populates="trades")

    def __repr__(self):
        return f"<Trade {self.id} pnl={self.pnl}>"


class Payout(db.Model):
    __tablename__ = "payouts"

    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False, index=True)
    requested_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    gross = db.Column(db.Numeric(12, 2), nullable=False)
    net = db.Column(db.Numeric(12, 2), nullable=False)
    balance_after = db.Column(db.Numeric(12, 2), nullable=False)

    account = db.relationship("Account", back_populates="payouts")


class Distribution(db.Model):
    __tablename__ = "distributions"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    source = db.Column(db.String(256), nullable=True)
    win_multiples_json = db.Column(db.Text, nullable=False)
    loss_multiples_json = db.Column(db.Text, nullable=False)
    qty_at_base_risk_json = db.Column(db.Text, nullable=True)
    base_risk = db.Column(db.Numeric(12, 2), nullable=False)
    signal_frequency = db.Column(db.Numeric(5, 4), nullable=False, default=0.68)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    @property
    def win_multiples(self):
        return json.loads(self.win_multiples_json)

    @win_multiples.setter
    def win_multiples(self, value):
        self.win_multiples_json = json.dumps(value)

    @property
    def loss_multiples(self):
        return json.loads(self.loss_multiples_json)

    @loss_multiples.setter
    def loss_multiples(self, value):
        self.loss_multiples_json = json.dumps(value)

    @property
    def win_rate(self):
        wins = [m for m in self.win_multiples if m > 0]
        losses = [m for m in self.loss_multiples if m < 0]
        total = len(wins) + len(losses)
        return len(wins) / total if total else 0

    def __repr__(self):
        return f"<Distribution {self.name}>"


class ActivityLog(db.Model):
    __tablename__ = "activity_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=True, index=True)
    kind = db.Column(db.Enum(ActivityKind), nullable=False)
    message = db.Column(db.Text, nullable=False)
    payload_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship("User", back_populates="activity_logs")
    account = db.relationship("Account", back_populates="activity_logs")

    @property
    def payload(self):
        if self.payload_json:
            return json.loads(self.payload_json)
        return {}

    def __repr__(self):
        return f"<ActivityLog {self.kind.value} {self.created_at}>"
