"""
Phase 1: Extraction engine with confidence scoring.

Important honesty note (read this before presenting confidence scores to
clients): these confidence values are the AI model's own self-reported
certainty, produced because we explicitly instruct it to estimate one.
They are a genuinely useful triage signal for routing likely-wrong fields
to a human in the HITL UI — but they are NOT a statistically calibrated
probability of correctness. Don't market them as "92% accurate" — market
them as "the system flags fields it's unsure about for human review,"
which is what they actually do reliably.
"""

import json
import re

EXTRACTION_PROMPT = """You are an expert at reading Indian GST invoices, purchase
orders, and related business documents, including messy scans, skewed
tables, and handwritten notes.

IMPORTANT: the attached PDF may contain MORE THAN ONE invoice (e.g. a
batch of scanned invoices in one file). You MUST identify and extract
EVERY distinct invoice found, in the order they appear — never stop
after the first one.

Read the attached document and extract data into this EXACT JSON shape.
Return ONLY the JSON object — no markdown fences, no commentary.

{
  "invoice_count": 0,
  "invoices": [
    {
      "document_type": "gst_invoice | purchase_order | receipt | other",
      "vendor": {
        "name": "string or null",
        "gstin": "string or null",
        "confidence": 0.0
      },
      "invoice_details": {
        "invoice_number": "string or null",
        "date": "string or null (YYYY-MM-DD if determinable)",
        "confidence": 0.0
      },
      "line_items": [
        {
          "description": "string",
          "hsn_sac_code": "string or null",
          "quantity": 0,
          "unit_price": 0.0,
          "tax_cgst": 0.0,
          "tax_sgst": 0.0,
          "tax_igst": 0.0,
          "total_value": 0.0,
          "confidence": 0.0
        }
      ],
      "total_amount": {
        "value": 0.0,
        "confidence": 0.0
      },
      "flags": []
    }
  ],
  "document_flags": []
}

Rules for "invoice_count" and "invoices":
- "invoice_count" MUST equal the number of items in "invoices" — count
  them yourself, don't guess.
- If the document contains only one invoice, "invoices" still has exactly
  one element — same shape either way.
- If pages clearly belong to the same single invoice (e.g. a multi-page
  itemized invoice), that is ONE invoice with all line items combined —
  don't split one invoice into multiple entries just because it spans
  pages. Only create separate entries for genuinely separate invoices
  (each with e.g. its own invoice number / vendor / total).

Confidence scoring rules (CRITICAL):
- Every "confidence" field is your own honest self-estimate from 0.0 (pure
  guess) to 1.0 (completely certain / clearly printed and unambiguous).
- If a field is handwritten, blurred, skewed, or you are inferring it
  rather than reading it directly, score it LOW (below 0.85) — do not
  inflate confidence to seem more useful.
- Never fabricate a number to fill a field. If a value truly cannot be
  determined, use null and give it a low confidence score.

Other rules:
- Only include CGST/SGST for intra-state invoices, or IGST for
  inter-state — set the ones that don't apply to 0.0, not null.
- Per-invoice "flags" notes anything unusual about THAT invoice, e.g.
  "handwritten_notes_detected", "table_partially_cut_off".
- Top-level "document_flags" notes anything about the file as a whole
  that isn't specific to one invoice, e.g. "scan_quality_poor_throughout".
- Loop through ALL pages for each invoice's line items — don't stop at
  the first page if a single invoice's table continues onto later pages.
"""


def _clean_json_response(text: str) -> dict:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(json)?", "", cleaned.strip())
    cleaned = re.sub(r"```$", "", cleaned.strip())
    cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def extract_document(ai_client, file_bytes: bytes, media_type: str, on_attempt=None) -> dict:
    """Runs extraction and returns the parsed result dict, matching the
    schema above. Raises on AI-call failure or unparseable output —
    caller is responsible for catching and marking the Document 'failed'.
    on_attempt (optional callable) is invoked before each real provider
    attempt so quota meters count retries accurately."""
    raw_text = ai_client.extract(file_bytes, media_type, EXTRACTION_PROMPT, on_attempt=on_attempt)
    return _clean_json_response(raw_text)