"""rule codes and alert resolution details

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-18 01:13:43.320542
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('alerts', sa.Column('resolved_values', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    # rules.code is required, but rows created before this migration have none: backfill, then enforce.
    op.add_column('rules', sa.Column('code', sa.String(length=20), nullable=True))
    op.execute("UPDATE rules SET code = 'RULE-' || id WHERE code IS NULL")
    op.alter_column('rules', 'code', existing_type=sa.String(length=20), nullable=False)
    op.create_unique_constraint(op.f('uq_rules_code'), 'rules', ['code'])


def downgrade() -> None:
    op.drop_constraint(op.f('uq_rules_code'), 'rules', type_='unique')
    op.drop_column('rules', 'code')
    op.drop_column('alerts', 'resolved_values')
