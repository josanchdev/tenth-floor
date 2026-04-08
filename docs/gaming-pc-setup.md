# Gaming PC Setup Guide

The gaming PC runs the pipeline entirely inside Docker. No Python, no venv, no coding required — just Docker, Git, and your `.env` file.

---

## One-time setup

### 1. Install prerequisites

Install **Docker Desktop** (includes Docker Compose):
- Download from https://www.docker.com/products/docker-desktop/
- During install, enable **WSL 2** backend when prompted (required for GPU access on Windows)

Install **NVIDIA Container Toolkit** (gives Docker access to the GPU):
- Follow: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html
- For Windows + WSL 2: install the toolkit inside WSL, not on Windows directly

Verify GPU access works:
```bash
docker run --rm --gpus all nvidia/cuda:12.0-base-ubuntu22.04 nvidia-smi
```
You should see your RTX 5090 listed.

### 2. Clone the repo

```bash
git clone https://github.com/josanchdev/tenth-floor.git
cd tenth-floor
```

### 3. Create your `.env`

```bash
cp .env.5090 .env
```

Open `.env` and fill in the four secrets (everything else is already set for the 5090):

| Variable | Where to get it |
|---|---|
| `LANGFUSE_PUBLIC_KEY` | cloud.langfuse.com → your project → API keys |
| `LANGFUSE_SECRET_KEY` | same page |
| `DISCORD_WEBHOOK_URL` | Discord server → Integrations → Webhooks |
| `DISCORD_TWEET_WEBHOOK_URL` | second webhook for tweet drafts (optional) |

### 4. Pull the vLLM image and download model weights

This only happens once. Weights (~20 GB) are cached in a Docker volume.

```bash
docker compose pull vllm
./run.sh --dry-run
```

The first run takes 10–20 minutes (model download). Every run after that starts in ~2 minutes.

---

## Daily usage

```bash
git pull          # pick up any improvements pushed from the dev machine
./run.sh          # start vLLM, run pipeline, post to Discord, tear down
```

That's it. When the pipeline finishes, Docker shuts everything down — GPU goes back to idle.

### Other commands

```bash
./run.sh --dry-run              # run without writing to DB or posting to Discord
./run.sh --outcomes-only        # resolve pending signals (no GPU needed)
./run.sh --reset-db             # wipe signal DB and start fresh
./run.sh --asset-class crypto   # crypto universe only
./run.sh BTCUSDT AAPL SPY       # specific symbols only
```

---

## Checking output

Signals are posted directly to your Discord channel.

To see the pipeline logs on the gaming PC:

```bash
# Live output during a run
docker compose logs -f pipeline

# After the run completes
cat logs/pipeline_YYYY-MM-DD.log
```

---

## Updating to a new version

```bash
git pull
docker compose build pipeline   # rebuild pipeline image with new code
./run.sh
```

The vLLM image and model weights do not need to be re-downloaded unless you change `VLLM_MODEL` in `.env`.

---

## Troubleshooting

**vLLM fails to start / OOM error**
- Check `docker compose logs vllm`
- Try lowering `VLLM_GPU_UTIL` from `0.92` to `0.88` in `.env`

**Pipeline exits before vLLM is ready**
- The healthcheck retries for up to 10 minutes. If vLLM takes longer on first boot (downloading weights), increase `retries` in `docker-compose.yml` or just re-run `./run.sh`

**"permission denied" on run.sh (Linux / WSL)**
```bash
chmod +x run.sh
```

**No signals posted to Discord**
- Run with `--dry-run` first and check the logs — the funnel summary shows where the pipeline stopped
