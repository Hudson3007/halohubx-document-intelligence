#!/usr/bin/env python3
"""
Interactive helper to label PDFs into benchmark/gold.json.

benchmark.py compares extraction output against this gold set, so the numbers
it produces are only as trustworthy as the labels. Label each document by
*reading the PDF*, not by copying the AI's answer — otherwise you're measuring
self-consistency, not accuracy.

Usage
-----
    python label_gold.py                        # label all unscored PDFs in benchmark/pdfs
    python label_gold.py --pdfs some/other/pdfs # label a different folder
    python label_gold.py --gold benchmark/gold.json --resume   # continue a partial set

For each PDF it prompts for the first invoice's key fields (vendor name/GSTIN,
invoice number/date, total amount) and appends them to gold.json. Line items
are optional (press Enter to skip), since they add time — start with the core
fields and add items when you need the fine-grained chart.

Run `python benchmark.py --pdfs benchmark/pdfs --gold benchmark/gold.json` after
labelling a batch to regenerate the report and the site's accuracy chart.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_PDFS = ROOT / "benchmark" / "pdfs"
DEFAULT_GOLD = ROOT / "benchmark" / "gold.json"


def _load(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _prompt(label: str, current: str = "") -> str:
    hint = f" [{current}]" if current else ""
    value = input(f"  {label}{hint}: ").strip()
    return value or (current if value != "" else "")


def main() -> None:
    ap = argparse.ArgumentParser(description="Label benchmark PDFs into gold.json")
    ap.add_argument("--pdfs", default=str(DEFAULT_PDFS), help="Folder of PDFs to label")
    ap.add_argument("--gold", default=str(DEFAULT_GOLD), help="Path to gold.json (created/updated)")
    ap.add_argument("--resume", action="store_true", help="Keep existing labels and only add missing")
    args = ap.parse_args()

    pdf_dir = Path(args.pdfs)
    gold_path = Path(args.gold)
    pdfs = sorted(p for p in pdf_dir.iterdir() if p.suffix.lower() == ".pdf")
    if not pdfs:
        print(f"No PDFs found in {pdf_dir}")
        return

    if not args.resume:
        existing = {}
    else:
        existing = _load(gold_path)

    print(f"Label {len(pdfs)} PDF(s). Reading source values from each document:\n")
    updated = 0
    for pdf in pdfs:
        if pdf.name in existing and existing[pdf.name]:
            print(f"[skip] {pdf.name} already labelled")
            continue
        print(f"== {pdf.name} ==")
        vendor = {"name": _prompt("Vendor name"), "gstin": _prompt("GSTIN")}
        details = {"invoice_number": _prompt("Invoice number"), "date": _prompt("Date (YYYY-MM-DD)")}
        total = _prompt("Total amount")
        entry = {"vendor": vendor, "invoice_details": details}
        if total:
            try:
                entry["total_amount"] = {"value": float(total)}
            except ValueError:
                print(f"    (skipping unparseable total: {total!r})")
        existing[pdf.name] = entry
        updated += 1

    existing["_readme"] = (
        "Gold set for HaloHubX benchmark.py. Each key maps a PDF filename under "
        "benchmark/pdfs/ to ground-truth values a human verified by reading the "
        "source PDF (NOT the AI's output). benchmark.py only scores the FIRST "
        "invoice and only fields present here; omit a field to skip it, and add "
        "'line_items' to score line items (index-aligned)."
    )
    gold_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {gold_path} ({updated} newly labelled, {len(pdfs)} total PDFs).")
    print("Next:  python benchmark.py --pdfs benchmark/pdfs --gold benchmark/gold.json")


if __name__ == "__main__":
    main()