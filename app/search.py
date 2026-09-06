"""
AI-powered document search ("ask your documents").

Given a natural-language query, picks the most relevant completed documents
for the partner, and runs the query against a compact prose index of each
document's extraction result so the LLM can answer with exact values and
cite exactly which document(s) the answer came from.

Design notes:
  * Retrieval is a cheap lexical (token-overlap) ranking over a flattened,
    human-readable index of each extraction. Only the top-K candidates are
    sent to the LLM, which keeps cost/latency bounded and answers grounded.
  * The LLM is forced to return strict JSON {answer, sources:[document_id]}
    so the console can render the answer and link back to the source docs.
  * Multi-tenant isolation is re-used everywhere: everything is scoped by
    actor.partner_id, exactly like the rest of the API.
"""

import json
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai_client import get_default_client
from app.auth import get_actor, Actor
from app.db import get_db
from app.logging_setup import get_logger
from app.models import Client, Document

router = APIRouter(prefix="/search", tags=["search"])
log = get_logger("search")

TOP_K = 6
MAX_INDEX_CHARS = 900
MAX_QUERY_CHARS = 2000

ANSWER_PROMPT = """You are the analytical search engine for a document-intelligence
platform (Indian GST invoices, purchase orders, receipts). Below are excerpts
from the user's own documents. Answer the user's question using ONLY these
documents — never outside knowledge.

RULES:
- Be precise. Quote exact numbers, invoice numbers, dates, vendor names, GSTINs.
- If the answer needs a calculation (sums, totals, averages), show the figures.
- If the documents do not contain an answer, say so clearly — do not invent.
- Cite the document_id(s) you actually used. Only use ids present in the JSON.

Return STRICT JSON, no markdown, no commentary:
{{"answer": "...", "sources": ["<document_id>", ...]}}

DOCUMENTS:
[START DOCUMENTS]
{documents}
[END DOCUMENTS]

USER QUESTION: {query}
"""


class SearchRequest(BaseModel):
    query: str


def _tokenize(text: str) -> set:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _flatten_result(result: dict, filename: str = "", client_name: str = "") -> str:
    """Turn an extraction result into a compact, searchable prose block."""
    parts = []
    if filename:
        parts.append(f"Document: {filename}")
    if client_name:
        parts.append(f"Client: {client_name}")
    result = result or {}
    invoices = result.get("invoices") or []
    if invoices:
        parts.append(f"Contains {len(invoices)} invoice(s).")
    for inv in invoices:
        vendor = inv.get("vendor") or {}
        details = inv.get("invoice_details") or {}
        total = inv.get("total_amount") or {}
        if vendor.get("name"):
            parts.append(
                f"Vendor: {vendor['name']}" + (f" (GSTIN {vendor['gstin']})" if vendor.get("gstin") else "")
            )
        if details.get("invoice_number"):
            parts.append(
                f"Invoice {details['invoice_number']}"
                + (f" dated {details.get('date')}" if details.get("date") else "")
            )
        if total.get("value") is not None:
            parts.append(f"Total amount: {total['value']}")
        for li in inv.get("line_items") or []:
            desc = li.get("description") or ""
            qty = li.get("quantity")
            price = li.get("unit_price")
            val = li.get("total_value")
            li_txt = f"Line item: {desc}"
            if qty is not None:
                li_txt += f", qty {qty}"
            if price is not None:
                li_txt += f", unit price {price}"
            if val is not None:
                li_txt += f", value {val}"
            if li.get("hsn_sac_code"):
                li_txt += f", HSN {li['hsn_sac_code']}"
            parts.append(li_txt)
        for flag in inv.get("flags") or []:
            parts.append(f"Flag: {flag}")
    for flag in result.get("document_flags") or []:
        parts.append(f"Flag: {flag}")
    return "\n".join(parts)[:MAX_INDEX_CHARS]


def _parse_answer(text: str) -> dict:
    text = text.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON object in model output")
    data = json.loads(match.group(0))
    return {
        "answer": str(data.get("answer") or "").strip(),
        "sources": [str(s) for s in (data.get("sources") or [])][:TOP_K],
    }


@router.post("")
def search_documents(
    payload: SearchRequest,
    actor: Actor = Depends(get_actor),
    db: Session = Depends(get_db),
):
    """Search a partner's completed documents and return a grounded answer."""
    query = (payload.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required.")
    if len(query) > MAX_QUERY_CHARS:
        raise HTTPException(status_code=400, detail="query is too long.")

    docs = (
        db.query(Document)
        .filter(
            Document.partner_id == actor.partner_id,
            Document.status == "completed",
            Document.deleted_at.is_(None),
        )
        .order_by(Document.created_at.desc())
        .limit(250)
        .all()
    )
    if not docs:
        return {
            "answer": "No completed documents yet. Upload and process a document first, then ask again.",
            "sources": [],
        }

    # Flatten each doc into a prose index and rank by token overlap with the query.
    q_tokens = _tokenize(query)
    ranked = []
    for d in docs:
        client = db.query(Client).filter(Client.id == d.client_id).first()
        client_name = client.name if client else ""
        text = _flatten_result(d.result_json or {}, filename=d.filename, client_name=client_name)
        # Score: overlap of query tokens weighted a bit for longer tokens.
        tokens = _tokenize(text)
        score = 0
        for t in q_tokens:
            if t in tokens:
                score += 3 if len(t) > 3 else 1
        ranked.append({"doc": d, "client_name": client_name, "text": text, "score": score})

    ranked.sort(key=lambda r: r["score"], reverse=True)
    top = [r for r in ranked if r["score"] > 0][:TOP_K]
    if not top:
        top = ranked[: min(TOP_K, len(ranked))]  # no lexical hits -> most recent docs

    doc_blocks = []
    id_set = set()
    for r in top:
        id_set.add(r["doc"].id)
        doc_blocks.append(
            json.dumps(
                {
                    "document_id": r["doc"].id,
                    "filename": r["doc"].filename,
                    "client": r["client_name"],
                    "content": r["text"],
                }
            )
        )

    ai_client = get_default_client()
    try:
        raw = ai_client.complete(
            ANSWER_PROMPT.format(query=query, documents="\n\n".join(doc_blocks))
        )
        parsed = _parse_answer(raw)
    except Exception as e:
        log.exception("search_complete_failed", extra={"partner_id": actor.partner_id})
        raise HTTPException(status_code=502, detail=f"Answer generation failed: {e}")

    # Map cited ids back to the actual docs so the UI can link to them.
    seen = set()
    sources = []
    for did in parsed["sources"]:
        if did in seen or did not in id_set:
            continue
        seen.add(did)
        src = next((r for r in top if r["doc"].id == did), None)
        if src is None:
            continue
        sources.append(
            {
                "document_id": did,
                "filename": src["doc"].filename,
                "client_name": src["client_name"],
            }
        )

    log.info("search_completed", extra={"partner_id": actor.partner_id, "sources": len(sources)})
    return {"answer": parsed["answer"], "sources": sources, "scanned": len(docs)}