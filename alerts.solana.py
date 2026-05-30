import requests
import time
import os
import hmac
import hashlib
import json
from pycoingecko import CoinGeckoAPI
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("CHAT_ID")

cg = CoinGeckoAPI()

# =============================
# STATE
# =============================
trading_active = True
last_update_id = None

last_buy_price = None
last_buy_time = 0
highest_price = None
market_mode = "neutraal"

STOP_LOSS_PERCENT = 0.04
TAKE_PROFIT_TRIGGER = 0.02
TRAILING_STOP = 0.01

MIN_HOLD_TIME = 600        # ✅ 10 minuten
MIN_PROFIT = 0.005         # ✅ 0.5%

last_analysis_day = None

# =============================
# TELEGRAM
# =============================
def send(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message})

def check_messages():
    global last_update_id
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        if last_update_id:
            url += f"?offset={last_update_id + 1}"

        data = requests.get(url).json()
        if not data["result"]:
            return []

        messages = []
        for update in data["result"]:
            last_update_id = update["update_id"]
            if "message" in update and "text" in update["message"]:
                messages.append(update["message"]["text"].strip().lower())

        return messages
    except:
        return []

# =============================
# BITVAVO
# =============================
def bitvavo_request(method, endpoint, body=None):
    timestamp = str(int(time.time() * 1000))
    body_str = json.dumps(body, separators=(',', ':')) if body else ""

    message = timestamp + method + endpoint + body_str

    signature = hmac.new(
        bytes(API_SECRET, 'utf-8'),
        bytes(message, 'utf-8'),
        hashlib.sha256
    ).hexdigest()

    headers = {
        "Bitvavo-Access-Key": API_KEY,
        "Bitvavo-Access-Signature": signature,
        "Bitvavo-Access-Timestamp": timestamp,
        "Content-Type": "application/json"
    }

    url = "https://api.bitvavo.com" + endpoint

    if method == "GET":
        return requests.get(url, headers=headers).json()
    return requests.post(url, headers=headers, json=body).json()

# =============================
# DATA
# =============================
def get_price():
    return cg.get_price(ids="solana", vs_currencies="eur")["solana"]["eur"]

def get_history(coin, days):
    data = cg.get_coin_market_chart_by_id(id=coin, vs_currency="eur", days=days)
    return [p[1] for p in data["prices"]]

def bepaal_trend(prices):
    change = (prices[-1] - prices[0]) / prices[0] * 100
    if change > 3:
        return "stijgend"
    elif change < -3:
        return "dalend"
    return "neutraal"

def bepaal_signaal(prices, btc_prices):
    support = min(prices[-20:])
    current = prices[-1]
    afstand = (current - support) / support * 100

    btc_trend = bepaal_trend(btc_prices)
    sol_trend = bepaal_trend(prices)

    if afstand < 2 and btc_trend != "dalend" and sol_trend != "dalend":
        return "BUY"
    if btc_trend == "dalend":
        return "SELL"
    return "WAIT"

# =============================
# ANALYSE
# =============================
def analyse_market():
    global market_mode

    sol_30 = get_history("solana", 30)
    trend = bepaal_trend(sol_30)

    if trend == "dalend":
        market_mode = "bearish"
    elif trend == "stijgend":
        market_mode = "bullish"
    else:
        market_mode = "neutraal"

    return f"📊 Market mode: {market_mode}"

# =============================
# BUY
# =============================
def buy_all():
    global last_buy_price, last_buy_time, highest_price

    eur, _ = get_balances()
    if eur > 5:
        price = get_price()

        body = {
            "market": "SOL-EUR",
            "side": "buy",
            "orderType": "market",
            "amountQuote": str(eur),
            "operatorId": str(int(time.time() * 1000))
        }

        response = bitvavo_request("POST", "/v2/order", body)
        send(f"BUY:\n{price}")

        last_buy_price = price
        last_buy_time = time.time()
        highest_price = price

# =============================
# SELL
# =============================
def sell_all(reason="SELL"):
    global last_buy_price, highest_price

    _, sol = get_balances()

    if sol > 0:
        price = get_price()

        body = {
            "market": "SOL-EUR",
            "side": "sell",
            "orderType": "market",
            "amount": str(sol),
            "operatorId": str(int(time.time() * 1000))
        }

        bitvavo_request("POST", "/v2/order", body)
        send(f"{reason} @ {price}")

        last_buy_price = None
        highest_price = None

# =============================
# BALANCE
# =============================
def get_balances():
    balances = bitvavo_request("GET", "/v2/balance")

    eur = next((float(b['available']) for b in balances if b['symbol'] == 'EUR'), 0)
    sol = next((float(b['available']) for b in balances if b['symbol'] == 'SOL'), 0)

    return eur, sol

# =============================
# MAIN
# =============================
def main():
    global last_analysis_day, highest_price

    send("🤖 Bot live")

    while True:
        try:
            now = datetime.now()

            # ✅ 00:01 analyse
            if now.hour == 0 and now.minute == 1:
                if last_analysis_day != now.day:
                    send(analyse_market())
                    last_analysis_day = now.day

            messages = check_messages()

            sol_price = get_price()
            sol_prices = get_history("solana", 30)
            btc_prices = get_history("bitcoin", 30)

            advies = bepaal_signaal(sol_prices, btc_prices)
            eur, sol = get_balances()
            time_since_buy = time.time() - last_buy_time if last_buy_time else 0

            # =============================
            # ✅ TRADING LOGIC
            # =============================
            if trading_active:

                # BUY alleen als niet bearish
                if advies == "BUY" and sol == 0 and market_mode != "bearish":
                    buy_all()

                # STOP LOSS
                elif sol > 0 and last_buy_price and sol_price < last_buy_price * (1 - STOP_LOSS_PERCENT):
                    sell_all("🚨 STOP LOSS")

                # TRAILING PROFIT
                elif sol > 0 and last_buy_price:

                    if sol_price > highest_price:
                        highest_price = sol_price

                    winst = (sol_price / last_buy_price) - 1

                    if winst > TAKE_PROFIT_TRIGGER:
                        drop = (highest_price - sol_price) / highest_price

                        if drop > TRAILING_STOP:
                            sell_all("💰 TRAILING")

                # SELL alleen als zinvol
                elif advies == "SELL" and sol > 0:

                    winst = (sol_price / last_buy_price) - 1

                    if winst > MIN_PROFIT and time_since_buy > MIN_HOLD_TIME:
                        sell_all("🔴 SIGNAL SELL")

            # =============================
            # COMMANDS
            # =============================
            for msg in messages:

                if "/update" in msg:
                    totaal = eur + (sol * sol_price)
                    status = "BUY" if sol > 0 else "SELL"

                    send(
                        f"Koers: €{sol_price:.2f}\n"
                        f"Status: {status}\n"
                        f"Saldo: €{totaal:.2f}\n"
                        f"Mode: {market_mode}"
                    )

                elif "/analyse" in msg:
                    send(analyse_market())

        except Exception as e:
            print("Error:", e)

        time.sleep(15)

# =============================
# START
# =============================
if __name__ == "__main__":
    main()
