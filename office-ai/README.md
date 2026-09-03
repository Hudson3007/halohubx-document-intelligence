# HalohubX Smart Office AI

A 3-page Streamlit app:
- **🏠 Dashboard (Company Brain)** — ask questions across a library of saved documents, with per-document or search-everything modes
- **⬆️ Upload Document** — preview, extract key fields from, and save invoices/receipts/contracts into Company Brain
- **🔍 Review Queue** — human-in-the-loop review of documents flagged low-confidence by the separate `halohubx-api` backend, before they're pushed to a client's ERP

---

## Step 1 — Install Python dependencies

```bash
cd smart-office-ai
python -m venv venv
venv\Scripts\activate          # Windows. Mac/Linux: source venv/bin/activate
pip install -r requirements.txt
```

## Step 2 — Add your AI key

You only need ONE of these two (pick whichever you're using):
- **Gemini (free tier)**: get a key at https://aistudio.google.com/apikey — no card required
- **Claude (paid)**: get a key at https://console.anthropic.com/

You can either:
- Just run the app (`streamlit run app.py`) and paste the key into the sidebar — it auto-saves to a local `.env` file after that, so you only type it once, OR
- Copy `.env.example` to `.env` yourself and fill it in before running

## Step 3 — Run the app

```bash
streamlit run app.py
```

Opens at `http://localhost:8501`. Try the **Dashboard** and **Upload Document** pages first — these work standalone, no other setup needed.

## Step 4 — (Optional) Set up the Review Queue page

The Review Queue page is different from the other two — it talks to a **separate backend service** (`halohubx-api`) instead of local files, because that's where multi-tenant partner data actually lives. If you don't have that backend built/running yet, the Review Queue page will just show a warning telling you to add a Backend URL + Partner API Key — the other two pages work completely fine without it.

If you do have `halohubx-api` set up (see that project's own README):
1. Make sure it's running (locally: `uvicorn app.main:app --reload`, or deployed on Render)
2. In this app's sidebar, under "Review Queue Backend":
   - **Backend API URL**: `http://127.0.0.1:8000` for local, or your Render URL if deployed
   - **Partner API Key**: the `hhx_...` key from that backend's `create_partner.py` script
3. Switch to the **🔍 Review Queue** page in the sidebar

## Folder structure

```
smart-office-ai/
├── app.py                     — main entry point, sidebar, page router
├── modules/
│   ├── ai_client.py            — Claude/Gemini provider wrapper
│   ├── company_brain.py         — Dashboard page (document library + Q&A)
│   ├── document_reader.py        — Upload page (extraction)
│   ├── review_queue.py            — Review Queue page (talks to halohubx-api)
│   └── theme.py                    — HalohubX branding/CSS
├── bulk_import.py             — CLI: bulk-import a whole folder of PDFs
├── requirements.txt
├── .env.example
├── .gitignore
└── .streamlit/
    └── config.toml             — native Streamlit color theme
```

## Where your data lives

- `data/library.json` + `data/chroma_store/` — your Company Brain document library (created automatically on first save). This is local to your machine and gitignored.
- `.env` — your API keys. Also local and gitignored — never commit this.

## Bulk-importing lots of PDFs

For a handful of files, use the Upload page directly. For dozens, use the "📦 Bulk-add" section that appears on the Upload page when you select multiple files at once. For hundreds or thousands, use the CLI script instead — it reads straight from a folder on disk:

```bash
python bulk_import.py "C:\path\to\your\pdfs"
python bulk_import.py "C:\path\to\your\pdfs" --extract   # also runs AI extraction per file (slower)
```
Safe to re-run — anything already imported is automatically skipped.

## Deploying

Push to GitHub, then deploy on **Streamlit Community Cloud** (https://share.streamlit.io) — connect the repo, set the main file to `app.py`, and add your API keys under that app's **Secrets** panel instead of relying on the local `.env` (which won't exist on the server). See `app.py`'s `_get_saved_key()` function — it already checks Streamlit secrets first, `.env` second, so no code changes needed to deploy.

**Note on Python version:** if Streamlit Cloud's dependency install fails, go to your app's Settings → Advanced Settings → Python version, and set it to **3.11 or 3.12** (some pinned package versions here don't have prebuilt wheels for the very newest Python versions yet).
