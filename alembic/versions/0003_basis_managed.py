"""Allow 'managed' as a third basis for metrics

JPMorgan publishes its key figures on two bases in the same table. Reported
basis is US GAAP. Managed basis adds fully taxable-equivalent adjustments so
that tax-exempt income is comparable with taxable income; the firm steers on
it and the analysts quote it.

That is a different thing from the UBS 'underlying' basis, which strips
negative goodwill and integration costs out of a reported figure. Mapping one
onto the other would make the two banks look comparable where they are not,
so the basis gets its own value.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-12
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
alter table metrics
    drop constraint metrics_basis_check,
    add constraint metrics_basis_check
        check (basis in ('reported', 'underlying', 'managed'));
"""

DOWNGRADE_SQL = """
delete from metrics where basis = 'managed';

alter table metrics
    drop constraint metrics_basis_check,
    add constraint metrics_basis_check
        check (basis in ('reported', 'underlying'));
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
