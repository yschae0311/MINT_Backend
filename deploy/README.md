# MINT Backend — systemd (EC2)

## 1. Prerequisites

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv

cd ~/MINT_Backend
cp .env.example .env   # DATABASE_URL, REDIS_URL, GEMINI_API_KEY, JWT_SECRET_KEY, CORS_ORIGINS
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

## 3. Start

```bash
sudo systemctl start mint-api
sudo systemctl start mint-celery-worker mint-celery-beat
```

API only (no scheduler):

```bash
sudo systemctl start mint-api
```

## 4. Logs & health

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
