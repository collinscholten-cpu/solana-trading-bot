import requests
import time
import os
import hmac
import hashlib
import json
from datetime import datetime
from pycoingecko import CoinGeckoAPI
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("CHAT_ID")

cg = CoinGeckoAPI()

last_update_id = None
trading_active = True
trade_log = []

last_buy_price = None
STOP_LOSS_PERCENT = 0.04

# =============================
# TELEGRAM
# =============================
def send(message):
    url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': message
    }
    requests.post(url, data=payload)

def check_messages():
    global last_update_id

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        response = requests.get(url)
        data = response.json()

        if not data["result"]:
            return None

        update = data["result"][-1]

        if update["update_id"] != last_update_id:
            last_update_id = update["update_id"]

            if "message" in update:
                return update["message"]["text"].strip().lower()

    except Exception as e:
        print("Telegram fout:", e)

    return None

# =============================
# BITVAVO
# =============================
def bitvavo_request(method, endpoint, body=None):
    timestamp = str(int(time.time() * 1000))
    body_str = json.dumps(body, separators=(',', ':')) if body else ""

    message = timestamp + method + endpoint + body_str

    signature = hmac.new(
        API_SECRET.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()

    headers = {
        "Bitvavo-Access-Key": API_KEY,
        "Bitvavo-Access-Signature": signature,
        "Bitvavo-Access-Timestamp": timestamp,
        "Bitvavo-Access-Window": "10000",
        "Content-Type": "application/json"
    }

    url = "https://api.bitvavo.com" + endpoint

    if method == "GET":
        return requests.get(url, headers=headers).json()
    else:
        return requests.post(url, headers=headers, json=body).json()

# =============================
# DATA
# =============================
def get_price():
    data = cg.get_price(ids='solana', vs_currencies='eur')
    return data['solana']['eur']

def get_history(coin, days):
    data = cg.get_coin_market_chart_by_id(id=coin, vs_currency='eur', days=days)
    return [p[1] for p in data['prices']]

def bepaal_trend(prices):
    change = (prices[-1] - prices[0]) / prices[0] * 100

    if change > 3:
        return "stijgend"
    elif change < -3:
        return "dalend"
    return "neutraal"

def bepaal_signaal(prices, sol_trend, btc_trend):
    current = prices[-1]
    support = min(prices[-20:])
    resistance = max(prices[-20:])

    afstand_support = (current - support) / support * 100
    afstand_resistance = (resistance - current) / current * 100

    near_support = afstand_support < 2

    if near_support and btc_trend != "dalend" and sol_trend != "dalend":
        return "BUY", "Support"

    if btc_trend == "dalend":
        return "SELL", "Zwak"

    return "WAIT", "Geen setup"

# =============================
# BUY
# =============================
def buy_all():
    global last_buy_price

    balances = bitvavo_request("GET", "/v2/balance")
    eur = next((float(b['available']) for b in balances if b['symbol'] == 'EUR'), 0)

    if eur > 5:
        price = get_price()

        body = {
            "market": "SOL-EUR",
            "side": "buy",
            "orderType": "market",
            "amountQuote": str(eur)
            "clientOrderId": str(int(time.time() * 1000))
        }

        response = bitvavo_request("POST", "/v2/order", body)

        print("BUY response:", response)
        send(f"BUY response:\n{response}")  # ✅ BELANGRIJKE FIX

        last_buy_price = price
        trade_log.append(f"BUY €{eur} @ {price:.2f}")

# =============================
# SELL
# =============================
def sell_all():
    global last_buy_price

    balances = bitvavo_request("GET", "/v2/balance")
    sol = next((float(b['available']) for b in balances if b['symbol'] == 'SOL'), 0)

    if sol > 0.01:
        price = get_price()

        body = {
            "market": "SOL-EUR",
            "side": "sell",
            "orderType": "market",
            "amount": str(sol)
        }

        response = bitvavo_request("POST", "/v2/order", body)

        print("SELL response:", response)
        send(f"SELL response:\n{response}")  # ✅ BELANGRIJKE FIX

        last_buy_price = None
        trade_log.append(f"SELL @ {price:.2f}")

# =============================
# MAIN
# =============================
def main():
    global trading_active

    send("🤖 Bot live 🚀")

    while True:
        try:
            msg = check_messages()

            # STOP-LOSS
            if last_buy_price:
                current_price = get_price()
                if current_price < last_buy_price * (1 - STOP_LOSS_PERCENT):
                    send("🚨 STOP-LOSS")
                    sell_all()

            # AUTO TRADING
            sol_prices = get_history('solana', 30)
            btc_prices = get_history('bitcoin', 30)

            sol_trend = bepaal_trend(sol_prices)
            btc_trend = bepaal_trend(btc_prices)

            advies, _ = bepaal_signaal(sol_prices, sol_trend, btc_trend)

            if trading_active:

                if advies == "BUY" and last_buy_price is None:
                    send("🤖 AUTO BUY")
                    buy_all()

                elif advies == "SELL" and last_buy_price is not None and get_price() > last_buy_price:
                    send("🤖 AUTO SELL")
                    sell_all()

            if msg:
                if msg == "/stop":
                    trading_active = False
                    send("⏸ gestopt")

                elif msg == "/start":
                    trading_active = True
                    send("▶️ gestart")

        except Exception as e:
            print("Fout:", e)

        time.sleep(5)

if __name__ == "__main__":
    main()
