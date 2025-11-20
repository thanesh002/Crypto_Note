#!/usr/bin/env python3
"""
Crypto Price Alert Bot (CoinLore realtime + CoinGecko OHLC)
- Uses CoinLore for realtime price and pump/dump detection
- Uses CoinGecko market_chart (7 days, hourly) to compute OHLC and TA
- Outputs STRONG BUY / BUY / SELL / STRONG SELL to Telegram
- Keeps price history and alert logs in SQLite
"""

import os, time, logging, sqlite3, math, requests
from typing import List, Dict
import pandas as pd
from indicators import compute_indicators, decide_signal, CandleInfo
from apscheduler.schedulers.blocking import BlockingScheduler
from pathlib import Path

# --- ENV / CONFIG ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
SCAN_INTERVAL_SECONDS = int(os.environ.get("SCAN_INTERVAL_SECONDS", "300"))
COIN_LIST_PATH = os.environ.get("COIN_LIST_PATH", "coinlist.csv")
DATABASE_PATH = os.environ.get("DATABASE_PATH", "signals.db")
THRESHOLD_PERCENT = float(os.environ.get("THRESHOLD_PERCENT", "0.5"))
COINLORE_BATCH_SIZE = int(os.environ.get("COINLORE_BATCH_SIZE", "50"))
CG_MAP_PATH = os.environ.get("CG_MAP_PATH", "coingecko_mapping.csv")
ALERT_COOLDOWN_SECONDS = int(os.environ.get("ALERT_COOLDOWN_SECONDS", "900"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# --- DB ---
def init_db(conn: sqlite3.Connection):
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS last_prices (
        id INTEGER PRIMARY KEY, coin_id TEXT UNIQUE, price_usd REAL, ts INTEGER
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS price_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, coin_id TEXT, ts INTEGER, price_usd REAL
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS alerts_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, coin_id TEXT, ts INTEGER, signal TEXT, price_usd REAL
    )""")
    conn.commit()

# --- helpers ---
def get_coin_ids(path: str) -> List[str]:
    p = Path(path)
    if not p.exists():
        logging.error("coinlist not found: %s", path)
        return []
    ids = []
    for line in p.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        ids.append(s.split(",")[0].strip())
    return ids

def load_cg_map(path: str) -> Dict[str, str]:
    # mapping: coinlore_id,coingecko_id
    p = Path(path)
    mapping = {}
    if not p.exists():
        logging.warning("CoinGecko mapping file not found: %s", path)
        return mapping
    df = pd.read_csv(p)
    for _, r in df.iterrows():
        mapping[str(int(r['coinlore_id']))] = str(r['coingecko_id'])
    return mapping

# --- CoinLore realtime ---
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
                result[str(coin.get('id'))] = coin
        except Exception as e:
            logging.exception("CoinLore fetch failed for %s: %s", id_param, e)
    return result

# --- CoinGecko OHLC via market_chart (hourly) ---
def fetch_coingecko_ohlc(cg_id: str, days: int = 7) -> pd.DataFrame:
    # returns DataFrame with Open,High,Low,Close,Volume indexed by datetime (hourly)
    try:
        url = f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart?vs_currency=usd&days={days}&interval=hourly"
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        j = r.json()
        # j contains 'prices' and 'total_volumes' arrays of [timestamp(ms), value]
        prices = j.get('prices', [])
        vols = j.get('total_volumes', [])
        if not prices:
            return pd.DataFrame()
        # build DataFrame from prices (ms -> datetime)
        dfp = pd.DataFrame(prices, columns=['ts','price'])
        dfp['dt'] = pd.to_datetime(dfp['ts'], unit='ms')
        dfp = dfp.set_index('dt')['price']
        # build volume series aligned (some timestamps may differ)
        dfv = pd.DataFrame(vols, columns=['ts','vol'])
        dfv['dt'] = pd.to_datetime(dfv['ts'], unit='ms')
        dfv = dfv.set_index('dt')['vol']
        # combine into hourly OHLC by resampling on 1H
        cc = pd.concat([dfp, dfv], axis=1)
        cc.columns = ['price','volume']
        # sometimes index duplicates; resample to hourly
        ohlc = cc['price'].resample('1H').ohlc()
        vol = cc['volume'].resample('1H').sum().fillna(0)
        df = pd.concat([ohlc, vol], axis=1).dropna()
        df.columns = ['Open','High','Low','Close','Volume']
        return df
    except Exception as e:
        logging.exception("Failed to fetch CoinGecko %s: %s", cg_id, e)
        return pd.DataFrame()

def should_cooldown(conn: sqlite3.Connection, coin_id: str) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT ts FROM alerts_log WHERE coin_id = ? ORDER BY ts DESC LIMIT 1", (coin_id,))
    row = cur.fetchone()
    if not row:
        return False
    return (time.time() - int(row[0])) < ALERT_COOLDOWN_SECONDS

def send_telegram(message: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.info("[DRY-RUN] "+message.replace("\\n", " | "))
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    ok = True
    for chat in [c.strip() for c in TELEGRAM_CHAT_ID.split(",") if c.strip()]:
        try:
            r = requests.post(url, json={"chat_id":chat,"text":message,"parse_mode":"Markdown"}, timeout=15)
            r.raise_for_status()
        except Exception as e:
            logging.exception("Failed send telegram to %s: %s", chat, e)
            ok = False
    return ok

def log_alert(conn: sqlite3.Connection, coin_id: str, signal: str, price: float):
    cur = conn.cursor()
    cur.execute("INSERT INTO alerts_log (coin_id, ts, signal, price_usd) VALUES (?, ?, ?, ?)", (coin_id, int(time.time()), signal, price))
    conn.commit()

# --- main job ---
def job():
    logging.info("Starting scan job...")
    ids = get_coin_ids(COIN_LIST_PATH)
    if not ids:
        logging.error("No coin ids found; aborting")
        return
    cg_map = load_cg_map(CG_MAP_PATH)
    coinlore = fetch_coinlore_prices(ids)
    with sqlite3.connect(DATABASE_PATH) as conn:
        init_db(conn)
        cur = conn.cursor()
        for cid in ids:
            coin = coinlore.get(cid)
            if not coin:
                logging.warning("No coinlore data for %s", cid)
                continue
            try:
                price = float(coin.get('price_usd',0))
            except:
                price = 0.0
            # save snapshot
            cur.execute("INSERT INTO price_history (coin_id, ts, price_usd) VALUES (?, ?, ?)", (cid, int(time.time()), price))
            conn.commit()
            # CoinGecko id mapping
            cg_id = cg_map.get(cid)
            indicators = None
            candle_info = CandleInfo()
            if cg_id:
                df = fetch_coingecko_ohlc(cg_id, days=7)
                if not df.empty:
                    indicators = compute_indicators(df)
                    candle_info = CandleInfo.from_df(df)
                else:
                    logging.warning("No OHLC for %s (%s)", cid, cg_id)
            else:
                logging.debug("No CoinGecko mapping for %s", cid)
            signal = decide_signal(indicators, candle_info) if indicators is not None else ""
            # check last price
            cur.execute("SELECT price_usd FROM last_prices WHERE coin_id = ?", (cid,))
            row = cur.fetchone()
            last_price = float(row[0]) if row else None
            alert_now = False
            if last_price and last_price>0:
                pct = ((price-last_price)/last_price)*100.0
                if abs(pct) >= THRESHOLD_PERCENT:
                    alert_now = True
            pump_dump = None
            if last_price and last_price>0:
                pct = ((price-last_price)/last_price)*100.0
                if pct >= 5.0:
                    pump_dump = "PUMP"
                if pct <= -5.0:
                    pump_dump = "DUMP"
            wants_alert = (signal in ("STRONG BUY","BUY","SELL","STRONG SELL")) or alert_now or (pump_dump is not None)
            if wants_alert and not should_cooldown(conn, cid):
                name = coin.get('name','')
                msg = []
                title = f"{signal} {name} alert!" if signal else f"{name} alert!"
                msg.append(f"*{title}*")
                msg.append(f"Price: {price}")
                if pump_dump:
                    msg.append(pump_dump+"!")
                if last_price and last_price>0:
                    msg.append(f"Change: {((price-last_price)/last_price)*100:.2f}%")
                send_telegram("\\n".join(msg))
                log_alert(conn, cid, signal or "ALERT", price)
            # update last price
            cur.execute("INSERT OR REPLACE INTO last_prices (coin_id, price_usd, ts) VALUES (?, ?, ?)", (cid, price, int(time.time())))
            conn.commit()
    logging.info("Scan job finished.")

def main():
    logging.info("Bot starting...")
    try:
        job()
    except Exception as e:
        logging.exception("Initial job failed: %s", e)
    sched = BlockingScheduler()
    sched.add_job(job, "interval", seconds=SCAN_INTERVAL_SECONDS, id="scan")
    try:
        logging.info("Scheduler starting with interval %s seconds."%SCAN_INTERVAL_SECONDS)
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        logging.info("Scheduler stopped.")

if __name__ == "__main__":
    main()
