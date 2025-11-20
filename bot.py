import os
import requests
import pandas as pd
import numpy as np
from datetime import datetime
from pytz import utc
from apscheduler.schedulers.blocking import BlockingScheduler
from telegram import Bot

# =========================
# ENV VARIABLES
# =========================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY")

if TELEGRAM_TOKEN is None:
    raise Exception("❌ TELEGRAM_TOKEN missing in Railway ENV!")
if TELEGRAM_CHAT_ID is None:
    raise Exception("❌ TELEGRAM_CHAT_ID missing in Railway ENV!")

bot = Bot(token=TELEGRAM_TOKEN)


# =========================
# COINS TO TRACK (10 coins)
# =========================
COINS = {
    90: "bitcoin",
    80: "ethereum",
    58: "ripple",
    518: "tether",
    2: "litecoin",
    1: "dogecoin",
    99: "shiba-inu",
    2710: "binancecoin",
    1958: "tron",
    825: "monero",
}


# =========================
# INDICATOR FUNCTIONS
# =========================
def calc_rsi(series, period=14):
    delta = series.diff()
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)

    avg_gain = pd.Series(gain).rolling(period).mean()
    avg_loss = pd.Series(loss).rolling(period).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calc_ema(series, period=14):
    return series.ewm(span=period, adjust=False).mean()


def calc_macd(series):
    ema12 = calc_ema(series, 12)
    ema26 = calc_ema(series, 26)
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    return macd, signal


# =========================
# FETCH FROM COINLORE
# =========================
def fetch_coinlore_price(coin_id):
    try:
        url = f"https://api.coinlore.net/api/ticker/?id={coin_id}"
        r = requests.get(url, timeout=10)
        return float(r.json()[0]["price_usd"])
    except:
        return None


# =========================
# FETCH OHLC FROM COINGECKO
# =========================
def fetch_coingecko_ohlc(cgid):
    url = f"https://api.coingecko.com/api/v3/coins/{cgid}/market_chart"
    params = {
        "vs_currency": "usd",
        "days": 7,
        "interval": "hourly",
        "x_cg_demo_api_key": COINGECKO_API_KEY,
    }

    r = requests.get(url, params=params, timeout=15)
    if r.status_code != 200:
        print(f"❌ CG error {cgid}: {r.status_code}")
        return None

    data = r.json()["prices"]
    df = pd.DataFrame(data, columns=["time", "price"])
    df["price"] = df["price"].astype(float)
    return df


# =========================
# SEND ALERT MESSAGE
# =========================
def send(msg):
    bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)


# =========================
# SIGNAL GENERATOR
# =========================
def analyze(coinlore_id, cg_id):
    price_now = fetch_coinlore_price(coinlore_id)
    if price_now is None:
        return

    df = fetch_coingecko_ohlc(cg_id)
    if df is None or len(df) < 20:
        return

    df["rsi"] = calc_rsi(df["price"])
    df["ema20"] = calc_ema(df["price"], 20)
    df["macd"], df["signal"] = calc_macd(df["price"])

    rsi = df["rsi"].iloc[-1]
    ema20 = df["ema20"].iloc[-1]
    macd = df["macd"].iloc[-1]
    signal = df["signal"].iloc[-1]

    condition_messages = []

    # BUY / SELL
    if rsi < 30 and price_now > ema20:
        condition_messages.append("🔥 STRONG BUY (RSI oversold + price above EMA20)")
    elif rsi < 40:
        condition_messages.append("🟢 BUY (RSI low)")
    elif rsi > 70 and price_now < ema20:
        condition_messages.append("🔴 STRONG SELL (RSI overbought + price below EMA20)")
    elif rsi > 60:
        condition_messages.append("🟡 SELL (RSI high)")

    # MACD
    if macd > signal:
        condition_messages.append("📈 MACD Bullish Crossover")
    else:
        condition_messages.append("📉 MACD Bearish Crossover")

    if not condition_messages:
        return

    message = f"""
📊 *{cg_id.upper()} ALERT*
Price: ${price_now}

{"\n".join(condition_messages)}
    """

    send(message)


# =========================
# JOB LOOP
# =========================
def job():
    print("🔍 Running scan...")
    for cid, cgid in COINS.items():
        analyze(cid, cgid)
    print("✅ Scan complete")


# =========================
# MAIN SCHEDULER
# =========================
if __name__ == "__main__":
    print("🚀 BOT STARTED...")

    sched = BlockingScheduler(timezone=utc)

    # every 30s
    sched.add_job(job, "interval", seconds=30, timezone=utc)

    job()

    sched.start()
