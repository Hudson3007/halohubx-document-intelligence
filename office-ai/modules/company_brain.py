"""
Task 2: Company Brain
A persistent local library of PDFs. Each uploaded PDF is chunked by page,
embedded locally (sentence-transformers), and stored in an on-disk ChromaDB
collection under ./data/chroma_store — no cloud database. A small local
JSON file (./data/library.json) tracks which documents exist so the
library survives app restarts.

Supports asking a question about ONE document, or searching across ALL
saved documents at once (with per-chunk file+page citations). Also shows
a deterministic "quick total" of extracted amounts across the whole
library, since summing numbers is something plain arithmetic does more
reliably than asking the AI to add things up from retrieved text.
"""

import hashlib
import json
import os
import re
import uuid
from datetime import datetime

import chromadb
import fitz  # PyMuPDF
import streamlit as st
from sentence_transformers import SentenceTransformer

CHUNK_TARGET_CHARS = 800
TOP_K_PER_DOC = 3       # chunks pulled from each document in "All Documents" mode
TOP_K_SINGLE = 4        # chunks pulled when asking about a single document
GLOBAL_TOP_K = 6        # final chunk count sent to the AI in "All Documents" mode
MAX_DOCS_FOR_GUARANTEED_COVERAGE = 40  # above this, fall back to plain top-K (avoid huge/costly prompts)

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(APP_DIR, "data")
CHROMA_DIR = os.path.join(DATA_DIR, "chroma_store")
LIBRARY_PATH = os.path.join(DATA_DIR, "library.json")

os.makedirs(CHROMA_DIR, exist_ok=True)

SINGLE_DOC_PROMPT_TEMPLATE = """You are a precise company-documents assistant.
Answer the user's question using ONLY the context excerpts below. Each excerpt
is labeled with the page it came from.

Rules:
- If the answer is not contained in the context, say clearly that the document
  doesn't seem to cover that, and do not guess or invent an answer.
- Keep the answer short and direct.
- At the end of your answer, add a citation line in this exact format:
  [Found on Page X] (use the page number(s) the answer actually came from).

Context:
{context}

Question: {question}
"""

ALL_DOCS_PROMPT_TEMPLATE = """You are a precise company-documents assistant with
access to excerpts pulled from MULTIPLE documents in a document library. Each
excerpt is labeled with which file and page it came from.

Rules:
- Answer using ONLY the context excerpts below.
- If the answer requires combining information across several documents (e.g.
  a total across multiple invoices), do so explicitly using the numbers shown
  in the excerpts, and do not invent or guess numbers not present in them.
- If the answer is not contained in the context, say clearly that the library
  doesn't seem to cover that.
- End your answer with a citation line listing each document and page used,
  in this exact format: [Found in: FileA.pdf p.2, FileB.pdf p.5]

Context:
{context}

Question: {question}
"""


@st.cache_resource(show_spinner=False)
def _load_embedder():
    return SentenceTransformer("all-MiniLM-L6-v2")


@st.cache_resource(show_spinner=False)
def _get_chroma_client():
    return chromadb.PersistentClient(path=CHROMA_DIR)


def _hash_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def find_existing_by_hash(pdf_bytes: bytes) -> dict | None:
    """Return the existing library entry for this exact file content, if any."""
    h = _hash_bytes(pdf_bytes)
    library = _load_library()
    return next((d for d in library if d.get("content_hash") == h), None)


def _load_library() -> list:
    if not os.path.exists(LIBRARY_PATH):
        return []
    with open(LIBRARY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_library(library: list):
    with open(LIBRARY_PATH, "w", encoding="utf-8") as f:
        json.dump(library, f, indent=2)


def _extract_page_chunks(pdf_bytes: bytes):
    """Return list of {"text": ..., "page": page_number(1-indexed)}."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    chunks = []
    for page_index in range(len(doc)):
        page_number = page_index + 1
        text = doc.load_page(page_index).get_text("text")
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

        buffer = ""
        for para in paragraphs:
            if len(buffer) + len(para) <= CHUNK_TARGET_CHARS:
                buffer = f"{buffer}\n\n{para}".strip()
            else:
                if buffer:
                    chunks.append({"text": buffer, "page": page_number})
                buffer = para
        if buffer:
            chunks.append({"text": buffer, "page": page_number})
    doc.close()
    return chunks


def add_document(pdf_bytes: bytes, filename: str, summary: dict | None = None) -> dict:
    """Chunk, embed, and permanently store a PDF as a new library entry.
    Returns the new library entry's metadata dict."""
    embedder = _load_embedder()
    chunks = _extract_page_chunks(pdf_bytes)

    client = _get_chroma_client()
    doc_id = uuid.uuid4().hex[:12]
    collection_name = f"doc_{doc_id}"
    collection = client.create_collection(name=collection_name)

    texts = [c["text"] for c in chunks]
    if texts:
        embeddings = embedder.encode(texts, show_progress_bar=False).tolist()
        ids = [str(i) for i in range(len(chunks))]
        metadatas = [{"page": c["page"]} for c in chunks]
        collection.add(ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas)

    entry = {
        "id": doc_id,
        "collection_name": collection_name,
        "filename": filename,
        "num_pages": chunks[-1]["page"] if chunks else 0,
        "num_chunks": len(chunks),
        "added_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "summary": summary or {},
        "content_hash": _hash_bytes(pdf_bytes),
    }
    library = _load_library()
    library.append(entry)
    _save_library(library)
    return entry


def delete_document(doc_id: str):
    library = _load_library()
    entry = next((d for d in library if d["id"] == doc_id), None)
    if entry is None:
        return
    client = _get_chroma_client()
    try:
        client.delete_collection(entry["collection_name"])
    except Exception:
        pass
    library = [d for d in library if d["id"] != doc_id]
    _save_library(library)


def _parse_amount(value) -> float | None:
    """Best-effort parse of a currency string like '$1,234.50' into 1234.50."""
    if value is None:
        return None
    cleaned = re.sub(r"[^\d.\-]", "", str(value))
    if not cleaned or cleaned in ("-", "."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _answer_question_single(client_ai, doc_entry: dict, question: str):
    embedder = _load_embedder()
    chroma_client = _get_chroma_client()
    collection = chroma_client.get_collection(doc_entry["collection_name"])

    q_emb = embedder.encode([question], show_progress_bar=False).tolist()
    results = collection.query(query_embeddings=q_emb, n_results=TOP_K_SINGLE)
    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]

    if not documents:
        return "I couldn't find anything relevant in this document.", []

    context_parts, pages_used = [], []
    for doc_text, meta in zip(documents, metadatas):
        page = meta.get("page")
        pages_used.append(page)
        context_parts.append(f"[Page {page}]\n{doc_text}")
    context = "\n\n---\n\n".join(context_parts)

    prompt = SINGLE_DOC_PROMPT_TEMPLATE.format(context=context, question=question)
    answer_text = client_ai.chat(prompt)
    return answer_text, sorted(set(pages_used))


def _answer_question_all(client_ai, library: list, question: str):
    embedder = _load_embedder()
    chroma_client = _get_chroma_client()
    q_emb = embedder.encode([question], show_progress_bar=False).tolist()

    per_doc_best = []   # guaranteed: best chunk from every document
    leftover_hits = []  # everything else, for topping up if there's budget left

    for entry in library:
        try:
            collection = chroma_client.get_collection(entry["collection_name"])
        except Exception:
            continue
        results = collection.query(query_embeddings=q_emb, n_results=TOP_K_PER_DOC)
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0] if results.get("distances") else [0] * len(documents)
        if not documents:
            continue

        hits = [
            {
                "text": t,
                "page": m.get("page"),
                "filename": entry["filename"],
                "distance": d if d is not None else 0,
            }
            for t, m, d in zip(documents, metadatas, distances)
        ]
        hits.sort(key=lambda h: h["distance"])
        per_doc_best.append(hits[0])
        leftover_hits.extend(hits[1:])

    if not per_doc_best:
        return "I couldn't find anything relevant across the saved documents.", []

    coverage_note = ""
    if len(library) <= MAX_DOCS_FOR_GUARANTEED_COVERAGE:
        # Guarantee every document contributes at least one chunk, then fill
        # any remaining budget with the next-best matches library-wide.
        leftover_hits.sort(key=lambda h: h["distance"])
        budget = max(GLOBAL_TOP_K - len(per_doc_best), 0)
        top_hits = per_doc_best + leftover_hits[:budget]
    else:
        # Library too large to guarantee full coverage without an oversized
        # prompt — fall back to library-wide top matches and say so.
        all_hits = per_doc_best + leftover_hits
        all_hits.sort(key=lambda h: h["distance"])
        top_hits = all_hits[:GLOBAL_TOP_K]
        coverage_note = (
            f"\n\n(Note: this library has {len(library)} documents — this answer is based on "
            "the most relevant matches found, not a guaranteed scan of every single file.)"
        )

    top_hits.sort(key=lambda h: h["distance"])
    context_parts, citations = [], []
    for hit in top_hits:
        context_parts.append(f"[{hit['filename']} - Page {hit['page']}]\n{hit['text']}")
        citations.append(f"{hit['filename']} (p.{hit['page']})")
    context = "\n\n---\n\n".join(context_parts)

    prompt = ALL_DOCS_PROMPT_TEMPLATE.format(context=context, question=question)
    answer_text = client_ai.chat(prompt) + coverage_note
    return answer_text, citations


def _try_meta_answer(library: list, question: str) -> str | None:
    """Answer questions ABOUT the library itself (counts, listings, page totals)
    directly from the stored metadata — no AI call, so it can't undercount or
    hallucinate. Content questions ('what does X say') still go through the
    normal semantic search path."""
    q = question.lower()

    count_keywords = [
        "how many document", "how many file", "how many pdf", "how many doc",
        "number of document", "number of file", "number of pdf",
        "total document", "total file", "total pdf",
        "count of document", "count of file",
    ]
    if any(kw in q for kw in count_keywords):
        return f"There are **{len(library)}** documents currently saved in Company Brain."

    list_keywords = [
        "list all document", "list the document", "list document", "list all file",
        "list all pdf", "show me all document", "show all document",
        "which documents", "what documents do", "what documents are", "what files do",
    ]
    if any(kw in q for kw in list_keywords):
        lines = "\n".join(
            f"- {d['filename']} ({d['num_pages']}p, added {d['added_at']})" for d in library
        )
        return f"Here are all {len(library)} documents currently saved:\n\n{lines}"

    page_keywords = ["how many pages", "total pages", "total number of pages"]
    if any(kw in q for kw in page_keywords):
        total_pages = sum(d.get("num_pages", 0) for d in library)
        return f"There are **{total_pages}** pages total across all {len(library)} saved documents."

    return None


def render(client):
    library = _load_library()

    if not library:
        st.info(
            "No documents saved yet. Click **⬆️ Upload Document** in the sidebar "
            "to add your first one."
        )
        return

    # --- Deterministic quick total across the whole library (not AI-guessed) ---
    parsed = [(d["filename"], _parse_amount(d.get("summary", {}).get("total_amount"))) for d in library]
    valid = [(f, v) for f, v in parsed if v is not None]
    missing = [f for f, v in parsed if v is None]
    if valid:
        total_sum = sum(v for _, v in valid)
        st.metric("💰 Quick Total across saved documents", f"{total_sum:,.2f}")
        caption = f"Calculated from {len(valid)} of {len(library)} document(s)."
        if missing:
            caption += f" No amount detected in: {', '.join(missing)}."
        st.caption(caption)
        st.divider()

    # --- Manage / bulk-delete documents ---
    manage_label_to_id = {
        f"{d['filename']}  ·  {d['num_pages']}p  ·  added {d['added_at']}": d["id"] for d in library
    }
    with st.expander(f"🗑️ Manage Documents ({len(library)} saved)"):
        selected_labels = st.multiselect(
            "Select documents to delete",
            list(manage_label_to_id.keys()),
            key="manage_delete_select",
        )
        col_del_sel, col_del_all = st.columns(2)

        with col_del_sel:
            if st.button(
                "🗑️ Delete Selected",
                disabled=not selected_labels,
                use_container_width=True,
                key="manage_delete_selected_btn",
            ):
                for lbl in selected_labels:
                    did = manage_label_to_id[lbl]
                    delete_document(did)
                    st.session_state.pop(f"brain_chat_{did}", None)
                st.success(f"Deleted {len(selected_labels)} document(s).")
                st.rerun()

        with col_del_all:
            confirm_all = st.checkbox(
                f"I understand this deletes all {len(library)} document(s)",
                key="manage_confirm_delete_all",
            )
            if st.button(
                "⚠️ Delete ALL Documents",
                disabled=not confirm_all,
                use_container_width=True,
                key="manage_delete_all_btn",
            ):
                for d in library:
                    delete_document(d["id"])
                    st.session_state.pop(f"brain_chat_{d['id']}", None)
                st.session_state.pop("brain_chat_all", None)
                st.success("All documents deleted.")
                st.rerun()

    st.divider()

    # --- Document / All-Documents selector ---
    label_to_id = manage_label_to_id
    all_docs_label = "🔎 All Documents (search everything)"
    mode_options = [all_docs_label] + list(label_to_id.keys())
    choice_label = st.selectbox("Ask about", mode_options)
    is_all_mode = choice_label == all_docs_label

    doc_entry = None
    if not is_all_mode:
        doc_id = label_to_id[choice_label]
        doc_entry = next(d for d in library if d["id"] == doc_id)

        _, col_delete = st.columns([3, 1])
        with col_delete:
            if st.button("🗑️ Delete this document", use_container_width=True):
                delete_document(doc_id)
                st.session_state.pop(f"brain_chat_{doc_id}", None)
                st.rerun()

        if doc_entry.get("summary"):
            with st.expander("📋 Extracted summary (from upload)"):
                for k, v in doc_entry["summary"].items():
                    if v and k != "line_items":
                        st.write(f"**{k.replace('_', ' ').title()}:** {v}")

    if client is None:
        st.warning("Add an API key in the sidebar (Claude or Gemini) to ask questions.")
        return

    history_key = "brain_chat_all" if is_all_mode else f"brain_chat_{doc_entry['id']}"
    widget_key = "all" if is_all_mode else doc_entry["id"]

    placeholder = (
        "e.g. What's the overall expense across all invoices?"
        if is_all_mode
        else "e.g. What is the return policy for damaged goods?"
    )
    question = st.text_input("Ask a question", placeholder=placeholder, key=f"q_{widget_key}")
    ask = st.button("Ask", type="primary", key=f"ask_{widget_key}")

    if ask and question.strip():
        with st.spinner("Searching and drafting answer..."):
            try:
                if is_all_mode:
                    meta_answer = _try_meta_answer(library, question.strip())
                    if meta_answer:
                        answer = meta_answer
                    else:
                        answer, _ = _answer_question_all(client, library, question.strip())
                else:
                    answer, _ = _answer_question_single(client, doc_entry, question.strip())
            except Exception as e:
                st.error(f"Something went wrong: {e}")
                return
        st.session_state.setdefault(history_key, []).append(
            {"question": question.strip(), "answer": answer}
        )

    for turn in reversed(st.session_state.get(history_key, [])):
        st.markdown(f"**Q: {turn['question']}**")
        st.write(turn["answer"])
        st.divider()
