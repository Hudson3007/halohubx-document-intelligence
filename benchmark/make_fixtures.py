#!/usr/bin/env python3
"""
Deterministic generator for the HaloHubX synthetic benchmark fixtures.

Every PDF is rendered from a small dict of ground-truth values below. Because
the fixture defs ARE what's printed on the page, gold.json (human-read ground
truth) is generated from the SAME source of truth — a legitimate, auditable
label (a human reading these pages sees exactly these values).

These are SYNTHETIC fixtures (fictional Indian companies/records) so no real
business data is involved, and the benchmark page therefore carries a
"synthetic set" caveat rather than being presented as real-world customer data.

Run from the repo root:

    python benchmark/make_fixtures.py            # writes benchmark/pdfs/*.pdf
    python benchmark/make_fixtures.py --gold     # ... and rewrites gold.json

Helper functions cover the block layout so the PDFs read like genuine Indian
commercial documents (tax-invoice tables, PO tables, receipt blocks).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parent
PDFS = ROOT / "pdfs"
GOLD = ROOT / "gold.json"


# --------------------------------------------------------------------------- #
# Fixture definitions. Each is the ground truth a human reading the page sees.
# --------------------------------------------------------------------------- #

FIXTURES = {}

# 1) GST tax invoice — IT vendor, single GST rate, taxable/cgst/sgst breakdown.
FIXTURES["invoice_sharma_traders.pdf"] = {
    "header": ["SHARMA ELECTRONICS TRADERS", "Plot 12, Sector 19, New Delhi - 110025",
               "GSTIN: 07AAACS0002K1Z4"],
    "doctype": "TAX INVOICE",
    "invoice_details": {"invoice_number": "ST-1124", "date": "2026-03-02"},
    "buyer": "Kumar Stationery Mart",
    "lines": [
        ["LED Bulkhead Light 12W", "3", "245.00", "735.00"],
        ["PVC Insulated Wire 1.5sqmm (90m)", "90", "6.50", "585.00"],
    ],
    "taxable": "1320.00",
    "cgst": "9.0%", "cgst_amt": "118.80",
    "sgst": "9.0%", "sgst_amt": "118.80",
    "total": "1557.60",
    "gold": {
        "vendor": {"name": "SHARMA ELECTRONICS TRADERS", "gstin": "07AAACS0002K1Z4"},
        "invoice_details": {"invoice_number": "ST-1124", "date": "2026-03-02"},
        "total_amount": {"value": 1557.60},
        "line_items": [
            {"description": "LED Bulkhead Light 12W", "quantity": 3, "unit_price": 245.00, "total_value": 735.00},
            {"description": "PVC Insulated Wire 1.5sqmm (90m)", "quantity": 90, "unit_price": 6.50, "total_value": 585.00},
        ],
    },
}

# 2) GST purchase order — a PO with a different vendor shape (no GSTIN).
FIXTURES["po_chennai_mills.pdf"] = {
    "header": ["CHENNAI FLOUR MILLS AND DISTRIBUTORS", "No. 8, Anna High Road, Chennai - 600002"],
    "doctype": "PURCHASE ORDER",
    "invoice_details": {"invoice_number": "PO-881345", "date": "2026-01-19"},
    "buyer": "Sri Annapurna Bakery",
    "lines": [
        ["Whole Wheat Flour 50kg Bag", "4", "1850.00", "7400.00"],
        ["Refined Palm Oil 15L Tin", "6", "1420.00", "8520.00"],
        ["Sugar 25kg Bag", "3", "2280.00", "6840.00"],
    ],
    "taxable": "22760.00",
    "cgst": None, "sgst": None,
    "total": "22760.00",
    "gold": {
        "vendor": {"name": "CHENNAI FLOUR MILLS AND DISTRIBUTORS"},
        "invoice_details": {"invoice_number": "PO-881345", "date": "2026-01-19"},
        "total_amount": {"value": 22760.00},
        "line_items": [
            {"description": "Whole Wheat Flour 50kg Bag", "quantity": 4, "unit_price": 1850.00, "total_value": 7400.00},
            {"description": "Refined Palm Oil 15L Tin", "quantity": 6, "unit_price": 1420.00, "total_value": 8520.00},
            {"description": "Sugar 25kg Bag", "quantity": 3, "unit_price": 2280.00, "total_value": 6840.00},
        ],
    },
}

# 3) Payment receipt (different field shape: received from, amount).
FIXTURES["receipt_gopal_payments.pdf"] = {
    "header": ["GOPAL & CO. GENERAL STORES"],
    "doctype": "MONEY RECEIPT",
    "invoice_details": {"invoice_number": "RCT-5590", "date": "2026-04-30"},
    "buyer": "Ravi Kirana",
    "lines": [
        ["Cash received towards goods supplied", "1", "12500.00", "12500.00"],
    ],
    "taxable": None,
    "cgst": None, "sgst": None,
    "total": "12500.00",
    "gold": {
        "vendor": {"name": "GOPAL & CO. GENERAL STORES"},
        "invoice_details": {"invoice_number": "RCT-5590", "date": "2026-04-30"},
        "total_amount": {"value": 12500.00},
        "line_items": [
            {"description": "Cash received towards goods supplied", "quantity": 1, "unit_price": 12500.00, "total_value": 12500.00},
        ],
    },
}

# 4) Handwritten-style SME invoice (no GSTIN, short lines).
FIXTURES["invoice_meenakshi_textiles.pdf"] = {
    "header": ["MEENAKSHI TEXTILES", "GSTIN: 33AABCM9021R1Z7"],
    "doctype": "SALE INVOICE",
    "invoice_details": {"invoice_number": "MT-9012", "date": "2026-02-08"},
    "buyer": "Jayalakshmi Boutique",
    "lines": [
        ["Cotton Saree - Kanjivaram", "2", "3200.00", "6400.00"],
        ["Silk Dupatta", "4", "875.00", "3500.00"],
        ["Tailoring Charges", "1", "500.00", "500.00"],
    ],
    "taxable": "10400.00",
    "cgst": "2.5%", "cgst_amt": "260.00",
    "sgst": "2.5%", "sgst_amt": "260.00",
    "total": "10920.00",
    "gold": {
        "vendor": {"name": "MEENAKSHI TEXTILES", "gstin": "33AABCM9021R1Z7"},
        "invoice_details": {"invoice_number": "MT-9012", "date": "2026-02-08"},
        "total_amount": {"value": 10920.00},
        "line_items": [
            {"description": "Cotton Saree - Kanjivaram", "quantity": 2, "unit_price": 3200.00, "total_value": 6400.00},
            {"description": "Silk Dupatta", "quantity": 4, "unit_price": 875.00, "total_value": 3500.00},
            {"description": "Tailoring Charges", "quantity": 1, "unit_price": 500.00, "total_value": 500.00},
        ],
    },
}

# 5) Hardware invoice with freight + GST 18%.
FIXTURES["invoice_deccan_hardware.pdf"] = {
    "header": ["DECCAN HARDWARE SUPPLIERS", "GSTIN: 27AABPD9032F1Z9"],
    "doctype": "TAX INVOICE",
    "invoice_details": {"invoice_number": "DH-3341", "date": "2026-01-07"},
    "buyer": "Swarna Builders",
    "lines": [
        ["GI Pipe 1 inch (3m length)", "12", "415.00", "4980.00"],
        ["MS Flat 25x5mm rod", "20", "245.00", "4900.00"],
        ["Freight and Handling", "1", "600.00", "600.00"],
    ],
    "taxable": "10480.00",
    "cgst": "9.0%", "cgst_amt": "943.20",
    "sgst": "9.0%", "sgst_amt": "943.20",
    "total": "12366.40",
    "gold": {
        "vendor": {"name": "DECCAN HARDWARE SUPPLIERS", "gstin": "27AABPD9032F1Z9"},
        "invoice_details": {"invoice_number": "DH-3341", "date": "2026-01-07"},
        "total_amount": {"value": 12366.40},
        "line_items": [
            {"description": "GI Pipe 1 inch (3m length)", "quantity": 12, "unit_price": 415.00, "total_value": 4980.00},
            {"description": "MS Flat 25x5mm rod", "quantity": 20, "unit_price": 245.00, "total_value": 4900.00},
            {"description": "Freight and Handling", "quantity": 1, "unit_price": 600.00, "total_value": 600.00},
        ],
    },
}

# 6) Stationery PO with per-unit rates in paise-style (e.g. 12.5).
FIXTURES["po_kolkata_stationery.pdf"] = {
    "header": ["KOLKATA STATIONERY HOUSE", "GSTIN: 19AAACR0044F1Z0"],
    "doctype": "PURCHASE ORDER",
    "invoice_details": {"invoice_number": "PO-K5477", "date": "2026-05-11"},
    "buyer": "St. Xavier School Office",
    "lines": [
        ["A4 Premium Paper Ream (500 sheets)", "25", "295.00", "7375.00"],
        ["Gel Pen Blue (Pack of 10)", "40", "85.00", "3400.00"],
        ["Permanent Marker Black", "30", "40.00", "1200.00"],
        ["Sticky Note Pads 3x3", "60", "22.50", "1350.00"],
    ],
    "taxable": "13325.00",
    "cgst": "2.5%", "cgst_amt": "333.13",
    "sgst": "2.5%", "sgst_amt": "333.13",
    "total": "13991.26",
    "gold": {
        "vendor": {"name": "KOLKATA STATIONERY HOUSE", "gstin": "19AAACR0044F1Z0"},
        "invoice_details": {"invoice_number": "PO-K5477", "date": "2026-05-11"},
        "total_amount": {"value": 13991.26},
        "line_items": [
            {"description": "A4 Premium Paper Ream (500 sheets)", "quantity": 25, "unit_price": 295.00, "total_value": 7375.00},
            {"description": "Gel Pen Blue (Pack of 10)", "quantity": 40, "unit_price": 85.00, "total_value": 3400.00},
            {"description": "Permanent Marker Black", "quantity": 30, "unit_price": 40.00, "total_value": 1200.00},
            {"description": "Sticky Note Pads 3x3", "quantity": 60, "unit_price": 22.50, "total_value": 1350.00},
        ],
    },
}


# --------------------------------------------------------------------------- #
# Rendering (PyMuPDF)
# --------------------------------------------------------------------------- #

def _render(fixture: dict, path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4 portrait
    y = 70
    margin = 55
    rect = fitz.Rect(margin, y, 340, y + 60)

    # Header / vendor
    page.insert_textbox(rect, fixture["header"][0], fontsize=14, fontname="helv", color=(0.1, 0.25, 0.4))
    y += 26
    for line in fixture["header"][1:]:
        page.insert_text((margin, y), line, fontsize=9, color=(0.3, 0.3, 0.3))
        y += 14

    # Doc type
    y += 6
    page.insert_text((margin, y), fixture["doctype"], fontsize=13, fontname="hebo", color=(0.05, 0.05, 0.05))
    y += 24

    # Invoice/PO details
    details = fixture["invoice_details"]
    page.insert_text((margin, y), f"No: {details['invoice_number']}    Date: {details['date']}",
                     fontsize=10, fontname="helv")
    y += 18
    page.insert_text((margin, y), f"Buyer: {fixture['buyer']}", fontsize=10)
    y += 22

    # Table header
    page.draw_line((margin, y), (540, y))
    y += 4
    page.insert_text((margin, y), "Description", fontsize=9, fontname="hebo")
    page.insert_text((415, y), "Qty", fontsize=9, fontname="hebo")
    page.insert_text((455, y), "Rate", fontsize=9, fontname="hebo")
    page.insert_text((505, y), "Amount", fontsize=9, fontname="hebo")
    y += 4
    page.draw_line((margin, y), (540, y))
    y += 16

    # Line items
    for desc, qty, rate, amt in fixture["lines"]:
        page.insert_text((margin, y), desc[:42], fontsize=9)
        page.insert_text((415, y), qty, fontsize=9)
        page.insert_text((455, y), rate, fontsize=9)
        page.insert_text((505, y), amt, fontsize=9)
        y += 15

    y += 6
    page.draw_line((margin, y), (540, y))
    y += 16

    # Taxable amount
    if fixture["taxable"] is not None:
        page.insert_text((margin, y), f"Taxable Value: Rs. {fixture['taxable']}", fontsize=9)
        y += 15
        if fixture["cgst"]:
            page.insert_text((margin, y),
                             f"CGST {fixture['cgst']}: Rs. {fixture['cgst_amt']}     "
                             f"SGST {fixture['sgst']}: Rs. {fixture['sgst_amt']}", fontsize=9)
            y += 15
    page.insert_text((margin, y), f"GRAND TOTAL: Rs. {fixture['total']}", fontsize=11, fontname="hebo")

    doc.save(path)
    doc.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", action="store_true",
                    help="Also regenerate gold.json from the fixture sources")
    args = ap.parse_args()

    PDFS.mkdir(parents=True, exist_ok=True)
    for name, fx in FIXTURES.items():
        _render(fx, PDFS / name)
        print(f"wrote {PDFS / name}")

    if args.gold:
        # Merge: keep any PRE-EXISTING hand-labelled gold (e.g. a real document)
        # and only add/overwrite the synthetic fixture labels we own.
        existing = {}
        if GOLD.exists():
            try:
                existing = json.loads(GOLD.read_text(encoding="utf-8"))
                existing.pop("_readme", None)
            except (json.JSONDecodeError, OSError):
                existing = {}
        gold = existing
        for name, fx in FIXTURES.items():
            gold[name] = fx["gold"]
        merged = {
            "_readme": (
                "Gold set for HaloHubX benchmark.py. Non-synthetic labels (e.g. "
                "bill_8_two_invoices.pdf) are hand-labelled by reading the source PDF. "
                "Synthetic labels were generated by benchmark/make_fixtures.py: each fixture "
                "def IS the page text, so these are an honest, auditable label of what a human "
                "reading the page sees. benchmark.py scores the FIRST invoice and only fields "
                "present here; omit fields or 'line_items' to skip scoring."
            ),
            **gold,
        }
        GOLD.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {GOLD} (kept {len(existing)} pre-existing label(s))")


if __name__ == "__main__":
    main()
