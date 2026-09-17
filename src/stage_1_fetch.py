""" Fetch the configured filings and record them in `filings`.

Two sources. EDGAR documents are fetched with the SEC user agent and a pause
between requests to stay inside the fair-access limit. Documents that a
company only publishes on its investor relations site (JPMorgan's
transcripts) are fetched without that pause, the limit does not apply there.

Idempotent: raw files already in data/raw are reused unless --refetch is
given, and existing `filings` rows are never overwritten. A checksum that
differs from the stored one is reported, not silently fixed.

Usage:
    python src/stage_1_fetch.py --bank UBS # transcripts, all quarters
    python src/stage_1_fetch.py --bank JPM --doc-type report # the key figures documents
    python src/stage_1_fetch.py --bank JPM --quarter 2Q23 # both 2Q23 calls
    python src/stage_1_fetch.py # every bank and transcript
"""
import argparse
import hashlib
import os
import time

import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from config import FIRMS, Filing, select_filings

load_dotenv()

EDGAR_PAUSE_S = 0.5  # stay well below the SEC fair-access limit
IR_PAUSE_S = 0.0


def get_engine():
    url = (
        f"postgresql+psycopg://{os.environ['POSTGRES_USER']}:"
        f"{os.environ['POSTGRES_PASSWORD']}@{os.environ['POSTGRES_HOST']}:"
        f"{os.environ['POSTGRES_PORT']}/{os.environ['POSTGRES_DB']}"
    )
    return create_engine(url)


def fetch(filing: Filing) -> bytes:
    """Download one document, with the headers its source expects."""
    if filing.source == "edgar":
        headers = {"User-Agent": os.environ["EDGAR_USER_AGENT"]}
    else:
        # A contactable user agent is good manners on a company site too, but
        # nothing here is enforced the way the SEC enforces it.
        headers = {"User-Agent": os.environ.get("IR_USER_AGENT",
                                                os.environ["EDGAR_USER_AGENT"])}
    resp = requests.get(filing.source_url, headers=headers, timeout=60)
    resp.raise_for_status()
    return resp.content


def load_raw(filing: Filing, refetch: bool) -> tuple[bytes, str]:
    """Return the document bytes and where they came from."""
    if filing.raw_path.exists() and not refetch:
        return filing.raw_path.read_bytes(), "disk"
    raw = fetch(filing)
    filing.raw_path.parent.mkdir(parents=True, exist_ok=True)
    filing.raw_path.write_bytes(raw)
    time.sleep(EDGAR_PAUSE_S if filing.source == "edgar" else IR_PAUSE_S)
    return raw, filing.source


def upsert_firm(conn, firm: dict) -> int:
    """
    Upserts a firm record into the `firms` table. If a record with the same ticker
    already exists, it updates the existing record with the provided data. If no such
    record exists, it inserts a new one. The method returns the ID of the newly updated
    or inserted record.
    """
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
        firm,
    ).scalar_one()


def record_filing(conn, firm_id: int, filing: Filing, checksum: str) -> str:
    """Insert the filing row if missing and return a short status."""
    key = {
        "firm_id": firm_id,
        "quarter": filing.quarter,
        "doc_type": filing.doc_type,
        "call_type": filing.call_type,
    }
    stored = conn.execute(
        text(
            """
            select checksum from filings
            where firm_id = :firm_id and quarter = :quarter
              and doc_type = :doc_type and call_type = :call_type
            """
        ),
        key,
    ).scalar_one_or_none()

    if stored is None:
        conn.execute(
            text(
                """
                insert into filings
                    (firm_id, quarter, doc_type, call_type, source,
                     source_url, accession_number, checksum)
                values
                    (:firm_id, :quarter, :doc_type, :call_type, :source,
                     :source_url, :accession_number, :checksum)
                """
            ),
            {
                **key,
                "source": filing.source,
                "source_url": filing.source_url,
                "accession_number": filing.accession_number,
                "checksum": checksum,
            },
        )
        return "inserted"
    if stored == checksum:
        return "unchanged"
    return "CHECKSUM MISMATCH, row not updated"


def label_hits(filing: Filing, raw: bytes) -> str:
    """How often the quarter label appears in the raw bytes.

    A rough sanity check that the right document was fetched. Only meaningful
    for HTML: in a PDF the text sits inside compressed streams, so counting
    the bytes would always return zero.
    """
    if filing.media != "html":
        return "  -"
    return f"{raw.count(filing.label.encode()):>3}"


def main() -> None:
    """
    Main entry point for processing financial filings.

    This function parses command-line arguments to filter and fetch financial filings
    based on various criteria such as bank, quarter, document type, call type,
    and the need to refetch existing files. It processes the selected filings,
    updates the associated database records, and outputs a summary for each filing.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bank", help=f"limit to one bank: {', '.join(FIRMS)}")
    parser.add_argument("--quarter", help="limit to one quarter, e.g. 2023-Q1 or 1Q23")
    parser.add_argument("--doc-type", default="transcript",
                        help="transcript (default) or report")
    parser.add_argument("--call-type",
                        help="earnings or event; default: both")
    parser.add_argument(
        "--refetch", action="store_true", help="download again even if the raw file exists"
    )
    args = parser.parse_args()

    filings = select_filings(args.quarter, args.doc_type, args.bank, args.call_type)

    engine = get_engine()
    firm_ids: dict[str, int] = {}
    with engine.begin() as conn:
        for ticker in sorted({f.firm for f in filings}):
            firm_ids[ticker] = upsert_firm(conn, FIRMS[ticker])

    for filing in filings:
        raw, origin = load_raw(filing, args.refetch)
        checksum = hashlib.sha256(raw).hexdigest()
        with engine.begin() as conn:
            status = record_filing(conn, firm_ids[filing.firm], filing, checksum)
        print(
            f"{filing.firm:<4} {filing.quarter} ({filing.label})  {filing.call_type:<8} "
            f"{origin:<5}  {len(raw):>9,} bytes  sha256 {checksum[:12]}  "
            f"label hits {label_hits(filing, raw)}  {status}"
        )


if __name__ == "__main__":
    main()