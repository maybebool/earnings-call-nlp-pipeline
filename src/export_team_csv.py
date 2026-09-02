"""Stage 8 (smoke test): export utterances as a dated CSV for the team.

Produces one row per utterance with plain-text speaker fields -- the shape
the team will work with in Sheets/Colab. Topics and sentiment columns join
in later once stage 6 exists.

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


QUERY = text(
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


def main() -> None:
    EXPORT_DIR.mkdir(exist_ok=True)
    out_path = EXPORT_DIR / f"utterances_{date.today().isoformat()}.csv"

    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(QUERY)
        rows = result.mappings().all()

    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"wrote {out_path} ({len(rows)} rows)")


if __name__ == "__main__":
    main()