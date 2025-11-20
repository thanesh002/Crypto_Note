#!/usr/bin/env python3
import os
import sqlite3
import time
import logging
from typing import List
import requests
from apscheduler.schedulers.blocking import BlockingScheduler

# --- Configuration from environment ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
SCAN_INTERVAL_SECONDS = int(os.environ.get("SCAN_INTERVAL_SECONDS", "300"))  # default 5 minutes
COIN_LIST_PATH = os.environ.get("COIN_LIST_PATH", "coinlist.csv")
DATABASE_PATH = os.environ.get("DATABASE_PATH", "signals.db")
THRESHOLD_PERCENT = float(os.environ.get("THRESHOLD_PERCENT", "2.0"))  # percent change threshold to alert
COINLORE_BATCH_SIZE = int(os.environ.get("COINLORE_BATCH_SIZE", "50"))  # how many ids per request (CoinLore supports many)

if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
    logging.warning("TELEGRAM_TOKEN or TELEGRAM_CHAT_ID not set - bot will not send messages, but will log actions.")

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# --- DB helpers ---
def init_db(conn):
    with conn:
        conn.execute(\"\"\"
        CREATE TABLE IF NOT EXISTS last_prices (
            id INTEGER PRIMARY KEY,
            coin_id TEXT UNIQUE,
            price_usd REAL,
            ts INTEGER
        )\"\"\")

def get_coin_ids(path: str) -> List[str]:
    ids = []
    p = Path(path)
    if not p.exists():
        logging.error(f"coin list not found at {path}. Create coinlist.csv with one CoinLore id per line.")
        return ids
    for line in p.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        # allow csv line of multiple ids e.g. "90,80"
        for part in s.split(","):
            if part.strip():
                ids.append(part.strip())
    return ids

def fetch_prices(ids: List[str]) -> dict:
    \"\"\"Fetch prices from CoinLore. Returns mapping coin_id -> data (dict)\"\"\"
    result = {}
    if not ids:
        return result
    base = "https://api.coinlore.net/api/ticker/"
    # CoinLore endpoint accepts ?id=1,2,3
    id_chunks = [ids[i:i+COINLORE_BATCH_SIZE] for i in range(0, len(ids), COINLORE_BATCH_SIZE)]
    for chunk in id_chunks:
        id_param = ",".join(chunk)
        url = f"{base}?id={id_param}"
        try:
            logging.info(f"Fetching prices for ids: {id_param}")
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            data = r.json()
            # data is a list of coin dicts
            for coin in data:
                coin_id = coin.get("id")
                price = float(coin.get("price_usd", 0))
                result[str(coin_id)] = coin
        except Exception as e:
            logging.exception(f"Failed to fetch from CoinLore for ids {id_param}: {e}")
    return result

def send_telegram(message: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.info(f"[DRY-RUN] Telegram message: {message}")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, json=payload, timeout=15)
        r.raise_for_status()
        logging.info("Telegram message sent.")
        return True
    except Exception as e:
        logging.exception(f"Failed to send telegram message: {e}")
        return False

def check_and_alert(conn, prices: dict):
    now_ts = int(time.time())
    cur = conn.cursor()
    for coin_id, data in prices.items():
        price = float(data.get("price_usd", 0))
        name = data.get("name") or data.get("symbol") or coin_id
        # get last price for coin_id
        cur.execute("SELECT price_usd FROM last_prices WHERE coin_id = ?", (coin_id,))
        row = cur.fetchone()
        if row is None:
            # insert
            cur.execute("INSERT OR REPLACE INTO last_prices (coin_id, price_usd, ts) VALUES (?, ?, ?)", (coin_id, price, now_ts))
            conn.commit()
            logging.info(f"Inserted initial price for {name} ({coin_id}): {price}")
            continue
        last_price = float(row[0])
        if last_price <= 0:
            change_pct = 0.0
        else:
            change_pct = ((price - last_price) / last_price) * 100.0
        logging.info(f"{name} ({coin_id}) price {price} USD; last {last_price} USD; change {change_pct:.2f}%")
        if abs(change_pct) >= THRESHOLD_PERCENT:
            msg = f"*Price Alert*: {name} ({coin_id})\\nPrice: {price} USD\\nChange: {change_pct:.2f}% since last check"
            send_telegram(msg)
            # update stored price to current after alert
            cur.execute("UPDATE last_prices SET price_usd = ?, ts = ? WHERE coin_id = ?", (price, now_ts, coin_id))
            conn.commit()
        else:
            # update price without alert (optional: only update timestamp)
            cur.execute("UPDATE last_prices SET price_usd = ?, ts = ? WHERE coin_id = ?", (price, now_ts, coin_id))
            conn.commit()

def job():
    logging.info("Starting scan job...")
    ids = get_coin_ids(COIN_LIST_PATH)
    if not ids:
        logging.error("No coin ids found; aborting this run.")
        return
    prices = fetch_prices(ids)
    if not prices:
        logging.warning("No prices fetched from CoinLore.")
        return
    with sqlite3.connect(DATABASE_PATH) as conn:
        init_db(conn)
        check_and_alert(conn, prices)
    logging.info("Scan job finished.")

def main():
    logging.info("Crypto Price Alert Bot starting...")
    # run once at startup
    try:
        job()
    except Exception as e:
        logging.exception(f"Initial job failed: {e}")
    # schedule periodic job
    sched = BlockingScheduler()
    sched.add_job(job, 'interval', seconds=SCAN_INTERVAL_SECONDS, id="scan")
    try:
        logging.info(f"Scheduler starting with interval {SCAN_INTERVAL_SECONDS} seconds.")
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        logging.info("Scheduler stopped.")

if __name__ == '__main__':
    main()
