"""One-off probe: show how the JPMorgan transcript PDFs come out as text.

Not part of the pipeline. It prints the raw repr of the first lines of a page
so that whitespace, separator lines and word-splitting artefacts are visible,
which is what the parser has to cope with.

Usage:
    python src/probe_pdf.py --bank JPM --quarter 3Q23
    python src/probe_pdf.py --bank JPM --quarter 4Q24
    python src/probe_pdf.py --bank JPM --quarter 2Q23 --call-type event
"""
import argparse

from filings_config import select_filings

HEAD_LINES = 45
QA_LINES = 60


def show(title: str, lines: list[str]) -> None:
    print(f"\n----- {title} ({len(lines)} lines) -----")
    for i, line in enumerate(lines):
        print(f"{i:3d} {line!r}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bank", default="JPM")
    ap.add_argument("--quarter", required=True)
    ap.add_argument("--call-type", default="earnings")
    args = ap.parse_args()

    try:
        import pdfplumber
    except ImportError:
        raise SystemExit("pdfplumber is missing, install it with: pip install pdfplumber")

    filing = select_filings(args.quarter, "transcript", args.bank, args.call_type)[0]
    print(f"{filing.firm} {filing.quarter} {filing.call_type}  {filing.raw_path}")

    with pdfplumber.open(filing.raw_path) as pdf:
        print(f"pages: {len(pdf.pages)}")

        page_texts = [(p.extract_text() or "") for p in pdf.pages]

        # Page 1: title block, section header, first speaker.
        show("page 1", page_texts[0].split("\n")[:HEAD_LINES])

        # The first page that carries the Q&A header, plus the one after it:
        # that is where the speaker blocks with the Q and A markers start.
        qa_index = next(
            (i for i, t in enumerate(page_texts) if "QUESTION AND ANSWER SECTION" in t),
            None,
        )
        if qa_index is None:
            print("\nQUESTION AND ANSWER SECTION not found on any page")
        else:
            print(f"\nQ&A header on page index {qa_index}")
            qa_lines = page_texts[qa_index].split("\n")
            start = next(i for i, l in enumerate(qa_lines)
                         if "QUESTION AND ANSWER SECTION" in l)
            show(f"page {qa_index}, from the Q&A header", qa_lines[start:start + QA_LINES])
            if qa_index + 1 < len(page_texts):
                show(f"page {qa_index + 1}, top",
                     page_texts[qa_index + 1].split("\n")[:30])

        # Last page: where the transcript ends and the disclaimer begins.
        show("last page", page_texts[-1].split("\n")[-25:])

        # Words split across a line break show up as glued or spaced pairs.
        joined = "\n".join(page_texts)
        print(f"\ntotal characters: {len(joined):,}")
        for probe in ("year-on", "through-the", "quarter-on", "- ", " -"):
            print(f"occurrences of {probe!r}: {joined.count(probe)}")


if __name__ == "__main__":
    main()