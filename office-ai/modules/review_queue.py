"""
Phase 3: Human-in-the-loop review queue.

Unlike the rest of this Streamlit app, this page does NOT use local
files/ChromaDB — it talks to the halohubx-api backend over HTTP, because
that's where the real multi-tenant document data lives (Postgres). You
need that backend running (locally or deployed) and a partner API key
from it (see halohubx-api/create_partner.py) for this page to work.
"""

import copy
import io

import fitz  # PyMuPDF
import pandas as pd
import requests
import streamlit as st
from PIL import Image


def _pdf_page_to_image(pdf_bytes: bytes, page_index: int = 0) -> Image.Image:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc.load_page(page_index)
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    doc.close()
    return img


def _headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"}


def render(backend_url: str, api_key: str):
    st.caption(
        "Documents the AI flagged for human review (low-confidence fields "
        "or unusual content) before they're pushed to the ERP. Clean "
        "documents skip this queue entirely and push through automatically."
    )

    if not backend_url or not api_key:
        st.warning("Add your backend URL and partner API key in the sidebar to load the review queue.")
        return

    backend_url = backend_url.rstrip("/")

    try:
        resp = requests.get(
            f"{backend_url}/documents",
            params={"review_status": "needs_review"},
            headers=_headers(api_key),
            timeout=15,
        )
        resp.raise_for_status()
        queue = resp.json()
    except Exception as e:
        st.error(f"Couldn't reach the backend: {e}")
        return

    if not queue:
        st.success("Nothing waiting for review right now. 🎉")
        return

    options = {
        f"{d['filename']}  ·  {d['invoice_count'] or '?'} invoice(s)  ·  {d['created_at']}": d["document_id"]
        for d in queue
    }
    choice = st.selectbox(f"Review queue ({len(queue)} waiting)", list(options.keys()))
    doc_id = options[choice]

    # Fetch full detail for the selected document
    try:
        detail_resp = requests.get(f"{backend_url}/documents/{doc_id}/review", headers=_headers(api_key), timeout=15)
        detail_resp.raise_for_status()
        detail = detail_resp.json()
    except Exception as e:
        st.error(f"Couldn't load document detail: {e}")
        return

    threshold = detail["low_confidence_threshold"]
    result = detail["result"]
    edited_result = copy.deepcopy(result)

    left, right = st.columns(2)

    with left:
        st.subheader("Original Document")
        if detail.get("has_file"):
            try:
                file_resp = requests.get(f"{backend_url}/documents/{doc_id}/file", headers=_headers(api_key), timeout=15)
                file_resp.raise_for_status()
                preview_img = _pdf_page_to_image(file_resp.content)
                st.image(preview_img, use_column_width=True)
            except Exception as e:
                st.warning(f"Couldn't load file preview: {e}")
        else:
            st.info("No file stored for this document.")

    with right:
        st.subheader("Extracted Data — review & correct")

        for idx, invoice in enumerate(result.get("invoices", [])):
            with st.expander(
                f"Invoice {idx + 1}: {invoice.get('vendor', {}).get('name') or 'Unknown vendor'}",
                expanded=True,
            ):
                if invoice.get("flags"):
                    st.warning("Flags: " + ", ".join(invoice["flags"]))

                vendor = invoice.get("vendor", {})
                vendor_conf = vendor.get("confidence", 1)
                v_label = "Vendor name" + (" ⚠️ low confidence" if vendor_conf < threshold else "")
                new_vendor_name = st.text_input(v_label, value=vendor.get("name") or "", key=f"vname_{idx}")

                gstin_conf = vendor_conf
                g_label = "GSTIN" + (" ⚠️ low confidence" if gstin_conf < threshold else "")
                new_gstin = st.text_input(g_label, value=vendor.get("gstin") or "", key=f"gstin_{idx}")

                inv_details = invoice.get("invoice_details", {})
                id_conf = inv_details.get("confidence", 1)
                col_a, col_b = st.columns(2)
                with col_a:
                    n_label = "Invoice number" + (" ⚠️" if id_conf < threshold else "")
                    new_invoice_number = st.text_input(n_label, value=inv_details.get("invoice_number") or "", key=f"invnum_{idx}")
                with col_b:
                    d_label = "Date" + (" ⚠️" if id_conf < threshold else "")
                    new_date = st.text_input(d_label, value=inv_details.get("date") or "", key=f"date_{idx}")

                st.write("**Line items**" + (" ⚠️ some rows below the confidence threshold" if any(
                    (li.get("confidence", 1) < threshold) for li in invoice.get("line_items", [])
                ) else ""))
                items_df = pd.DataFrame(invoice.get("line_items", []))
                edited_df = st.data_editor(
                    items_df,
                    use_container_width=True,
                    disabled=["confidence"],
                    key=f"items_{idx}",
                )

                total = invoice.get("total_amount", {})
                total_conf = total.get("confidence", 1)
                t_label = "Total amount" + (" ⚠️ low confidence" if total_conf < threshold else "")
                new_total = st.number_input(t_label, value=float(total.get("value") or 0), key=f"total_{idx}")

                # Write corrections back into edited_result
                inv_out = edited_result["invoices"][idx]
                inv_out["vendor"]["name"] = new_vendor_name
                inv_out["vendor"]["gstin"] = new_gstin
                inv_out["invoice_details"]["invoice_number"] = new_invoice_number
                inv_out["invoice_details"]["date"] = new_date
                inv_out["line_items"] = edited_df.to_dict(orient="records")
                inv_out["total_amount"]["value"] = new_total

        st.divider()
        if st.button("✅ Approve & Push to ERP", type="primary", use_container_width=True):
            try:
                approve_resp = requests.post(
                    f"{backend_url}/documents/{doc_id}/approve",
                    json=edited_result,
                    headers=_headers(api_key),
                    timeout=20,
                )
                approve_resp.raise_for_status()
                outcome = approve_resp.json()
            except Exception as e:
                st.error(f"Approval failed: {e}")
                return

            if outcome.get("webhook_delivered"):
                st.success("Approved and delivered to the partner's webhook. ✅")
            else:
                st.success("Approved — no webhook was configured for this document, so nothing was pushed.")
            st.rerun()
