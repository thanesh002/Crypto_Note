import requests
import csv
import os
import time
from apscheduler.schedulers.blocking import BlockingScheduler

BOT_TOKEN = os.getenv("8367746826:AAGvDBqPGE0y9eAa8YOoApzGzvfv9CG8zew")
CHAT_ID = os.getenv("782614632")

def tg(msg):
    """Send Telegram message"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": msg})

def load_coins():
    """Load coins from CSV (symbol + coinlore ID)"""
    coins = []
    with open("coinlist.csv", "r") as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            coins.append({"symbol": row[0], "id": row[1]})
    return coins

def get_price(coin_id):
    """Fetch price from CoinLore"""
    url = f"https://api.coinlore.net/api/ticker/?id={coin_id}"
    try:
        data = requests.get(url, timeout=10).json()
        return float(data[0]["price_usd"])
    except:
        return None
        print(f"Fetching price for ID {coin_id}")


def get_signal(price):
    """Simple Buy/Sell logic"""
    if price is None:
        return "DEAD"
    if price < 1:
        return "STRONG BUY"
    if 1 <= price < 10:
        return "BUY"
    if 10 <= price < 50:
        return "WAIT"
    return "SELL"

def check_market():
    coins = load_coins()

    for c in coins:
        price = get_price(c["id"])
        signal = get_signal(price)
        msg = f"{c['symbol']} → ${price}\nSignal: {signal}"

        tg(msg)
        time.sleep(1)
print(f"Checking coin: {c['symbol']}")


def start():
    print("Bot is starting…")  # Railway will show this
    tg("🚀 Crypto Alert Bot Started Successfully!")

    scheduler = BlockingScheduler()
    scheduler.add_job(check_market, "interval", minutes=5)

    print("Scheduler started")  # Railway will show this
    scheduler.start()

if __name__ == "__main__":
    start()
