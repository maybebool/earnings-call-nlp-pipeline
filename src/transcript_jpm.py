"""Parse the JPMorgan transcript PDFs into utterances.

Same contract as the UBS branch in parse_transcript: parse_pdf returns a list
of utterance dicts and a meta dict of diagnostics.

Page numbers sit on their own line at the foot of a page. When an utterance
runs across a page break the speaker block is repeated at the top of the next
page, so consecutive blocks of the same speaker are merged back into one.
"""
import re
from collections import Counter

SEPARATOR_RE = re.compile(r"^\.{20,}$")
PAGE_NUMBER_RE = re.compile(r"^\d{1,3}$")
OPERATOR_RE = re.compile(r"^Operator:\s*(?P<body>.*)$")
END_RE = re.compile(r"^Disclaimer\s*$")

SECTION_HEADERS = {
    "MANAGEMENT DISCUSSION SECTION": "prepared",
    "QUESTION AND ANSWER SECTION": "qa",
}

# Name line, optionally carrying the Q or A marker of the Q&A section.
# Covers 'Jamie Dimon', "Matt O'Connor Q", 'Ebrahim H. Poonawala Q'.
_UPPER = "A-Z\u00c0-\u00dd"

# Name line. The Q or A marker of the Q&A section is stripped beforehand,
# because a single capital letter also satisfies the name pattern and would
# otherwise be swallowed by it.
MARKER_RE = re.compile(r"\s+[QA]$")
NAME_LINE_RE = re.compile(
    rf"^[{_UPPER}][\w.\u2019'-]*(?: [{_UPPER}][\w.\u2019'-]*){{1,3}}$"
)

# Role line, 'Analyst, Wolfe Research LLC' or
# 'Chief Financial Officer, JPMorganChase'.
ROLE_LINE_RE = re.compile(r"^(?P<title>[A-Z][^,]{2,60}?),\s*(?P<org>.{2,60})$")

NAME_MAX_LEN = 45
ROLE_MAX_LEN = 80

# Spelling variants inside and across the documents, so one analyst and one
# house carry the same spelling everywhere. Real moves between houses stay,
# for example John McDonald from Autonomous Research to Truist Securities.
# Typographic apostrophes are normalised in classify_speaker, so O'Connor
# needs no entry here.
ANALYST_NAME_FIXES: dict[str, str] = {}
ANALYST_ORG_FIXES: dict[str, str] = {
    "Bank of America Merrill Lynch": "Bank of America",
    "BofA Securities, Inc.": "Bank of America",
    "Deutsche Bank Securities, Inc.": "Deutsche Bank",
    "HSBC Securities (USA), Inc": "HSBC Securities",
    "HSBC Securities (USA), Inc.": "HSBC Securities",
    "Jefferies LLC": "Jefferies",
    "Morgan Stanley & Co. LLC": "Morgan Stanley",
    "Piper Sandler & Co.": "Piper Sandler",
    "Portales Partners LLC": "Portales Partners",
    "RBC Capital Markets LLC": "RBC Capital Markets",
    "Seaport Global Securities LLC": "Seaport Global Securities",
    "Truist Securities, Inc.": "Truist Securities",
    "UBS Securities LLC": "UBS",
    "Wells Fargo Securities LLC": "Wells Fargo Securities",
    "Wolfe Research LLC": "Wolfe Research",
}


def _key(s: str) -> str:
    return re.sub(r"[^a-z]", "", s.lower())


NAME_FIXES_BY_KEY = {_key(k): v for k, v in ANALYST_NAME_FIXES.items()}
ORG_FIXES_BY_KEY = {_key(k): v for k, v in ANALYST_ORG_FIXES.items()}


def page_lines(path) -> list[tuple[str, bool]]:
    """Every non-empty line of the PDF, with a flag for the first of a page."""
    import pdfplumber

    out: list[tuple[str, bool]] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            first = True
            for raw in (page.extract_text() or "").split("\n"):
                line = raw.strip()
                if not line:
                    continue
                out.append((line, first))
                first = False
    return out


def classify_speaker(name_line: str, role_line: str):
    """('name', 'org', 'role') for a speaker block, or None."""
    if len(name_line) > NAME_MAX_LEN or len(role_line) > ROLE_MAX_LEN:
        return None
    name = MARKER_RE.sub("", name_line).strip().replace("\u2019", "'")
    role_match = ROLE_LINE_RE.match(role_line)
    if NAME_LINE_RE.match(name) is None or role_match is None:
        return None
    title = role_match["title"].strip()
    org = role_match["org"].strip()

    if title == "Analyst":
        name = NAME_FIXES_BY_KEY.get(_key(name), name)
        org = ORG_FIXES_BY_KEY.get(_key(org), org)
        return name, org, "analyst"
    # Everything else on a speaker block is the company's own side. The
    # employer is the bank itself, which the utterance table leaves empty.
    return name, None, "management"


def parse_pdf(path) -> tuple[list[dict], dict]:
    """
    Parses a PDF document into structured utterances and metadata.
    """
    lines = page_lines(path)
    meta = {
        "cover_end": None,
        "qa_header": False,
        "pages_skipped": 0,
        "numbers_kept": 0,
        "separators": 0,
        "merged_continuations": 0,
        "suspects": Counter(),
        "roles_seen": Counter(),
    }

    utterances: list[dict] = []
    section = None  # nothing is kept before the first section header
    current: dict | None = None
    after_separator = True

    def flush() -> None:
        """
        Flushes the current utterance buffer into the `utterances` list or merges it
        with the previous utterance if it matches specific criteria.
        """
        nonlocal current
        if current is None or not current["parts"]:
            current = None
            return
        body = " ".join(current["parts"])
        previous = utterances[-1] if utterances else None
        # A speaker block repeated at the top of a page continues the same
        # utterance rather than starting a new one.
        if (previous is not None
                and previous["speaker_name"] == current["name"]
                and previous["speaker_role"] == current["role"]
                and previous["section"] == current["section"]):
            previous["body"] = f"{previous['body']} {body}"
            meta["merged_continuations"] += 1
        else:
            utterances.append(
                {
                    "seq": len(utterances) + 1,
                    "speaker_name": current["name"],
                    "speaker_org": current["org"],
                    "speaker_role": current["role"],
                    "section": current["section"],
                    "body": body,
                }
            )
        current = None

    index = 0
    while index < len(lines):
        line, page_start = lines[index]

        if END_RE.match(line):
            break

        if line in SECTION_HEADERS:
            flush()
            section = SECTION_HEADERS[line]
            if section == "prepared":
                meta["cover_end"] = "MANAGEMENT DISCUSSION SECTION"
            else:
                meta["qa_header"] = True
            after_separator = True
            index += 1
            continue

        if SEPARATOR_RE.match(line):
            meta["separators"] += 1
            after_separator = True
            index += 1
            continue

        if PAGE_NUMBER_RE.match(line):
            meta["pages_skipped"] += 1
            index += 1
            continue

        if section is None:  # cover page
            index += 1
            continue

        operator = OPERATOR_RE.match(line)
        if operator:
            flush()
            current = {"name": "Operator", "org": None, "role": "operator",
                       "section": section, "parts": [operator["body"]]}
            after_separator = False
            index += 1
            continue

        # A speaker block only starts right after a separator or at the top of
        # a page. Without that guard an ordinary sentence could be read as a
        # name whenever the next line happens to contain a comma.
        if (after_separator or page_start) and index + 1 < len(lines):
            speaker = classify_speaker(line, lines[index + 1][0])
            if speaker is not None:
                flush()
                name, org, role = speaker
                meta["roles_seen"][(role, name, org)] += 1
                current = {"name": name, "org": org, "role": role,
                           "section": section, "parts": []}
                after_separator = False
                index += 2
                continue

        if current is not None:
            current["parts"].append(line)
        elif after_separator:
            # Text where a speaker block was expected: worth looking at.
            meta["suspects"][(section, line[:80])] += 1
        after_separator = False
        index += 1

    flush()
    return utterances, meta