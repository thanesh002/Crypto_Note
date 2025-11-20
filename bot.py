import os
import time
import logging
import requests
import pandas as pd
from datetime import datetime
from indicators import compute_indicators
from telegram import Bot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# ----------------------------
# CONFIG
# ----------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY")

bot = Bot(token=TELEGRAM_BOT_TOKEN)

# Scan 10 coins maximum (stable)
TEST_COINS = {
    "bitcoin": 90,
    "ethereum": 80,
    "ripple": 58,
    "tether": 518,
    "litecoin": 2,
    "dogecoin": 1,
    "shiba-inu": 99,
    "binancecoin": 2710,
    "cardano": 257,
    "solana": 48543
}

# ----------------------------
# FETCH COINGECKO OHLC
# ----------------------------
def fetch_cg_ohlc(cg_id):
    headers = {
        "x-cg-demo-api-key": COINGECKO_API_KEY
    }

    url = (
        f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart"
        f"?vs_currency=usd&days=7&interval=hourly"
    )

    try:
        r = requests.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logging.error(f"Failed OHLC {cg_id}: {e}")
        return None

    if "prices" not in data:
        return None

    df = pd.DataFrame({
        "time": [p[0] for p in data["prices"]],
        "price": [p[1] for p in data["prices"]],
    })

    df["close"] = df["price"]
    df["open"] = df["close"].shift(1)
    df["high"] = df["close"].rolling(2).max()
    df["low"] = df["close"].rolling(2).min()

    df = df.dropna().reset_index(drop=True)
    return df


# ----------------------------
# SEND TELEGRAM MESSAGE
# ----------------------------
def send_msg(text):
    try:
        bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Telegram error: {e}")


# ----------------------------
# CLASSIFY SIGNAL
# ----------------------------
def classify_signal(df):
    rsi = df["rsi"].iloc[-1]
    macd = df["macd"].iloc[-1]
    signal_line = df["signal"].iloc[-1]
    ema20 = df["ema20"].iloc[-1]
    close = df["close"].iloc[-1]

    # Basic trend
    if close > ema20 and macd > signal_line and rsi < 70:
        return "BUY"
    if close < ema20 and macd < signal_line and rsi > 30:
        return "SELL"

    if rsi <= 25 and macd > signal_line:
        return "STRONG BUY"

    if rsi >= 75 and macd < signal_line:
        return "STRONG SELL"

    return "WAIT"


# ----------------------------
# SCAN ONE COIN
# ----------------------------
def scan_coin(cg_id, cl_id):
    df = fetch_cg_ohlc(cg_id)
    if df is None:
        return

    df = compute_indicators(df)
    signal = classify_signal(df)

    price = df["close"].iloc[-1]
    rsi = df["rsi"].iloc[-1]
    macd = df["macd"].iloc[-1]
    volume_spike = df["vol_spike"].iloc[-1]
    pump = df["pump"].iloc[-1]
    dump = df["dump"].iloc[-1]
    candle = df["pattern"].iloc[-1]

    msg = f"""
<b>{cg_id.upper()}</b>
Price: ${price:,.4f}

Signal: <b>{signal}</b>

RSI: {rsi:.2f}
MACD: {macd:.4f}

<b>Events</b>:
Pump: {pump}
Dump: {dump}
Volume Spike: {volume_spike}
Candle: {candle}
"""

    send_msg(msg)


# ----------------------------
# MAIN LOOP
# ----------------------------
def main():
    logging.info("Bot starting...")

    while True:
        logging.info("Starting scan cycle...")

        for cg_id, cl_id in TEST_COINS.items():
            scan_coin(cg_id, cl_id)
            time.sleep(2)  # avoid rate limit

        logging.info("Cycle done. Sleeping 60 seconds…")
        time.sleep(60)


if __name__ == "__main__":
    main()
