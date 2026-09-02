"""Stage 2: parse the transcript HTML into calls and utterances.

Usage:
    python src/parse_transcript.py            # dry run: print what would be written
    python src/parse_transcript.py --write    # write calls + utterances to the DB

Smoke test scope: UBS 2Q26. Speaker names and call date are pinned below;
discovery from the cover page comes later when we generalize to more quarters.
"""
import argparse
import os
import re
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

TICKER = "UBS"
QUARTER = "2Q26"
CALL_DATE = date(2026, 7, 29)
RAW_FILE = Path("data/raw/UBS_2Q26_transcript.htm")

# Management speakers as they appear in the document (leaf div == name).
MANAGEMENT = {
    "Sergio P. Ermotti": "management",
    "Todd Tuckner": "management",
    "Sarah Mackey": "ir",  # IR host, adjust if the name differs
}

QA_HEADER = "Analyst Q&A (CEO and CFO)"

# An analyst speaker line looks like "First Last, Institution" and is short.
ANALYST_RE = re.compile(
    r"^[A-Z][\w.\u2019'-]*(?: [A-Z][\w.\u2019'-]*){1,3}, [A-Za-z&.\u2019' -]{2,40}$"
)

# Leaves to drop entirely.
PAGE_NUMBER_RE = re.compile(r"^\d{1,3}$")
SLIDE_HEADER_RE = re.compile(r"^Slide \d+", re.IGNORECASE)


def get_engine():
    url = (
        f"postgresql+psycopg://{os.environ['POSTGRES_USER']}:"
        f"{os.environ['POSTGRES_PASSWORD']}@{os.environ['POSTGRES_HOST']}:"
        f"{os.environ['POSTGRES_PORT']}/{os.environ['POSTGRES_DB']}"
    )
    return create_engine(url)


def leaf_divs(html: str) -> list[str]:
    """Group text nodes by their nearest div ancestor.

    SEC filing HTML mixes text and nested divs inside one div; a strict
    leaf-div filter drops exactly those mixed containers (and with them
    the speaker names). Grouping by nearest ancestor keeps every text
    node and reassembles split names like 'Sergio P. Ermotti'.
    """
    soup = BeautifulSoup(html, "lxml")
    order: list[int] = []
    groups: dict[int, list[str]] = {}
    for s in soup.find_all(string=True):
        stripped = s.strip()
        if not stripped:
            continue
        div = s.find_parent("div")
        if div is None:
            continue
        key = id(div)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(stripped)

    blocks = [" ".join(" ".join(groups[k]).split()) for k in order]

    # Glue tiny ordinal fragments ('st', 'nd', ...) back onto the block before.
    merged: list[str] = []
    for b in blocks:
        if merged and re.fullmatch(r"(st|nd|rd|th|-)", b):
            merged[-1] = f"{merged[-1]}{b}"
        else:
            merged.append(b)
    return merged


def classify(leaf: str):
    """Return ('speaker', name, org, role) or ('qa_header',) or ('skip',) or ('text',)."""
    if leaf == QA_HEADER:
        return ("qa_header",)
    if PAGE_NUMBER_RE.match(leaf) or SLIDE_HEADER_RE.match(leaf):
        return ("skip",)
    if leaf in MANAGEMENT:
        return ("speaker", leaf, None, MANAGEMENT[leaf])
    if leaf.lower() == "operator":
        return ("speaker", "Operator", None, "operator")
    if len(leaf) <= 60 and ANALYST_RE.match(leaf):
        name, org = leaf.split(",", 1)
        return ("speaker", name.strip(), org.strip(), "analyst")
    return ("text",)


def parse(leaves: list[str]) -> list[dict]:
    """Build the utterance list from the leaf sequence."""
    utterances = []
    section = "prepared"
    current = None
    started = False  # becomes True at the first recognized speaker

    def flush():
        nonlocal current
        if current and current["parts"]:
            utterances.append(
                {
                    "seq": len(utterances) + 1,
                    "speaker_name": current["name"],
                    "speaker_org": current["org"],
                    "speaker_role": current["role"],
                    "section": current["section"],
                    "body": " ".join(current["parts"]),
                }
            )
        current = None

    for leaf in leaves:
        kind = classify(leaf)
        if kind[0] == "qa_header":
            flush()
            section = "qa"
        elif kind[0] == "speaker":
            flush()
            started = True
            _, name, org, role = kind
            current = {"name": name, "org": org, "role": role,
                       "section": section, "parts": []}
        elif kind[0] == "text" and started and current is not None:
            current["parts"].append(leaf)
        # 'skip' and pre-speaker cover text fall through silently

    flush()
    return utterances


def dry_run_report(utterances: list[dict]) -> None:
    print(f"{len(utterances)} utterances\n")
    print("--- speaker overview ---")
    seen = {}
    for u in utterances:
        key = (u["speaker_name"], u["speaker_org"], u["speaker_role"], u["section"])
        seen[key] = seen.get(key, 0) + 1
    for (name, org, role, section), n in seen.items():
        org_part = f" [{org}]" if org else ""
        print(f"{section:8s} {role:10s} {name}{org_part}  ({n} utterances)")
    print()
    print("--- first 12 utterances ---")
    for u in utterances[:12]:
        print(f"{u['seq']:3d} {u['section']:8s} {u['speaker_role']:10s} "
              f"{u['speaker_name']:25s} {u['body'][:70]}")


def write(utterances: list[dict]) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                select f.id as firm_id, fi.id as filing_id
                from firms f
                join filings fi on fi.firm_id = f.id
                where f.ticker = :ticker and fi.quarter = :quarter
                  and fi.doc_type = 'transcript'
                """
            ),
            {"ticker": TICKER, "quarter": QUARTER},
        ).one()

        call_id = conn.execute(
            text(
                """
                insert into calls (firm_id, filing_id, quarter, call_date)
                values (:firm_id, :filing_id, :quarter, :call_date)
                on conflict (firm_id, quarter)
                    do update set call_date = excluded.call_date
                returning id
                """
            ),
            {
                "firm_id": row.firm_id,
                "filing_id": row.filing_id,
                "quarter": QUARTER,
                "call_date": CALL_DATE,
            },
        ).scalar_one()

        # Re-runs replace the utterances of this call (sentences cascade).
        conn.execute(text("delete from utterances where call_id = :c"), {"c": call_id})

        for u in utterances:
            conn.execute(
                text(
                    """
                    insert into utterances
                        (call_id, seq, speaker_name, speaker_org, speaker_role,
                         section, body)
                    values
                        (:call_id, :seq, :speaker_name, :speaker_org, :speaker_role,
                         :section, :body)
                    """
                ),
                {**u, "call_id": call_id},
            )
    print(f"wrote call {call_id} with {len(utterances)} utterances")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true",
                    help="write to the database (default: dry run)")
    args = ap.parse_args()

    html = RAW_FILE.read_text(encoding="utf-8", errors="replace")
    utterances = parse(leaf_divs(html))
    dry_run_report(utterances)

    if args.write:
        write(utterances)
    else:
        print("\ndry run only -- rerun with --write to store")


if __name__ == "__main__":
    main()