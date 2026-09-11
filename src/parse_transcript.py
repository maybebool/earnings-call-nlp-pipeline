"""Stage 2: parse the transcript HTML into calls and utterances.

Usage:
    python src/parse_transcript.py                         # summary dry run, all quarters
    python src/parse_transcript.py --quarter 1Q23          # detailed dry run, one quarter
    python src/parse_transcript.py --quarter 1Q23 --write  # write one quarter
    python src/parse_transcript.py --write                 # write all quarters

Quarters, raw files and call dates come from filings_config. A quarter is
only written if its Q&A header was found and it produced utterances.
"""
import argparse
import os
import re
from collections import Counter

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from filings_config import FIRM, Filing, select_filings

load_dotenv()

# Canonical name -> (role, spellings seen in the documents). Matching ignores
# spaces, dots and case, so 'Sergio P.Ermotti' and 'ToddTuckner' match too.
MANAGEMENT = {
    "Sergio P. Ermotti": ("management", ["Sergio P. Ermotti", "Sergio Ermotti",
                                         "Serio P. Ermotti"]),
    "Todd Tuckner": ("management", ["Todd Tuckner"]),
    "Sarah Youngwood": ("management", ["Sarah Youngwood"]),
    "Sarah Mackey": ("ir", ["Sarah Mackey"]),
}

# Typos and spelling variants in analyst lines of the source documents, so
# one analyst and one house carry the same spelling across all quarters.
# Real moves between houses (e.g. Amit Goel, Barclays -> Mediobanca) stay.
ANALYST_NAME_FIXES = {
    "Chis Hallam": "Chris Hallam",
    "Tom Hallet": "Tom Hallett",
    "Nicholas Payen": "Nicolas Payen",
    "Giulia Aurora Miotto": "Giulia Miotto",
}
ANALYST_ORG_FIXES = {
    "Citigroup": "Citi",
    "JP Morgan": "JPMorgan",
    "Bank of America Merrill Lynch": "Bank of America",
    "Autonomous": "Autonomous Research",
    # Exane is BNP Paribas' equity research arm; the documents use all three.
    "Exane": "BNP Paribas Exane",
    "Exane BNP Paribas": "BNP Paribas Exane",
    "BNP Paribas": "BNP Paribas Exane",
    "Société Génerale": "Société Générale",
    "Kepler Chevreux": "Kepler Cheuvreux",
}

# The cover page ends with the note pointing to ubs.com/investors. Layouts
# without that note fall back to the first slide header.
COVER_END_RE = re.compile(r"ubs\.com/investors", re.IGNORECASE)
SLIDE_HEADER_RE = re.compile(r"^Slide \d+\s*[\u2013\u2014:-]")
# The real header is 'Analyst Q&A (CEO and CFO)', possibly split after '(CEO'.
# The cover sentence 'Analyst Q&A, which appears ...' must not match.
QA_HEADER_RE = re.compile(r"^Analyst Q&A\s*(\(|$)")
END_RE = re.compile(r"^(Cautionary statement|SIGNATURES$)", re.IGNORECASE)

# Suffix after a speaker name, e.g. 'Sergio P. Ermotti – closing remarks'.
SPEAKER_SUFFIX_RE = re.compile(r"\s+[\u2013\u2014-]\s+")
# Analyst line: 'Name, Institution'; a semicolon occurs as separator too.
ANALYST_LINE_RE = re.compile(r"^(?P<name>[^,;]{3,40}?)\s*[,;]\s*(?P<org>[^,;]{2,40})$")
_UPPER = "A-Z\u00c0-\u00dd"
NAME_RE = re.compile(rf"^[{_UPPER}][\w.\u2019'-]*(?: [{_UPPER}][\w.\u2019'-]*){{1,3}}$")
ORG_RE = re.compile(rf"^[{_UPPER}][\w&.\u2019' -]*[\w)]$")
# Short capitalized lines that look like a speaker but were not recognized.
SUSPECT_RE = re.compile(rf"^[{_UPPER}][\w.\u2019'-]*(?:[ ,;]+[{_UPPER}&][\w.\u2019'&-]*){{1,5}}$")
BRACKET_RE = re.compile(r"\[[^\]]{1,60}\]")

SPEAKER_MAX_LEN = 60
PAGE_STEP_MAX = 3  # a page number may skip up to two unnumbered pages


def _key(s: str) -> str:
    return re.sub(r"[^a-z]", "", s.lower())


MANAGEMENT_BY_KEY = {
    _key(alias): (name, role)
    for name, (role, aliases) in MANAGEMENT.items()
    for alias in aliases
}
NAME_FIXES_BY_KEY = {_key(k): v for k, v in ANALYST_NAME_FIXES.items()}
ORG_FIXES_BY_KEY = {_key(k): v for k, v in ANALYST_ORG_FIXES.items()}


def get_engine():
    url = (
        f"postgresql+psycopg://{os.environ['POSTGRES_USER']}:"
        f"{os.environ['POSTGRES_PASSWORD']}@{os.environ['POSTGRES_HOST']}:"
        f"{os.environ['POSTGRES_PORT']}/{os.environ['POSTGRES_DB']}"
    )
    return create_engine(url)


# Two ways to cut the HTML into text blocks. Some years put each paragraph in
# its own <p> inside a page-sized <div>; others split one line (even one name)
# over several <p> inside a line-sized <div>. Both are tried per document.
GROUPINGS = {
    "div": ["div"],
    "paragraph": ["p", "div", "td", "li", "h1", "h2", "h3", "h4", "h5", "h6"],
}


def text_blocks(soup: BeautifulSoup, tags: list[str]) -> list[str]:
    """Group text nodes by their nearest ancestor among `tags`.

    Grouping by nearest ancestor (instead of strict leaf elements) keeps
    text that sits next to nested elements and reassembles names that are
    split over several text nodes, like 'Sergio P.' + 'Ermotti'.
    """
    order: list[int] = []
    groups: dict[int, list[str]] = {}
    for s in soup.find_all(string=True):
        stripped = s.strip()
        if not stripped:
            continue
        parent = s.find_parent(tags)
        if parent is None:
            continue
        key = id(parent)
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


def best_parse(html: bytes) -> tuple[list[dict], dict]:
    """Parse with every grouping and keep the one with the most utterances.

    Bytes are passed in so BeautifulSoup detects the encoding itself.
    On a tie the div grouping wins, which is what the 2Q26 smoke test used.
    """
    soup = BeautifulSoup(html, "lxml")
    results = {}
    for name, tags in GROUPINGS.items():
        blocks = text_blocks(soup, tags)
        utterances, meta = parse(blocks)
        meta["grouping"] = name
        meta["blocks"] = len(blocks)
        meta["longest_block"] = max((len(b) for b in blocks), default=0)
        results[name] = (utterances, meta)
    utterances, meta = max(results.values(), key=lambda r: len(r[0]))
    meta["utterances_per_grouping"] = {n: len(r[0]) for n, r in results.items()}
    return utterances, meta


def unglue(name: str) -> str:
    """'P.Ermotti' -> 'P. Ermotti'; a single token 'TomHallet' -> 'Tom Hallet'."""
    name = re.sub(r"\.(?=[A-Z])", ". ", name)
    if " " not in name:
        name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)
    return name


def match_management(block: str):
    head = SPEAKER_SUFFIX_RE.split(block, maxsplit=1)[0]
    return MANAGEMENT_BY_KEY.get(_key(head))


def match_analyst(block: str):
    m = ANALYST_LINE_RE.match(block)
    if m is None:
        return None
    name = unglue(m["name"].strip())
    org = m["org"].strip()
    if NAME_RE.match(name) is None or ORG_RE.match(org) is None:
        return None
    name = NAME_FIXES_BY_KEY.get(_key(name), name)
    org = ORG_FIXES_BY_KEY.get(_key(org), org)
    return name, org


def collect_analysts(blocks: list[str]) -> dict[str, tuple[str, str]]:
    """Name key -> (name, org) for every 'Name, Institution' line in the Q&A.

    Lets bare follow-up lines like 'Nicholas Payen' resolve to the analyst
    who was introduced with an institution earlier in the same call.
    """
    known: dict[str, tuple[str, str]] = {}
    in_qa = False
    for block in blocks:
        if END_RE.match(block):
            break
        if QA_HEADER_RE.match(block):
            in_qa = True
            continue
        if in_qa and len(block) <= SPEAKER_MAX_LEN:
            hit = match_analyst(block)
            if hit:
                known[_key(ANALYST_LINE_RE.match(block)["name"])] = hit
                known[_key(hit[0])] = hit
    return known


def classify(block: str, section: str, known: dict):
    """Return ('speaker', name, org, role), ('qa_header',), ('slide',) or ('text',)."""
    if QA_HEADER_RE.match(block):
        return ("qa_header",)
    if SLIDE_HEADER_RE.match(block):
        return ("slide",)
    if len(block) > SPEAKER_MAX_LEN:
        return ("text",)
    mgmt = match_management(block)
    if mgmt:
        return ("speaker", mgmt[0], None, mgmt[1])
    if block.lower() == "operator":
        return ("speaker", "Operator", None, "operator")
    if section == "qa":
        hit = match_analyst(block) or known.get(_key(block))
        if hit:
            return ("speaker", hit[0], hit[1], "analyst")
    return ("text",)


def parse(blocks: list[str]) -> tuple[list[dict], dict]:
    """Build the utterance list from the block sequence, plus diagnostics."""
    known = collect_analysts(blocks)
    meta = {
        "cover_end": None,
        "qa_header": False,
        "pages_skipped": 0,
        "numbers_kept": 0,
        "suspects": Counter(),
    }
    utterances: list[dict] = []
    section = "prepared"
    current = None
    last_page = 0

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

    for block in blocks:
        if END_RE.match(block):
            break

        if meta["cover_end"] is None:
            if COVER_END_RE.search(block):
                # Everything so far is title page; the first speaker follows.
                meta["cover_end"] = "ubs.com/investors note"
                utterances.clear()
                current = None
                section = "prepared"
                meta["qa_header"] = False
                meta["suspects"].clear()
                continue
            if SLIDE_HEADER_RE.match(block):
                # Fallback: the speaker line precedes the first slide header,
                # so keep the speaker and drop the cover text collected so far.
                meta["cover_end"] = "first slide header"
                utterances.clear()
                if current is not None:
                    current["parts"] = []
                section = "prepared"
                meta["qa_header"] = False
                meta["suspects"].clear()
                continue

        if block.isdigit() and len(block) <= 3:
            n = int(block)
            if last_page < n <= last_page + PAGE_STEP_MAX:
                last_page = n
                meta["pages_skipped"] += 1
                continue
            meta["numbers_kept"] += 1

        kind = classify(block, section, known)
        if kind[0] == "slide":
            continue
        if kind[0] == "qa_header":
            flush()
            section = "qa"
            meta["qa_header"] = True
        elif kind[0] == "speaker":
            flush()
            _, name, org, role = kind
            current = {"name": name, "org": org, "role": role,
                       "section": section, "parts": []}
        elif current is not None:
            current["parts"].append(block)
            if (len(block) <= SPEAKER_MAX_LEN and SUSPECT_RE.match(block)
                    and not block.endswith((".", "?", "!"))):
                meta["suspects"][(section, block)] += 1
        # text before the first speaker is dropped silently

    flush()
    return utterances, meta


def write_blocker(utterances: list[dict], meta: dict) -> str | None:
    if not utterances:
        return "no utterances"
    if not meta["qa_header"]:
        return "Q&A header not found"
    return None


def summary_line(filing: Filing, utterances: list[dict], meta: dict) -> None:
    sections = Counter(u["section"] for u in utterances)
    roles = {r: {u["speaker_name"] for u in utterances if u["speaker_role"] == r}
             for r in ("management", "analyst")}
    print(
        f"{filing.quarter} ({filing.label})  utt {len(utterances):4d}  "
        f"prep {sections['prepared']:3d}  qa {sections['qa']:4d}  "
        f"mgmt {len(roles['management'])}  analysts {len(roles['analyst']):2d}  "
        f"suspects {len(meta['suspects']):2d}  "
        f"qa-header {'yes' if meta['qa_header'] else 'NO '}  "
        f"grouping {meta['grouping']:9s}  cover {meta['cover_end']}"
    )


def dry_run_report(filing: Filing, utterances: list[dict], meta: dict) -> None:
    sections = Counter(u["section"] for u in utterances)
    print(f"=== {filing.quarter} ({filing.label}), call {filing.call_date}, {filing.raw_path}")
    print(f"cover end: {meta['cover_end']}  |  qa header: "
          f"{'found' if meta['qa_header'] else 'NOT FOUND'}")
    print(f"page numbers skipped: {meta['pages_skipped']}, "
          f"standalone numbers kept as text: {meta['numbers_kept']}")
    per_grouping = ", ".join(f"{n} {c}" for n, c in meta["utterances_per_grouping"].items())
    print(f"grouping: {meta['grouping']} (utterances per grouping: {per_grouping}), "
          f"{meta['blocks']} blocks, longest {meta['longest_block']:,} chars")
    print(f"{len(utterances)} utterances (prepared {sections['prepared']}, qa {sections['qa']})\n")

    print("--- speaker overview ---")
    seen = Counter((u["section"], u["speaker_role"], u["speaker_name"], u["speaker_org"])
                   for u in utterances)
    for (section, role, name, org), n in seen.items():
        org_part = f" [{org}]" if org else ""
        print(f"{section:8s} {role:10s} {name}{org_part}  ({n})")

    print("\n--- unrecognized speaker-like lines (check these) ---")
    if not meta["suspects"]:
        print("none")
    for (section, block), n in meta["suspects"].most_common(25):
        print(f"{section:8s} {n:3d}x  {block}")

    brackets = Counter(b for u in utterances for b in BRACKET_RE.findall(u["body"]))
    print("\n--- bracket insertions ---")
    print(", ".join(f"{b} ({n})" for b, n in brackets.most_common()) or "none")

    def show(rows):
        for u in rows:
            print(f"{u['seq']:3d} {u['section']:8s} {u['speaker_role']:10s} "
                  f"{u['speaker_name']:22s} {u['body'][:70]}")

    print("\n--- first 8 utterances ---")
    show(utterances[:8])
    print("\n--- last 3 utterances ---")
    show(utterances[-3:])
    print("\n--- 3 longest utterances (merged speakers show up here) ---")
    for u in sorted(utterances, key=lambda u: len(u["body"]), reverse=True)[:3]:
        print(f"{u['seq']:3d} {len(u['body']):6,d} chars  {u['speaker_name']}: "
              f"...{u['body'][len(u['body']) // 2:][:90]}")
    print()


def write(filing: Filing, utterances: list[dict]) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                select f.id as firm_id, fi.id as filing_id
                from firms f
                join filings fi on fi.firm_id = f.id
                where f.ticker = :ticker and fi.quarter = :quarter
                  and fi.doc_type = :doc_type
                """
            ),
            {"ticker": FIRM["ticker"], "quarter": filing.quarter,
             "doc_type": filing.doc_type},
        ).one()

        call_id = conn.execute(
            text(
                """
                insert into calls (firm_id, filing_id, quarter, call_date)
                values (:firm_id, :filing_id, :quarter, :call_date)
                on conflict (firm_id, quarter)
                    do update set call_date = excluded.call_date,
                                  filing_id = excluded.filing_id
                returning id
                """
            ),
            {
                "firm_id": row.firm_id,
                "filing_id": row.filing_id,
                "quarter": filing.quarter,
                "call_date": filing.call_date,
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
    print(f"  wrote call {call_id} ({filing.quarter}) with {len(utterances)} utterances")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quarter", help="one quarter, e.g. 1Q23 or 2023-Q1 (default: all)")
    ap.add_argument("--write", action="store_true",
                    help="write to the database (default: dry run)")
    args = ap.parse_args()

    for filing in select_filings(args.quarter):
        utterances, meta = best_parse(filing.raw_path.read_bytes())
        if args.quarter:
            dry_run_report(filing, utterances, meta)
        else:
            summary_line(filing, utterances, meta)
        if args.write:
            problem = write_blocker(utterances, meta)
            if problem:
                print(f"  NOT WRITTEN {filing.quarter}: {problem}")
            else:
                write(filing, utterances)

    if not args.write:
        print("\ndry run only, rerun with --write to store")


if __name__ == "__main__":
    main()