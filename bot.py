# refresh 1
#!/usr/bin/env python3
import os
import sqlite3
import time
import logging
from typing import List
import requests
from apscheduler.schedulers.blocking import BlockingScheduler
from pathlib import Path

# --- Load env variables ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
SCAN_INTERVAL_SECONDS = int(os.environ.get("SCAN_INTERVAL_SECONDS", "300"))
COIN_LIST_PATH = os.environ.get("COIN_LIST_PATH", "coinlist.csv")
DATABASE_PATH = os.environ.get("DATABASE_PATH", "signals.db")
THRESHOLD_PERCENT = float(os.environ.get("THRESHOLD_PERCENT", "2.0"))
COINLORE_BATCH_SIZE = int(os.environ.get("COINLORE_BATCH_SIZE", "50"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


# --- DB SETUP ---
def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS last_prices (
            id INTEGER PRIMARY KEY,
            coin_id TEXT UNIQUE,
            price_usd REAL,
            ts INTEGER
        )
    """)


# --- READ IDS ---
def get_coin_ids(path: str) -> List[str]:
    ids = []
    p = Path(path)
    if not p.exists():
        logging.error(f"coinlist.csv not found at {path}")
        return ids

    for line in p.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        for part in s.split(","):
            if part.strip():
                ids.append(part.strip())
    return ids


# --- API ---
def fetch_prices(ids: List[str]) -> dict:
    result = {}
    if not ids:
        return result

    base = "https://api.coinlore.net/api/ticker/"
    chunks = [ids[i:i+COINLORE_BATCH_SIZE] for i in range(0, len(ids), COINLORE_BATCH_SIZE)]

    for chunk in chunks:
        id_param = ",".join(chunk)
        url = f"{base}?id={id_param}"
        try:
            logging.info(f"Fetching prices for IDs: {id_param}")
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            data = r.json()

            for coin in data:
                cid = str(coin.get("id"))
                result[cid] = coin

        except Exception as e:
            logging.exception(f"Error fetching from CoinLore: {e}")

    return result


# --- Telegram Alert ---
def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning("Telegram credentials missing.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg,
        "parse_mode": "Markdown"
    }

    try:
        r = requests.post(url, json=payload, timeout=15)
        r.raise_for_status()
        logging.info("Alert sent.")
        return True
    except Exception as e:
        logging.exception(f"Telegram error: {e}")
        return False


# --- MAIN LOGIC ---
def check_and_alert(conn, prices: dict):
    ts = int(time.time())
    cur = conn.cursor()

    for cid, data in prices.items():
        price = float(data.get("price_usd", 0))
        name = data.get("name", cid)

        cur.execute("SELECT price_usd FROM last_prices WHERE coin_id = ?", (cid,))
        row = cur.fetchone()

        if row is None:
            cur.execute(
                "INSERT OR REPLACE INTO last_prices (coin_id, price_usd, ts) VALUES (?, ?, ?)",
                (cid, price, ts)
            )
            conn.commit()
            continue

        last_price = float(row[0])
        change = ((price - last_price) / last_price) * 100 if last_price else 0

        if abs(change) >= THRESHOLD_PERCENT:
            msg = f"*{name}* alert!\nPrice: {price}\nChange: {change:.2f}%"
            send_telegram(msg)

        cur.execute(
            "UPDATE last_prices SET price_usd = ?, ts = ? WHERE coin_id = ?",
            (price, ts, cid)
        )
        conn.commit()


# --- SCHEDULER JOB ---
def job():
    ids = get_coin_ids(COIN_LIST_PATH)
    prices = fetch_prices(ids)

    with sqlite3.connect(DATABASE_PATH) as conn:
        init_db(conn)
        check_and_alert(conn, prices)


# --- ENTRY ---
def main():
    logging.info("Bot started.")

    job()

    scheduler = BlockingScheduler()
    scheduler.add_job(job, "interval", seconds=SCAN_INTERVAL_SECONDS)

    scheduler.start()


if __name__ == "__main__":
    main()
