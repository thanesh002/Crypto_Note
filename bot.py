import os
import time
import requests
import pandas as pd
from telegram import Bot
from apscheduler.schedulers.background import BackgroundScheduler
import pytz

# -------------------------
# ENV VARIABLES
# -------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY")

bot = Bot(token=TELEGRAM_BOT_TOKEN)

TOP10 = {
    90: "bitcoin",
    80: "ethereum",
    58: "ripple",
    518: "tether",
    2: "litecoin",
    1: "dogecoin",
    99: "shiba-inu",
    2710: "binancecoin",
    6535: "solana",
    24478: "cardano"
}

# -------------------------
# FETCH OHLC FROM COINGECKO
# -------------------------
def fetch_coingecko_ohlc(cgid):
    url = f"https://api.coingecko.com/api/v3/coins/{cgid}/market_chart"
    params = {
        "vs_currency": "usd",
        "days": 7,
        "interval": "hourly"
    }

    headers = {
        "x-cg-api-key": COINGECKO_API_KEY    # <-- CORRECT HEADER
    }

    r = requests.get(url, params=params, headers=headers, timeout=15)

    if r.status_code != 200:
        print("CG ERROR:", cgid, r.status_code, r.text[:100])
        return None

    data = r.json().get("prices", [])
    if not data:
        print("CG EMPTY:", cgid)
        return None

    df = pd.DataFrame(data, columns=["time", "price"])
    df["price"] = df["price"].astype(float)
    return df


# -------------------------
# SIMPLE INDICATORS
# -------------------------
def compute_indicators(df):
    df["rsi"] = df["price"].pct_change().rolling(14).mean()
    df["ema20"] = df["price"].ewm(span=20).mean()
    df["ema50"] = df["price"].ewm(span=50).mean()

    last = df.iloc[-1]

    signals = []

    if last["rsi"] < -0.02:
        signals.append("RSI Oversold")

    if last["rsi"] > 0.02:
        signals.append("RSI Overbought")

    if last["ema20"] > last["ema50"]:
        signals.append("Bullish Trend")

    if last["ema20"] < last["ema50"]:
        signals.append("Bearish Trend")

    return signals


# -------------------------
# SEND TELEGRAM ALERT
# -------------------------
def send_alert(msg):
    bot.send_message(chat_id=CHAT_ID, text=msg)


# -------------------------
# MAIN JOB
# -------------------------
def job():
    print("Scanning…")

    for cid, cg_id in TOP10.items():
        df = fetch_coingecko_ohlc(cg_id)

        if df is None:
            continue

        sig = compute_indicators(df)

        if sig:
            text = f"📊 *{cg_id.upper()} SIGNALS:*\n" + "\n".join(["• " + s for s in sig])
            send_alert(text)

    print("Done.")


# -------------------------
# SCHEDULER
# -------------------------
timezone = pytz.UTC
sched = BackgroundScheduler(timezone=timezone)
sched.add_job(job, "interval", seconds=30)
sched.start()

print("🚀 Bot started.")

job()  # run once at start

while True:
    time.sleep(999999)
