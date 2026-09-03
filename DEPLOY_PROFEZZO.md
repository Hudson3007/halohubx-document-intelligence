# Profezzo — Deployment & Demo Guide

Client-facing onboarding pack for the **HaloHubX ERP document layer** pitch.
This project is the full, runnable product — not a mockup — and this guide
tells you exactly how to see it working on *your* machine.

> **Please read the "Accuracy — what we actually promise" section first.**
> It replaces the earlier "92–98% accuracy" phrasing with verified, honest
> framing. Numbers on the marketing site are measured by our benchmark tool,
> not copied from marketing boilerplate.

---

## What you're getting

| Piece | Where | What it does |
|-------|-------|--------------|
| Document Intelligence API | `app/` | Turns PDFs into GST-aware JSON: vendor, invoice, line items, totals, per-field **confidence** scores, multi-invoice detection |
| Human-in-the-loop console | `frontend/` | **Email+password login/signup** (self-serve onboarding provisions your workspace + API key), review side-by-side with the original PDF, correct fields, approve & push to ERP |
| Usage & credit meter | console + `GET /usage` | 1 credit = 1 PDF page. Monthly quota **plus** top-up pool; blocked (HTTP 402) when out |
| Roles & audit | console + API | **Owner/analyst RBAC** (owners upload & invite members via a one-time link; analysts review/approve only) + a full **Activity log** (`GET /audit`) of uploads, edits, approvals and logins |
| Office AI workspace | `office-ai/` | Streamlit: "Company Brain" RAG Q&A over your documents, Document Reader, Review Queue |
| Marketing website | `site/` | The product demo / landing page |
| On-prem sync agent | `sync-agent/` | Folder watch → upload → JSON **+ Tally XML** out |
| Docker | root | One-command deployment (optional) |

**ERP integrations:** Tally (via sync agent, XML ready); Odoo / Zoho via the
same confirmed-JSON webhook (deliver the approved result to any endpoint).

---

## Architecture in one line

```
PDF ──▶ POST /upload ──▶ AI extraction (Gemini/Claude) ──▶ JSON + confidence
        ▲                                                    │
     Bearer <api_key>                                        ▼
                                                  Review queue (human)
                                                        │
                                     improve fields │ approve │ original PDF beside it
                                                  ▼
                                   confirm + webhook ──▶ your ERP
```

---

## Quick start (your machine)

### Prerequisites
- Python 3.10/3.11, Node.js 18+ (for the UI), and a Postgres database.
- **No AWS and no GPU needed.** A Supabase free Postgres + any Gemini key suffice.
- Docker is optional — everything also runs from plain terminals.

### 1. Database
We provision a Supabase Postgres for you (or point us at one you own).
You'll get a connection string like `postgresql://...`.

### 2. Backend
```bash
python -m venv venv
venv\Scripts\activate          # Unix: source venv/bin/activate
pip install -r requirements.txt
python -m app.init_db          # create tables + apply migrations
uvicorn app.main:app --reload  # http://127.0.0.1:8000/docs
```

### 3. Create your partner (billing + auth)
```bash
python create_partner.py "Profezzo" --quota 500 --topup 200
```
This prints an **API key** (shown once) and provisions 500 monthly credits plus
a 200-credit top-up pool (never resets) — see the **Credit metering** section below.

### 4. Console + website + Office AI
```bash
cd frontend && npm install && npm run dev   # console  :5173  (enter your API key to sign in)
cd site     && npm install && npm run dev   # landing  :5174
cd office-ai && pip install -r requirements.txt && streamlit run app.py   # :8501
```

### 5. Try it end-to-end
```bash
curl -X POST http://127.0.0.1:8000/upload \
  -H "Authorization: Bearer <your_api_key>" \
  -F "file=@sample_invoice.pdf" \
  -F "client_name=Acme Trading Co"
```
Then open the console → **Documents → Review** to correct fields against the
PDF, approve, and watch it push to your ERP.

---

## Docker (optional — one command)
```bash
docker compose up --build
# api:8000 · console ui:8080 · landing site:8081 · office-ai:8501 · (+ optional sync agent)
docker compose --profile onprem up
```

---

## Accuracy — what we actually promise

We do **not** quote a headline "92–98%" without proof. Instead we ship a
benchmark you can run on *your* PDFs and see the number yourself:

```bash
python benchmark.py --pdfs you/labelled/pdfs --gold you/labelled/gold.json
```

- Compares every extracted field against human-labelled ground truth.
- Reports **per-field accuracy** (vendor, GSTIN, invoice number/date, line
  items, totals) plus overall.
- Writes an HTML report **and** auto-updates the marketing site's accuracy
  chart (`site/public/benchmark.json`) — so the numbers the client sees are
  the ones we actually measured on real documents.

The AI's **confidence** score shown in the console is a triage signal
(flag fields below threshold for review). The benchmark's per-field
**accuracy** is the only place we claim a measured number.

**Ground rule we follow:** the landing page says "measured, not claimed" and
links back to this benchmark. When a real number was in the 90s we show it;
when it wasn't, we improve the extractor first rather than bury it.

---

## Credit metering (productised billing)

- **Unit:** 1 credit = 1 PDF page scanned.
- **Two pools per partner:** a monthly quota that resets each period, plus a
  top-up pool that never resets. Top-ups are consumed first.
- `POST /upload` counts PDF pages, checks the balance, and returns **HTTP 402
  `insufficient_credits`** if you're out.
- The console shows the meter (top bar) and a full **Usage** page from
  `GET /usage`.

This is the reseller-friendly model we built with you: your partners get a
quota, you can top them up, and usage is auditable per document via
`page_count`.

---

## What was fixed vs. the earlier pitch
- ~~"92–98% accuracy"~~ → **measured benchmark**, no unverifiable claims.
- ~~"AWS / GCP Mumbai + on-prem Docker"~~ → production-ready on **Supabase
  Postgres + any AI key**; Docker is provided but optional and validated config
  (install Docker to run it on-prem).
- ~~"Zero retention"~~ — **documented honestly**: originals are retained under
  `data/uploads/` because the human review needs the source PDF beside the
  extraction.

---

## Security & isolation
- Multi-tenant: a partner sees only its own clients' documents (`Partner →
  Client → Document`).
- Every API call requires `Authorization: Bearer <api_key>` (server-to-server:
  ERP, sync agent, Office AI). Console logins use **email + password** with
  PBKDF2-hashed passwords and a revocable per-user session token.
- **Roles are enforced server-side** (`require_role`): an analyst cannot call
  owner endpoints (upload, member management) and receives `HTTP 403`.
- Keys are generated server-side and stored salted/hashed scope: `.env` is
  gitignored; never commit secrets. Don't share a key with a client you
  can't revoke — one partner = one key. Human access is audited in the
  Activity log, so you can always see who approved what.
- **Database least-privilege (`app/db.py` guardrails):** the API refuses to
  connect as a DB superuser (`postgres`/`admin`/`root`) unless
  `ALLOW_SUPERUSER_DB=1` is explicitly set. In production create a
  dedicated app role that owns only the schema/tables this app uses and run
  `python -m app.init_db` migrations once as an admin, then point
  `DATABASE_URL` at the app role. Remote (internet) connections are forced
  over TLS (`sslmode=require`) unless `DB_SSL_DISABLE=1`.
- **Upload + webhook safety:** uploads are capped at `MAX_UPLOAD_BYTES`
  (25 MB) and strictly validated (PDF magic bytes + a real parse — a file
  renamed to `.pdf` is rejected). Outbound webhook URLs are SSRF-guarded:
  only public `https://` targets are allowed; loopback/RFC1918/cloud-metadata
  hosts are blocked at the single delivery point.

---

## Support / next steps
We'll walk the sample-set benchmark through your first batch of real PDFs
and refresh the site's accuracy chart with the result. Ask us for:
- a branded single-partner build (your logo/name in the console + site),
- lock-in of a specific ERP webhook payload,
- a hosted deployment so your customers sign in online.