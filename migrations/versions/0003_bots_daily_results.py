"""add bots, daily results, and idempotent trade imports

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-26
"""
from alembic import op
import sqlalchemy as sa


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False, server_default="1"),
        sa.Column("source", sa.String(length=128), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name", "version"),
    )
    op.create_index(op.f("ix_bots_user_id"), "bots", ["user_id"], unique=False)

    with op.batch_alter_table("distributions") as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_distributions_user_id_users", "users", ["user_id"], ["id"])
        batch_op.create_index(op.f("ix_distributions_user_id"), ["user_id"], unique=False)
        batch_op.add_column(sa.Column("bot_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_distributions_bot_id_bots", "bots", ["bot_id"], ["id"])
        batch_op.create_index(op.f("ix_distributions_bot_id"), ["bot_id"], unique=False)

    with op.batch_alter_table("trades") as batch_op:
        batch_op.add_column(sa.Column("bot_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("import_hash", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("signal_name", sa.String(length=256), nullable=True))
        batch_op.create_foreign_key("fk_trades_bot_id_bots", "bots", ["bot_id"], ["id"])
        batch_op.create_index(op.f("ix_trades_bot_id"), ["bot_id"], unique=False)
        batch_op.create_unique_constraint("uq_trades_account_import_hash", ["account_id", "import_hash"])

    op.create_table(
        "daily_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("bot_id", sa.Integer(), nullable=True),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("pnl", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False, server_default="MANUAL"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("balance_before", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("peak_before", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("floor_before", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("best_day_before", sa.Numeric(precision=12, scale=2), nullable=False, server_default="0"),
        sa.Column("phase_before", sa.String(length=16), nullable=False),
        sa.Column("closed_at_before", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["bot_id"], ["bots.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "trade_date", name="uq_daily_results_account_date"),
    )
    op.create_index(op.f("ix_daily_results_account_id"), "daily_results", ["account_id"], unique=False)
    op.create_index(op.f("ix_daily_results_bot_id"), "daily_results", ["bot_id"], unique=False)

    op.create_table(
        "bot_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("bot_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=24), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("event_at", sa.DateTime(), nullable=False),
        sa.Column("source_key", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["bot_id"], ["bots.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_key"),
    )
    op.create_index(op.f("ix_bot_events_bot_id"), "bot_events", ["bot_id"], unique=False)
    op.create_index(op.f("ix_bot_events_account_id"), "bot_events", ["account_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_bot_events_account_id"), table_name="bot_events")
    op.drop_index(op.f("ix_bot_events_bot_id"), table_name="bot_events")
    op.drop_table("bot_events")
    op.drop_index(op.f("ix_daily_results_bot_id"), table_name="daily_results")
    op.drop_index(op.f("ix_daily_results_account_id"), table_name="daily_results")
    op.drop_table("daily_results")
    with op.batch_alter_table("trades") as batch_op:
        batch_op.drop_constraint("uq_trades_account_import_hash", type_="unique")
        batch_op.drop_index(op.f("ix_trades_bot_id"))
        batch_op.drop_constraint("fk_trades_bot_id_bots", type_="foreignkey")
        batch_op.drop_column("import_hash")
        batch_op.drop_column("signal_name")
        batch_op.drop_column("bot_id")
    with op.batch_alter_table("distributions") as batch_op:
        batch_op.drop_index(op.f("ix_distributions_bot_id"))
        batch_op.drop_constraint("fk_distributions_bot_id_bots", type_="foreignkey")
        batch_op.drop_column("bot_id")
        batch_op.drop_index(op.f("ix_distributions_user_id"))
        batch_op.drop_constraint("fk_distributions_user_id_users", type_="foreignkey")
        batch_op.drop_column("user_id")
    op.drop_index(op.f("ix_bots_user_id"), table_name="bots")
    op.drop_table("bots")