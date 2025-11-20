import os
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler
from telegram import Bot

# ---------------------------
# ENV variables
# ---------------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY")

if not TELEGRAM_TOKEN:
    raise Exception("❌ TELEGRAM_TOKEN missing in Railway Variables")

bot = Bot(token=TELEGRAM_TOKEN)

# ---------------------------
# 10 SAFE COINS (CoinGecko IDs)
# ---------------------------
COINS = {
    "bitcoin": 90,
    "ethereum": 80,
    "tether": 518,
    "ripple": 58,
    "binancecoin": 2710,
    "dogecoin": 1,
    "cardano": 257,
    "solana": 4128,
    "avalanche-2": 5805,
    "polkadot": 121
}

# ---------------------------
# Indicators
# ---------------------------
def rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def ema(series, period=20):
    return series.ewm(span=period, adjust=False).mean()

def macd(series):
    ema12 = series.ewm(span=12).mean()
    ema26 = series.ewm(span=26).mean()
    macd_line = ema12 - ema26
    signal = macd_line.ewm(span=9).mean()
    return macd_line, signal

# ---------------------------
# Fetch OHLC (7 days, 1h candles)
# ---------------------------
headers = {"x-cg-api-key": COINGECKO_API_KEY}

def fetch_ohlc(coin_id):
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
    params = {"vs_currency": "usd", "days": 7, "interval": "hourly"}
    r = requests.get(url, headers=headers, params=params)

    if r.status_code == 401:
        print("❌ CoinGecko API Key incorrect / expired")
        return None

    if r.status_code != 200:
        print(f"❌ Failed {coin_id}: {r.status_code}")
        return None

    data = r.json()
    ohlc = pd.DataFrame(data["prices"], columns=["t", "price"])
    ohlc["t"] = pd.to_datetime(ohlc["t"], unit="ms")
    return ohlc

# ---------------------------
# Alerts
# ---------------------------
def analyze(coin, df):
    alerts = []

    df["rsi"] = rsi(df["price"])
    df["ema20"] = ema(df["price"], 20)
    df["ema50"] = ema(df["price"], 50)
    df["macd"], df["signal"] = macd(df["price"])

    latest = df.iloc[-1]

    # RSI Overbought/Oversold
    if latest["rsi"] >= 70:
        alerts.append("🔴 RSI Overbought (Sell)")
    elif latest["rsi"] <= 30:
        alerts.append("🟢 RSI Oversold (Buy)")

    # EMA Cross
    if latest["ema20"] > latest["ema50"]:
        alerts.append("📈 EMA Bullish Crossover")
    else:
        alerts.append("📉 EMA Bearish Crossover")

    # MACD
    if latest["macd"] > latest["signal"]:
        alerts.append("🟢 MACD Bullish")
    else:
        alerts.append("🔴 MACD Bearish")

    # Pump / Dump
    if df["price"].pct_change().iloc[-1] >= 0.05:
        alerts.append("🚀 Pump Detected (+5%)")
    if df["price"].pct_change().iloc[-1] <= -0.05:
        alerts.append("⚠️ Dump Detected (-5%)")

    return alerts

# ---------------------------
# Telegram sender
# ---------------------------
def send(msg):
    try:
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)
    except Exception as e:
        print("Telegram error:", e)

# ---------------------------
# MAIN SCAN
# ---------------------------
def job():
    print("Running scan...")

    for cg_id in COINS.keys():
        df = fetch_ohlc(cg_id)
        if df is None:
            continue

        alerts = analyze(cg_id, df)

        if alerts:
            msg = f"📊 <b>{cg_id.upper()}</b>\n" + "\n".join(alerts)
            send(msg)

    print("Scan complete.")

# ---------------------------
# Scheduler
# ---------------------------
sched = BlockingScheduler()
sched.add_job(job, "interval", seconds=30)

print("✅ Bot is running...")
job()
sched.start()
