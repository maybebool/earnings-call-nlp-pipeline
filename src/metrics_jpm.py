"""Read the key figures of a JPMorgan earnings release financial supplement.

The document is Exhibit 99.2 of the quarterly earnings 8-K: ordinary HTML
with ordinary tables, unlike the UBS reports from 2Q23 onwards. Two tables
carry the figures, 'Consolidated Financial Highlights' and its continuation,
and both list five quarters side by side. The quarter the report is about is
always the first data column, so the value sits at index 1 of a data row,
right after the label.

Three things the layout does that the parser has to absorb:

  Currency and percent signs are cells of their own and appear in some rows
  but not others, so all symbol-only cells are dropped before indexing.

  Footnote markers such as (e) are cells of their own too. They always follow
  the value they belong to, so dropping them keeps index 1 correct.

  The same label occurs several times in one document. 'Total net revenue'
  appears under Reported Basis, under Managed Basis and again under J.P.
  Morgan Payments. A metric is therefore addressed by section and label, not
  by label alone.
"""
import re

from bs4 import BeautifulSoup

HIGHLIGHTS_RE = re.compile(r"CONSOLIDATED FINANCIAL HIGHLIGHTS", re.IGNORECASE)
CONTENTS_RE = re.compile(r"TABLE OF CONTENTS", re.IGNORECASE)
QUARTER_LABEL_RE = re.compile(r"^[1-4]Q\d{2}$")
FOOTNOTE_CELL_RE = re.compile(r"^\([a-z]\)$")
SYMBOL_CELLS = {"$", "%", "", "\u2014", "\u2013"}
NUMBER_RE = re.compile(r"^\(?-?[\d,]+(?:\.\d+)?\)?$")

# Footnote markers and quoted aliases inside a label: 'Pre-provision profit
# (a)', 'Return on common equity (“ROE”)'.
LABEL_NOISE_RE = re.compile(r"\s*\((?:[a-z]|\u201c[^\u201d]*\u201d)\)")

# A label that ends in a colon carries over to the bare labels below it, so
# that 'Net income: Basic' followed by 'Diluted' stays unambiguous against
# 'Average shares: Basic' followed by its own 'Diluted'.
CARRY_RE = re.compile(r"^(?P<prefix>.+):\s*(?P<rest>.*)$")

BASIS_SECTIONS = {
    "reported basis": "reported",
    "managed basis": "managed",
}

# Single-cell rows that are not section titles: the subtitle in parentheses,
# the '1Q23 Change' column caption, the running company name, and the group
# captions ending in a colon such as 'Loans:' and 'U.S. offices:'.
IGNORE_SINGLE_RE = re.compile(
    r"^(?:\(.*\)|.*:|(?:[1-4]Q\d{2}|\d{4}) Change|JPMORGAN CHASE & CO\.?)$"
)

# (section, label) -> (metric_name, unit). Sections and labels are normalised
# by `normalise`. Rows that are not listed here show up as unmapped in the dry
# run rather than being stored silently, the same as in the UBS branch.
# Names are kept identical to the UBS ones wherever the figure means the same
# thing, so that the two banks can be put side by side.
METRIC_NAMES: dict[tuple[str, str], tuple[str, str]] = {
    # --- selected income statement data, both bases -----------------------
    ("selected income statement data", "total net revenue"):
        ("total_revenues", "USD m"),
    ("selected income statement data", "total noninterest expense"):
        ("operating_expenses", "USD m"),
    ("selected income statement data", "pre-provision profit"):
        ("pre_provision_profit", "USD m"),
    ("selected income statement data", "provision for credit losses"):
        ("credit_loss_expense", "USD m"),
    ("selected income statement data", "net income"):
        ("net_profit", "USD m"),
    # --- earnings per share ----------------------------------------------
    ("earnings per share data", "net income: basic"): ("basic_eps", "USD"),
    ("earnings per share data", "net income: diluted"): ("diluted_eps", "USD"),
    ("earnings per share data", "average shares: basic"):
        ("average_shares_basic", "count m"),
    ("earnings per share data", "average shares: diluted"):
        ("average_shares_diluted", "count m"),
    # --- market and per share data ---------------------------------------
    ("market and per common share data", "market capitalization"):
        ("market_capitalization", "USD m"),
    ("market and per common share data", "common shares at period-end"):
        ("common_shares", "count m"),
    ("market and per common share data", "book value per share"):
        ("total_book_value_per_share", "USD"),
    ("market and per common share data", "tangible book value per share"):
        ("tangible_book_value_per_share", "USD"),
    ("market and per common share data", "cash dividends declared per share"):
        ("dividend_per_share", "USD"),
    # --- ratios -----------------------------------------------------------
    ("financial ratios", "return on common equity"):
        ("return_on_equity", "percent"),
    ("financial ratios", "return on tangible common equity"):
        ("return_on_tangible_equity", "percent"),
    ("financial ratios", "return on assets"): ("return_on_assets", "percent"),
    ("capital ratios", "common equity tier 1 capital ratio"):
        ("cet1_ratio", "percent"),
    ("capital ratios", "tier 1 capital ratio"): ("tier1_ratio", "percent"),
    ("capital ratios", "total capital ratio"): ("total_capital_ratio", "percent"),
    ("capital ratios", "tier 1 leverage ratio"):
        ("tier1_leverage_ratio", "percent"),
    ("capital ratios", "supplementary leverage ratio"): ("slr", "percent"),
    # --- balance sheet ----------------------------------------------------
    ("selected balance sheet data (period-end)", "total assets"):
        ("total_assets", "USD m"),
    ("selected balance sheet data (period-end)", "total loans"):
        ("total_loans", "USD m"),
    ("selected balance sheet data (period-end)", "total deposits"):
        ("total_deposits", "USD m"),
    ("selected balance sheet data (period-end)", "long-term debt"):
        ("long_term_debt", "USD m"),
    ("selected balance sheet data (period-end)", "common stockholders\u2019 equity"):
        ("equity_attributable_to_shareholders", "USD m"),
    ("selected balance sheet data (period-end)", "total stockholders\u2019 equity"):
        ("total_equity", "USD m"),
    ("selected balance sheet data (period-end)", "loans-to-deposits ratio"):
        ("loans_to_deposits_ratio", "percent"),
    ("selected balance sheet data (period-end)", "headcount"):
        ("personnel_fte", "count"),
    ("selected balance sheet data (period-end)", "employees"):
        ("personnel_fte", "count"),
}


def normalise(label: str) -> str:
    label = LABEL_NOISE_RE.sub("", " ".join(label.split()))
    return label.strip().strip(":").lower()


def parse_number(value: str) -> float | None:
    value = value.replace("\u2212", "-").replace("\u2013", "-").strip()
    if NUMBER_RE.fullmatch(value) is None:
        return None
    negative = value.startswith("(") and value.endswith(")")
    return (-1 if negative else 1) * float(value.strip("()").replace(",", ""))


def cell_texts(row) -> list[str]:
    """Row cells without the symbol-only and footnote-only ones."""
    out = [" ".join(c.get_text(" ").split()) for c in row.find_all(["td", "th"])]
    return [c for c in out
            if c not in SYMBOL_CELLS and FOOTNOTE_CELL_RE.fullmatch(c) is None]


def highlight_tables(soup) -> list:
    """The Consolidated Financial Highlights tables, without the contents page."""
    tables = []
    for table in soup.find_all("table"):
        text = table.get_text(" ", strip=True)
        if HIGHLIGHTS_RE.search(text) and not CONTENTS_RE.search(text):
            tables.append(table)
    return tables


def header_offset(rows: list[list[str]], quarter_label: str) -> int | None:
    """Index of the target quarter in the header row, or None if it is wrong.

    The report must lead with its own quarter. If a later column matched
    first, the wrong document or the wrong table would be read.
    """
    for cells in rows:
        quarters = [(i, c) for i, c in enumerate(cells) if QUARTER_LABEL_RE.fullmatch(c)]
        if not quarters:
            continue
        index, first = quarters[0]
        return index if first == quarter_label else None
    return None


def key_figures(html: bytes, quarter_label: str) -> tuple[list[dict], dict]:
    """Metrics of one supplement, plus diagnostics for the dry run."""
    soup = BeautifulSoup(html, "lxml")
    tables = highlight_tables(soup)
    meta = {"tables": len(tables), "rows": 0, "unmapped": [], "table_found": bool(tables)}

    metrics: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for table in tables:
        rows = [cell_texts(r) for r in table.find_all("tr")]
        rows = [r for r in rows if r]
        if header_offset(rows, quarter_label) is None:
            meta["unmapped"].append(("header", f"{quarter_label} is not the first column"))
            continue

        section = ""
        basis = "reported"
        carry = ""

        for cells in rows:
            if any(QUARTER_LABEL_RE.fullmatch(c) for c in cells):
                # Header row. In the first table its first cell also carries
                # the section title, in the continuation table it does not.
                if not QUARTER_LABEL_RE.fullmatch(cells[0]):
                    section = normalise(cells[0])
                    basis = "reported"
                    carry = ""
                continue

            if len(cells) == 1:
                heading = normalise(cells[0])
                if heading in BASIS_SECTIONS:
                    basis = BASIS_SECTIONS[heading]
                elif IGNORE_SINGLE_RE.match(cells[0].strip()):
                    pass
                else:
                    section = heading
                    basis = "reported"
                    carry = ""
                continue

            raw_label = cells[0].strip()
            match = CARRY_RE.match(raw_label)
            if match and match["rest"]:
                carry = normalise(match["prefix"])
                label = normalise(raw_label)
            elif carry and ":" not in raw_label:
                label = f"{carry}: {normalise(raw_label)}"
            else:
                label = normalise(raw_label)

            meta["rows"] += 1
            value = parse_number(cells[1]) if len(cells) > 1 else None
            mapped = METRIC_NAMES.get((section, label))

            if mapped is None:
                if value is not None:
                    meta["unmapped"].append((section, label))
                continue
            if value is None:
                continue

            name, unit = mapped
            if (name, basis) in seen:  # a repeated block, keep the first
                continue
            seen.add((name, basis))
            metrics.append({"metric_name": name, "value": value,
                            "unit": unit, "basis": basis})

    return metrics, meta
