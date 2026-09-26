"""add best_day_so_far to accounts

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-26
"""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('accounts',
        sa.Column('best_day_so_far', sa.Numeric(precision=12, scale=2),
                  nullable=False, server_default='0')
    )


def downgrade():
    op.drop_column('accounts', 'best_day_so_far')
