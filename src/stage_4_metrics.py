"""Read the 'Our key figures' table of each quarterly report.

Usage:
    python src/stage_4_metrics.py # dry run over all quarters
    python src/stage_4_metrics.py --quarter 1Q23 # dry run, one quarter
    python src/stage_4_metrics.py --matrix # metric x quarter overview
    python src/stage_4_metrics.py --write # write to the metrics table

Only the reporting quarter's own figures are stored. In the 1Q23 report that
is the first data column of the table; from 2Q23 on the reports are PDF
conversions with inline XBRL, and the figure is chosen by its XBRL period so
comparative and year-to-date columns cannot be mistaken for the quarter.
Rows labelled 'excluding negative goodwill, integration-related expenses,
and acquisition costs' are stored with basis 'underlying'.
"""
import argparse
import os
import re
import warnings
from collections import Counter
from datetime import date

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from config import FIRMS, Filing, select_filings
from parsers.jpm_metrics import key_figures

load_dotenv()

# Normalised row label -> metric_name. Rows that are not listed here are
# reported as 'unmapped' in the dry run instead of being stored silently.
METRIC_NAMES = {
    "total revenues": "total_revenues",
    "negative goodwill": "negative_goodwill",
    "credit loss expense / (release)": "credit_loss_expense",
    "operating expenses": "operating_expenses",
    "operating profit / (loss) before tax": "pbt",
    "net profit / (loss) attributable to shareholders": "net_profit",
    "diluted earnings per share (usd)": "diluted_eps",
    "return on equity (%)": "return_on_equity",
    "return on tangible equity (%)": "return_on_tangible_equity",
    "return on common equity tier 1 capital (%)": "return_on_cet1",
    "return on leverage ratio denominator, gross (%)": "return_on_lrd_gross",
    "cost / income ratio (%)": "cost_income_ratio",
    "effective tax rate (%)": "effective_tax_rate",
    "net profit growth (%)": "net_profit_growth",
    "total assets": "total_assets",
    "equity attributable to shareholders": "equity_attributable_to_shareholders",
    "common equity tier 1 capital": "cet1_capital",
    "risk-weighted assets": "rwa",
    "common equity tier 1 capital ratio (%)": "cet1_ratio",
    "going concern capital ratio (%)": "going_concern_capital_ratio",
    "total loss-absorbing capacity ratio (%)": "tlac_ratio",
    "leverage ratio denominator": "lrd",
    "common equity tier 1 leverage ratio (%)": "cet1_leverage_ratio",
    "liquidity coverage ratio (%)": "lcr",
    "net stable funding ratio (%)": "nsfr",
    "invested assets (usd bn)": "invested_assets",
    "personnel (full-time equivalents)": "personnel_fte",
    "market capitalization": "market_capitalization",
    "total book value per share (usd)": "total_book_value_per_share",
    "tangible book value per share (usd)": "tangible_book_value_per_share",
}

# Suffixes marking a row as the underlying (adjusted) variant of a metric.
UNDERLYING_RE = re.compile(
    r"\s*\((?:excluding|adjusted for)[^)]*(?:negative goodwill|integration-related)[^)]*\)",
    re.IGNORECASE,
)
# Trailing footnote markers: 'Risk-weighted assets3', 'Cost / income ratio (%)2,3'.
FOOTNOTE_RE = re.compile(r"(?<=[a-z)%])\s*\d+(?:\s*,\s*\d+)*$")
UNIT_BY_SUFFIX = (
    ("(%)", "percent"),
    ("(usd bn)", "USD bn"),
    ("(usd)", "USD"),
    ("(full-time equivalents)", "count"),
)
DEFAULT_UNIT = "USD m"  # the table header says 'USD m, except where indicated'

NUMBER_RE = re.compile(r"^\(?-?[\d,]+(?:\.\d+)?\)?$")

# The table we want contains all of these rows.
REQUIRED_ROWS = ("total revenues", "cost / income ratio", "risk-weighted assets")

PARSERS = ("lxml", "html.parser", "lxml-xml")
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

COORD_RE = re.compile(r"(left|top)\s*:\s*(-?[\d.]+)px")
# nodes of one row share a top within this margin
ROW_TOLERANCE_PX = 4.0

# gap between the label block and the first column
MIN_LABEL_GAP_PX = 150.0

# no data column starts left of this
MIN_DATA_LEFT_PX = 300.0

# '30.6.23' column header
DATE_HEADER_RE = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{2}$")

# a wrapped label spans at most this many lines
MAX_LABEL_LINES = 3

KEY_FIGURES_ANCHOR = "Our key figures"

# The key figures table is followed by this paragraph in every report.
GEOMETRY_STOP = ("alternative performance measures", "an alternative performance")

MAX_GEOMETRY_ROWS = 80

# how far an untagged value may sit from the header
MAX_HEADER_OFFSET_PX = 30.0


def get_engine():
    """
    Generates a SQL Alchemy engine instance for connecting to a PostgreSQL database.

    The function constructs a database URL by using the required environment
    variables for the database user, password, host, port, and database name. The
    engine created provides a connection interface to the specified PostgreSQL
    database instance.
    """
    url = (
        f"postgresql+psycopg://{os.environ['POSTGRES_USER']}:"
        f"{os.environ['POSTGRES_PASSWORD']}@{os.environ['POSTGRES_HOST']}:"
        f"{os.environ['POSTGRES_PORT']}/{os.environ['POSTGRES_DB']}"
    )
    return create_engine(url)


def cell_texts(row) -> list[str]:
    return [" ".join(c.get_text(" ").split()) for c in row.find_all(["td", "th"])]


def normalise_label(label: str) -> str:
    """
    Normalizes the given label by applying transformations to remove unwanted
    footnote patterns, collapsing white spaces, trimming, and converting to
    lowercase.
    """
    label = FOOTNOTE_RE.sub("", " ".join(label.split()))
    return label.strip().lower()


def parse_number(value: str) -> float | None:
    """
    Parses a string representation of a number and converts it into a floating-point value.
    Handles various number formats, such as negative numbers indicated by parentheses
    and certain Unicode minus signs. If the string does not match a valid number
    format, returns None.
    """
    value = value.replace("\u2212", "-").replace("\u2013", "-").strip()
    if NUMBER_RE.fullmatch(value) is None:
        return None
    negative = value.startswith("(") and value.endswith(")")
    number = float(value.strip("()").replace(",", ""))
    return -number if negative else number


def unit_for(label: str) -> str:
    for suffix, unit in UNIT_BY_SUFFIX:
        if label.endswith(suffix):
            return unit
    return DEFAULT_UNIT


def rows_from_table(soup) -> list[tuple[str, list[str]]] | None:
    """Rows of the key figures <table>, identified by its row labels.

    Columns that are empty in every row are spacers and are dropped, so
    index 0 of the returned values is the reporting quarter.
    """
    for table in soup.find_all("table"):
        rows = [cells for row in table.find_all("tr")
                if (cells := cell_texts(row)) and len(cells) >= 2]
        labels = {normalise_label(cells[0]) for cells in rows}
        if not all(any(l.startswith(r) for l in labels) for r in REQUIRED_ROWS):
            continue
        width = max(len(cells) for cells in rows)
        keep = [i for i in range(1, width)
                if any(len(cells) > i and cells[i].strip() for cells in rows)]
        return [(cells[0], [cells[i] if len(cells) > i else "" for i in keep])
                for cells in rows]
    return None


def _box(node) -> tuple[float | None, float | None]:
    """Absolute left/top of the nearest positioned ancestor."""
    box: dict[str, float] = {}
    for parent in node.parents:
        for axis, value in COORD_RE.findall(parent.get("style", "") or ""):
            box.setdefault(axis, float(value))
        if "left" in box and "top" in box:
            break
    return box.get("left"), box.get("top")


def quarter_period(quarter: str) -> tuple[date, date]:
    """'2023-Q2' -> (2023-04-01, 2023-06-30)."""
    year, q = int(quarter[:4]), int(quarter[-1])
    start = date(year, 3 * q - 2, 1)
    end_month = 3 * q
    next_start = date(year + 1, 1, 1) if end_month == 12 else date(year, end_month + 1, 1)
    return start, date.fromordinal(next_start.toordinal() - 1)


def context_periods(soup) -> dict[str, dict]:
    """Context id -> its period, so a fact can be tied to the quarter."""
    periods = {}
    for context in soup.find_all(lambda t: t.name == "context"):
        period = context.find(lambda t: t.name == "period")
        if period is None:
            continue
        periods[context.get("id")] = {
            c.name: c.get_text(strip=True) for c in period.find_all(True)
        }
    return periods


def is_reporting_period(period: dict, start: date, end: date) -> bool:
    """True for the quarter itself, not for comparatives or year-to-date."""
    if period.get("instant"):
        return period["instant"] == end.isoformat()
    return (period.get("startDate") == start.isoformat()
            and period.get("endDate") == end.isoformat())


def enclosing_fact(node):
    """The ix:nonFraction element a text node sits in, if any."""
    for parent in node.parents:
        if parent.name == "nonFraction":
            return parent
        if parent.name in ("body", "html"):
            break
    return None


def rows_from_geometry(soup, quarter: str) -> list[tuple[str, list[str]]] | None:
    """Rebuild the key figures rows from the pixel coordinates of the divs.

    Rows and labels come from the layout, but the value of a row is chosen
    by its inline XBRL period, so comparative and year-to-date columns can
    never be mistaken for the reporting quarter. Rows whose figures are not
    XBRL-tagged fall back to the column header positions.
    """
    start, end = quarter_period(quarter)
    periods = context_periods(soup)

    nodes = []
    for string in soup.find_all(string=True):
        stripped = " ".join(string.split())
        if not stripped:
            continue
        left, top = _box(string)
        if left is None or top is None:
            continue
        fact = enclosing_fact(string)
        period = periods.get(fact.get("contextRef")) if fact is not None else None
        negative = fact is not None and fact.get("sign") == "-"
        nodes.append((top, left, stripped, period, negative))

    anchor = next((i for i, n in enumerate(nodes) if n[2] == KEY_FIGURES_ANCHOR), None)
    if anchor is None:
        return None
    nodes = nodes[anchor + 1:]

    # Group into rows: consecutive nodes whose top is within the tolerance.
    rows: list[list[tuple]] = []
    current: list[tuple] = []
    current_top = None
    for top, left, txt, period, negative in nodes:
        if current_top is None or abs(top - current_top) <= ROW_TOLERANCE_PX:
            current_top = top if current_top is None else current_top
            current.append((left, txt, period, negative))
            continue
        rows.append(current)
        current, current_top = [(left, txt, period, negative)], top
        if len(rows) >= MAX_GEOMETRY_ROWS:
            break
    if current:
        rows.append(current)

    split: list[tuple[str, list[tuple]]] = []
    header: float | None = None
    for row in rows:
        row = sorted(row)
        label = row[0][1]
        if any(normalise_label(label).startswith(stop) for stop in GEOMETRY_STOP):
            break
        values: list[tuple] = []
        if len(row) >= 2:
            # The widest horizontal gap separates the label block (label plus
            # its footnote markers) from the first data column.
            gaps = [(row[i + 1][0] - row[i][0], i + 1) for i in range(len(row) - 1)]
            gap, first = max(gaps)
            if gap >= MIN_LABEL_GAP_PX and row[first][0] >= MIN_DATA_LEFT_PX:
                values = row[first:]
        # The date header row fixes where the reporting quarter's column sits.
        if header is None and values and DATE_HEADER_RE.match(values[0][1]):
            header = values[0][0]
        split.append((label, values))

    out: list[tuple[str, list[str]]] = []
    for label, values in split:
        chosen = next((v for v in values
                       if v[2] and is_reporting_period(v[2], start, end)), None)
        if chosen is None and values and header is not None:
            # Untagged row: take the value nearest the reporting column.
            candidate = min(values, key=lambda v: abs(v[0] - header))
            if abs(candidate[0] - header) <= MAX_HEADER_OFFSET_PX:
                chosen = candidate
        if chosen is None:
            out.append((label, []))
            continue
        text_value = f"-{chosen[1]}" if chosen[3] else chosen[1]
        out.append((label, [text_value]))
    return out


def key_figures_rows(html: bytes, quarter: str):
    """Rows of the key figures table, whichever way the report encodes it."""
    for parser in PARSERS:
        soup = BeautifulSoup(html, parser)
        rows = rows_from_table(soup)
        if rows:
            return rows, f"{parser}/table"
    # The XBRL reports need the XML parser so the ix: elements stay intact.
    soup = BeautifulSoup(html, "lxml-xml")
    rows = rows_from_geometry(soup, quarter)
    if rows and sum(1 for _, values in rows if values) >= 10:
        return rows, "lxml-xml/xbrl"
    return None, None


def resolve_label(label: str, pending: list[str], following: list[str]):
    """Map a row label to (label, metric_name, basis, lines consumed after it).

    A long label wraps over several lines in the PDF layout, and the figure
    may sit on any of them. Neighbouring lines are joined back on, but only
    when that produces a metric we know: the unjoined label is tried first,
    so a section heading such as 'Profitability and growth' is never glued
    to the row below it.
    """
    candidates = [(label, 0)]
    candidates += [(" ".join(pending[-n:] + [label]), 0) for n in range(1, len(pending) + 1)]
    candidates += [(" ".join([label] + following[:n]), n) for n in range(1, len(following) + 1)]
    for candidate, used in candidates:
        basis = "underlying" if UNDERLYING_RE.search(candidate) else "reported"
        name = METRIC_NAMES.get(normalise_label(UNDERLYING_RE.sub("", candidate)))
        if name is not None:
            return candidate, name, basis, used
    return label, None, "reported", 0


def extract_ubs(filing: Filing) -> tuple[list[dict], dict]:
    """
    Extracts key metrics and metadata from the provided filing.

    This function processes raw contents of a financial filing to extract relevant
    key metrics and associated metadata. The extraction involves parsing tables
    for key figures, resolving labels with adjacent lines for context, filtering
    invalid or overly ambiguous labels, and normalizing the resulting metrics and
    units. The returned metadata includes statistics about the extraction process,
    such as the number of processed rows, unmapped labels, and information on
    duplicate entries.
    """
    meta = {"rows": 0, "unmapped": [], "table_found": False}
    rows, parser = key_figures_rows(filing.raw_path.read_bytes(), filing.quarter)
    if rows is None:
        return [], meta
    meta["table_found"] = True
    meta["parser"] = parser

    # Materialise first: a wrapped label may carry its figure on any of its
    # lines, so rows need to be looked at both backwards and forwards.
    parsed = []
    for label, cells in rows:
        label = " ".join(label.split())
        if not label or len(label) > 160:
            continue
        parsed.append((label, parse_number(cells[0]) if cells else None))

    metrics: list[dict] = []
    consumed = 0  # rows already absorbed as the tail of a wrapped label
    for index, (label, value) in enumerate(parsed):
        if consumed:
            consumed -= 1
            continue
        if value is None:
            continue
        meta["rows"] += 1

        pending = [l for l, v in parsed[max(0, index - MAX_LABEL_LINES):index] if v is None]
        following = [l for l, _ in parsed[index + 1:index + 1 + MAX_LABEL_LINES]]

        label, name, basis, consumed = resolve_label(label, pending, following)
        if name is None:
            meta["unmapped"].append(label)
            continue

        metrics.append({
            "firm": filing.firm,
            "quarter": filing.quarter,
            "metric_name": name,
            "value": value,
            "unit": unit_for(normalise_label(UNDERLYING_RE.sub("", label))),
            "basis": basis,
            "label": label,
        })

    # Guard against a row appearing twice in one table.
    seen = Counter((m["metric_name"], m["basis"]) for m in metrics)
    meta["duplicates"] = [k for k, n in seen.items() if n > 1]
    return metrics, meta

def extract_jpm(filing: Filing) -> tuple[list[dict], dict]:
    """
    Extract key financial metrics and metadata from a filing instance.

    This function parses raw data from the provided filing, extracts relevant
    financial metrics, and organizes them into a structured format for downstream
    processing. Additionally, it compiles metadata about the parsing process and
    anomalies such as duplicate metrics.
    """
    found, raw_meta = key_figures(filing.raw_path.read_bytes(), filing.label)
    metrics = [
        {**m, "firm": filing.firm, "quarter": filing.quarter,
         "label": m["metric_name"]}
        for m in found
    ]
    seen = Counter((m["metric_name"], m["basis"]) for m in metrics)
    return metrics, {
        "rows": raw_meta["rows"],
        "unmapped": [f"[{section}] {label}"
                     for section, label in raw_meta["unmapped"]],
        "table_found": raw_meta["table_found"],
        "parser": f"html, {raw_meta['tables']} highlights tables",
        "duplicates": [k for k, n in seen.items() if n > 1],
    }


def extract(filing: Filing) -> tuple[list[dict], dict]:
    """Dispatch to the branch the bank's report format needs."""
    if filing.firm == "JPM":
        return extract_jpm(filing)
    return extract_ubs(filing)

def report(filing: Filing, metrics: list[dict], meta: dict) -> None:
    """
    Generates a detailed report for financial filings, including metrics and metadata. The function
    prints the firm name, quarter, and other relevant details based on the provided data. It also
    handles scenarios where certain tables or data points are missing or unmapped, and gives a
    summary of key figures.
    """
    print(f" {filing.firm} {filing.quarter} ({filing.label})  {filing.raw_path}")
    if not meta["table_found"]:
        print("KEY FIGURES TABLE NOT FOUND\n")
        return
    per_basis = Counter(m["basis"] for m in metrics)
    print(f"parser: {meta['parser']}")
    print(f"{meta['rows']} table rows -> "
          + ", ".join(f"{n} {b}" for b, n in sorted(per_basis.items())))
    if meta["duplicates"]:
        print(f"DUPLICATES: {meta['duplicates']}")
    if meta["unmapped"]:
        print("unmapped rows carrying a figure (add to METRIC_NAMES if wanted):")
        for label in meta["unmapped"]:
            print(f"    {label}")
    for m in metrics:
        basis = "" if m["basis"] == "reported" else f"  [{m['basis']}]"
        print(f"  {m['metric_name']:<38} {m['value']:>14,.2f} {m['unit']:<8}{basis}")
    print()


def matrix(all_metrics: list[dict], columns: list[tuple[str, str]]) -> None:
    """
    Generates and prints a formatted matrix displaying metrics, their basis, units, and corresponding
    values for each column provided. The metrics are organized and formatted in a human-readable way
    for review or analysis.
    """
    names = sorted({(m["metric_name"], m["basis"], m["unit"]) for m in all_metrics})
    by_key = {(m["firm"], m["quarter"], m["metric_name"], m["basis"]): m["value"]
              for m in all_metrics}
    head = "".join(f"{firm + ' ' + quarter[2:]:>14s}" for firm, quarter in columns)
    print(f"{'metric':<40}{'unit':<8}{head}")
    for name, basis, unit in names:
        label = name if basis == "reported" else f"{name} ({basis})"
        cells = "".join(
            f"{by_key[(firm, quarter, name, basis)]:>14,.1f}"
            if (firm, quarter, name, basis) in by_key else f"{'-':>14s}"
            for firm, quarter in columns
        )
        print(f"{label:<40}{unit:<8}{cells}")


def write(all_metrics: list[dict]) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                select f.ticker, fi.quarter, f.id as firm_id, fi.id as filing_id
                from filings fi
                         join firms f on f.id = fi.firm_id
                where fi.doc_type = 'report'
                """
            ),
        ).all()
        ids = {(r.ticker, r.quarter): (r.firm_id, r.filing_id) for r in rows}

        missing = sorted({(m["firm"], m["quarter"]) for m in all_metrics} - ids.keys())
        if missing:
            raise SystemExit(
                f"no report filing row for {missing}; "
                f"run stage_1_fetch --doc-type report for those banks"
            )
        if missing:
            raise SystemExit(f"no report filing row for {missing}; "
                             f"run stage_1_fetch --doc-type report")

        written = 0
        for m in all_metrics:
            firm_id, filing_id = ids[(m["firm"], m["quarter"])]
            conn.execute(
                text(
                    """
                    insert into metrics
                        (firm_id, filing_id, quarter, metric_name, value, unit, basis)
                    values
                        (:firm_id, :filing_id, :quarter, :metric_name, :value, :unit, :basis)
                    on conflict (firm_id, quarter, metric_name, basis)
                        do update set value = excluded.value,
                                      unit = excluded.unit,
                                      filing_id = excluded.filing_id
                    """
                ),
                {**{k: v for k, v in m.items() if k not in ("label", "firm")},
                 "firm_id": firm_id, "filing_id": filing_id},
            )
            written += 1
    print(f"wrote {written} metric rows")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bank", help=f"one bank: {', '.join(FIRMS)} (default: all)")
    ap.add_argument("--quarter", help="one quarter, e.g. 1Q23 or 2023-Q1 (default: all)")
    ap.add_argument("--matrix", action="store_true",
                    help="print a metric x quarter overview instead of per-quarter detail")
    ap.add_argument("--write", action="store_true",
                    help="write to the database (default: dry run)")
    args = ap.parse_args()

    filings = select_filings(args.quarter, "report", args.bank)
    all_metrics: list[dict] = []
    problems = []
    for filing in filings:
        metrics, meta = extract(filing)
        all_metrics += metrics
        if not args.matrix:
            report(filing, metrics, meta)
        if not meta["table_found"] or meta["duplicates"] or not metrics:
            problems.append(f"{filing.firm} {filing.quarter}")

    if args.matrix:
        matrix(all_metrics, [(f.firm, f.quarter) for f in filings])
        print()

    print(f"{len(all_metrics)} metric rows over {len(filings)} quarters")
    if problems:
        print(f"PROBLEM QUARTERS: {problems}")

    if args.write:
        if problems:
            raise SystemExit("not written: fix the problem quarters first")
        write(all_metrics)
    else:
        print("dry run only, rerun with --write to store")


if __name__ == "__main__":
    main()