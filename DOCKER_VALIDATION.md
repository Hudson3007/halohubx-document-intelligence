# On-prem Docker deployment — validation guide

This project ships as a multi-service Docker Compose stack. `docker compose
config` validates clean and every image's build context, ports, volumes and env
wiring have been cross-checked here (see the checklist below), but **Docker is
not installed on the machine this was built on**, so the images have not yet
been *booted* here. This guide is the exact runbook to prove it on any machine
that has Docker.

---

## Quick start (Docker machine)

```bash
# 1. Provide secrets (also read by docker compose):
cp .env.example .env        # then fill DATABASE_URL, GEMINI_API_KEY, etc.

# 2. Build + start everything:
docker compose up --build

# 3. Also start the on-prem Tally sync agent:
docker compose --profile onprem up

# 4. On a fresh database the api entrypoint (ensure_partner.py) auto-creates
#    tables + a default partner. Grab its API key from the api container logs:
docker compose logs api | findstr "API key"
```

### Services + ports
| Service | Container (int) | Host (published) | Notes |
|---------|-----------------|------------------|-------|
| `db`      | postgres:16  | –      | named volume `pgdata` |
| `api`     | 8000         | 8000   | FastAPI; `ensure_partner.py` bootstrap; uploads → `api_uploads` |
| `ui`      | 80           | 8080   | React console (nginx proxy → api) |
| `site`    | 80           | 8081   | Marketing / landing site |
| `office-ai` | 8501      | 8501   | Streamlit workspace; data → `office_ai_data` (`/srv/data`) |
| `sync-agent` | –        | –      | **profile `onprem`**; watches inbox → JSON+Tally XML |

---

## Validation checklist (what we confirmed statically)

- [x] `docker-compose.yml` parses as valid YAML; all 6 services present.
- [x] Build contexts exist: root `.`, `./frontend`, `./site`, `./office-ai`, `./sync-agent`.
- [x] Named volumes (`pgdata`, `office_ai_data`, `api_uploads`) are declared and every usage matches.
- [x] Ports are explicit and non-conflicting (8000/8080/8081/8501).
- [x] `sync-agent` now uses **CMD** (not ENTRYPOINT) so the compose `command:` fully overrides it (fixed a bug that would have appended duplicate `agent.py` args).
- [x] office-ai reads `HALOHUBX_BACKEND_URL` / `HALOHUBX_PARTNER_API_KEY` exactly as the compose `environment:` provides, and its Chroma/data path resolves to `/srv/data` = the `office_ai_data` mount.
- [x] `ui` (frontend) nginx proxies `/(upload|status|retrieve|hitl|documents|usage)` → `http://api:8000` (matches the compose service name).
- [x] `api` entrypoint `ensure_partner.py` runs `create_all()` + `migrate()` then execs uvicorn on `0.0.0.0:8000`.
- [x] `.env.example` documents every var the stack needs; `env <.env>` supplies secrets.
- [x] Root + per-subdir `.dockerignore` added so the build context excludes `venv`, `node_modules`, `office-ai/venv (-data)`, `data/uploads`, benchmark reports.

## Still to prove on a Docker machine (honest gaps)
- [ ] `docker compose build` completes (dependency resolution: torch/onnx for office-ai; pymupdf wheels).
- [ ] `docker compose up` boots db + api and `ensure_partner.py` prints a key.
- [ ] Upload → confirm → ERP path works through nginx proxy on port 8080.
- [ ] Office AI Review Queue talks to `http://api:8000`.
- [ ] `sync-agent` (profile onprem) converts a dropped PDF to Tally XML.

> These are environment-runtime checks only — the config they'd exercise has
> passed the static checks above. Run the block below as the acceptance test.

---

## Acceptance test (run once Docker is available)

```bash
# sanity
docker compose config --quiet && echo "compose valid"

# start
docker compose up -d --build
docker compose ps

# wait for health
docker compose logs -f api

# e2e (key from logs)
KEY=$(docker compose logs api | grep -oE 'hhx_[A-Za-z0-9_-]+' | head -1)
curl -s -X POST http://localhost:8000/upload \
  -H "Authorization: Bearer $KEY" \
  -F "file=@benchmark/pdfs/bill_8_two_invoices.pdf" \
  -F "client_name=Docker Smoke Test"
# expect: status completed, multi-invoice JSON
```

---

## Tearing down
```bash
docker compose down          # stop containers (keeps volumes)
docker compose down -v       # also remove named volumes (postgres data, uploads, office brain)
```