import requests
import time
import os
import hmac
import hashlib
import json
from pycoingecko import CoinGeckoAPI
from dotenv import load_dotenv

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
last_buy_price = None
last_buy_time = 0
last_update_id = 0

STOP_LOSS_PERCENT = 0.04
TAKE_PROFIT_PERCENT = 0.01
MIN_HOLD_TIME = 120

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
        url += f"?offset={last_update_id + 1}"

        data = requests.get(url).json()

        if "result" not in data or not data["result"]:
            return []

        messages = []

        for update in data["result"]:
            last_update_id = update["update_id"]

            if "message" in update and "text" in update["message"]:
                messages.append(update["message"]["text"].strip().lower())

        return messages

    except Exception as e:
        print("Telegram fout:", e)
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
        return requests.get(url, headers=headers).json()
    else:
        return requests.post(url, headers=headers, json=body).json()

# =============================
# BALANCES
# =============================
def get_balances():
    balances = bitvavo_request("GET", "/v2/balance")

    eur = next((float(b['available']) for b in balances if b['symbol'] == 'EUR'), 0)
    sol = next((float(b['available']) for b in balances if b['symbol'] == 'SOL'), 0)

    return eur, sol

# =============================
# MARKET DATA
# =============================
def get_price():
    return cg.get_price(ids="solana", vs_currencies="eur")["solana"]["eur"]

def get_history(coin, days):
    data = cg.get_coin_market_chart_by_id(id=coin, vs_currency='eur', days=days)
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
# BUY
# =============================
def buy_all():
    global last_buy_price, last_buy_time

    eur, _ = get_balances()

    if eur > 5:
        price = get_price()

        body = {
            "market": "SOL-EUR",
            "side": "buy",
            "orderType": "market",
            "amountQuote": str(eur)
        }

        response = bitvavo_request("POST", "/v2/order", body)
        send(f"BUY response:\n{response}")

        last_buy_price = price
        last_buy_time = time.time()

# =============================
# SELL
# =============================
def sell_all(reason="SELL"):
    global last_buy_price

    _, sol = get_balances()

    if sol > 0:
        price = get_price()

        body = {
            "market": "SOL-EUR",
            "side": "sell",
            "orderType": "market",
            "amount": str(sol)
        }

        response = bitvavo_request("POST", "/v2/order", body)
        send(f"{reason}\nSELL response:\n{response}")

        last_buy_price = None

# =============================
# MAIN LOOP ✅ (FIXED!)
# =============================
def main():
    global trading_active, last_buy_price

    send("🤖 Bot live 🚀")

    while True:
        try:
            # ✅ ALTIJD EERST TELEGRAM
            messages = check_messages()

            # ✅ DIRECT COMMANDS
            for msg in messages:

                if "/update" in msg:
                    now = time.strftime("%d-%m-%Y %H:%M")

                    eur, sol = get_balances()
                    sol_price = get_price()

                    totaal = eur + (sol * sol_price)
                    status = "BUY" if sol > 0 else "SELL"

                    send(
                        f"Solana {now}\n\n"
                        f"Koers: €{sol_price:.2f}\n"
                        f"Status Bitvavo: {status}\n"
                        f"Saldo: €{totaal:.2f}"
                    )

                elif "/sell" in msg:
                    sell_all("⚡ HANDMATIG")

                elif "/stop" in msg:
                    trading_active = False
                    send("⏸ Bot gepauzeerd")

                elif "/start" in msg:
                    trading_active = True
                    send("▶️ Bot actief")

            # ✅ MARKET ANALYSE
            sol_price = get_price()
            sol_prices = get_history("solana", 30)
            btc_prices = get_history("bitcoin", 30)

            sol_trend = bepaal_trend(sol_prices)
            btc_trend = bepaal_trend(btc_prices)

            advies = bepaal_signaal(sol_prices, sol_trend, btc_trend)

            eur, sol = get_balances()

            time_since_buy = time.time() - last_buy_time if last_buy_time else 0

            # ✅ AUTO TRADING
            if trading_active:

                # BUY
                if advies == "BUY" and sol == 0:
                    buy_all()

                # TAKE PROFIT
                elif sol > 0 and last_buy_price and sol_price > last_buy_price * (1 + TAKE_PROFIT_PERCENT):
                    sell_all("💰 TAKE PROFIT")

                # STOP LOSS
                elif sol > 0 and last_buy_price and sol_price < last_buy_price * (1 - STOP_LOSS_PERCENT):
                    sell_all("🚨 STOP LOSS")

                # SIGNAL SELL
                elif advies == "SELL" and sol > 0 and time_since_buy > MIN_HOLD_TIME:
                    sell_all("🔴 SIGNAL SELL")

        except Exception as e:
            print("FOUT:", e)

        time.sleep(15)

# =============================
# START ✅
# =============================
if __name__ == "__main__":
    main()
