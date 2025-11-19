import os
import time
import requests

TOKEN = os.getenv("8367746826:AAGvDBqPGE0y9eAa8YOoApzGzvfv9CG8zew")
CHAT_ID = os.getenv("782614632")


def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg}
    r = requests.post(url, json=payload)
    print("Sent:", r.text)


print("Bot started successfully!")

# test message on deploy
send_telegram("Crypto bot started on Render! 🚀")

# main loop
while True:
    send_telegram("Heartbeat: Bot is running ❤️")  
    time.sleep(60)
