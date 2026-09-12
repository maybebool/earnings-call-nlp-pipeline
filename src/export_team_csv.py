"""Stage 8: export a dated release of utterance- and sentence-level CSVs.

Layout of one release (the folder is copied as a whole to the shared Drive):

    exports/<release-date>/
        README.md
        all_utterances.csv
        all_sentences.csv
        all_metrics.csv
        UBS/
            UBS_2023-Q1_call_utterances.csv
            UBS_2023-Q1_call_sentences.csv
            ...
        JPM/
            JPM_2023-Q2_call_utterances.csv
            JPM_2023-Q2_event_call_utterances.csv
            ...

A quarter can hold more than one call, so the file name of anything other
than the quarterly earnings call carries its call type. The earnings call
names stay as they were.

Usage:
    python src/export_team_csv.py                        # release named after today
    python src/export_team_csv.py --release 2026-09-12   # explicit release date
    python src/export_team_csv.py --force                # rebuild an existing release
"""
import argparse
import csv
import os
import shutil
from collections import defaultdict
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

EXPORT_ROOT = Path("exports")


def get_engine():
    url = (
        f"postgresql+psycopg://{os.environ['POSTGRES_USER']}:"
        f"{os.environ['POSTGRES_PASSWORD']}@{os.environ['POSTGRES_HOST']}:"
        f"{os.environ['POSTGRES_PORT']}/{os.environ['POSTGRES_DB']}"
    )
    return create_engine(url)


UTTERANCE_QUERY = text(
    """
    select
        f.ticker            as bank,
        c.quarter           as quarter,
        c.call_type         as call_type,
        c.call_date         as call_date,
        u.seq               as position_in_call,
        u.section           as section,
        u.speaker_role      as speaker_role,
        u.speaker_name      as speaker_name,
        coalesce(u.speaker_org, '') as speaker_institution,
        length(u.body)      as text_length,
        u.body              as text
    from utterances u
    join calls c on c.id = u.call_id
    join firms f on f.id = c.firm_id
    order by f.ticker, c.quarter, c.call_date, u.seq
    """
)

SENTENCE_QUERY = text(
    """
    select
        f.ticker            as bank,
        c.quarter           as quarter,
        c.call_type         as call_type,
        c.call_date         as call_date,
        u.seq               as position_in_call,
        u.section           as section,
        u.speaker_role      as speaker_role,
        u.speaker_name      as speaker_name,
        coalesce(u.speaker_org, '') as speaker_institution,
        s.id                as sentence_id,
        s.seq               as sentence_number,
        s.body              as sentence
    from sentences s
    join utterances u on u.id = s.utterance_id
    join calls c on c.id = u.call_id
    join firms f on f.id = c.firm_id
    order by f.ticker, c.quarter, c.call_date, u.seq, s.seq
    """
)

METRIC_QUERY = text(
    """
    select
        f.ticker            as bank,
        m.quarter           as quarter,
        m.metric_name       as metric_name,
        m.value             as value,
        m.unit              as unit,
        m.basis             as basis
    from metrics m
    join firms f on f.id = m.firm_id
    order by f.ticker, m.quarter, m.metric_name, m.basis
    """
)

SUMMARY_QUERY = text(
    """
    select
        f.ticker                as bank,
        c.quarter               as quarter,
        c.call_type             as call_type,
        c.call_date             as call_date,
        coalesce(fi.accession_number, 'published (' || fi.source || ')')
                                as source_reference,
        count(distinct u.id)    as utterances,
        count(s.id)             as sentences,
        case when c.call_type = 'earnings' then
            (select count(*) from metrics m
              where m.firm_id = f.id and m.quarter = c.quarter)
        else 0 end              as metrics
    from calls c
    join firms f on f.id = c.firm_id
    join filings fi on fi.id = c.filing_id
    join utterances u on u.call_id = c.id
    left join sentences s on s.utterance_id = u.id
    group by f.id, f.ticker, c.quarter, c.call_type, c.call_date,
             fi.accession_number, fi.source
    order by f.ticker, c.quarter, c.call_date
    """
)

METRIC_COLUMNS = {
    "bank": "`UBS` or `JPM`.",
    "quarter": "Reporting period in ISO form YYYY-Qn, the same key as in the text files.",
    "metric_name": "Short name of the figure, e.g. `total_revenues`, `cet1_ratio`, "
                   "`personnel_fte`. Names shared by both banks mean the same thing.",
    "value": "The figure as published, in the unit given in `unit`.",
    "unit": "`USD m`, `USD bn`, `USD` (per share), `percent`, `count` or `count m`.",
    "basis": "`reported` as published. `underlying` (UBS only) is the variant "
             "excluding negative goodwill, integration-related expenses and "
             "acquisition costs. `managed` (JPM only) adds fully taxable-equivalent "
             "adjustments so that tax-exempt income is comparable with taxable income.",
}

UTTERANCE_COLUMNS = {
    "bank": "`UBS` or `JPM`.",
    "quarter": "Reporting period in ISO form YYYY-Qn. This is not the call date: "
               "the UBS 2024-Q4 call, for example, took place in February 2025.",
    "call_type": "`earnings` for the quarterly call, `event` for a call outside the "
                 "quarterly rhythm. There is one event call: JPMorgan on the First "
                 "Republic acquisition, 1 May 2023, which sits in 2023-Q2 next to "
                 "that quarter's earnings call.",
    "call_date": "Date of the call (YYYY-MM-DD).",
    "position_in_call": "Order of the statement within the call, starting at 1.",
    "section": "`prepared` for the prepared remarks, `qa` for the analyst Q&A.",
    "speaker_role": "`management`, `analyst`, `ir` (investor relations host) or `operator`.",
    "speaker_name": "Name of the speaker, spelling harmonised across quarters.",
    "speaker_institution": "Employer of the analyst, empty for the bank's own speakers.",
    "text_length": "Number of characters in `text`.",
    "text": "Full wording of the statement.",
}
SENTENCE_COLUMNS = {
    **{k: v for k, v in UTTERANCE_COLUMNS.items() if k not in ("text_length", "text")},
    "sentence_id": "Database ID of the sentence, the trace-back reference (see below).",
    "sentence_number": "Position of the sentence within its statement, restarts at 1 "
                       "for every statement.",
    "sentence": "Wording of the sentence.",
}


def fetch(query) -> list[dict]:
    engine = get_engine()
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(query).mappings().all()]


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {path} ({len(rows):,} rows)")


def document_stem(bank: str, quarter: str, call_type: str) -> str:
    """File name stem of one call; the type is named only when it is not earnings."""
    middle = "call" if call_type == "earnings" else f"{call_type}_call"
    return f"{bank}_{quarter}_{middle}"


def group_by_document(rows: list[dict]) -> dict[tuple[str, str, str], list[dict]]:
    groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in rows:
        groups[(row["bank"], row["quarter"], row["call_type"])].append(row)
    return groups


def column_table(columns: dict[str, str]) -> str:
    lines = ["| Column | Meaning |", "|---|---|"]
    lines += [f"| `{name}` | {meaning} |" for name, meaning in columns.items()]
    return "\n".join(lines)


def build_readme(release: str, summary: list[dict]) -> str:
    contents = ["| Bank | Quarter | Type | Call date | Source reference | Statements "
                "| Sentences | Metrics |",
                "|---|---|---|---|---|---|---|---|"]
    contents += [
        f"| {r['bank']} | {r['quarter']} | {r['call_type']} | {r['call_date']} "
        f"| {r['source_reference']} | {r['utterances']:,} | {r['sentences']:,} "
        f"| {r['metrics']:,} |"
        for r in summary
    ]
    totals = {k: sum(r[k] for r in summary) for k in ("utterances", "sentences", "metrics")}
    contents.append(f"| **Total** | | | | | **{totals['utterances']:,}** "
                    f"| **{totals['sentences']:,}** | **{totals['metrics']:,}** |")

    return f"""# Earnings call transcripts, release {release}

Text data for the Bank of England employer project. Two banks, each with eight quarters from 2023-Q1 to 2024-Q4, plus one event call. Every file contains the prepared remarks and the analyst Q&A of one call; cover page, section headings, page numbers and the legal disclaimer at the end are removed.

The two banks are in the dataset because each of them absorbed a failed competitor in 2023. UBS and Credit Suisse was a transformative merger of two globally systemic banks. JPMorgan and First Republic was an opportunistic bolt-on out of an FDIC receivership. The pair is therefore not symmetric, and that is the point: JPMorgan works as a control case for how much of the signal at UBS really comes from the merger rather than from the sector as a whole.

## Rules

Analysis reads only these exports; nobody goes back to the original PDF or HTML documents. Always work with the newest release folder. Do not edit the files; report problems or wishes to Roman, they are fixed in the pipeline and appear in the next release.

## Where the text comes from

UBS files its transcripts with the SEC as Form 6-K, so those documents carry an EDGAR accession number. JPMorgan is a domestic issuer and does not file its calls at all; its official verbatim transcripts are published as PDFs on the company's investor relations site. Both are primary sources from the issuer itself. Nothing in this release comes from a third-party transcript vendor, and nothing from such a vendor may be added to it.

## Files

`all_utterances.csv` and `all_sentences.csv` contain everything in one file each, with `bank`, `quarter` and `call_type` as columns. The event call is included; filter it out with `call_type == "earnings"` if a comparison needs one call per quarter.

The folders `UBS/` and `JPM/` hold the same data split into one file per call. The names follow `<bank>_<quarter>_call_utterances.csv` and `<bank>_<quarter>_call_sentences.csv`; the event call carries its type, so `JPM_2023-Q2_event_call_utterances.csv`. "call" means the file includes the prepared remarks as well as the Q&A; filter on `section` to keep only one part.

`all_metrics.csv` holds the reported figures of all quarters and is not split, being small; join it to the text on `bank` and `quarter`.

{chr(10).join(contents)}

## Columns of the utterance files (one row per statement)

{column_table(UTTERANCE_COLUMNS)}

## Columns of the sentence files (one row per sentence)

{column_table(SENTENCE_COLUMNS)}

## Columns of all_metrics.csv (one row per figure, quarter and basis)

{column_table(METRIC_COLUMNS)}

The UBS figures come from the "Our key figures" table of the UBS Group AG quarterly report, the JPMorgan figures from the Consolidated Financial Highlights of the Earnings Release Financial Supplement, Exhibit 99.2 of the quarterly earnings 8-K. Both are taken as originally published. Comparative and year-to-date columns are not included, and figures restated in a later report are not applied, so every number matches the quarter's own report.

A figure a quarter does not publish is absent rather than zero. UBS reports no effective tax rate or profit growth in the loss-making quarters, negative goodwill arises only in 2023-Q2, and the `underlying` basis exists only there. JPMorgan publishes no cost/income ratio in this table; it can be derived from `operating_expenses` over `total_revenues`, but then it is a derived figure and should be labelled as one.

To read the figures as a table rather than join them, pivot in one line: `df.pivot_table(index="metric_name", columns=["bank", "quarter"], values="value")`.

## What is not comparable between the two banks

**The structural break has very different weight.** Credit Suisse was roughly 45 percent of UBS. Between 2023-Q1 and 2023-Q2 the UBS balance sheet jumps from 1.05 to 1.68 trillion and headcount from 73,814 to 119,100, and the profit and return figures of that quarter are driven by USD 28.9bn of negative goodwill. First Republic was roughly 5 percent of JPMorgan; the corresponding jump is from 3.74 to 3.87 trillion and from 296,877 to 300,066. A series across the whole window is not comparable throughout for UBS, and largely is for JPMorgan.

**JPMorgan has its own outlier for another reason.** The 2024-Q2 profit of USD 18.1bn and EPS of 6.12 are lifted by a one-off gain on a Visa share exchange, not by operations.

**Statements are cut differently.** A UBS utterance holds 12.2 sentences on average, a JPMorgan one 5.9. The JPMorgan prepared remarks are two to four statements for the whole call, because the document does not break the CFO's presentation into sections, while the UBS transcripts break it by slide. Anything computed per utterance is therefore not comparable between the banks. Per sentence it is.

**The management side is not equally staffed.** UBS has two speakers in every quarter. At JPMorgan the 2024-Q2 call has only the CFO, and in the 2024-Q1 call the CEO leaves midway through the Q&A.

**First Republic is separately disclosed only from 2023-Q2 to 2024-Q1.** From 2024-Q2 the prior-year period contains the acquisition anyway, so the firm stopped reporting figures excluding it. Segment figures have a second break: JPMorgan merged its Corporate & Investment Bank with its Commercial Bank in 2024-Q1 and restated the prior periods.

## sentence_id versus sentence_number

`sentence_number` is the position of a sentence within its statement and starts at 1 again for every statement. `sentence_id` is a running ID from the database and identifies one sentence across the whole release, so it is the reference to report a specific sentence or to join model results back to the text. It is only stable within one release: when the pipeline is rebuilt, the IDs change. To compare sentences across releases, use the combination `bank`, `quarter`, `call_type`, `position_in_call` and `sentence_number`.

## Notes on the text

Editorial insertions are kept exactly as published, in square brackets, for example `[edit: 14 million]` (a correction of a spoken figure), `[indiscernible]` or clarifications such as `[assets]`. They occur in the UBS transcripts; the JPMorgan ones carry none. Remove them with the regular expression `\\[[^\\]]*\\]` if they disturb an analysis.

Analyst names and institutions are harmonised within each bank, so that one analyst and one house carry the same spelling everywhere. For UBS all spellings of Exane become `BNP Paribas Exane`; for JPMorgan the legal suffixes are dropped, so `Wolfe Research LLC` becomes `Wolfe Research`. Real moves between institutions are kept: Amit Goel appears with Barclays in 2023 and with Mediobanca in 2024, John McDonald with Autonomous Research and later with Truist Securities. The same analyst may appear in both banks' calls, Erika Najarian of UBS covers JPMorgan throughout the window.

Files are UTF-8 encoded and comma-separated, text fields are quoted where needed.
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--release", default=date.today().isoformat(),
                    help="release folder name, default: today (YYYY-MM-DD)")
    ap.add_argument("--force", action="store_true",
                    help="rebuild the release folder if it already exists")
    args = ap.parse_args()

    release_dir = EXPORT_ROOT / args.release
    if release_dir.exists():
        if not args.force:
            raise SystemExit(f"{release_dir} exists; use --force or another --release")
        shutil.rmtree(release_dir)

    utterances = fetch(UTTERANCE_QUERY)
    sentences = fetch(SENTENCE_QUERY)
    metrics = fetch(METRIC_QUERY)
    summary = fetch(SUMMARY_QUERY)
    if not utterances or not sentences:
        raise SystemExit("nothing to export: utterances or sentences are empty")
    if not metrics:
        raise SystemExit("nothing to export: metrics are empty, run extract_metrics --write")

    write_csv(release_dir / "all_utterances.csv", utterances)
    write_csv(release_dir / "all_sentences.csv", sentences)
    write_csv(release_dir / "all_metrics.csv", metrics)

    utt_groups = group_by_document(utterances)
    sent_groups = group_by_document(sentences)
    for key in sorted(utt_groups):
        bank, quarter, call_type = key
        stem = document_stem(bank, quarter, call_type)
        write_csv(release_dir / bank / f"{stem}_utterances.csv", utt_groups[key])
        write_csv(release_dir / bank / f"{stem}_sentences.csv", sent_groups[key])

    readme = release_dir / "README.md"
    readme.write_text(build_readme(args.release, summary), encoding="utf-8")
    print(f"wrote {readme}")


if __name__ == "__main__":
    main()