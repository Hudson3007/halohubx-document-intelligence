# HaloHubX Document Intelligence — single software

One codebase combining the FastAPI Document Intelligence API (the app core),
the React HITL staging UI, an Office AI Streamlit workspace with a Company
Brain RAG document library, an on-prem Tally sync agent, and Docker packaging.

A multi-tenant backend: partners upload PDFs, get back GST-aware extraction
with per-field confidence scores, optionally receive the result via webhook,
and can review/correct the extraction in a human-in-the-loop (HITL) UI before
it's pushed to an ERP (Odoo, Zoho, or Tally via the sync agent).

## What's built so far
- **Backend core (`app/`)** — Partner → Client → Document schema
  - `POST /upload` — submit a PDF, returns extraction synchronously
  - `GET /status/{id}`, `GET /retrieve/{id}` — lifecycle + extraction result
  - `GET /documents?review_status=needs_review`, `GET /documents/{id}/review`,
    `GET /documents/{id}/file`, `POST /documents/{id}/approve` — HITL review
    queue with the original PDF stored for side-by-side preview
  - **Usage / credit metering** — `GET /usage` plus page-based charging on
    upload (1 credit = 1 PDF page). Hybrid billing: a monthly quota that
    resets each period **plus** a never-resetting top-up pool; extract is
    blocked with `HTTP 402` when credits run out.
  - Retrying webhook delivery; per-field confidence scores; multi-invoice JSON
  - Auth: `Authorization: Bearer <api_key>` for the API, **plus console login**
    (`POST /auth/signup`, `POST /auth/login`) with email+password, session
    tokens, and **RBAC** — owners do everything, analysts review/approve but
    can't upload or manage members (enforced server-side via `require_role`,
    verified: analyst gets `HTTP 403` on owner actions). Owners invite
    teammates with a one-time **invite link** (`/auth/invite/<token>`); the
    invitee sets their own password.
  - **Audit log** — `GET /audit` records uploads, review edits, approvals,
    logins and member changes (actor, role, document, timestamp) for the
    Activity screen.
- **Marketing website (`site/`)** — a polished React + Vite landing page
  showcasing the product: how it works, the HITL flow, measured field-level
  accuracy charts, and pricing (a scrollable demo, ready to serve publicly)
- **Benchmark harness (`benchmark.py` + `benchmark/` + `label_gold.py`)**
  — measures field-level accuracy of the real extraction pipeline against
  human-labelled ground truth, writes an HTML report, and auto-refreshes the
  site's accuracy chart. The site refuses to headline a number until a
  representative labelled sample exists (see "Benchmark & accuracy" below).
- **HITL UI (`frontend/`)** — React/Vite console: email+password **login /
  signup** (self-serve onboarding provisions a partner + API key), review the
  side-by-side extracted data, correct fields, approve & push to ERP, plus
  Usage meter, **Activity** (audit) and **Members** (owner-only) screens
- **Office AI (`office-ai/`)** — Streamlit workspace:
  - **Company Brain** — a local RAG library: ask questions across saved
    documents (ChromaDB + embeddings) with per-page/document citations
  - **Document Reader** — upload PDF/image, extract, export to Excel
  - **Review Queue** — connects to the backend's `/documents*` endpoints
    (ordered by the low-confidence flag) and lets a human approve/correct
- **On-prem sync agent (`sync-agent/`)** — watches a folder, uploads PDFs,
  polls the API, writes JSON **and Tally-ready XML**
- **Docker packaging** — `Dockerfile` + `docker-compose.yml` (api + postgres + ui + office-ai + optional sync-agent)

## Requirements
- Python 3.10/3.11, and a Postgres database (Supabase works — no AWS needed)
- A Gemini (`GEMINI_API_KEY`) or Claude (`ANTHROPIC_API_KEY`) key
- Node.js for the UI (frontend development/build)

## 1. Set up a database (Supabase — no AWS account needed)
1. Go to https://supabase.com, sign up, create a new project
2. Project Settings → Database → Connection string → copy the URI (starts with `postgresql://...`)

## 2. Backend setup
```bash
python -m venv venv
venv\Scripts\activate            # Windows; Unix: source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```
Edit `.env`: paste your Supabase `DATABASE_URL`, and your `GEMINI_API_KEY` (or `ANTHROPIC_API_KEY`) — reuse the same keys as the Streamlit app.

## 3. Create the tables
```bash
python -m app.init_db
```

## 4. Create your first partner
```bash
python create_partner.py "Vamsi Integrations"
```
This prints an API key — **save it**, it's shown only once. Partners use it as `Authorization: Bearer <key>`.

## 5. Run the API
```bash
uvicorn app.main:app --reload
```
Visit http://127.0.0.1:8000/docs for an interactive API tester.

## 6. Run the HITL UI
```bash
cd frontend
npm install
npm run dev        # http://localhost:5173 — connects to the API on :8000
```
Sign in with **Create account** (self-serve onboarding — email + password, a
partner workspace and API key are provisioned instantly) or **Sign in** with an
existing account. From there: upload a PDF, review the extraction, approve it,
and watch the **Activity** log + **Members** (owners invite teammates via a
one-time link — the invitee sets their own password).

## 6a. Run the marketing website
```bash
cd site
npm install
npm run dev        # http://localhost:5174 — scrollable product demo
```

## 6b. Run Office AI (Streamlit workspace)
```bash
cd office-ai
venv\Scripts\activate        # or: pip install -r requirements.txt in a fresh venv
streamlit run app.py         # http://localhost:8501
```
- **Upload / Document Reader** builds a local Company Brain (ChromaDB) library.
- **Review Queue** connects to the backend: set the sidebar "Backend API URL" and
  "Partner API Key" (or put `HALOHUBX_BACKEND_URL` / `HALOHUBX_PARTNER_API_KEY`
  in `office-ai/.env`, which is pre-seeded when you run the merged project).

## 7. Test with curl
```bash
curl -X POST http://127.0.0.1:8000/upload \
  -H "Authorization: Bearer hhx_YOUR_KEY_HERE" \
  -F "file=@test_invoice.pdf" \
  -F "client_name=Test Client Co" \
  -F "webhook_url=https://webhook.site/your-test-url"
```

## 8. On-prem Tally sync agent
```bash
cd sync-agent
pip install -r requirements.txt
python agent.py --input ./inbox --output ./out --archive ./archive \
  --api http://localhost:8000 --api-key hhx_YOUR_KEY_HERE --client-name "Acme Trading Co"
```
Drops PDFs in `inbox/`; extracted JSON + Tally XML land in `out/`; processed PDFs are archived.

## 9. Docker
```bash
docker compose up --build            # api(:8000) + db + ui(:8080) + site(:8081) + office-ai(:8501)
docker compose --profile onprem up   # also start the sync agent
```
The entrypoint creates tables and a default partner on a fresh database
(its API key is printed in the api container logs). Pass `HALOHUBX_PARTNER_API_KEY`
to wire the Office AI Review Queue to the api service.

> The compose config has been statically validated (all services, volumes,
> ports, env wiring) but was **not booted here** — Docker isn't installed on
> the build machine. See **[`DOCKER_VALIDATION.md`](DOCKER_VALIDATION.md)** for
> the exact acceptance runbook to prove boot + the upload→confirm→ERP path on
> any machine that has Docker.

## 10. Benchmark & accuracy (measured, not claimed)
The accuracy chart on the marketing site comes from this benchmark, never from
marketing estimates. It runs the real extraction pipeline over a golden set and
compares every field to ground truth a human verified from the source PDF.

```bash
# 1. Drop PDFs you want measured into  benchmark/pdfs/
# 2. Label the first invoice's fields for each PDF (reads the PDF, not the AI):
python label_gold.py            # interactive; --resume to continue partial sets
# 3. Measure:
python benchmark.py --pdfs benchmark/pdfs --gold benchmark/gold.json \
  --out benchmark/reports/report.html
```
- Produces `reports/report.html` (per-field + per-document detail) and the
  terminal summary.
- Auto-writes `site/public/benchmark.json`, which the site fetches at runtime,
  so the Accuracy section shows the freshest measured numbers on refresh.
- The site does **not** headline an overall % until the sample is large enough
  (≥ 5 labelled documents); smaller runs show the number with an explicit
  "small sample — not a product accuracy claim" disclosure, or a "no published
  figure yet" state when there's no measurement.

> Use real, independently-labelled PDFs for a number you'd show a client.
> Labelling from the AI's own output would only measure self-consistency.

## Honest limitations of the MVP
- **Confidence scores are the AI's self-reported estimate** — good for triage, not a "% accurate" claim.
- **Processing is synchronous** — `/upload` waits for extraction. High-volume PDFs later should move to a background job queue.
- **No partner dashboard yet** — onboarding partners is a CLI script.
- **Source PDFs are now retained** for the HITL review preview (stored under `data/uploads/`). The `create_partner.py --zero-retention` flag no longer removes stored files automatically — if you need zero-retention, delete files from `UPLOAD_DIR` (or set `UPLOAD_DIR` to an ephemeral path) on the retention schedule you require.
