import os
import time
import requests
import pandas as pd
from telegram import Bot
from apscheduler.schedulers.background import BackgroundScheduler
import pytz

# ---------------------------------------
# ENV VARS
# ---------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY")

bot = Bot(token=TELEGRAM_BOT_TOKEN)

TOP10 = {
    "bitcoin": "bitcoin",
    "ethereum": "ethereum",
    "ripple": "ripple",
    "tether": "tether",
    "litecoin": "litecoin",
    "dogecoin": "dogecoin",
    "shiba-inu": "shiba-inu",
    "binancecoin": "binancecoin",
    "solana": "solana",
    "cardano": "cardano"
}

# ---------------------------------------
# FETCH OHLC FROM COINGECKO PRO
# ---------------------------------------
def fetch_coingecko_ohlc(cgid):
    url = f"https://pro-api.coingecko.com/api/v3/coins/{cgid}/market_chart"

    params = {
        "vs_currency": "usd",
        "days": 7,         # PRO version uses this
        # "interval": "hourly"  <-- REMOVE (NOT ALLOWED IN PRO)
    }

    headers = {
        "accept": "application/json",
        "x-cg-pro-api-key": COINGECKO_API_KEY
    }

    r = requests.get(url, params=params, headers=headers)

    if r.status_code != 200:
        print("CG ERROR:", cgid, r.status_code, r.text[:150])
        return None

    data = r.json().get("prices", [])
    if not data:
        print("CG EMPTY:", cgid)
        return None

    df = pd.DataFrame(data, columns=["time", "price"])
    df["price"] = df["price"].astype(float)
    return df


# ---------------------------------------
# INDICATORS
# ---------------------------------------
def compute_indicators(df):
    df["returns"] = df["price"].pct_change()
    df["rsi"] = df["returns"].rolling(14).mean()
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


# ---------------------------------------
# SEND TELEGRAM ALERT
# ---------------------------------------
def send_alert(msg):
    bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")


# ---------------------------------------
# MAIN JOB
# ---------------------------------------
def job():
    print("Scanning…")

    for cg_id in TOP10.values():
        df = fetch_coingecko_ohlc(cg_id)

        if df is None:
            continue

        sig = compute_indicators(df)

        if sig:
            text = f"📊 *{cg_id.upper()} SIGNALS:*\n" + "\n".join(f"• {s}" for s in sig)
            send_alert(text)

    print("Done.")


# ---------------------------------------
# SCHEDULER
# ---------------------------------------
sched = BackgroundScheduler(timezone=pytz.UTC)
sched.add_job(job, "interval", seconds=30)
sched.start()

print("🚀 Bot started.")
job()

while True:
    time.sleep(999999)
