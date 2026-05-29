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
highest_price = None
market_mode = "neutraal"

STOP_LOSS_PERCENT = 0.04
TAKE_PROFIT_TRIGGER = 0.02
TRAILING_STOP = 0.01

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
        "Bitvavo-Access-Window": "60000",
        "Content-Type": "application/json"
    }

    url = "https://api.bitvavo.com" + endpoint

    if method == "GET":
        r = requests.get(url, headers=headers)
    else:
        r = requests.post(url, headers=headers, json=body)

    return r.json()

# =============================
# BALANCE
# =============================
def get_balances():
    balances = bitvavo_request("GET", "/v2/balance")

    eur = next((float(b['available']) for b in balances if b['symbol'] == 'EUR'), 0)
    sol = next((float(b['available']) for b in balances if b['symbol'] == 'SOL'), 0)

    return eur, sol

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

def bepaal_signaal(prices, sol_trend, btc_trend):
    support = min(prices[-20:])
    current = prices[-1]
    afstand = (current - support) / support * 100

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

    sol_prices_7 = get_history("solana", 7)
    sol_prices_30 = get_history("solana", 30)

    price = sol_prices_7[-1]

    change_7d = ((price - sol_prices_7[0]) / sol_prices_7[0]) * 100
    change_30d = ((price - sol_prices_30[0]) / sol_prices_30[0]) * 100

    trend_7d = bepaal_trend(sol_prices_7)
    trend_30d = bepaal_trend(sol_prices_30)

    # ✅ MARKET MODE
    if trend_30d == "dalend":
        market_mode = "bearish"
    elif trend_30d == "stijgend":
        market_mode = "bullish"
    else:
        market_mode = "neutraal"

    bericht = (
        f"📊 Daganalyse Solana\n\n"
        f"Koers: €{price:.2f}\n"
        f"7 dagen: {change_7d:.2f}%\n"
        f"30 dagen: {change_30d:.2f}%\n\n"
        f"Trend 7d: {trend_7d}\n"
        f"Trend 30d: {trend_30d}\n\n"
        f"Market mode: {market_mode}\n"
    )

    return bericht

# =============================
# BUY
# =============================
def buy_all():
    global last_buy_price, highest_price

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
        send(f"BUY response:\n{response}")

        last_buy_price = price
        highest_price = None

# =============================
# SELL
# =============================
def sell_all():
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

        response = bitvavo_request("POST", "/v2/order", body)
        send(f"SELL response:\n{response}")

        last_buy_price = None
        highest_price = None

# =============================
# MAIN
# =============================
def main():
    global trading_active, last_buy_price, highest_price, last_analysis_day

    send("🤖 Bot live 🚀")

    while True:
        try:
            now = datetime.now()

            # =============================
            # ✅ 00:01 DAGELIJKSE ANALYSE
            # =============================
            if now.hour == 0 and now.minute == 1:
                if last_analysis_day != now.day:
                    analysis = analyse_market()
                    send(analysis)
                    last_analysis_day = now.day

            messages = check_messages()

            sol_price = get_price()
            sol_prices = get_history("solana", 30)
            btc_prices = get_history("bitcoin", 30)

            sol_trend = bepaal_trend(sol_prices)
            btc_trend = bepaal_trend(btc_prices)

            advies = bepaal_signaal(sol_prices, sol_trend, btc_trend)

            eur, sol = get_balances()

            # =============================
            # ✅ AUTO TRADING (MET MARKET MODE)
            # =============================
            if trading_active:

                # BUY (gebonden aan market_mode)
                if advies == "BUY" and sol == 0:

                    if market_mode != "bearish":
                        last_buy_price = sol_price
                        buy_all()

                # SELL signaal
                elif advies == "SELL" and sol > 0:
                    sell_all()

                # ✅ TRAILING PROFIT
                if last_buy_price and sol > 0:

                    if not highest_price:
                        highest_price = sol_price

                    if sol_price > highest_price:
                        highest_price = sol_price

                    if highest_price >= last_buy_price * (1 + TAKE_PROFIT_TRIGGER):

                        if sol_price <= highest_price * (1 - TRAILING_STOP):
                            send("💰 TAKE PROFIT SELL")
                            sell_all()

            # =============================
            # COMMANDS
            # =============================
            for msg in messages:

                if "/analyse" in msg:
                    send(analyse_market())

                elif "/update" in msg:
                    totaal = eur + (sol * sol_price)
                    status = "BUY" if sol > 0 else "SELL"

                    send(
                        f"Koers: €{sol_price:.2f}\n"
                        f"Status: {status}\n"
                        f"Saldo: €{totaal:.2f}\n"
                        f"Market mode: {market_mode}"
                    )

                elif "/sell" in msg:
                    sell_all()

        except Exception as e:
            print("Fout:", e)

        time.sleep(15)

# =============================
# START
# =============================
if __name__ == "__main__":
    main()
