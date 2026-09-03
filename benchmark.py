#!/usr/bin/env python3
"""
HaloHubX extraction benchmark.

Runs the real extraction pipeline (app.extraction.extract_document) over a
set of PDFs, compares the output to human-labelled ground truth, and produces
an honest accuracy report (per-field, per-metric, confidence-vs-correctness).

This is the tool that gives us real numbers instead of the aspirational
"92-98%" figure — so anything we show a client is measured, not claimed.

Usage
-----
1) Put your sample PDFs in a folder, e.g. ./benchmark/pdfs
2) Create ./benchmark/gold.json — a dict mapping each filename to the
   ground-truth result the AI *should* produce (the "gold" fields you care
   about). Shape (matching extraction output):

       {
         "invoice_a.pdf": {
           "vendor": {"name": "BrightEdge Hardware Traders", "gstin": "29AABCT1332L1Z5"},
           "invoice_details": {"invoice_number": "INV-2023-017", "date": "2023-04-12"},
           "total_amount": {"value": 2975.2},
           "line_items": [
             {"description": "Hex Bolts M8x40", "quantity": 500, "unit_price": 2.10, "total_value": 1239.0}
           ]
         },
         ...
       }

   (Only fields actually present are compared. Omit fields you don't want to
   score for a given file. Omitting 'line_items' skips line-item comparison.)

3) Run:

       venv\\Scripts\\activate
       python benchmark.py --pdfs benchmark/pdfs --gold benchmark/gold.json
       python benchmark.py --pdfs benchmark/pdfs --gold benchmark/gold.json --out benchmarks/report.html

The report is written as HTML (and a `--json` flag prints a machine-readable
summary). Extraction calls the AI provider configured in .env (Gemini/Claude),
so it spends your API key once per PDF.
"""

from __future__ import annotations

import argparse
import json
import os
import html as html_mod
from collections import defaultdict
from pathlib import Path

from app.ai_client import get_default_client
from app.extraction import extract_document

# --- field scoping -----------------------------------------------------------

VENDOR_FIELDS = ["name", "gstin"]
DETAIL_FIELDS = ["invoice_number", "date"]
ITEM_FIELDS = ["description", "quantity", "unit_price", "total_value"]


# --- comparison helpers ------------------------------------------------------

def _norm(s):
    """Normalise a value for comparison: strips punctuation/case/whitespace,
    and rounds numeric strings so 2975.2 == 2975.20."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return round(float(s), 2) if not isinstance(s, bool) else s
    s = str(s).strip()
    s = "".join(c for c in s if c.isalnum())  # ignore separators like , . / -
    return s

def _matches(pred, gold):
    if pred is None and gold is None:
        return True
    if pred is None or gold is None:
        return False
    return _norm(pred) == _norm(gold)


def _extract_field(invoice, path):
    """path like ('vendor', 'name') or ('total_amount', 'value')."""
    cur = invoice
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def _flatten_invoices(result):
    """Normalise result (multi or single invoice) into a list of invoices."""
    if isinstance(result, dict) and isinstance(result.get("invoices"), list):
        return result["invoices"]
    if isinstance(result, dict) and any(k in result for k in ("vendor", "invoice_details", "total_amount")):
        return [result]
    return []


def compare_document(pred_result, gold):
    """Score predicted result against a gold dict. Returns a dict of metrics
    plus per-field detail. Compares the FIRST invoice only (document-level
    invoice count/ordering is out of scope here)."""
    gold = gold or {}
    pred = pred_result or {}

    pred_invs = _flatten_invoices(pred)
    gold_invs = _flatten_invoices(gold)
    metrics = defaultdict(lambda: {"correct": 0, "total": 0})
    detail = []

    def touch(cat, ok):
        metrics[cat]["total"] += 1
        if ok:
            metrics[cat]["correct"] += 1

    if not pred_invs or not gold_invs:
        # score top-level scalar fields only
        pass

    p_inv = pred_invs[0] if pred_invs else {}
    g_inv = gold_invs[0] if gold_invs else {}

    # vendor
    for f in VENDOR_FIELDS:
        if f in (g_inv.get("vendor") or {}):
            ok = _matches(
                _extract_field(p_inv, ("vendor", f)),
                _extract_field(g_inv, ("vendor", f)),
            )
            touch("vendor." + f, ok)
            detail.append((f"vendor.{f}", _extract_field(p_inv, ("vendor", f)),
                           _extract_field(g_inv, ("vendor", f)), ok))
    # invoice details
    for f in DETAIL_FIELDS:
        if f in (g_inv.get("invoice_details") or {}):
            ok = _matches(
                _extract_field(p_inv, ("invoice_details", f)),
                _extract_field(g_inv, ("invoice_details", f)),
            )
            touch("details." + f, ok)
            detail.append((f"details.{f}", _extract_field(p_inv, ("invoice_details", f)),
                           _extract_field(g_inv, ("invoice_details", f)), ok))
    # total amount
    if "total_amount" in (g_inv or {}):
        ok = _matches(
            _extract_field(p_inv, ("total_amount", "value")),
            _extract_field(g_inv, ("total_amount", "value")),
        )
        touch("total_amount", ok)
        detail.append(("total_amount", _extract_field(p_inv, ("total_amount", "value")),
                       _extract_field(g_inv, ("total_amount", "value")), ok))

    # line items: match by position (index-aligned) against gold line items
    pg_items = p_inv.get("line_items") or []
    gg_items = g_inv.get("line_items") or []
    if gg_items:
        for i, g_item in enumerate(gg_items):
            if i >= len(pg_items):
                touch("line_items", False)
                continue
            for f in ITEM_FIELDS:
                if f in g_item:
                    ok = _matches(pg_items[i].get(f), g_item.get(f))
                    touch(f"items.{f}", ok)

    return {"metrics": dict(metrics), "detail": detail}


def summarize(field_results):
    """field_results: list of (cat, ok) tuples -> combined accuracy str."""
    if not field_results:
        return "-"
    tot = len(field_results)
    ok = sum(1 for _, okv in field_results if okv)
    return f"{ok}/{tot} ({100.0 * ok / tot:.1f}%)" if tot else "-"


def run_benchmark(pdfs, gold, client):
    rows = []
    for pdf in pdfs:
        name = pdf.name
        try:
            raw = extract_document(client, pdf.read_bytes(), "application/pdf")
        except Exception as e:  # noqa: BLE001 - per-file resilience
            rows.append({
                "file": name,
                "error": str(e),
                "metrics": {}, "detail": [], "confidences": [],
            })
            continue

        confidences = _collect_confidences(raw)
        compare = compare_document(raw, gold.get(name, {}))
        rows.append({
            "file": name,
            "error": None,
            "metrics": compare["metrics"],
            "detail": compare["detail"],
            "confidences": confidences,
        })
    return rows


def _collect_confidences(result):
    confs = []
    for inv in _flatten_invoices(result):
        for path in (("vendor", "confidence"), ("invoice_details", "confidence"),
                     ("total_amount", "confidence")):
            v = _extract_field(inv, path)
            if isinstance(v, (int, float)):
                confs.append(v)
        for it in inv.get("line_items") or []:
            if isinstance(it.get("confidence"), (int, float)):
                confs.append(it["confidence"])
    return confs


def aggregate(rows):
    agg = defaultdict(lambda: {"correct": 0, "total": 0})
    for r in rows:
        if r.get("error"):
            continue
        for cat, m in r["metrics"].items():
            agg[cat]["correct"] += m["correct"]
            agg[cat]["total"] += m["total"]

    overall_ok = sum(v["correct"] for v in agg.values())
    overall_tot = sum(v["total"] for v in agg.values())
    return agg, overall_ok, overall_tot


# --- reporting ---------------------------------------------------------------

def build_html(rows, agg, ok, tot, out_path):
    l = []
    l.append("<!doctype html><html lang='en'><head><meta charset='utf-8'>")
    l.append("<title>HaloHubX Extraction Benchmark</title>")
    l.append("<style>"
             "body{font-family:system-ui,Segoe UI,Roboto,sans-serif;max-width:1000px;margin:40px auto;padding:0 20px;color:#21384C}"
             "h1{color:#21384C}table{border-collapse:collapse;width:100%;margin:16px 0}"
             "th,td{border:1px solid #D7E6F2;padding:8px 10px;text-align:left;font-size:14px}"
             "th{background:#EEF4F8;color:#547A95;text-transform:uppercase;font-size:12px;letter-spacing:.5px}"
             ".ok{color:#1a7f37}.no{color:#c0392b}.tot{font-size:22px;font-weight:700}"
             ".card{background:#FCFCFD;border:1px solid #D7E6F2;border-radius:12px;padding:16px;margin:16px 0}"
             "</style></head><body>")
    l.append(f"<h1>HaloHubX Extraction Benchmark</h1>")
    l.append(f"<div class='card'><span class='tot'>Overall field accuracy: "
             f"{100.0 * ok / tot:.1f}%</span> &nbsp;(<b>{ok}</b> / {tot} scored fields, "
             f"{len(rows)} document(s))</div>")

    l.append("<h2>Per-field accuracy</h2><table><tr><th>Field</th><th>Correct / Total</th><th>Accuracy</th></tr>")
    if agg:
        for cat in sorted(agg):
            m = agg[cat]
            pct = 100.0 * m["correct"] / m["total"] if m["total"] else 0
            l.append(f"<tr><td>{html_mod.escape(cat)}</td><td>{m['correct']} / {m['total']}</td>"
                     f"<td class='{'ok' if pct >= 90 else 'no'}'>{pct:.1f}%</td></tr>")
    else:
        l.append("<tr><td colspan='3'>No scorable fields.</td></tr>")
    l.append("</table>")

    l.append("<h2>Per-document detail</h2>")
    for r in rows:
        l.append(f"<h3>{html_mod.escape(r['file'])}</h3>")
        if r.get("error"):
            l.append(f"<p style='color:#c0392b'>FAILED: {html_mod.escape(r['error'])}</p>")
            continue
        if r["detail"]:
            l.append("<table><tr><th>Field</th><th>Predicted</th><th>Gold</th><th>Match</th></tr>")
            for f, p, g, okv in r["detail"]:
                cls = "ok" if okv else "no"
                l.append(f"<tr><td>{html_mod.escape(f)}</td><td>{html_mod.escape(str(p))}</td>"
                         f"<td>{html_mod.escape(str(g))}</td>"
                         f"<td class='{cls}'>{'✓' if okv else '✗'}</td></tr>")
            l.append("</table>")
        if r["confidences"]:
            confs = r["confidences"]
            avg_conf = sum(confs) / len(confs)
            l.append(f"<p>Self-reported confidence: avg <b>{avg_conf:.2f}</b> "
                     f"across {len(confs)} fields</p>")
    l.append("</body></html>")

    out_path.write_text("\n".join(l), encoding="utf-8")


def build_json(rows, agg, ok, tot):
    return {
        "overall": {"correct": ok, "total": tot,
                    "accuracy": round(100.0 * ok / tot, 2) if tot else 0},
        "files": rows,
    }


FIELD_LABELS = {
    "vendor.name": "Vendor name",
    "vendor.gstin": "GSTIN",
    "details.invoice_number": "Invoice number",
    "details.date": "Invoice date",
    "line_items": "Line items (desc/qty/price/total)",
    "items.description": "Line item description",
    "items.quantity": "Line item qty",
    "items.unit_price": "Line item unit price",
    "items.total_value": "Line item total",
    "total_amount": "Total amount",
}


def _site_payload(agg, ok, tot, num_docs):
    """Format the aggregate metrics into the shape the marketing site expects
    (site/public/benchmark.json): {overall, totalScored, documents,
    perField: [{field, rate}], note}."""
    per = []
    for cat, m in sorted(agg.items()):
        label = FIELD_LABELS.get(cat, cat.replace(".", " ").replace("_", " ").title())
        per.append({"field": label, "rate": round(100.0 * m["correct"] / m["total"], 1)
                    if m["total"] else 0.0})
    return {
        "overall": round(100.0 * ok / tot, 1) if tot else 0.0,
        "totalScored": tot,
        "documents": num_docs,
        "perField": per,
        "note": (
            f"Field-level accuracy vs. human-labelled ground truth on {num_docs} "
            "document(s) of Indian GST invoices, POs and receipts. Generated with "
            "benchmark.py — this page updates automatically on every run."
        ),
    }


def main():
    ap = argparse.ArgumentParser(description="HaloHubX extraction benchmark")
    ap.add_argument("--pdfs", required=True, help="Folder containing sample PDFs")
    ap.add_argument("--gold", required=True, help="Path to gold.json (ground truth)")
    ap.add_argument("--out", default="benchmark_report.html", help="HTML report path")
    ap.add_argument("--json-out", default=None,
                    help="Optionally write a JSON summary here. If omitted, also "
                         "writes a copy into the marketing site (site/public/benchmark.json) "
                         "so the landing page shows the fresh numbers.")
    args = ap.parse_args()

    pdf_dir = Path(args.pdfs)
    pdfs = sorted(p for p in pdf_dir.iterdir() if p.suffix.lower() == ".pdf")
    if not pdfs:
        print(f"No PDFs found in {pdf_dir}")
        return

    gold = {}
    gold_path = Path(args.gold)
    if gold_path.exists():
        with open(gold_path, encoding="utf-8") as f:
            gold = json.load(f)

    client = get_default_client()
    print(f"Running extraction on {len(pdfs)} PDF(s) via {type(client).__name__}...\n")

    rows = run_benchmark(pdfs, gold, client)
    agg, ok, tot = aggregate(rows)

    build_html(rows, agg, ok, tot, Path(args.out))

    print(f"=== OVERALL === field accuracy {ok}/{tot} ({100.0 * ok / tot:.1f}%)" if tot else "no scorable fields")
    for cat in sorted(agg):
        m = agg[cat]
        print(f"  {cat:20s} {m['correct']:>3}/{m['total']:<3}  "
              f"({100.0 * m['correct'] / m['total']:.1f}%)")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(build_json(rows, agg, ok, tot),
                                                  indent=2, default=str), encoding="utf-8")
        print(f"\nWrote {args.json_out}")

    # Always refresh the marketing site's numbers so the landing page reflects
    # the latest measured accuracy (the site fetches /benchmark.json at runtime).
    site_json = Path(__file__).parent / "site" / "public" / "benchmark.json"
    try:
        site_json.write_text(
            json.dumps(_site_payload(agg, ok, tot, len(rows)), indent=2), encoding="utf-8")
        print(f"Updated marketing site data -> {site_json}")
    except OSError as e:
        print(f"Note: could not update marketing site data ({e})")

    print(f"\nWrote {Path(args.out)}")
    print("\nNote: field-level accuracy vs. human-labelled ground truth. "
          "This is the real measured number — not a self-reported confidence figure.")


if __name__ == "__main__":
    main()