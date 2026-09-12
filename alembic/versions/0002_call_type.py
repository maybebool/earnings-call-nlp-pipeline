"""Add call_type and source, widen the uniqueness of filings and calls

A quarter can hold more than one call. JPMorgan held the First Republic
acquisition call on 1 May 2023, which falls into 2023-Q2 next to that
quarter's regular earnings call. Both the filing rows and the call rows were
unique per quarter, so the second call could not be stored.

`source` records whether a document was filed with the SEC or only published
on the company's investor relations site. JPMorgan does not file its
transcripts, so those rows carry no accession number.

Existing rows are filled from the column defaults: every UBS row becomes
('earnings', 'edgar'), which is what it is. No data is rewritten and no
rebuild of the pipeline is needed.

The downgrade fails by design if two calls of the same quarter exist, since
the old constraint cannot hold them. Delete the event call first.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-12
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
alter table filings
    add column call_type text not null default 'earnings'
        check (call_type in ('earnings', 'event')),
    add column source text not null default 'edgar'
        check (source in ('edgar', 'ir'));

alter table filings
    drop constraint filings_firm_id_quarter_doc_type_key,
    add constraint filings_firm_id_quarter_doc_type_call_type_key
        unique (firm_id, quarter, doc_type, call_type);

alter table calls
    add column call_type text not null default 'earnings'
        check (call_type in ('earnings', 'event'));

alter table calls
    drop constraint calls_firm_id_quarter_key,
    add constraint calls_firm_id_quarter_call_type_key
        unique (firm_id, quarter, call_type);
"""

DOWNGRADE_SQL = """
alter table calls
    drop constraint calls_firm_id_quarter_call_type_key,
    add constraint calls_firm_id_quarter_key unique (firm_id, quarter);

alter table calls
    drop column call_type;

alter table filings
    drop constraint filings_firm_id_quarter_doc_type_call_type_key,
    add constraint filings_firm_id_quarter_doc_type_key
        unique (firm_id, quarter, doc_type);

alter table filings
    drop column source,
    drop column call_type;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
