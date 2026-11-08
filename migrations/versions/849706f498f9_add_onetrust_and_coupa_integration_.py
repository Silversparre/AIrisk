"""Add OneTrust and Coupa integration records

Revision ID: 849706f498f9
Revises: 1aba309dd140
Create Date: 2026-11-08 17:59:19.348248

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '849706f498f9'
down_revision = '1aba309dd140'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('external_link',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('provider', sa.String(length=20), nullable=False),
    sa.Column('assessment_id', sa.Integer(), nullable=True),
    sa.Column('system_id', sa.Integer(), nullable=True),
    sa.Column('vendor_id', sa.Integer(), nullable=True),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('api_url', sa.String(length=1000), nullable=False),
    sa.Column('url_template', sa.String(length=1000), nullable=False),
    sa.Column('correlation', sa.String(length=36), nullable=False),
    sa.Column('external_id', sa.String(length=80), nullable=True),
    sa.Column('state', sa.String(length=20), nullable=False),
    sa.Column('source', sa.JSON(), nullable=False),
    sa.Column('progress', sa.JSON(), nullable=True),
    sa.Column('request_payload', sa.JSON(), nullable=True),
    sa.Column('publication', sa.JSON(), nullable=True),
    sa.Column('publication_state', sa.String(length=20), nullable=False),
    sa.Column('write_target', sa.JSON(), nullable=True),
    sa.Column('published_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('last_synced_at', sa.DateTime(), nullable=True),
    sa.Column('next_sync_at', sa.DateTime(), nullable=True),
    sa.Column('failure_count', sa.Integer(), nullable=False),
    sa.Column('last_error', sa.String(length=500), nullable=False),
    sa.Column('lease_token', sa.String(length=36), nullable=True),
    sa.Column('lease_until', sa.DateTime(), nullable=True),
    sa.CheckConstraint("(provider = 'onetrust' AND assessment_id IS NOT NULL AND system_id IS NULL) OR (provider = 'coupa' AND assessment_id IS NULL)", name=op.f('ck_external_link_provider_scope')),
    sa.ForeignKeyConstraint(['assessment_id'], ['assessment.id'], name=op.f('fk_external_link_assessment_id_assessment')),
    sa.ForeignKeyConstraint(['created_by'], ['user.id'], name=op.f('fk_external_link_created_by_user')),
    sa.ForeignKeyConstraint(['system_id'], ['ai_system.id'], name=op.f('fk_external_link_system_id_ai_system')),
    sa.ForeignKeyConstraint(['vendor_id'], ['vendor.id'], name=op.f('fk_external_link_vendor_id_vendor')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_external_link')),
    sa.UniqueConstraint('assessment_id', name='uq_external_assessment'),
    sa.UniqueConstraint('correlation', name=op.f('uq_external_link_correlation')),
    sa.UniqueConstraint('provider', 'api_url', 'external_id', name='uq_external_remote'),
    sa.UniqueConstraint('system_id', name='uq_external_system')
    )


def downgrade():
    op.drop_table('external_link')
