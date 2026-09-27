"""add inbound_token to users

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-27
"""
from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("inbound_token", sa.String(length=64), nullable=True))
    op.create_unique_constraint("uq_users_inbound_token", "users", ["inbound_token"])


def downgrade():
    op.drop_constraint("uq_users_inbound_token", "users", type_="unique")
    op.drop_column("users", "inbound_token")
