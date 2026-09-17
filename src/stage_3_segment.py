"""Split utterances into sentences.

Usage:
    python src/stage_3_segment.py # dry run over everything
    python src/stage_3_segment.py --bank JPM # dry run for one bank
    python src/stage_3_segment.py --bank JPM --write # write that bank's sentences

Uses syntok, which handles abbreviations, decimal numbers and currency
figures well -- the usual breaking points in finance text.

A write replaces the sentences of the calls in scope and nothing else. Every
model result hangs off `sentences` with `on delete cascade`, so from stage 5
onwards a careless rebuild would silently take the sentiments and topic
assignments with it. Limiting the scope with --bank keeps a rerun for one
bank from touching the other one.
"""
import argparse
import os
from collections import Counter

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from syntok import segmenter

from config import FIRMS

load_dotenv()

UTTERANCE_QUERY = text(
    """
    select u.id as id, u.body as body, f.ticker as bank
    from utterances u
    join calls c on c.id = u.call_id
    join firms f on f.id = c.firm_id
    where cast(:ticker as text) is null or f.ticker = :ticker
    order by u.id
    """
)

DELETE_QUERY = text(
    """
    delete from sentences s
    using utterances u, calls c, firms f
    where s.utterance_id = u.id
      and u.call_id = c.id
      and c.firm_id = f.id
      and (cast(:ticker as text) is null or f.ticker = :ticker)
    """
)


def get_engine():
    """
    Creates and returns a SQLAlchemy engine for connecting to a PostgreSQL database.

    This function dynamically constructs the database URL from environment variables
    and initializes a SQLAlchemy engine instance.
    """
    url = (
        f"postgresql+psycopg://{os.environ['POSTGRES_USER']}:"
        f"{os.environ['POSTGRES_PASSWORD']}@{os.environ['POSTGRES_HOST']}:"
        f"{os.environ['POSTGRES_PORT']}/{os.environ['POSTGRES_DB']}"
    )
    return create_engine(url)


def split_sentences(body: str) -> list[str]:
    sentences = []
    for paragraph in segmenter.process(body):
        for sentence in paragraph:
            s = "".join(token.spacing + token.value for token in sentence).strip()
            if s:
                sentences.append(s)
    return sentences


def report(utterances: list, rows: list[dict]) -> None:
    """
    Analyzes and prints statistical information about utterances and corresponding sentences, categorizing by
    bank and examining sentence lengths.
    """
    lengths = sorted(len(r["body"]) for r in rows)
    n = len(lengths)
    print(f"{len(utterances)} utterances -> {n} sentences")
    print(f"chars per sentence: min {lengths[0]}, median {lengths[n // 2]}, "
          f"max {lengths[-1]}")

    per_bank_utt = Counter(u.bank for u in utterances)
    per_bank_sent = Counter(r["bank"] for r in rows)
    print("\n--- per bank ---")
    for bank in sorted(per_bank_utt):
        utt, sent = per_bank_utt[bank], per_bank_sent[bank]
        print(f"{bank:<4} {utt:5,} utterances  {sent:6,} sentences  "
              f"{sent / utt:5.1f} per utterance")

    print("\n--- longest 5 sentences (check for missed splits) ---")
    for r in sorted(rows, key=lambda r: -len(r["body"]))[:5]:
        print(f"[{r['bank']}, {len(r['body'])} chars] {r['body'][:100]}")
    print("\n--- shortest 5 sentences (check for fragments) ---")
    for r in sorted(rows, key=lambda r: len(r["body"]))[:5]:
        print(f"[{r['bank']}, {len(r['body'])} chars] {r['body']}")


def main() -> None:
    """
    Parses command-line arguments, processes utterances from a database, and writes
    split sentences back to the database or outputs a dry-run summary.

    This script connects to a database engine, retrieves utterances based on the
    provided bank, and performs the following operations:
    - Queries utterances for a specified bank or all banks if no bank is provided.
    - Splits retrieved utterance text into sentences.
    - Generates a report summarizing the processing results.
    - Optionally writes the processed sentences to the database if the `--write`
      flag is specified.
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--bank", help=f"one bank: {', '.join(FIRMS)} (default: all)")
    ap.add_argument("--write", action="store_true",
                    help="write to the database (default: dry run)")
    args = ap.parse_args()

    if args.bank is not None and args.bank not in FIRMS:
        raise SystemExit(f"unknown bank {args.bank!r}, expected one of {tuple(FIRMS)}")
    scope = {"ticker": args.bank}

    engine = get_engine()
    with engine.connect() as conn:
        utterances = conn.execute(UTTERANCE_QUERY, scope).all()
    if not utterances:
        raise SystemExit(f"no utterances for bank={args.bank!r}")

    rows = [
        {"utterance_id": u.id, "seq": seq, "body": s, "bank": u.bank}
        for u in utterances
        for seq, s in enumerate(split_sentences(u.body), start=1)
    ]

    report(utterances, rows)

    if not args.write:
        print("\ndry run only -- rerun with --write to store")
        return

    with engine.begin() as conn:
        removed = conn.execute(DELETE_QUERY, scope).rowcount
        for row in rows:
            conn.execute(
                text(
                    """
                    insert into sentences (utterance_id, seq, body)
                    values (:utterance_id, :seq, :body)
                    """
                ),
                {k: v for k, v in row.items() if k != "bank"},
            )
    target = args.bank or "all banks"
    print(f"\n{target}: deleted {removed:,} sentences, wrote {len(rows):,}")


if __name__ == "__main__":
    main()