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
    order by f.ticker, c.quarter, u.seq
    """
)

METRIC_COLUMNS = {
    "bank": "Ticker of the bank (currently only UBS).",
    "quarter": "Reporting period in ISO form YYYY-Qn, the same key as in the text files.",
    "metric_name": "Short name of the figure, e.g. `pbt`, `cost_income_ratio`, `cet1_ratio`.",
    "value": "The figure as published, in the unit given in `unit`.",
    "unit": "`USD m`, `USD bn`, `USD` (per share), `percent` or `count`.",
    "basis": "`reported` as published, or `underlying` for the variant excluding "
             "negative goodwill, integration-related expenses and acquisition costs.",
}

SENTENCE_QUERY = text(
    """
    select
        f.ticker            as bank,
        c.quarter           as quarter,
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
    order by f.ticker, c.quarter, u.seq, s.seq
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
        c.call_date             as call_date,
        fi.accession_number     as accession_number,
        count(distinct u.id)    as utterances,
        count(s.id)             as sentences,
        (select count(*) from metrics m
          where m.firm_id = f.id and m.quarter = c.quarter) as metrics
    from calls c
    join firms f on f.id = c.firm_id
    join filings fi on fi.id = c.filing_id
    join utterances u on u.call_id = c.id
    left join sentences s on s.utterance_id = u.id
    group by f.id, f.ticker, c.quarter, c.call_date, fi.accession_number
    order by f.ticker, c.quarter
    """
)

UTTERANCE_COLUMNS = {
    "bank": "Ticker of the bank (currently only UBS).",
    "quarter": "Reporting period in ISO form YYYY-Qn. This is not the call date: "
               "the 2024-Q4 call, for example, took place in February 2025.",
    "call_date": "Date of the earnings call (YYYY-MM-DD).",
    "position_in_call": "Order of the statement within the call, starting at 1.",
    "section": "`prepared` for the prepared remarks, `qa` for the analyst Q&A.",
    "speaker_role": "`management`, `analyst`, `ir` (investor relations host) or `operator`.",
    "speaker_name": "Name of the speaker, spelling harmonised across quarters.",
    "speaker_institution": "Employer of the analyst, empty for UBS speakers.",
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


def group_by_document(rows: list[dict]) -> dict[tuple[str, str], list[dict]]:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        groups[(row["bank"], row["quarter"])].append(row)
    return groups


def column_table(columns: dict[str, str]) -> str:
    lines = ["| Column | Meaning |", "|---|---|"]
    lines += [f"| `{name}` | {meaning} |" for name, meaning in columns.items()]
    return "\n".join(lines)


def build_readme(release: str, summary: list[dict]) -> str:
    contents = ["| Bank | Quarter | Call date | EDGAR accession | Statements | Sentences | Metrics |",
                "|---|---|---|---|---|---|---|"]
    contents += [
        f"| {r['bank']} | {r['quarter']} | {r['call_date']} | {r['accession_number']} "
        f"| {r['utterances']:,} | {r['sentences']:,} | {r['metrics']:,} |"
        for r in summary
    ]
    totals = {k: sum(r[k] for r in summary) for k in ("utterances", "sentences", "metrics")}
    contents.append(f"| **Total** | | | | **{totals['utterances']:,}** "
                    f"| **{totals['sentences']:,}** | **{totals['metrics']:,}** |")
    metric_table = column_table(METRIC_COLUMNS)

    return f"""# Earnings call transcripts, release {release}

Text data for the Bank of England employer project. The data comes from the earnings call transcripts that UBS files with the SEC as Form 6-K (EDGAR). Each file contains the prepared remarks and the analyst Q&A of one call; cover page, slide headings, page numbers and the legal disclaimer at the end are removed.

## Rules

Analysis reads only these exports; nobody parses the original PDF or HTML documents. Always work with the newest release folder. Do not edit the files; report problems or wishes to Roman, they are fixed in the pipeline and appear in the next release.

## Files

`all_utterances.csv` and `all_sentences.csv` contain all quarters in one file each (`bank` and `quarter` are columns). The folder `UBS/` contains the same data split into one file per call, named `UBS_<quarter>_call_utterances.csv` and `UBS_<quarter>_call_sentences.csv`. "call" means the files include the prepared remarks as well as the Q&A; filter on `section` to keep only one part. `all_metrics.csv` holds the reported figures of all quarters and is not split, being small; join it to the text on `bank` and `quarter`.

{chr(10).join(contents)}

## Columns of the utterance files (one row per statement)

{column_table(UTTERANCE_COLUMNS)}

## Columns of the sentence files (one row per sentence)

{column_table(SENTENCE_COLUMNS)}

## Columns of all_metrics.csv (one row per figure and quarter)

{metric_table}

The figures come from the "Our key figures" table of the UBS Group AG quarterly report of that quarter, as originally published. Comparative and year-to-date columns are not included, and figures restated in a later report are not applied, so every number matches the quarter's own report. A figure that a quarter does not publish is absent rather than zero: UBS reports no effective tax rate or profit growth in the loss-making quarters, and negative goodwill arises only in 2023-Q2. The `underlying` basis likewise exists only in 2023-Q2. To read the figures as a table rather than join them, pivot in one line: `df.pivot_table(index="metric_name", columns="quarter", values="value")`.

Note that the Credit Suisse acquisition closed in 2023-Q2. Balance sheet and headcount figures jump between 2023-Q1 and 2023-Q2 for that reason, and the profit and return figures of 2023-Q2 are driven by USD 28.9bn of negative goodwill. A series across the whole window is therefore not comparable throughout.

## sentence_id versus sentence_number

`sentence_number` is the position of a sentence within its statement and starts at 1 again for every statement. `sentence_id` is a running ID from the database and identifies one sentence across the whole release, so it is the reference to report a specific sentence or to join model results back to the text. It is only stable within one release: when the pipeline is rebuilt, the IDs change. To compare sentences across releases, use the combination `bank`, `quarter`, `position_in_call` and `sentence_number`.

## Notes on the text

Editorial insertions from the transcripts are kept exactly as published, in square brackets, for example `[edit: 14 million]` (a correction of a spoken figure), `[indiscernible]` or clarifications such as `[assets]`. Remove them with the regular expression `\\[[^\\]]*\\]` if they disturb an analysis. Analyst names and institutions are harmonised across quarters (for example all spellings of Exane become `BNP Paribas Exane`); real moves between institutions are kept, so Amit Goel appears with Barclays in 2023 and with Mediobanca in 2024. Files are UTF-8 encoded and comma-separated, text fields are quoted where needed.
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
    for bank, quarter in sorted(utt_groups):
        stem = release_dir / bank / f"{bank}_{quarter}_call"
        write_csv(stem.with_name(f"{stem.name}_utterances.csv"), utt_groups[(bank, quarter)])
        write_csv(stem.with_name(f"{stem.name}_sentences.csv"), sent_groups[(bank, quarter)])

    readme = release_dir / "README.md"
    readme.write_text(build_readme(args.release, summary), encoding="utf-8")
    print(f"wrote {readme}")


if __name__ == "__main__":
    main()