"""Stage 8: export utterance-level and sentence-level CSVs for the team.

Produces two dated files in exports/:
  utterances_<date>.csv  -- one row per statement (readable overview)
  sentences_<date>.csv   -- one row per sentence (analysis-ready unit)

Both carry plain-text speaker columns; sentence_id stays in the sentence
export as the trace-back reference into the database.

Usage:
    python src/export_team_csv.py
"""
import csv
import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

EXPORT_DIR = Path("exports")


def get_engine():
    url = (
        f"postgresql+psycopg://{os.environ['POSTGRES_USER']}:"
        f"{os.environ['POSTGRES_PASSWORD']}@{os.environ['POSTGRES_HOST']}:"
        f"{os.environ['POSTGRES_PORT']}/{os.environ['POSTGRES_DB']}"
    )
    return create_engine(url)


UTTERANCE_QUERY = text(
    """
    select
        f.ticker            as bank,
        c.quarter           as quarter,
        c.call_date         as call_date,
        u.seq               as position_in_call,
        u.section           as section,
        u.speaker_role      as speaker_role,
        u.speaker_name      as speaker_name,
        coalesce(u.speaker_org, '') as speaker_institution,
        length(u.body)      as text_length,
        u.body              as text
    from utterances u
    join calls c on c.id = u.call_id
    join firms f on f.id = c.firm_id
    order by f.ticker, c.quarter, u.seq
    """
)

SENTENCE_QUERY = text(
    """
    select
        f.ticker            as bank,
        c.quarter           as quarter,
        u.seq               as position_in_call,
        u.section           as section,
        u.speaker_role      as speaker_role,
        u.speaker_name      as speaker_name,
        coalesce(u.speaker_org, '') as speaker_institution,
        s.id                as sentence_id,
        s.seq               as sentence_number,
        s.body              as sentence
    from sentences s
    join utterances u on u.id = s.utterance_id
    join calls c on c.id = u.call_id
    join firms f on f.id = c.firm_id
    order by f.ticker, c.quarter, u.seq, s.seq
    """
)


def export(query, filename: str) -> None:
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()

    out_path = EXPORT_DIR / filename
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {out_path} ({len(rows)} rows)")


def main() -> None:
    EXPORT_DIR.mkdir(exist_ok=True)
    today = date.today().isoformat()
    export(UTTERANCE_QUERY, f"utterances_{today}.csv")
    export(SENTENCE_QUERY, f"sentences_{today}.csv")


if __name__ == "__main__":
    main()