"""Stage 1: fetch one filing from EDGAR and record it in `filings`.

Smoke test scope: a single known document (UBS 2Q26 transcript).
Later this grows into discovery via the EDGAR submissions API.
"""
import hashlib
import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

FIRM = {
    "ticker": "UBS",
    "name": "UBS Group AG",
    "cik": "1114446",
    "peer_group": "EU universal bank",
}

DOC = {
    "quarter": "2Q26",
    "doc_type": "transcript",
    "source_url": "https://www.sec.gov/Archives/edgar/data/1114446/000161052026000089/investorpresotext2026.htm",
    "accession_number": "0001610520-26-000089",
}

DATA_DIR = Path("data/raw")


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


def main() -> None:
    raw = fetch(DOC["source_url"])
    checksum = hashlib.sha256(raw).hexdigest()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / f"{FIRM['ticker']}_{DOC['quarter']}_{DOC['doc_type']}.htm"
    out_path.write_bytes(raw)

    engine = get_engine()
    with engine.begin() as conn:
        firm_id = conn.execute(
            text(
                """
                insert into firms (ticker, name, cik, peer_group)
                values (:ticker, :name, :cik, :peer_group)
                on conflict (ticker) do update set name = excluded.name
                returning id
                """
            ),
            FIRM,
        ).scalar_one()

        conn.execute(
            text(
                """
                insert into filings
                    (firm_id, quarter, doc_type, source_url, accession_number, checksum)
                values
                    (:firm_id, :quarter, :doc_type, :source_url, :accession_number, :checksum)
                on conflict (firm_id, quarter, doc_type) do nothing
                """
            ),
            {**DOC, "firm_id": firm_id, "checksum": checksum},
        )

    print(f"saved {out_path} ({len(raw):,} bytes, sha256 {checksum[:12]}...)")


if __name__ == "__main__":
    main()