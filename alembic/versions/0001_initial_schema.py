"""Initial schema: 11 tables for the earnings call NLP pipeline

Revision ID: 0001
Revises:
Create Date: 2026-09-01
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA_SQL = """
-- Earnings call NLP project -- PostgreSQL schema
-- Layers: source -> raw text -> facts -> model results

-- ---------------------------------------------------------------
-- Reference and provenance
-- ---------------------------------------------------------------

create table firms (
    id          serial primary key,
    ticker      text not null unique,
    name        text not null,
    cik         text,                       -- SEC identifier, null for non-filers
    peer_group  text                        -- e.g. 'EU universal bank'
);

create table filings (
    id                serial primary key,
    firm_id           int  not null references firms(id),
    quarter           text not null,        -- '2Q26'
    doc_type          text not null,        -- 'transcript' | 'media_release'
    source_url        text not null,
    accession_number  text,                 -- EDGAR accession, null for IR downloads
    retrieved_at      timestamptz not null default now(),
    checksum          text not null,        -- sha256 of the fetched bytes
    unique (firm_id, quarter, doc_type)
);

-- ---------------------------------------------------------------
-- Raw text layer
-- ---------------------------------------------------------------

create table calls (
    id         serial primary key,
    firm_id    int  not null references firms(id),
    filing_id  int  not null references filings(id),
    quarter    text not null,
    call_date  date not null,
    unique (firm_id, quarter)
);

create table utterances (
    id            serial primary key,
    call_id       int  not null references calls(id) on delete cascade,
    seq           int  not null,            -- order within the call
    speaker_name  text,
    speaker_org   text,                     -- analyst's employer, null for management
    speaker_role  text not null
        check (speaker_role in ('analyst', 'management', 'ir', 'operator')),
    section       text not null
        check (section in ('prepared', 'qa')),
    body          text not null,
    unique (call_id, seq)
);

create table sentences (
    id            serial primary key,
    utterance_id  int  not null references utterances(id) on delete cascade,
    seq           int  not null,            -- order within the utterance
    body          text not null,
    unique (utterance_id, seq)
);

-- ---------------------------------------------------------------
-- Reported figures (from the media release, not the transcript)
-- ---------------------------------------------------------------

create table metrics (
    id           serial primary key,
    firm_id      int  not null references firms(id),
    filing_id    int  not null references filings(id),
    quarter      text not null,
    metric_name  text not null,             -- 'pbt', 'cost_income_ratio', 'cet1_ratio'
    value        numeric not null,
    unit         text not null,             -- 'USD m', 'CHF m', 'percent'
    basis        text not null default 'reported'
        check (basis in ('reported', 'underlying')),
    unique (firm_id, quarter, metric_name, basis)
);

-- ---------------------------------------------------------------
-- Model results -- never overwritten, always a new run
-- ---------------------------------------------------------------

create table model_runs (
    id             serial primary key,
    task           text not null
        check (task in ('sentiment', 'embedding', 'topic', 'summary')),
    model_name     text not null,           -- 'ProsusAI/finbert'
    model_version  text,
    params         jsonb not null default '{}'::jsonb,
    created_at     timestamptz not null default now()
);

create table sentiments (
    id           serial primary key,
    sentence_id  int  not null references sentences(id) on delete cascade,
    run_id       int  not null references model_runs(id) on delete cascade,
    label        text not null,             -- 'positive' | 'negative' | 'neutral'
    score        numeric not null,          -- signed, -1 .. 1
    raw          jsonb,                     -- full model output, schema-free
    unique (sentence_id, run_id)
);

create table topics (
    id           serial primary key,
    run_id       int  not null references model_runs(id) on delete cascade,
    topic_index  int  not null,             -- BERTopic's own id, -1 = outlier
    label        text,                      -- human-readable name
    keywords     text[],
    unique (run_id, topic_index)
);

create table topic_assignments (
    id           serial primary key,
    sentence_id  int  not null references sentences(id) on delete cascade,
    run_id       int  not null references model_runs(id) on delete cascade,
    topic_id     int  not null references topics(id) on delete cascade,
    probability  numeric,
    unique (sentence_id, run_id)
);

create table summaries (
    id           serial primary key,
    run_id       int  not null references model_runs(id) on delete cascade,
    firm_id      int  not null references firms(id),
    quarter      text,                      -- null = across all quarters
    grouping     text not null
        check (grouping in ('topic', 'metric', 'speaker_role')),
    group_key    text not null,             -- topic label, metric name or role
    body         text not null,
    source_ids   int[] not null             -- sentence ids the summary rests on
);

-- ---------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------

create index on calls (firm_id, quarter);
create index on utterances (call_id, seq);
create index on utterances (speaker_role, section);
create index on sentences (utterance_id);
create index on metrics (firm_id, quarter, metric_name);
create index on sentiments (run_id);
create index on topic_assignments (run_id, topic_id);
"""

DROP_SQL = """
drop table if exists summaries;
drop table if exists topic_assignments;
drop table if exists topics;
drop table if exists sentiments;
drop table if exists model_runs;
drop table if exists metrics;
drop table if exists sentences;
drop table if exists utterances;
drop table if exists calls;
drop table if exists filings;
drop table if exists firms;
"""


def upgrade() -> None:
    op.execute(SCHEMA_SQL)


def downgrade() -> None:
    op.execute(DROP_SQL)
