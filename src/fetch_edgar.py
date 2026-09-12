"""Stage 1: fetch the configured filings from EDGAR and record them in `filings`.

Idempotent: raw files already in data/raw are reused unless --refetch is
given, and existing `filings` rows are never overwritten. A checksum that
differs from the stored one is reported, not silently fixed.
"""
import argparse
import hashlib
import os
import time

import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from filings_config import FIRM, Filing, select_filings

load_dotenv()

REQUEST_PAUSE_S = 0.5  # stay well below the SEC fair-access limit


def get_engine():
    url = (
        f"postgresql+psycopg://{os.environ['POSTGRES_USER']}:"
        f"{os.environ['POSTGRES_PASSWORD']}@{os.environ['POSTGRES_HOST']}:"
        f"{os.environ['POSTGRES_PORT']}/{os.environ['POSTGRES_DB']}"
    )
    return create_engine(url)


def fetch(url: str) -> bytes:
    headers = {"User-Agent": os.environ["EDGAR_USER_AGENT"]}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.content


def load_raw(filing: Filing, refetch: bool) -> tuple[bytes, str]:
    """Return the document bytes and where they came from."""
    if filing.raw_path.exists() and not refetch:
        return filing.raw_path.read_bytes(), "disk"
    raw = fetch(filing.source_url)
    filing.raw_path.parent.mkdir(parents=True, exist_ok=True)
    filing.raw_path.write_bytes(raw)
    time.sleep(REQUEST_PAUSE_S)
    return raw, "edgar"


def upsert_firm(conn) -> int:
    return conn.execute(
        text(
            """
            insert into firms (ticker, name, cik, peer_group)
            values (:ticker, :name, :cik, :peer_group)
            on conflict (ticker) do update
                set name = excluded.name,
                    cik = excluded.cik,
                    peer_group = excluded.peer_group
            returning id
            """
        ),
        FIRM,
    ).scalar_one()


def record_filing(conn, firm_id: int, filing: Filing, checksum: str) -> str:
    """Insert the filing row if missing and return a short status."""
    stored = conn.execute(
        text(
            """
            select checksum from filings
            where firm_id = :firm_id and quarter = :quarter and doc_type = :doc_type
            """
        ),
        {"firm_id": firm_id, "quarter": filing.quarter, "doc_type": filing.doc_type},
    ).scalar_one_or_none()

    if stored is None:
        conn.execute(
            text(
                """
                insert into filings
                    (firm_id, quarter, doc_type, source_url, accession_number, checksum)
                values
                    (:firm_id, :quarter, :doc_type, :source_url, :accession_number, :checksum)
                """
            ),
            {
                "firm_id": firm_id,
                "quarter": filing.quarter,
                "doc_type": filing.doc_type,
                "source_url": filing.source_url,
                "accession_number": filing.accession_number,
                "checksum": checksum,
            },
        )
        return "inserted"
    if stored == checksum:
        return "unchanged"
    return "CHECKSUM MISMATCH, row not updated"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quarter", help="limit to one quarter, e.g. 2023-Q1 or 1Q23")
    parser.add_argument("--doc-type", default="transcript",
                        help="transcript (default) or report")
    parser.add_argument(
        "--refetch", action="store_true", help="download again even if the raw file exists"
    )
    args = parser.parse_args()

    engine = get_engine()
    with engine.begin() as conn:
        firm_id = upsert_firm(conn)

    for filing in select_filings(args.quarter, args.doc_type):
        raw, origin = load_raw(filing, args.refetch)
        checksum = hashlib.sha256(raw).hexdigest()
        with engine.begin() as conn:
            status = record_filing(conn, firm_id, filing, checksum)
        label_hits = raw.count(filing.label.encode())
        print(
            f"{filing.quarter} ({filing.label})  {origin:<5}  {len(raw):>9,} bytes  "
            f"sha256 {checksum[:12]}  label hits {label_hits:>3}  {status}"
        )


if __name__ == "__main__":
    main()