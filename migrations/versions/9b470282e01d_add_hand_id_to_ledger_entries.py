"""add hand_id to ledger_entries

Revision ID: 9b470282e01d
Revises: 1d9d5d806472
Create Date: 2026-04-29 19:40:32.802247

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9b470282e01d'
down_revision = '1d9d5d806472'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('ledger_entries',
        sa.Column('hand_id', sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        'fk_ledger_hand_id',
        'ledger_entries', 'vc_hands',
        ['hand_id'], ['id']
    )

def downgrade():
    op.drop_constraint('fk_ledger_hand_id', 'ledger_entries', type_='foreignkey')
    op.drop_column('ledger_entries', 'hand_id')
