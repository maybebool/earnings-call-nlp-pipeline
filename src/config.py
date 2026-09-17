"""Target filings for the multi-bank build: UBS and JPMorgan Chase, 1Q23 to 4Q24.

Per bank and quarter: the earnings call transcript and the quarterly report
that carries the key figures table. JPMorgan adds one event call, the First
Republic acquisition call of 1 May 2023, which falls into 2023-Q2 alongside
that quarter's regular earnings call.

Quarters are ISO strings (YYYY-Qn) everywhere: database, raw file names and
exports, so they sort chronologically. The label form (1Q23) is derived for
display only; both banks use it in their own documents. `quarter` is the
reporting period, not the call date (the UBS 4Q24 call took place in
February 2025).
"""
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw"

FIRMS = {
    "UBS": {
        "ticker": "UBS",
        "name": "UBS Group AG",
        "cik": "1610520",
        "peer_group": "EU universal bank",
    },
    "JPM": {
        "ticker": "JPM",
        "name": "JPMorgan Chase & Co.",
        "cik": "19617",
        "peer_group": "US universal bank",
    },
}

DOC_TYPES = ("transcript", "report")
CALL_TYPES = ("earnings", "event")
SOURCES = ("edgar", "ir")

_ISO_QUARTER = re.compile(r"(\d{4})-Q([1-4])")
_LABEL_QUARTER = re.compile(r"([1-4])Q(\d{2})")
_ACCESSION_SEGMENT = re.compile(r"\d{18}")


def iso_to_label(quarter: str) -> str:
    """'2023-Q1' -> '1Q23'."""
    match = _ISO_QUARTER.fullmatch(quarter)
    if match is None:
        raise ValueError(f"not an ISO quarter: {quarter!r}")
    year, q = match.groups()
    return f"{q}Q{year[2:]}"


def label_to_iso(label: str) -> str:
    """'1Q23' -> '2023-Q1'."""
    match = _LABEL_QUARTER.fullmatch(label)
    if match is None:
        raise ValueError(f"not a quarter label: {label!r}")
    q, yy = match.groups()
    return f"20{yy}-Q{q}"


def accession_from_url(url: str) -> str:
    """Derive '0001610520-23-000082' from the EDGAR archive path segment."""
    segment = url.rstrip("/").split("/")[-2]
    if _ACCESSION_SEGMENT.fullmatch(segment) is None:
        raise ValueError(f"no accession segment in URL: {url}")
    return f"{segment[:10]}-{segment[10:12]}-{segment[12:]}"


def cik_from_url(url: str) -> str:
    return url.split("/edgar/data/")[1].split("/")[0]


@dataclass(frozen=True)
class Filing:
    firm: str  # ticker, a key of FIRMS
    quarter: str  # ISO reporting period, e.g. "2023-Q1"
    call_date: date
    source_url: str
    doc_type: str = "transcript"
    call_type: str = "earnings"
    source: str = "edgar"

    @property
    def label(self) -> str:
        return iso_to_label(self.quarter)

    @property
    def accession_number(self) -> str | None:
        """None for documents that were published rather than filed."""
        if self.source != "edgar":
            return None
        return accession_from_url(self.source_url)

    @property
    def suffix(self) -> str:
        """'.htm' or '.pdf', taken from the source URL."""
        return Path(self.source_url.split("?")[0]).suffix.lower()

    @property
    def media(self) -> str:
        """'pdf' or 'html', the format the parser has to handle."""
        return "pdf" if self.suffix == ".pdf" else "html"

    @property
    def raw_path(self) -> Path:
        """data/raw/JPM_2023-Q2_transcript_event.pdf and the like.

        The call_type part is omitted for earnings calls, which keeps the
        existing UBS file names valid.
        """
        parts = [self.firm, self.quarter, self.doc_type]
        if self.call_type != "earnings":
            parts.append(self.call_type)
        return RAW_DIR / f"{'_'.join(parts)}{self.suffix}"


# UBS Group AG, Form 6-K. The transcripts carry prepared remarks and Q&A in
# one document; the quarterly reports carry the "Our key figures" table.
# Not the UBS AG reports (CIK 1114446) and not the Pillar 3, capital
# instruments or standalone filings of the same day.
UBS_TRANSCRIPTS = [
    Filing(
        "UBS", "2023-Q1", date(2023, 4, 25),
        "https://www.sec.gov/Archives/edgar/data/1610520/000161052023000084/investorpreso20230426.htm",
    ),
    Filing(
        "UBS", "2023-Q2", date(2023, 8, 31),
        "https://www.sec.gov/Archives/edgar/data/1610520/000161052023000139/investorpreso20230901.htm",
    ),
    Filing(
        "UBS", "2023-Q3", date(2023, 11, 7),
        "https://www.sec.gov/Archives/edgar/data/1610520/000161052023000176/investorpreso20231108.htm",
    ),
    Filing(
        "UBS", "2023-Q4", date(2024, 2, 6),
        "https://www.sec.gov/Archives/edgar/data/1610520/000161052024000026/investorpreso20240207.htm",
    ),
    Filing(
        "UBS", "2024-Q1", date(2024, 5, 7),
        "https://www.sec.gov/Archives/edgar/data/1610520/000161052024000108/investorpresot20240508.htm",
    ),
    Filing(
        "UBS", "2024-Q2", date(2024, 8, 14),
        "https://www.sec.gov/Archives/edgar/data/1610520/000161052024000147/investorpreso20240815.htm",
    ),
    Filing(
        "UBS", "2024-Q3", date(2024, 10, 30),
        "https://www.sec.gov/Archives/edgar/data/1610520/000161052024000166/investorpreso20241101.htm",
    ),
    Filing(
        "UBS", "2024-Q4", date(2025, 2, 4),
        "https://www.sec.gov/Archives/edgar/data/1610520/000161052025000004/6k20250205invpr.htm",
    ),
]

UBS_REPORTS = [
    Filing(
        "UBS", "2023-Q1", date(2023, 4, 25),
        "https://www.sec.gov/Archives/edgar/data/1610520/000161052023000078/EDGARq23ubsgroupag.htm",
        doc_type="report",
    ),
    Filing(
        "UBS", "2023-Q2", date(2023, 8, 31),
        "https://www.sec.gov/Archives/edgar/data/1610520/000161052023000128/ubs-20230630.htm",
        doc_type="report",
    ),
    Filing(
        "UBS", "2023-Q3", date(2023, 11, 7),
        "https://www.sec.gov/Archives/edgar/data/1610520/000161052023000166/edgar3q23ubsgroup.htm",
        doc_type="report",
    ),
    Filing(
        "UBS", "2023-Q4", date(2024, 2, 6),
        "https://www.sec.gov/Archives/edgar/data/1610520/000161052024000018/edgarq23ubsgroupag.htm",
        doc_type="report",
    ),
    Filing(
        "UBS", "2024-Q1", date(2024, 5, 7),
        "https://www.sec.gov/Archives/edgar/data/1610520/000161052024000098/edgar1q24ubsgroup.htm",
        doc_type="report",
    ),
    Filing(
        "UBS", "2024-Q2", date(2024, 8, 14),
        "https://www.sec.gov/Archives/edgar/data/1610520/000161052024000141/ubs-20240630.htm",
        doc_type="report",
    ),
    Filing(
        "UBS", "2024-Q3", date(2024, 10, 30),
        "https://www.sec.gov/Archives/edgar/data/1610520/000161052024000156/edgar3q24ubsgroup.htm",
        doc_type="report",
    ),
    Filing(
        "UBS", "2024-Q4", date(2025, 2, 4),
        "https://www.sec.gov/Archives/edgar/data/1610520/000161052025000001/edgar4q24ubsgroup.htm",
        doc_type="report",
    ),
]


# JPMorgan Chase & Co. Transcripts are PDFs from the investor relations site:
# as a domestic issuer JPMorgan does not file its calls with the SEC. The
# file names follow no pattern, four variants appear over two years, so every
# URL is verified individually rather than constructed.
_JPM_IR = ("https://www.jpmorganchase.com/content/dam/jpmc/jpmorgan-chase-and-co"
           "/investor-relations/documents")

JPM_TRANSCRIPTS = [
    Filing(
        "JPM", "2023-Q1", date(2023, 4, 14),
        f"{_JPM_IR}/quarterly-earnings/2023/1st-quarter/1q23-earnings-transcript.pdf",
        source="ir",
    ),
    Filing(
        "JPM", "2023-Q2", date(2023, 7, 14),
        f"{_JPM_IR}/quarterly-earnings/2023/2nd-quarter/2q23-earnings-transcript.pdf",
        source="ir",
    ),
    Filing(
        "JPM", "2023-Q3", date(2023, 10, 13),
        f"{_JPM_IR}/quarterly-earnings/2023/3rd-quarter/jpm-3q23-earnings-call-transcript.pdf",
        source="ir",
    ),
    Filing(
        "JPM", "2023-Q4", date(2024, 1, 12),
        f"{_JPM_IR}/quarterly-earnings/2023/4th-quarter/jpm-4q23-earnings-call-transcript.pdf",
        source="ir",
    ),
    Filing(
        "JPM", "2024-Q1", date(2024, 4, 12),
        f"{_JPM_IR}/quarterly-earnings/2024/1st-quarter/jpm-1q24-earnings-call-transcript.pdf",
        source="ir",
    ),
    Filing(
        "JPM", "2024-Q2", date(2024, 7, 12),
        f"{_JPM_IR}/quarterly-earnings/2024/2nd-quarter/jpm-2q24-earnings-call-transcript-final.pdf",
        source="ir",
    ),
    Filing(
        "JPM", "2024-Q3", date(2024, 10, 11),
        f"{_JPM_IR}/quarterly-earnings/2024/3rd-quarter/jpm-3q24-earnings-call-transcript-final.pdf",
        source="ir",
    ),
    Filing(
        "JPM", "2024-Q4", date(2025, 1, 15),
        f"{_JPM_IR}/quarterly-earnings/2024/4th-quarter/4q24-earnings-transcript.pdf",
        source="ir",
    ),
]

# The First Republic acquisition call, held the morning the deal closed.
# Same speakers and same document layout as the quarterly calls, but the
# first page carries the transaction title instead of '<q>Q<yy> FINANCIAL
# RESULTS'. Assigned to the quarter it took place in.
JPM_EVENT_CALLS = [
    Filing(
        "JPM", "2023-Q2", date(2023, 5, 1),
        f"{_JPM_IR}/events/2023/jpmorgan-chase-acquires-substantial-majority-of-assets-and-"
        f"assumes-certain-liabilities-of-first-republic-bank-conference-call-/"
        f"JPM_FRC-Conference-Call-Final-Transcript.pdf",
        call_type="event",
        source="ir",
    ),
]

# Exhibit 99.2 of the quarterly earnings 8-K, the Earnings Release Financial
# Supplement. Its Consolidated Financial Highlights on pages 2 to 3 are the
# counterpart to the UBS key figures table. Not the 10-Q: JPMorgan files none
# for the fourth quarter, the 10-K covers it, which would break the series.
# Filing date equals the call date for every quarter in this window.
JPM_REPORTS = [
    Filing(
        "JPM", "2023-Q1", date(2023, 4, 14),
        "https://www.sec.gov/Archives/edgar/data/19617/000162828023011546/"
        "a1q23erfex992supplement.htm",
        doc_type="report",
    ),
    Filing(
        "JPM", "2023-Q2", date(2023, 7, 14),
        "https://www.sec.gov/Archives/edgar/data/19617/000001961723000425/"
        "a2q23erfex992supplement.htm",
        doc_type="report",
    ),
    Filing(
        "JPM", "2023-Q3", date(2023, 10, 13),
        "https://www.sec.gov/Archives/edgar/data/19617/000001961723000508/"
        "a3q23erfex992supplement.htm",
        doc_type="report",
    ),
    Filing(
        "JPM", "2023-Q4", date(2024, 1, 12),
        "https://www.sec.gov/Archives/edgar/data/19617/000001961724000049/"
        "a4q23erfex992supplement.htm",
        doc_type="report",
    ),
    Filing(
        "JPM", "2024-Q1", date(2024, 4, 12),
        "https://www.sec.gov/Archives/edgar/data/19617/000001961724000315/"
        "a1q24erfex992supplement.htm",
        doc_type="report",
    ),
    Filing(
        "JPM", "2024-Q2", date(2024, 7, 12),
        "https://www.sec.gov/Archives/edgar/data/19617/000001961724000446/"
        "a2q24erfex992supplement.htm",
        doc_type="report",
    ),
    Filing(
        "JPM", "2024-Q3", date(2024, 10, 11),
        "https://www.sec.gov/Archives/edgar/data/19617/000001961724000555/"
        "a3q24erfex992supplement.htm",
        doc_type="report",
    ),
    Filing(
        "JPM", "2024-Q4", date(2025, 1, 15),
        "https://www.sec.gov/Archives/edgar/data/19617/000001961725000040/"
        "a4q24erfex992supplement.htm",
        doc_type="report",
    ),
]

TRANSCRIPTS = UBS_TRANSCRIPTS + JPM_TRANSCRIPTS + JPM_EVENT_CALLS
REPORTS = UBS_REPORTS + JPM_REPORTS
FILINGS = TRANSCRIPTS + REPORTS


def _validate() -> None:
    """
    Validates the consistency and integrity of filings data in the `FILINGS` global collection.
    """
    groups: dict[tuple[str, str, str], list[str]] = {}
    for f in FILINGS:
        if f.firm not in FIRMS:
            raise ValueError(f"unknown firm {f.firm!r}")
        if f.doc_type not in DOC_TYPES:
            raise ValueError(f"{f.firm} {f.quarter}: unknown doc_type {f.doc_type!r}")
        if f.call_type not in CALL_TYPES:
            raise ValueError(f"{f.firm} {f.quarter}: unknown call_type {f.call_type!r}")
        if f.source not in SOURCES:
            raise ValueError(f"{f.firm} {f.quarter}: unknown source {f.source!r}")
        if f.doc_type == "report" and f.call_type != "earnings":
            raise ValueError(f"{f.firm} {f.quarter}: a report has no call_type")
        iso_to_label(f.quarter)
        if f.source == "edgar":
            accession_from_url(f.source_url)
            if cik_from_url(f.source_url) != FIRMS[f.firm]["cik"]:
                raise ValueError(f"{f.firm} {f.quarter}: CIK in URL differs from FIRMS")
        groups.setdefault((f.firm, f.doc_type, f.call_type), []).append(f.quarter)

    for (firm, doc_type, call_type), quarters in groups.items():
        if quarters != sorted(set(quarters)):
            raise ValueError(
                f"{firm} {doc_type} {call_type} must be unique and in chronological order"
            )

    for field, values in (
        ("key", [(f.firm, f.quarter, f.doc_type, f.call_type) for f in FILINGS]),
        ("source_url", [f.source_url for f in FILINGS]),
        ("raw_path", [f.raw_path for f in FILINGS]),
    ):
        if len(values) != len(set(values)):
            raise ValueError(f"{field} is not unique across FILINGS")


def select_filings(quarter: str | None,
                   doc_type: str | None = "transcript",
                   firm: str | None = None,
                   call_type: str | None = None) -> list[Filing]:
    """Filings matching all given filters; None means no filter on that field."""
    selected = FILINGS
    if firm is not None:
        if firm not in FIRMS:
            raise SystemExit(f"unknown firm {firm!r}, expected one of {tuple(FIRMS)}")
        selected = [f for f in selected if f.firm == firm]
    if doc_type is not None:
        if doc_type not in DOC_TYPES:
            raise SystemExit(f"unknown doc_type {doc_type!r}, expected one of {DOC_TYPES}")
        selected = [f for f in selected if f.doc_type == doc_type]
    if call_type is not None:
        if call_type not in CALL_TYPES:
            raise SystemExit(f"unknown call_type {call_type!r}, expected one of {CALL_TYPES}")
        selected = [f for f in selected if f.call_type == call_type]
    if quarter is not None:
        iso = quarter if "-Q" in quarter else label_to_iso(quarter)
        selected = [f for f in selected if f.quarter == iso]
    if not selected:
        raise SystemExit(
            f"no filing for firm={firm!r}, quarter={quarter!r}, "
            f"doc_type={doc_type!r}, call_type={call_type!r}"
        )
    return selected


_validate()


if __name__ == "__main__":
    for f in FILINGS:
        print(f"{f.firm:<4} {f.quarter}  {f.label:<5} {f.doc_type:<10} "
              f"{f.call_type:<8} {f.source:<5} {f.media:<4} "
              f"{f.accession_number or '-':<20}  {f.raw_path}")
    print(f"\n{len(FILINGS)} filings, "
          f"{len(TRANSCRIPTS)} transcripts, {len(REPORTS)} reports")