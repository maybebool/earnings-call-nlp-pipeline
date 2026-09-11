"""Target filings for the multi-quarter build: UBS 1Q23 to 4Q24.

Quarters are ISO strings (YYYY-Qn) everywhere: database, raw file names and
exports, so they sort chronologically. The UBS label form (1Q23) is derived
for display only. `quarter` is the reporting period, not the call date
(the 4Q24 call took place in February 2025).
"""
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

RAW_DIR = Path("data/raw")

FIRM = {
    "ticker": "UBS",
    "name": "UBS Group AG",
    "cik": "1610520",
    "peer_group": "EU universal bank",
}

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
        raise ValueError(f"not a UBS quarter label: {label!r}")
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
    quarter: str  # ISO reporting period, e.g. "2023-Q1"
    call_date: date
    source_url: str
    doc_type: str = "transcript"

    @property
    def label(self) -> str:
        return iso_to_label(self.quarter)

    @property
    def accession_number(self) -> str:
        return accession_from_url(self.source_url)

    @property
    def raw_path(self) -> Path:
        return RAW_DIR / f"{FIRM['ticker']}_{self.quarter}_{self.doc_type}.htm"


FILINGS = [
    Filing(
        "2023-Q1", date(2023, 4, 25),
        "https://www.sec.gov/Archives/edgar/data/1610520/000161052023000084/investorpreso20230426.htm",
    ),
    Filing(
        "2023-Q2", date(2023, 8, 31),
        "https://www.sec.gov/Archives/edgar/data/1610520/000161052023000139/investorpreso20230901.htm",
    ),
    Filing(
        "2023-Q3", date(2023, 11, 7),
        "https://www.sec.gov/Archives/edgar/data/1610520/000161052023000176/investorpreso20231108.htm",
    ),
    Filing(
        "2023-Q4", date(2024, 2, 6),
        "https://www.sec.gov/Archives/edgar/data/1610520/000161052024000026/investorpreso20240207.htm",
    ),
    Filing(
        "2024-Q1", date(2024, 5, 7),
        "https://www.sec.gov/Archives/edgar/data/1610520/000161052024000108/investorpresot20240508.htm",
    ),
    Filing(
        "2024-Q2", date(2024, 8, 14),
        "https://www.sec.gov/Archives/edgar/data/1610520/000161052024000147/investorpreso20240815.htm",
    ),
    Filing(
        "2024-Q3", date(2024, 10, 30),
        "https://www.sec.gov/Archives/edgar/data/1610520/000161052024000166/investorpreso20241101.htm",
    ),
    Filing(
        "2024-Q4", date(2025, 2, 4),
        "https://www.sec.gov/Archives/edgar/data/1610520/000161052025000004/6k20250205invpr.htm",
    ),
]


def _validate() -> None:
    quarters = [f.quarter for f in FILINGS]
    if quarters != sorted(set(quarters)):
        raise ValueError("FILINGS must be unique and in chronological order")
    for f in FILINGS:
        iso_to_label(f.quarter)
        accession_from_url(f.source_url)
        if cik_from_url(f.source_url) != FIRM["cik"]:
            raise ValueError(f"{f.quarter}: CIK in URL differs from FIRM")


_validate()


if __name__ == "__main__":
    for f in FILINGS:
        print(f"{f.quarter}  {f.label:<5} {f.call_date}  {f.accession_number}  {f.raw_path}")