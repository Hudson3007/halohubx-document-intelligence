"""PDF page counting used for credit metering (1 credit = 1 page).

Uses PyMuPDF (imported as fitz) when available. Falls back to 1 so a
page-counting failure never blocks an upload — billing still works, it just
charges the atomic minimum.
"""


def count_pdf_pages(file_bytes: bytes) -> int:
    if not file_bytes:
        return 0
    try:
        import fitz  # PyMuPDF

        with fitz.open(stream=file_bytes, filetype="pdf") as doc:
            return doc.page_count or 1
    except Exception:
        return 1