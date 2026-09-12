"""Helper: list UBS Group AG 6-K filings on EDGAR so we can pick the reports.

The quarterly and annual reports are filed as plain HTML, one 6-K each, but
the document names are not predictable. This script asks the EDGAR
submissions API for every 6-K of the configured CIK in a date window and
prints the candidates, so the URLs can be pasted into filings_config.

Usage:
    python src/find_reports.py                       # 2023-01-01 to 2025-03-31
    python src/find_reports.py --from 2023-01-01 --to 2025-03-31
    python src/find_reports.py --all                 # do not filter by name
    python src/find_reports.py --describe            # label each candidate
"""
import argparse
import html
import os
import re
import time

import requests
from dotenv import load_dotenv

from filings_config import FIRM

load_dotenv()

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:0>10}.json"
SHARD_URL = "https://data.sec.gov/submissions/{name}"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
# Report documents are named like 'EDGARq23ubsgroupag.htm' or 'edgar1q25ubsgroup.htm'.
REPORT_HINTS = ("ubsgroup", "ubsgroupag", "annualreport", "ar23", "ar24")
REQUEST_PAUSE_S = 0.2
# Every one of these documents opens with a cover page stating what it is.
CONSISTS_RE = re.compile(r"consists of (.{0,180})", re.IGNORECASE)
DESCRIBE_BYTES = 1_500_000  # the cover page is at the top; never pull whole reports


def get(url: str) -> dict:
    headers = {"User-Agent": os.environ["EDGAR_USER_AGENT"]}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    time.sleep(REQUEST_PAUSE_S)
    return resp.json()


def describe(url: str) -> str:
    """Read the start of the document and return its self-description."""
    headers = {"User-Agent": os.environ["EDGAR_USER_AGENT"]}
    chunks, size = [], 0
    with requests.get(url, headers=headers, timeout=60, stream=True) as resp:
        resp.raise_for_status()
        for chunk in resp.iter_content(65536):
            chunks.append(chunk)
            size += len(chunk)
            if size >= DESCRIBE_BYTES:
                break
    time.sleep(REQUEST_PAUSE_S)
    text = html.unescape(re.sub(r"<[^>]+>", " ", b"".join(chunks).decode("utf-8", "replace")))
    match = CONSISTS_RE.search(" ".join(text.split()))
    if match is None:
        return "?"
    return match.group(1).split(", which")[0].strip()


def rows_from(block: dict) -> list[dict]:
    """Turn the parallel-arrays format of the API into a list of dicts."""
    keys = ("accessionNumber", "filingDate", "reportDate", "form",
            "primaryDocument", "primaryDocDescription")
    present = [k for k in keys if k in block]
    return [dict(zip(present, values)) for values in zip(*(block[k] for k in present))]


def all_filings(cik: str) -> list[dict]:
    data = get(SUBMISSIONS_URL.format(cik=cik))
    rows = rows_from(data["filings"]["recent"])
    for shard in data["filings"].get("files", []):
        rows += rows_from(get(SHARD_URL.format(name=shard["name"])))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", default="2023-01-01")
    ap.add_argument("--to", dest="date_to", default="2025-03-31")
    ap.add_argument("--form", default="6-K")
    ap.add_argument("--all", action="store_true",
                    help="list every filing in the window, not just report-like names")
    ap.add_argument("--describe", action="store_true",
                    help="fetch each document's cover page and print what it says it is")
    args = ap.parse_args()

    cik = FIRM["cik"]
    rows = [
        r for r in all_filings(cik)
        if r.get("form") == args.form
        and args.date_from <= r.get("filingDate", "") <= args.date_to
    ]
    if not args.all:
        rows = [r for r in rows
                if any(h in r.get("primaryDocument", "").lower() for h in REPORT_HINTS)]

    rows.sort(key=lambda r: r["filingDate"])
    print(f"{len(rows)} filings ({args.form}, {args.date_from} to {args.date_to}, "
          f"CIK {cik}{'' if args.all else ', report-like names only'})\n")
    for r in rows:
        accession = r["accessionNumber"].replace("-", "")
        url = ARCHIVE_URL.format(cik=cik, accession=accession, document=r["primaryDocument"])
        label = f"  <- {describe(url)}" if args.describe else ""
        print(f"{r['filingDate']}  period {r.get('reportDate', '?'):10s}  "
              f"{r['accessionNumber']}{label}\n    {url}")


if __name__ == "__main__":
    main()