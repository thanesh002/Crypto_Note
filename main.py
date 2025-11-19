# main.py
# FULL TELEGRAM CRYPTO ALERT BOT
# CoinLore scanner, scoring, DB, scheduler, alerts, FastAPI endpoints

import os
import asyncio
import aiohttp
import aiosqlite
import csv
import math
from datetime import datetime, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL_SECONDS", "60"))
COIN_LIST_PATH = os.getenv("COIN_LIST_PATH", "coinlist.csv")
DATABASE_PATH = os.getenv("DATABASE_PATH", "signals.db")

COINLORE_API = "https://api.coinlore.net/api/tickers/?start=0&limit=200"

app = FastAPI()
scheduler = AsyncIOScheduler()

def load_coin_list(path):
    coins = []
    try:
        with open(path, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for r in reader:
                sym = (r.get('symbol') or "").strip().upper()
                name = (r.get('name') or "").strip()
                if sym:
                    coins.append({'symbol': sym, 'name': name})
    except Exception as e:
        print("[coinlist] load error:", e)
    return coins

COIN_LIST = load_coin_list(COIN_LIST_PATH)
WATCH_SYMBOLS = [c['symbol'] for c in COIN_LIST]

def now_iso():
    return datetime.now(timezone.utc).isoformat()

async def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[telegram] not configured")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, data=payload, timeout=15) as resp:
                await resp.text()
    except Exception as e:
        print("[telegram] error", e)

async def init_db():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS signals ("
            "symbol TEXT PRIMARY KEY, last_signal TEXT, last_score INTEGER, last_checked TEXT);"
        )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS history ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, symbol TEXT, "
            "old_signal TEXT, new_signal TEXT, old_score INTEGER, new_score INTEGER);"
        )
        await db.commit()
    print("[db] initialized")

def compute_score_from_coinlore(item, median_volume):
    def fnum(v):
        try:
            return float(v)
        except:
            return None

    price = fnum(item.get("price_usd"))
    ch24 = fnum(item.get("percent_change_24h")) or 0.0
    ch7 = fnum(item.get("percent_change_7d")) or 0.0
    vol = fnum(item.get("volume24")) or 0.0
    mc = fnum(item.get("market_cap_usd")) or None

    mid_daily = ch7 / 7.0
    momentum = 0.7 * ch24 + 0.3 * mid_daily

    liquidity = 0.0
    if mc and mc > 0:
        liquidity = vol / mc

    vol_spike = ""
    if median_volume and median_volume > 0:
        if vol >= median_volume * 3:
            vol_spike = "3x"
        elif vol >= median_volume * 2:
            vol_spike = "2x"

    pseudo = 50 + (ch24 * 1.6) + (mid_daily * 1.0)
    if vol_spike == "3x":
        pseudo += math.copysign(6, ch24)
    elif vol_spike == "2x":
        pseudo += math.copysign(3, ch24)
    pseudo = max(1, min(99, pseudo))

    sma = None
    if price is not None:
        avg_daily = (mid_daily / 100.0)
        avg_daily = max(-0.2, min(0.2, avg_daily))
        divisor = 1 + (avg_daily * 25)
        if divisor != 0:
            sma = price / divisor

    s = 50.0
    s += (50 - pseudo) * 0.6
    s += max(min(momentum, 30), -30) * 0.8
    if vol_spike == "3x":
        s += 8
    elif vol_spike == "2x":
        s += 5
    if liquidity > 0.001:
        s += 8
    elif liquidity > 0.0001:
        s += 4
    if sma and price:
        if price > sma * 1.02:
            s += 5
        elif price < sma * 0.98:
            s -= 3
    if mc and mc < 1e8 and momentum > 5:
        s += 3

    is_dead = False
    if mc and vol and mc > 0:
        if vol < mc * 0.000001:
            is_dead = True
    if ch7 <= -40:
        is_dead = True

    if is_dead:
        s = 0

    s = int(max(0, min(100, round(s))))
    if is_dead:
        signal = "Dead"
    elif s >= 85:
        signal = "Strong Buy"
    elif s >= 65:
        signal = "Buy"
    elif s >= 40:
        signal = "Neutral"
    else:
        signal = "Sell"

    return {
        "score": s,
        "signal": signal,
        "rsi": round(pseudo, 2),
        "price": price,
        "ch24": ch24,
        "ch7": ch7,
        "vol": vol,
        "vol_spike": vol_spike,
        "liquidity": liquidity,
        "sma": sma,
        "momentum": round(momentum, 3)
    }

async def fetch_coinlore():
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(COINLORE_API, timeout=20) as r:
                if r.status != 200:
                    print("[coinlore] status", r.status)
                    return None
                return await r.json()
    except Exception as e:
        print("[coinlore] fetch error", e)
        return None

async def scan_and_alert():
    data = await fetch_coinlore()
    if not data or "data" not in data:
        print("[scan] coinlore missing data")
        return

    arr = data["data"]
    mapping = {str(x.get("symbol", "")).upper(): x for x in arr}

    vols = []
    for x in arr:
        try:
            v = float(x.get("volume24") or 0.0)
            if v > 0:
                vols.append(v)
        except:
            pass
    vols.sort()
    median_volume = vols[len(vols) // 2] if vols else 0

    async with aiosqlite.connect(DATABASE_PATH) as db:
        for sym in WATCH_SYMBOLS:
            item = mapping.get(sym)
            if not item:
                print(f"[scan] coinlore missing symbol {sym}")
                continue

            stats = compute_score_from_coinlore(item, median_volume)

            async with db.execute("SELECT last_signal,last_score FROM signals WHERE symbol = ?", (sym,)) as cur:
                row = await cur.fetchone()
                prev_signal = row[0] if row else None
                prev_score = row[1] if row else None

            new_signal = stats["signal"]
            new_score = stats["score"]
            now = now_iso()

            await db.execute(
                "INSERT INTO signals(symbol,last_signal,last_score,last_checked) VALUES(?,?,?,?) "
                "ON CONFLICT(symbol) DO UPDATE SET last_signal=excluded.last_signal, last_score=excluded.last_score, last_checked=excluded.last_checked",
                (sym, new_signal, new_score, now)
            )
            await db.commit()

            if prev_signal != new_signal:
                await db.execute(
                    "INSERT INTO history(ts,symbol,old_signal,new_signal,old_score,new_score) VALUES(?,?,?,?,?,?)",
                    (now, sym, prev_signal or "", new_signal, int(prev_score) if prev_score is not None else None, new_score)
                )
                await db.commit()

                name = next((c["name"] for c in COIN_LIST if c["symbol"] == sym), sym)
                msg = (
                    f"{new_signal}: {name} ({sym})\n"
                    f"Score: {new_score}  RSI-like: {stats['rsi']}  24h%: {stats['ch24']}  7d%: {stats['ch7']}\n"
                    f"Momentum: {stats['momentum']}  VolSpike: {stats['vol_spike']}  Liquidity: {stats['liquidity']:.3e}\n"
                    f"Price: {stats['price']}"
                )
                print("[alert]", msg)
                await send_telegram(msg)
    print("[scan] completed", now_iso())

async def start_scheduler():
    await init_db()
    scheduler.add_job(scan_and_alert, "interval", seconds=SCAN_INTERVAL, next_run_time=None)
    scheduler.start()
    print(f"[scheduler] started every {SCAN_INTERVAL}s")

@app.on_event("startup")
async def startup_event():
    loop = asyncio.get_event_loop()
    loop.create_task(start_scheduler())

@app.get("/health")
async def health():
    return {"status": "ok", "time": now_iso()}

@app.get("/top")
async def top_n(limit: int = 20):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        rows = await db.execute_fetchall("SELECT symbol,last_signal,last_score,last_checked FROM signals ORDER BY last_score DESC LIMIT ?", (limit,))
        out = [{"symbol": r[0], "signal": r[1], "score": r[2], "last_checked": r[3]} for r in rows]
        return out

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=False)
