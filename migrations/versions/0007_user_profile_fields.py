"""Add email, timezone, bio to users table

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('email', sa.String(256), nullable=True))
    op.add_column('users', sa.Column('timezone', sa.String(64), nullable=False, server_default='America/New_York'))
    op.add_column('users', sa.Column('bio', sa.Text, nullable=True))
    with op.batch_alter_table('users') as batch_op:
        batch_op.create_index('ix_users_email', ['email'], unique=True)


def downgrade():
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_index('ix_users_email')
    op.drop_column('users', 'bio')
    op.drop_column('users', 'timezone')
    op.drop_column('users', 'email')
