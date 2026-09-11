"""Stage 3: split utterances into sentences.

Usage:
    python src/segment_sentences.py            # dry run: stats + samples, writes nothing
    python src/segment_sentences.py --write    # write sentences to the DB

Uses syntok, which handles abbreviations, decimal numbers and currency
figures well -- the usual breaking points in finance text. Re-runs replace
the sentences of every call present (utterance cascade keeps things clean).
"""
import argparse
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from syntok import segmenter

load_dotenv()


def get_engine():
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true",
                    help="write to the database (default: dry run)")
    args = ap.parse_args()

    engine = get_engine()
    with engine.connect() as conn:
        utterances = conn.execute(
            text("select id, body from utterances order by id")
        ).all()

    all_rows = []
    for utt_id, body in utterances:
        for seq, s in enumerate(split_sentences(body), start=1):
            all_rows.append({"utterance_id": utt_id, "seq": seq, "body": s})

    # dry-run report
    lengths = sorted(len(r["body"]) for r in all_rows)
    n = len(lengths)
    print(f"{len(utterances)} utterances -> {n} sentences")
    print(f"chars per sentence: min {lengths[0]}, median {lengths[n // 2]}, "
          f"max {lengths[-1]}")
    print("\n--- longest 5 sentences (check for missed splits) ---")
    for r in sorted(all_rows, key=lambda r: -len(r["body"]))[:5]:
        print(f"[{len(r['body'])} chars] {r['body'][:100]}")
    print("\n--- shortest 5 sentences (check for fragments) ---")
    for r in sorted(all_rows, key=lambda r: len(r["body"]))[:5]:
        print(f"[{len(r['body'])} chars] {r['body']}")

    if not args.write:
        print("\ndry run only -- rerun with --write to store")
        return

    with engine.begin() as conn:
        conn.execute(text("delete from sentences"))
        for row in all_rows:
            conn.execute(
                text(
                    """
                    insert into sentences (utterance_id, seq, body)
                    values (:utterance_id, :seq, :body)
                    """
                ),
                row,
            )
    print(f"\nwrote {n} sentences")


if __name__ == "__main__":
    main()