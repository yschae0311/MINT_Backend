# MINT Backend — systemd (EC2)

## 1. Prerequisites

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv

cd ~/MINT_Backend
cp .env.example .env   # DATABASE_URL, REDIS_URL, GEMINI_API_KEY, JWT_SECRET_KEY, CORS_ORIGINS, FRONTEND_URL
PYTHON=python3.12 ./scripts/setup.sh
```

## 2. Install units

```bash
chmod +x deploy/install-systemd.sh
./deploy/install-systemd.sh
```

Custom paths:

```bash
APP_DIR=/home/ubuntu/MINT_Backend SVC_USER=ubuntu ./deploy/install-systemd.sh
```

## 3. Redis (required for Celery)

```bash
sudo apt install -y redis-server
sudo systemctl enable redis-server
sudo systemctl start redis-server
redis-cli ping   # PONG
```

If Redis stops on reboot, Celery worker cannot consume tasks even though `systemctl status` shows running.

## 4. Start

```bash
sudo systemctl start mint-api
sudo systemctl start mint-celery-worker mint-celery-beat
```

API only (no scheduler):

```bash
sudo systemctl start mint-api
```

## 5. Logs & health

```bash
sudo systemctl status mint-api
journalctl -u mint-api -f
curl http://localhost:8100/api/v1/health
```

## Common mistake

`ExecStart` must point to **venv** binaries:

```
/home/ubuntu/MINT_Backend/.venv/bin/uvicorn
```

Not `/usr/bin/uvicorn` — that uses system Python 3.14 without FastAPI.

After code or dependency changes:

```bash
cd ~/MINT_Backend && git pull
source .venv/bin/activate && pip install -r requirements.txt && pip install --no-deps .
sudo systemctl restart mint-api mint-celery-worker mint-celery-beat
```

## Daily pipeline (KST)

| Time | Task |
|------|------|
| 05:30 | 미승인 AI 발견 후보 자동 삭제 (기본 14일 초과 pending) |
| 06:00 | 크롤 + 디스커버리 파이프라인 |
| 08:00 | 데일리 리포트 생성 (당일 KST 수집분) |
| 08:30 | Slack 전송 |

Manual full run (crawl → report → slack):

```bash
celery -A app.workers.celery_app call app.workers.tasks.daily_pipeline_task
journalctl -u mint-celery-worker -f
```

Prerequisites: active sources in DB, `GEMINI_API_KEY`, Slack webhook (`purpose=daily` or `all`).
