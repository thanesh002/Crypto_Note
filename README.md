# Crypto Price Alert Bot (Telegram) - Minimal, Railway-friendly

This is a small, stable Telegram price alert bot designed to deploy on Railway's free plan using Python 3.10 and `requests` (no `aiohttp`). Follow the instructions below.

## Files included
- `bot.py` — main bot; uses CoinLore API and APScheduler
- `requirements.txt` — dependencies (`requests`, `APScheduler`)
- `Procfile` — `worker: python bot.py` (Railway will run it)
- `runtime.txt` — `python-3.10.13` (force Python 3.10 on Railway)
- `.env.example` — template for environment variables (DO NOT commit `.env`)
- `coinlist.csv` — list of CoinLore IDs (sample)
- `signals.db` — (created by bot at runtime)
- `.gitignore` — ignore `.env`, DB and pycache
- `README.md` — this file

## Quick local test (Linux / macOS / WSL)
1. Create and activate a Python 3.10 venv (recommended).
2. Install requirements:
   ```bash
   pip install -r requirements.txt
   ```
3. Create a local `.env` file (copy `.env.example`) and fill TELEGRAM_TOKEN and TELEGRAM_CHAT_ID.
4. Run locally:
   ```bash
   python bot.py
   ```
   The bot runs one scan at startup and then every `SCAN_INTERVAL_SECONDS`.

## Deployment to GitHub + Railway (step-by-step)
1. Create a new GitHub repository (do NOT add a `.env` file).
2. Commit all files from this project except `.env` (keep `.env.example`).

   Example (Linux/macOS/WSL):
   ```bash
   git init
   git add .
   git commit -m "Initial commit - crypto price alert bot"
   git branch -M main
   git remote add origin https://github.com/<yourname>/<repo>.git
   git push -u origin main
   ```

3. On Railway:
   - Create a new Project -> Deploy from GitHub and connect your repository.
   - In Railway's Environment variables section add these keys (exact names):
     - `TELEGRAM_TOKEN` — your bot token
     - `TELEGRAM_CHAT_ID` — chat id or channel id
     - `SCAN_INTERVAL_SECONDS` — e.g. `300`
     - `COIN_LIST_PATH` — `coinlist.csv`
     - `DATABASE_PATH` — `signals.db`
     - `THRESHOLD_PERCENT` — e.g. `2.0`
     - (optional) `COINLORE_BATCH_SIZE` — `50`
   - Railway will use `runtime.txt` to select Python 3.10 and auto-run the `Procfile` which executes `worker: python bot.py`.

4. Check logs in Railway to see startup messages and periodic scans.

## Notes / Troubleshooting
- Do NOT use `aiohttp` or Python 3.11+ on Railway — they are known to cause build issues in this setup.
- Do not commit real `.env` to GitHub. Use Railway's environment variables UI to store secrets.
- If you need more sophisticated indicators (RSI/SMA), we can add them later — this is intentionally minimal.
- `coinlist.csv` must contain CoinLore numeric IDs. Examples are included; replace them with the coins you need.
