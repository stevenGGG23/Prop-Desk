"""Add forward_url to accounts for TradersPost/webhook forwarding

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('accounts', sa.Column('forward_url', sa.String(512), nullable=True))


def downgrade():
    op.drop_column('accounts', 'forward_url')
