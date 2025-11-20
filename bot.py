#!/usr/bin/env python3
"""Upgraded Crypto Price Alert Bot (CoinLore + Yahoo Finance)
Features:
- 1m candles from yfinance (1 day history)
- Indicators: RSI(14), EMA(20,50), SMA(10), MACD(12,26,9)
- Simple candlestick detectors: bullish engulfing, hammer
- Volume spike detection (based on relative volume in yfinance candles)
- Pump/Dump detection using CoinLore real-time price
- Weighted rule engine that outputs: STRONG BUY / BUY / SELL / STRONG SELL
- Alerts to Telegram (concise; no long explanations)
- Price history stored in SQLite (price_history) for backup
"""

import os, time, logging, sqlite3
from typing import List, Dict
import requests
import pandas as pd
import yfinance as yf
from indicators import compute_indicators, decide_signal, CandleInfo
from apscheduler.schedulers.blocking import BlockingScheduler
from pathlib import Path

# --- Config from env ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()  # single or comma-separated list
SCAN_INTERVAL_SECONDS = int(os.environ.get("SCAN_INTERVAL_SECONDS", "300"))
COIN_LIST_PATH = os.environ.get("COIN_LIST_PATH", "coinlist.csv")
DATABASE_PATH = os.environ.get("DATABASE_PATH", "signals.db")
THRESHOLD_PERCENT = float(os.environ.get("THRESHOLD_PERCENT", "0.5"))
COINLORE_BATCH_SIZE = int(os.environ.get("COINLORE_BATCH_SIZE", "50"))
YAHOO_MAP_PATH = os.environ.get("YAHOO_MAP_PATH", "yahoo_mapping.csv")
ALERT_COOLDOWN_SECONDS = int(os.environ.get("ALERT_COOLDOWN_SECONDS", "900"))  # per coin cooldown

# yfinance settings
YF_INTERVAL = os.environ.get("YF_INTERVAL", "1m")   # 1m or 5m
YF_PERIOD = os.environ.get("YF_PERIOD", "1d")      # 1d history

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def init_db(conn: sqlite3.Connection):
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS last_prices (
        id INTEGER PRIMARY KEY,
        coin_id TEXT UNIQUE,
        price_usd REAL,
        ts INTEGER
    )""" )
    cur.execute("""
    CREATE TABLE IF NOT EXISTS price_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        coin_id TEXT,
        ts INTEGER,
        price_usd REAL
    )""" )
    cur.execute("""
    CREATE TABLE IF NOT EXISTS alerts_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        coin_id TEXT,
        ts INTEGER,
        signal TEXT,
        price_usd REAL
    )""" )
    conn.commit()

def get_coin_ids(path: str) -> List[str]:
    ids = []
    p = Path(path)
    if not p.exists():
        logging.error(f"coinlist not found at {path}")
        return ids
    for line in p.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        for part in s.split(","):
            if part.strip():
                ids.append(part.strip())
    return ids

def load_yahoo_map(path: str) -> Dict[str, str]:
    mapping = {}
    p = Path(path)
    if not p.exists():
        logging.warning("Yahoo mapping not found; proceeding without yahoo candles for those coins")
        return mapping
    df = pd.read_csv(p)
    for _, r in df.iterrows():
        mapping[str(int(r["coinlore_id"]))] = str(r["yahoo_symbol"])
    return mapping

def fetch_coinlore_prices(ids: List[str]) -> dict:
    result = {}
    if not ids:
        return result
    base = "https://api.coinlore.net/api/ticker/"
    chunks = [ids[i:i+COINLORE_BATCH_SIZE] for i in range(0, len(ids), COINLORE_BATCH_SIZE)]
    for chunk in chunks:
        id_param = ",".join(chunk)
        url = f"{base}?id={id_param}"
        try:
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            data = r.json()
            for coin in data:
                cid = str(coin.get("id"))
                result[cid] = coin
        except Exception as e:
            logging.exception(f"CoinLore fetch failed for {id_param}: {e}")
    return result

def fetch_yahoo_ohlc(symbol: str, interval: str = YF_INTERVAL, period: str = YF_PERIOD) -> pd.DataFrame:
    try:
        t = yf.Ticker(symbol)
        df = t.history(interval=interval, period=period, prepost=False)
        if df.empty:
            return pd.DataFrame()
        df = df.dropna()
        df = df[[ "Open", "High", "Low", "Close", "Volume" ]]
        return df
    except Exception as e:
        logging.exception(f"yfinance fetch failed for {symbol}: {e}")
        return pd.DataFrame()

def send_telegram(message: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.info("[DRY-RUN] Telegram: " + message.replace("\n"," | "))
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    chat_ids = [c.strip() for c in TELEGRAM_CHAT_ID.split(",") if c.strip()]
    ok = True
    for chat in chat_ids:
        payload = {"chat_id": chat, "text": message, "parse_mode": "Markdown"}
        try:
            r = requests.post(url, json=payload, timeout=15)
            r.raise_for_status()
        except Exception as e:
            logging.exception(f"Failed to send telegram to {chat}: {e}")
            ok = False
    return ok

def should_cooldown(conn: sqlite3.Connection, coin_id: str) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT ts FROM alerts_log WHERE coin_id = ? ORDER BY ts DESC LIMIT 1", (coin_id,))
    row = cur.fetchone()
    if not row:
        return False
    last_ts = int(row[0])
    return (time.time() - last_ts) < ALERT_COOLDOWN_SECONDS

def log_alert(conn: sqlite3.Connection, coin_id: str, signal: str, price: float):
    cur = conn.cursor()
    cur.execute("INSERT INTO alerts_log (coin_id, ts, signal, price_usd) VALUES (?, ?, ?, ?)", (coin_id, int(time.time()), signal, price))
    conn.commit()

def job():
    logging.info("Starting scan job...")
    ids = get_coin_ids(COIN_LIST_PATH)
    if not ids:
        logging.error("No coin ids; aborting")
        return

    yahoo_map = load_yahoo_map(YAHOO_MAP_PATH)
    coinlore = fetch_coinlore_prices(ids)

    with sqlite3.connect(DATABASE_PATH) as conn:
        init_db(conn)
        cur = conn.cursor()

        for cid in ids:
            coin = coinlore.get(cid)
            if not coin:
                logging.warning(f"No coinlore data for {cid}")
                continue
            try:
                price = float(coin.get("price_usd", 0))
            except:
                price = 0.0

            cur.execute("INSERT INTO price_history (coin_id, ts, price_usd) VALUES (?, ?, ?)", (cid, int(time.time()), price))
            conn.commit()

            yahoo_symbol = yahoo_map.get(cid)
            indicators = None
            candle_info = CandleInfo()

            if yahoo_symbol:
                df = fetch_yahoo_ohlc(yahoo_symbol)
                if not df.empty:
                    indicators = compute_indicators(df)
                    candle_info = CandleInfo.from_df(df)
            else:
                cur.execute("SELECT ts, price_usd FROM price_history WHERE coin_id = ? ORDER BY ts DESC LIMIT 200", (cid,))
                rows = cur.fetchall()[::-1]
                if len(rows) >= 5:
                    series = pd.Series([r[1] for r in rows], index=pd.to_datetime([r[0] for r in rows], unit="s"))
                    tmp_df = pd.DataFrame({"Close": series})
                    indicators = compute_indicators(tmp_df)
                    candle_info = CandleInfo()

            signal = decide_signal(indicators or None, candle_info)
            cur.execute("SELECT price_usd FROM last_prices WHERE coin_id = ?", (cid,))
            row = cur.fetchone()
            last_price = float(row[0]) if row else None
            alert_now = False
            if last_price and last_price > 0:
                pct = ((price - last_price) / last_price) * 100.0
                if abs(pct) >= THRESHOLD_PERCENT:
                    alert_now = True

            pump_dump = None
            if last_price and last_price > 0:
                pct = ((price - last_price) / last_price) * 100.0
                if pct >= 5.0:
                    pump_dump = "PUMP"
                if pct <= -5.0:
                    pump_dump = "DUMP"

            wants_alert = (signal in ("STRONG BUY", "BUY", "SELL", "STRONG SELL")) or alert_now or (pump_dump is not None)

            if wants_alert and not should_cooldown(conn, cid):
                symbol = coin.get("symbol", "")
                name = coin.get("name", "")
                msg_lines = []
                title = f"{signal} {name} alert!" if signal else f"{name} alert!"
                msg_lines.append(f"*{title}*")
                msg_lines.append(f"Price: {price}")
                if pump_dump:
                    msg_lines.append(f"{pump_dump}!")
                if last_price and last_price > 0:
                    msg_lines.append(f"Change: {((price-last_price)/last_price)*100:.2f}%")
                message = "\n".join(msg_lines)
                sent = send_telegram(message)
                if sent:
                    log_alert(conn, cid, signal or "ALERT", price)
            cur.execute("INSERT OR REPLACE INTO last_prices (coin_id, price_usd, ts) VALUES (?, ?, ?)", (cid, price, int(time.time())))
            conn.commit()

    logging.info("Scan job finished.")

def main():
    logging.info("Bot starting...")
    try:
        job()
    except Exception as e:
        logging.exception(f"Initial job failed: {e}")
    sched = BlockingScheduler()
    sched.add_job(job, "interval", seconds=SCAN_INTERVAL_SECONDS, id="scan")
    try:
        logging.info(f"Scheduler starting with interval {SCAN_INTERVAL_SECONDS} seconds.")
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        logging.info("Scheduler stopped.")

if __name__ == "__main__":
    main()