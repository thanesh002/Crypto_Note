#!/usr/bin/env python3
"""
CoinLore Technical Alert Bot
- Single-source: CoinLore API only
- Stores history in SQLite (signals.db)
- Computes simple technical signals using historical prices saved by the bot
- Sends Telegram messages via Bot Token (requests)
"""

import os
import time
import json
import sqlite3
from datetime import datetime, timezone
import requests
import pandas as pd
import numpy as np
from apscheduler.schedulers.blocking import BlockingScheduler
import pytz

# ----------------------------
# Environment / Defaults
# ----------------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_IDS = os.getenv("TELEGRAM_CHAT_IDS", "")  # comma separated
SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "60"))  # scanning interval
COIN_LIST_PATH = os.getenv("COIN_LIST_PATH", "coinlist.csv")
DATABASE_PATH = os.getenv("DATABASE_PATH", "signals.db")
THRESH_PCT = float(os.getenv("THRESHOLD_PERCENT", "0.5"))  # not used for everything, default small
HISTORY_LOOKBACK = int(os.getenv("HISTORY_LOOKBACK", "48"))  # number of stored rows used for history (per coin)

if not TELEGRAM_TOKEN:
    raise Exception("TELEGRAM_TOKEN required in ENV variables")

CHAT_ID_LIST = [c.strip() for c in TELEGRAM_CHAT_IDS.split(",") if c.strip()]
if not CHAT_ID_LIST:
    print("WARNING: TELEGRAM_CHAT_IDS empty — no messages will be sent. Set TELEGRAM_CHAT_IDS env.")

# Telegram helper (simple requests-based)
TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

def send_telegram(text, parse_mode="Markdown"):
    if not CHAT_ID_LIST:
        print("No chat IDs configured; skipping telegram send.")
        return
    for chat in CHAT_ID_LIST:
        try:
            payload = {"chat_id": chat, "text": text, "parse_mode": parse_mode}
            r = requests.post(TELEGRAM_URL, json=payload, timeout=10)
            if r.status_code != 200:
                print("Telegram send failed:", chat, r.status_code, r.text[:200])
        except Exception as e:
            print("Telegram exception:", e)

# ----------------------------
# DB: history table
# ----------------------------
def init_db(path=DATABASE_PATH):
    conn = sqlite3.connect(path, check_same_thread=False)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        coinlore_id TEXT,
        symbol TEXT,
        name TEXT,
        ts INTEGER,
        price REAL,
        market_cap REAL,
        volume_24 REAL,
        percent_change_24h REAL,
        percent_change_7d REAL
    )
    """)
    conn.commit()
    return conn

DB = init_db(DATABASE_PATH)

def save_history_row(conn, coin):
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO history (coinlore_id, symbol, name, ts, price, market_cap, volume_24, percent_change_24h, percent_change_7d)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        str(coin["id"]), coin.get("symbol","").upper(), coin.get("name",""),
        int(time.time()), float(coin.get("price_usd") or 0.0),
        float(coin.get("market_cap_usd") or 0.0),
        float(coin.get("volume24") or 0.0),
        float(coin.get("percent_change_24h") or 0.0),
        float(coin.get("percent_change_7d") or 0.0),
    ))
    conn.commit()

def fetch_recent_history(conn, coinlore_id, limit=HISTORY_LOOKBACK):
    cur = conn.cursor()
    cur.execute("SELECT ts, price, volume_24, percent_change_24h, percent_change_7d FROM history WHERE coinlore_id=? ORDER BY ts DESC LIMIT ?", (str(coinlore_id), limit))
    rows = cur.fetchall()
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["ts","price","volume_24","pct24","pct7"])
    df = df.sort_values("ts").reset_index(drop=True)
    return df

# ----------------------------
# Coin list loader (expects coinlore_id,symbol,name)
# ----------------------------
def load_coinlist(path=COIN_LIST_PATH):
    df = pd.read_csv(path, dtype=str)
    # Accept multiple formats
    # if header contains symbol,name only -> user earlier had that; but we need coinlore ids.
    # We'll expect coinlist with columns: coinlore_id,symbol,name
    expected = ["coinlore_id","symbol","name"]
    lower = [c.lower() for c in df.columns]
    if all(x in lower for x in expected):
        df.columns = [c.lower() for c in df.columns]
        coins = []
        for _, r in df.iterrows():
            coins.append({
                "id": r["coinlore_id"],
                "symbol": str(r["symbol"]).upper(),
                "name": r["name"]
            })
        return coins
    else:
        # try parse simple symbol,name but warn user
        print("coinlist.csv not in 'coinlore_id,symbol,name' format. Expecting that. Trying fallback by reading symbol,name and will attempt coinlore lookup (may be slow).")
        coins=[]
        for _, r in df.iterrows():
            sym = r.iloc[0]
            name = r.iloc[1] if len(r.index)>1 else sym
            coins.append({"id": None, "symbol": str(sym).upper(), "name": name})
        return coins

# ----------------------------
# CoinLore fetch
# ----------------------------
def fetch_coinlore_by_id(coinlore_id):
    # Single id endpoint
    try:
        url = f"https://api.coinlore.net/api/ticker/?id={coinlore_id}"
        r = requests.get(url, timeout=12)
        if r.status_code!=200:
            print("CoinLore HTTP error:", coinlore_id, r.status_code)
            return None
        data = r.json()
        if not data:
            return None
        return data[0]  # dict
    except Exception as e:
        print("CoinLore exception:", e)
        return None

def fetch_coinlore_by_ids_batch(id_list):
    # coinlore supports multiple ids? We'll call per id to be safe
    out=[]
    for i in id_list:
        c = fetch_coinlore_by_id(i)
        if c:
            out.append(c)
    return out

# ----------------------------
# Signal rules (CoinLore-based)
# ----------------------------
def compute_rules(coin, history_df):
    # coin is CoinLore dict for current tick
    price = float(coin.get("price_usd") or 0.0)
    pct24 = float(coin.get("percent_change_24h") or 0.0)
    pct7 = float(coin.get("percent_change_7d") or 0.0)
    volume = float(coin.get("volume24") or 0.0)
    market_cap = float(coin.get("market_cap_usd") or 0.0)

    reasons=[]
    score=0

    # 1) Recent movement: compare with last saved price (previous scan)
    last_price = None
    last_ts = None
    if history_df is not None and len(history_df)>0:
        last_price = history_df["price"].iloc[-1]
        last_ts = history_df["ts"].iloc[-1]

    if last_price is not None and last_price>0:
        pct_since_last = (price - last_price) / last_price * 100.0
        reasons.append(f"Δ since last: {pct_since_last:.2f}%")
        # pump/dump detection window: if last_ts within 10 minutes and change >5%
        time_diff = int(time.time()) - int(last_ts)
        if time_diff <= 10*60 and pct_since_last >= 5.0:
            reasons.append("PUMP detected (fast +≥5% within 10m)")
            score += 30
        if time_diff <= 10*60 and pct_since_last <= -5.0:
            reasons.append("DUMP detected (fast -≥5% within 10m)")
            score -= 30
        # small momentum
        if pct_since_last > 0.5:
            score += 4
        if pct_since_last < -0.5:
            score -= 4
    else:
        reasons.append("No prior point to compare")

    # 2) 24h / 7d context
    reasons.append(f"24h: {pct24:.2f}%  7d: {pct7:.2f}%")
    # Simple weighting
    if pct24 > 5:
        score += 6
    if pct24 < -5:
        score -= 6
    if pct7 > 8:
        score += 6
    if pct7 < -8:
        score -= 6

    # 3) Volume spike: compare to avg volume in history
    vol_spike=False
    if history_df is not None and "volume_24" in history_df.columns and len(history_df)>=6:
        avg_vol = history_df["volume_24"].iloc[-HISTORY_LOOKBACK:].mean()
        if avg_vol and avg_vol>0:
            if volume > avg_vol * 2.5:
                reasons.append(f"Volume spike: {volume:.0f} > 2.5x avg ({avg_vol:.0f})")
                score += 8
                vol_spike=True

    # 4) Trend measure via simple moving averages on stored price history
    if history_df is not None and len(history_df) >= 6:
        # build pd Series including current price appended
        hist_prices = list(history_df["price"].values) + [price]
        s = pd.Series(hist_prices)
        ema_short = s.ewm(span=6, adjust=False).mean().iloc[-1]
        ema_long = s.ewm(span=20, adjust=False).mean().iloc[-1] if len(s)>=20 else s.ewm(span=12, adjust=False).mean().iloc[-1]
        reasons.append(f"EMA_short: {ema_short:.4f} EMA_long: {ema_long:.4f}")
        if ema_short > ema_long:
            score += 5
        else:
            score -= 5

    # 5) Market cap filter: small caps are noisy; adjust score
    if market_cap < 5e6:
        reasons.append("Small market cap (<5M) — noisy")
        score -= 2
    elif market_cap > 1e9:
        score += 2

    # 6) Compose final signal from score and rule triggers
    sig = None
    if "PUMP detected" in " ".join(reasons):
        sig = "PUMP"
    elif "DUMP detected" in " ".join(reasons):
        sig = "DUMP"
    else:
        # thresholds for buy/sell
        if score >= 15:
            sig = "STRONG BUY"
        elif score >= 5:
            sig = "BUY"
        elif score <= -15:
            sig = "STRONG SELL"
        elif score <= -5:
            sig = "SELL"
        else:
            sig = "NEUTRAL"

    return {
        "signal": sig,
        "score": score,
        "reasons": reasons,
        "price": price,
        "pct24": pct24,
        "pct7": pct7,
        "volume": volume,
        "market_cap": market_cap
    }

# ----------------------------
# Top gainer scanner (24h)
# ----------------------------
def top_gainers(coins_data, top_n=5):
    # coins_data: list of coin dicts
    ranked = sorted(coins_data, key=lambda c: float(c.get("percent_change_24h") or 0.0), reverse=True)
    return ranked[:top_n]

# ----------------------------
# Main job: fetch, store, analyze, notify
# ----------------------------
def job():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Starting scan job...")
    coins = load_coinlist(COIN_LIST_PATH)
    collected = []
    for c in coins:
        # if coin list has id use it; else try to lookup coin by symbol via CoinLore tickers (not efficient)
        cid = c.get("id")
        if cid and str(cid).strip():
            data = fetch_coinlore_by_id(cid)
        else:
            # fallback: try to find by symbol via CoinLore /tickers (limited)
            sym = c.get("symbol","").lower()
            try:
                r = requests.get("https://api.coinlore.net/api/tickers/", timeout=12)
                if r.status_code==200:
                    found=None
                    for item in r.json().get("data",[]):
                        if item.get("symbol","").lower()==sym:
                            found=item
                            break
                    data = found
                else:
                    data=None
            except:
                data=None

        if not data:
            print("No data for coin:", c)
            continue

        # Save history
        save_history_row(DB, data)
        collected.append(data)

        # Analyze using history
        hist = fetch_recent_history(DB, data["id"], limit=HISTORY_LOOKBACK)
        res = compute_rules(data, hist)

        # Notify if interesting: PUMP/DUMP/STRONG BUY/STRONG SELL (user asked for professional & technical)
        if res["signal"] in ("PUMP","DUMP","STRONG BUY","STRONG SELL","BUY","SELL"):
            # build short message
            name = data.get("name") or data.get("symbol")
            symbol = (data.get("symbol") or "").upper()
            lines = []
            lines.append(f"*{name}* ({symbol}) — *{res['signal']}*")
            lines.append(f"Price: ${res['price']:.6g}  |  24h: {res['pct24']:.2f}%  |  7d: {res['pct7']:.2f}%")
            if res["market_cap"]>0:
                lines.append(f"Market cap: ${res['market_cap']:.0f}  Vol24: ${res['volume']:.0f}")
            # brief reasons (max 6 lines)
            for r in res["reasons"][:6]:
                lines.append(f"• {r}")
            lines.append(f"_Score: {res['score']}_")
            msg = "\n".join(lines)
            send_telegram(msg, parse_mode="Markdown")

    # after scanning all, send top gainers (optional)
    try:
        top = top_gainers(collected, top_n=5)
        if top:
            txt = "*Top 5 gainers (24h)*\n" + "\n".join([f"{t.get('symbol','').upper()}: {float(t.get('percent_change_24h') or 0):+.2f}%" for t in top])
            send_telegram(txt, parse_mode="Markdown")
    except Exception as e:
        print("Top gainers error:", e)

    print(f"[{datetime.now(timezone.utc).isoformat()}] Scan job finished. Found {len(collected)} coins.")

# ----------------------------
# Scheduler main
# ----------------------------
if __name__ == "__main__":
    print("Starting CoinLore technical bot...")
    sched = BlockingScheduler(timezone=pytz.UTC)
    sched.add_job(job, "interval", seconds=SCAN_INTERVAL_SECONDS, next_run_time=datetime.now())
    # run first immediately
    try:
        job()
    except Exception as e:
        print("Initial job error:", e)
    print(f"Scheduler started (interval {SCAN_INTERVAL_SECONDS}s).")
    sched.start()
