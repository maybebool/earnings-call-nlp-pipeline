"""Diagnostic: show how a quarterly report encodes its key figures.

Usage:
        python src/tools/ubs_inspect_report.py --quarter 2Q23
"""
import argparse
import warnings

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from config import select_filings

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

PARSERS = ("lxml", "html.parser", "lxml-xml")
ANCHORS = ("Our key figures", "Total revenues", "Cost / income", "Risk-weighted")


def main() -> None:
    """
    Main entry point for processing financial data filings with optional geometry
    and XBRL analysis. This function orchestrates multiple tasks including reading
    filing data, extracting raw details, and analyzing specific dimensions like rows,
    columns, and XBRL facts based on the arguments provided.
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--quarter", required=True)
    ap.add_argument("--parser", default=None, help="inspect one parser in detail")
    ap.add_argument("--rows", type=int, default=0,
                    help="dump the geometry of this many key-figure rows")
    ap.add_argument("--columns", type=int, default=0,
                    help="dump left/width/right geometry of this many key-figure rows")
    ap.add_argument("--xbrl", action="store_true",
                    help="show the inline XBRL facts around the key figures")
    ap.add_argument("--raw", type=int, default=0,
                    help="print this many raw characters around the anchors")
    args = ap.parse_args()

    filing = select_filings(args.quarter, doc_type="report")[0]
    raw = filing.raw_path.read_bytes()
    print(f"{filing.quarter}  {filing.raw_path}  {len(raw):,} bytes")
    print(f"first 200 bytes: {raw[:200]!r}\n")

    if args.raw:
        source = raw.decode("utf-8", "replace")
        lowered = source.lower()
        print(f"ix:nonfraction tags: {lowered.count('<ix:nonfraction')}, "
              f"ix:nonnumeric: {lowered.count('<ix:nonnumeric')}, "
              f"xbrli:context: {lowered.count('<xbrli:context')}")
        for anchor in ("Our key figures", "Total revenues"):
            at = source.find(anchor)
            print(f"\n===== raw source around {anchor!r} (offset {at}) =====")
            if at < 0:
                continue
            print(source[max(0, at - args.raw // 4): at + args.raw])
        return
    if args.rows:
        import re as _re
        soup = BeautifulSoup(raw, "lxml")
        coord = _re.compile(r"(left|top)\s*:\s*(-?[\d.]+)px")

        def geometry(node):
            """
            Calculate the geometry of a given node by determining its relative 'left'
            and 'top' position based on its parent's styles.

            This function iterates through the node's parent elements. It evaluates
            the styles for each parent to extract the values for the 'left' and 'top'
            position. As soon as both 'left' and 'top' are identified, it short-circuits
            further processing. If no such values are found, it returns None for the
            respective axis.
            """
            box = {}
            for parent in node.parents:
                for axis, value in coord.findall(parent.get("style", "") or ""):
                    box.setdefault(axis, float(value))
                if "left" in box and "top" in box:
                    break
            return box.get("left"), box.get("top")

        def tagged(node):
            return any("nonfraction" in (p.name or "").lower() for p in node.parents)

        nodes = []
        for s in soup.find_all(string=True):
            stripped = " ".join(s.split())
            if not stripped:
                continue
            left, top = geometry(s)
            if left is None or top is None:
                continue
            nodes.append({"text": stripped, "left": left, "top": top, "ix": tagged(s)})

        start = next((i for i, n in enumerate(nodes) if n["text"] == "Total revenues"), None)
        print(f"{len(nodes)} positioned text nodes, 'Total revenues' at index {start}\n")
        if start is None:
            return
        print(f"{'left':>8} {'top':>9}  ix   text")
        for n in nodes[start:start + args.rows * 8]:
            print(f"{n['left']:>8.1f} {n['top']:>9.1f}  {'Y' if n['ix'] else '.'}    {n['text'][:55]}")
        return

    if args.columns:
        import re as _re
        soup = BeautifulSoup(raw, "lxml")
        prop = _re.compile(r"(left|top|width)\s*:\s*(-?[\d.]+)px")

        def geometry(node):
            box = {}
            for parent in node.parents:
                for axis, value in prop.findall(parent.get("style", "") or ""):
                    box.setdefault(axis, float(value))
                if "left" in box and "top" in box:
                    break
            return box

        nodes = []
        for s in soup.find_all(string=True):
            stripped = " ".join(s.split())
            if not stripped:
                continue
            box = geometry(s)
            if "left" in box and "top" in box:
                nodes.append({"text": stripped, **box})

        start = next((i for i, n in enumerate(nodes)
                      if n["text"] == "Our key figures"), None)
        print(f"anchor 'Our key figures' at index {start}, "
              f"{sum(1 for n in nodes if 'width' in n)} of {len(nodes)} nodes have a width\n")
        if start is None:
            return

        block, top = nodes[start + 1:], None
        printed = 0
        for n in block:
            if top is None or abs(n["top"] - top) > 4:
                if printed >= args.columns:
                    break
                print()
                printed += 1
                top = n["top"]
            width = n.get("width")
            right = f"{n['left'] + width:8.1f}" if width is not None else "       ?"
            width_txt = f"{width:7.1f}" if width is not None else "      ?"
            print(f"  left {n['left']:>7.1f} width {width_txt} right {right}  {n['text'][:42]}")
        return

    if args.xbrl:
        soup = BeautifulSoup(raw, "lxml-xml")
        facts = soup.find_all(lambda t: t.name == "nonFraction")
        contexts = {}
        for context in soup.find_all(lambda t: t.name == "context"):
            period = context.find(lambda t: t.name == "period")
            if period is None:
                continue
            parts = {c.name: c.get_text(strip=True) for c in period.find_all(True)}
            contexts[context.get("id")] = parts

        print(f"{len(facts)} nonFraction facts, {len(contexts)} contexts with a period")
        sample = list(contexts.items())[:3]
        for cid, parts in sample:
            print(f"  context {cid}: {parts}")

        print("\n--- first 20 facts whose text is a number ---")
        shown = 0
        for fact in facts:
            txt = " ".join(fact.get_text().split())
            if not txt:
                continue
            ref = fact.get("contextRef")
            period = contexts.get(ref, {})
            print(f"  {txt:>14}  name={fact.get('name')}  scale={fact.get('scale')} "
                  f"sign={fact.get('sign')}  period={period}")
            shown += 1
            if shown >= 20:
                break

        node = next((s for s in soup.find_all(string=True)
                     if " ".join(s.split()) == "Total revenues"), None)
        if node is not None:
            print("\n--- ancestors of the first 'Total revenues' text node ---")
            print(" < ".join(p.name for p in node.parents if p.name)[:200])
        return

    for parser in PARSERS:
        soup = BeautifulSoup(raw, parser)
        tables = soup.find_all("table")
        rows = sum(len(t.find_all("tr")) for t in tables)
        hits = {a: len(soup.find_all(string=lambda s, a=a: a in s)) for a in ANCHORS}
        print(f"{parser:<12} tables {len(tables):>5}  tr {rows:>6}  text hits {hits}")

    parser = args.parser or PARSERS[0]
    soup = BeautifulSoup(raw, parser)
    node = next(iter(soup.find_all(string=lambda s: "Total revenues" in s)), None)
    if node is None:
        print("\n'Total revenues' not found as a text node")
        return

    print(f"\n--- ancestors of the first 'Total revenues' ({parser}) ---")
    chain = [p.name for p in node.parents][:12]
    print(" < ".join(chain))

    print("\n--- 40 text nodes from there, with their parent tag ---")
    seen = 0
    for s in node.parent.next_elements if node.parent else []:
        if getattr(s, "name", None) is not None:
            continue
        stripped = " ".join(s.split())
        if not stripped:
            continue
        print(f"{s.parent.name:<6} {stripped[:60]}")
        seen += 1
        if seen >= 40:
            break


if __name__ == "__main__":
    main()