"""
Task 1: Smart Document Reader (Upload page)
Upload one or more invoices / receipts / contracts (PDF or image). Pick
which one to preview (with page navigation for multi-page PDFs) and
extract, then optionally save PDFs into the Company Brain library.
Switching between uploaded files preserves each file's own progress.
"""

import io
import json
import re

import fitz  # PyMuPDF
import pandas as pd
import streamlit as st
from PIL import Image

from modules import company_brain

EXTRACTION_PROMPT = """You are an expert document-data-extraction assistant.
Read the attached document carefully (it may be a messy scan, photo, invoice,
receipt, or contract) and extract the following fields as accurately as possible.

Return ONLY a single valid JSON object (no markdown fences, no commentary) with
this exact shape:

{
  "vendor_name": "string or null",
  "date": "string or null",
  "invoice_number": "string or null",
  "line_items": [
    {"description": "string", "quantity": "string or null", "unit_price": "string or null", "total": "string or null"}
  ],
  "total_amount": "string or null",
  "notes": "anything unclear or low-confidence, or empty string"
}

Rules:
- If a field is not present in the document, use null (not a guess).
- Do not invent numbers. Only report what is actually printed/written on the document.
- Keep currency symbols as they appear in the document.
- line_items should be an empty list if none are found.
"""

MAX_IMAGE_DIMENSION = 2000  # px, longest side
JPEG_QUALITY = 85


def _compress_image(file_bytes: bytes) -> tuple[bytes, str]:
    """Shrink large photos/scans before sending them to the AI."""
    try:
        img = Image.open(io.BytesIO(file_bytes))
        img = img.convert("RGB")
        if max(img.size) > MAX_IMAGE_DIMENSION:
            ratio = MAX_IMAGE_DIMENSION / max(img.size)
            new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
            img = img.resize(new_size, Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=JPEG_QUALITY)
        return out.getvalue(), "image/jpeg"
    except Exception:
        return file_bytes, "image/jpeg"


def _pdf_page_count(pdf_bytes: bytes) -> int:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    count = doc.page_count
    doc.close()
    return count


def _pdf_page_to_image(pdf_bytes: bytes, page_index: int) -> Image.Image:
    """Render a specific PDF page (0-indexed) to a PIL image for preview."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc.load_page(page_index)
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    doc.close()
    return img


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


def _run_extraction(client, file_bytes: bytes, media_type: str, is_pdf: bool):
    text = client.extract(file_bytes, media_type, is_pdf, EXTRACTION_PROMPT)
    return _clean_json_response(text)


def render(client):
    st.caption(
        "Upload one or more invoices, receipts, or contracts (PDF or image). "
        "Pick one below to preview and extract, then save PDFs into Company Brain."
    )

    uploaded_files = st.file_uploader(
        "Drag and drop file(s) here",
        type=["pdf", "png", "jpg", "jpeg"],
        accept_multiple_files=True,
        key="doc_reader_upload",
    )

    if not uploaded_files:
        st.info("Upload one or more documents to get started.")
        return

    # Track a stable identity per file so state (page, extraction, saved
    # status) survives switching between files, and resets only if a file
    # with the same name is genuinely replaced by different content.
    identities = {f.name: f"{f.name}:{len(f.getvalue())}" for f in uploaded_files}

    if len(uploaded_files) > 1:
        selected_name = st.selectbox(
            "Choose which file to preview / extract",
            list(identities.keys()),
        )
    else:
        selected_name = uploaded_files[0].name

    uploaded = next(f for f in uploaded_files if f.name == selected_name)
    file_bytes = uploaded.getvalue()
    is_pdf = uploaded.type == "application/pdf" or uploaded.name.lower().endswith(".pdf")
    media_type = "application/pdf" if is_pdf else (uploaded.type or "image/png")

    file_identity = identities[selected_name]

    # Per-file state, keyed by identity, so different uploaded files don't
    # clobber each other's page position / extraction result / saved flag.
    page_idx_map = st.session_state.setdefault("doc_reader_page_idx_map", {})
    results_map = st.session_state.setdefault("doc_reader_results_map", {})
    saved_map = st.session_state.setdefault("doc_reader_saved_map", {})

    size_mb = len(file_bytes) / (1024 * 1024)
    if size_mb > 10:
        st.warning(
            f"This file is {size_mb:.1f}MB — large files can take a long time or "
            "stall, especially on Gemini's free tier. If it hangs, try a smaller/"
            "compressed version of the file."
        )

    left, right = st.columns(2)

    with left:
        st.subheader("Document")
        if is_pdf:
            try:
                page_count = _pdf_page_count(file_bytes)
                page_idx = page_idx_map.get(file_identity, 0)
                page_idx = max(0, min(page_idx, page_count - 1))

                preview_img = _pdf_page_to_image(file_bytes, page_idx)
                st.image(preview_img, caption=uploaded.name, use_column_width=True)

                if page_count > 1:
                    nav_left, nav_center, nav_right = st.columns([1, 2, 1])
                    with nav_left:
                        if st.button("◀", use_container_width=True, disabled=(page_idx == 0)):
                            page_idx_map[file_identity] = page_idx - 1
                            st.rerun()
                    with nav_center:
                        st.markdown(
                            f"<div style='text-align:center; padding-top:6px;'>"
                            f"Page {page_idx + 1} of {page_count}</div>",
                            unsafe_allow_html=True,
                        )
                    with nav_right:
                        if st.button(
                            "▶", use_container_width=True, disabled=(page_idx == page_count - 1)
                        ):
                            page_idx_map[file_identity] = page_idx + 1
                            st.rerun()
            except Exception as e:
                st.warning(f"Could not render a preview of this PDF: {e}")
        else:
            st.image(file_bytes, caption=uploaded.name, use_column_width=True)

    with right:
        st.subheader("Extracted Data")

        if client is None:
            st.warning("Add an API key in the sidebar (Claude or Gemini) to run extraction.")
            return

        run = st.button(
            "🔍 Extract Data", type="primary", use_container_width=True, key=f"extract_{file_identity}"
        )

        if run:
            send_bytes, send_media_type = (
                (file_bytes, media_type) if is_pdf else _compress_image(file_bytes)
            )
            with st.spinner("Reading document..."):
                try:
                    data = _run_extraction(client, send_bytes, send_media_type, is_pdf)
                except Exception as e:
                    st.error(f"Extraction failed: {e}")
                    return
            results_map[file_identity] = data
            saved_map[file_identity] = False

        data = results_map.get(file_identity)
        if not data:
            st.caption("Click **Extract Data** to process this document.")
            return

        summary_df = pd.DataFrame(
            [
                {"Field": "Vendor Name", "Value": data.get("vendor_name")},
                {"Field": "Date", "Value": data.get("date")},
                {"Field": "Invoice Number", "Value": data.get("invoice_number")},
                {"Field": "Total Amount", "Value": data.get("total_amount")},
            ]
        )
        st.table(summary_df)

        line_items = data.get("line_items") or []
        items_df = pd.DataFrame(line_items) if line_items else pd.DataFrame(
            columns=["description", "quantity", "unit_price", "total"]
        )
        st.write("**Line Items**")
        st.dataframe(items_df, use_container_width=True)

        if data.get("notes"):
            st.caption(f"⚠️ Note: {data['notes']}")

        # Excel export
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            summary_df.to_excel(writer, index=False, sheet_name="Summary")
            items_df.to_excel(writer, index=False, sheet_name="Line Items")
        st.download_button(
            "⬇️ Download as Excel",
            data=buffer.getvalue(),
            file_name=f"extracted_{uploaded.name.rsplit('.', 1)[0]}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"download_{file_identity}",
        )

        st.divider()

        if not is_pdf:
            st.caption(
                "ℹ️ Only PDFs can be saved into Company Brain (it needs a text "
                "layer to index for Q&A). This image's extracted data above is "
                "still yours to keep via the Excel download."
            )
            return

        existing_dup = company_brain.find_existing_by_hash(file_bytes)

        if saved_map.get(file_identity) or existing_dup:
            if existing_dup and not saved_map.get(file_identity):
                st.info(f"This exact file is already in Company Brain as '{existing_dup['filename']}'.")
            else:
                st.success("✅ Saved to Company Brain.")
            if st.button(
                "← Back to Dashboard", type="primary", use_container_width=True,
                key=f"back_{file_identity}",
            ):
                st.session_state["page"] = "dashboard"
                st.rerun()
        else:
            if st.button(
                "💾 Save to Company Brain", use_container_width=True, key=f"save_{file_identity}"
            ):
                with st.spinner("Indexing document for search..."):
                    try:
                        company_brain.add_document(file_bytes, uploaded.name, summary=data)
                    except Exception as e:
                        st.error(f"Could not save to Company Brain: {e}")
                        return
                saved_map[file_identity] = True
                st.rerun()

    # --- Bulk import: process every uploaded PDF at once, skipping the ---
    # --- one-by-one preview/extract step. Good for tens to ~100+ files. ---
    # --- For hundreds/thousands of files, use bulk_import.py instead    ---
    # --- (reads straight from a folder on disk, see README).            ---
    pdf_batch = [
        f for f in uploaded_files
        if f.type == "application/pdf" or f.name.lower().endswith(".pdf")
    ]
    if len(pdf_batch) > 1:
        st.divider()
        with st.expander(f"📦 Bulk-add all {len(pdf_batch)} uploaded PDFs to Company Brain at once"):
            st.caption(
                "Skips the one-by-one preview step. For hundreds or thousands of files, "
                "use `bulk_import.py` from a terminal instead — it reads straight from a "
                "folder on disk and doesn't need browser uploads at all (see README)."
            )
            run_extraction_in_bulk = st.checkbox(
                "Also run AI extraction (vendor/date/total) for each file — slower, "
                "uses one API call per file",
                value=False,
                key="bulk_extract_checkbox",
            )
            if st.button("⚡ Add All PDFs to Company Brain", key="bulk_add_button"):
                progress = st.progress(0.0)
                status_area = st.empty()
                results = []
                for i, f in enumerate(pdf_batch):
                    fbytes = f.getvalue()
                    status_area.write(f"Processing {i + 1} of {len(pdf_batch)}: {f.name}")

                    existing = company_brain.find_existing_by_hash(fbytes)
                    if existing:
                        results.append((f.name, "skipped (duplicate)"))
                        progress.progress((i + 1) / len(pdf_batch))
                        continue

                    summary, note = None, ""
                    if run_extraction_in_bulk and client is not None:
                        try:
                            summary = _run_extraction(client, fbytes, "application/pdf", True)
                        except Exception as e:
                            note = f" (extraction failed, saved without summary: {e})"

                    try:
                        company_brain.add_document(fbytes, f.name, summary=summary)
                        results.append((f.name, "added" + note))
                    except Exception as e:
                        results.append((f.name, f"failed to index: {e}"))

                    progress.progress((i + 1) / len(pdf_batch))

                status_area.empty()
                st.success("Bulk import finished.")
                st.dataframe(
                    pd.DataFrame(results, columns=["File", "Result"]),
                    use_container_width=True,
                )
