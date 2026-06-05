# MINT Backend

MotrexEV Intelligence & News Tracker API (FastAPI).

## Quick start

```bash
cd MINT_Backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e .                    # local dev
# pip install -r requirements.txt && pip install --no-deps .   # production / AWS (Python 3.11+)
cp .env.example .env   # default: SQLite ./mint_dev.db
uvicorn app.main:app --reload --port 8100
```

- Health: `GET http://localhost:8100/api/v1/health`
- Auth: `POST /api/v1/auth/register`, `POST /api/v1/auth/login`, `GET /api/v1/auth/me`
- Seed admin (optional): see `.env` (`SEED_ADMIN_EMAIL` / `SEED_ADMIN_PASSWORD`)

**Tables:** On startup the app runs `create_all` — missing tables are created automatically (SQLite & PostgreSQL). You do **not** need `alembic upgrade head` for first run.

## PostgreSQL

Use `docker compose` from repo root, then set in `.env`:

```
DATABASE_URL=postgresql+psycopg://mint_user:mint_password@localhost:5432/mint_db
```

The DB user must be allowed to `CREATE TABLE` on schema `public`. If `alembic upgrade head` fails with `permission denied for schema public`, grant privileges or use `create_all` via app startup instead.

## AWS / EC2 (production)

**Do not** use system `uvicorn` (`/usr/lib/python3/dist-packages`). Use a project venv.

```bash
# Ubuntu: install Python 3.12 (avoid 3.14 — dependency issues)
sudo apt update
sudo apt install -y python3.12 python3.12-venv

cd MINT_Backend
cp .env.example .env   # edit DATABASE_URL, REDIS_URL, GEMINI_API_KEY, JWT_SECRET_KEY

chmod +x scripts/setup.sh scripts/start-api.sh
./scripts/setup.sh
./scripts/start-api.sh          # http://0.0.0.0:8100
```

Manual equivalent:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install --no-deps .
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8100
```

Verify: `curl http://localhost:8100/api/v1/health`

## Celery worker

```bash
source .venv/bin/activate
celery -A app.workers.celery_app worker -l info
celery -A app.workers.celery_app beat -l info
```
