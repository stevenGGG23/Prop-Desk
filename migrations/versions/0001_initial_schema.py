"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-26

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(length=64), nullable=False),
        sa.Column('display_name', sa.String(length=128), nullable=False),
        sa.Column('password_hash', sa.String(length=256), nullable=False),
        sa.Column('must_change_password', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('username')
    )
    op.create_index(op.f('ix_users_username'), 'users', ['username'], unique=True)

    op.create_table('firms',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('slug', sa.String(length=64), nullable=False),
        sa.Column('logo_filename', sa.String(length=128), nullable=True),
        sa.Column('default_rules_json', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
        sa.UniqueConstraint('slug')
    )

    op.create_table('distributions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('source', sa.String(length=256), nullable=True),
        sa.Column('win_multiples_json', sa.Text(), nullable=False),
        sa.Column('loss_multiples_json', sa.Text(), nullable=False),
        sa.Column('qty_at_base_risk_json', sa.Text(), nullable=True),
        sa.Column('base_risk', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('signal_frequency', sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    op.create_table('accounts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('firm_id', sa.Integer(), nullable=False),
        sa.Column('nickname', sa.String(length=128), nullable=False),
        sa.Column('external_id', sa.String(length=128), nullable=True),
        sa.Column('phase', sa.Enum('EVAL', 'FUNDED', 'LIVE', 'BREACHED', 'PASSED', name='phase'), nullable=False),
        sa.Column('starting_balance', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('current_balance', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('peak_balance', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('max_loss_limit', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('drawdown_amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('drawdown_type', sa.Enum('EOD_TRAILING', 'INTRADAY_TRAILING', 'STATIC', name='drawdowntype'), nullable=False),
        sa.Column('lock_threshold', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('profit_target', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('daily_loss_limit', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('dll_is_hard', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('consistency_pct', sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column('contract_cap', sa.Integer(), nullable=True),
        sa.Column('current_risk', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('cost_paid', sa.Numeric(precision=10, scale=2), nullable=False, server_default='0'),
        sa.Column('distribution_id', sa.Integer(), nullable=True),
        sa.Column('opened_at', sa.DateTime(), nullable=True),
        sa.Column('closed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['distribution_id'], ['distributions.id']),
        sa.ForeignKeyConstraint(['firm_id'], ['firms.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_accounts_firm_id'), 'accounts', ['firm_id'], unique=False)
    op.create_index(op.f('ix_accounts_user_id'), 'accounts', ['user_id'], unique=False)

    op.create_table('trades',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('opened_at', sa.DateTime(), nullable=False),
        sa.Column('closed_at', sa.DateTime(), nullable=True),
        sa.Column('direction', sa.String(length=8), nullable=True),
        sa.Column('signal_price', sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column('fill_price', sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column('exit_signal_price', sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column('exit_fill_price', sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column('quantity', sa.Integer(), nullable=True),
        sa.Column('pnl', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('r_multiple', sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column('was_rejected', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('rejection_reason', sa.String(length=256), nullable=True),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_trades_account_id'), 'trades', ['account_id'], unique=False)

    op.create_table('payouts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('requested_at', sa.DateTime(), nullable=True),
        sa.Column('gross', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('net', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('balance_after', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_payouts_account_id'), 'payouts', ['account_id'], unique=False)

    op.create_table('activity_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=True),
        sa.Column('kind', sa.Enum('TRADE', 'SETTING_CHANGE', 'PAYOUT', 'PHASE_CHANGE', 'BREACH', 'NOTE', name='activitykind'), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('payload_json', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_activity_logs_account_id'), 'activity_logs', ['account_id'], unique=False)
    op.create_index(op.f('ix_activity_logs_user_id'), 'activity_logs', ['user_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_activity_logs_user_id'), table_name='activity_logs')
    op.drop_index(op.f('ix_activity_logs_account_id'), table_name='activity_logs')
    op.drop_table('activity_logs')
    op.drop_index(op.f('ix_payouts_account_id'), table_name='payouts')
    op.drop_table('payouts')
    op.drop_index(op.f('ix_trades_account_id'), table_name='trades')
    op.drop_table('trades')
    op.drop_index(op.f('ix_accounts_user_id'), table_name='accounts')
    op.drop_index(op.f('ix_accounts_firm_id'), table_name='accounts')
    op.drop_table('accounts')
    op.drop_table('distributions')
    op.drop_table('firms')
    op.drop_index(op.f('ix_users_username'), table_name='users')
    op.drop_table('users')
    op.execute("DROP TYPE IF EXISTS activitykind")
    op.execute("DROP TYPE IF EXISTS drawdowntype")
    op.execute("DROP TYPE IF EXISTS phase")
