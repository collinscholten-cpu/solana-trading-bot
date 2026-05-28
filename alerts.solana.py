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
trade_log = []
last_buy_price = None
last_update_id = None
STOP_LOSS_PERCENT = 0.04

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

    except Exception as e:
        print("Telegram fout:", e)
        return []

# =============================
# BITVAVO API
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
    return cg.get_price(ids='solana', vs_currencies='eur')['solana']['eur']

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
    global last_buy_price

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
        trade_log.append(f"BUY €{eur:.2f} @ {price:.2f}")

# =============================
# SELL
# =============================
def sell_all():
    global last_buy_price

    _, sol = get_balances()
    send(f"DEBUG SELL: SOL={sol}")
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
        trade_log.append(f"SELL @ €{price:.2f}")

# =============================
# MAIN LOOP
# =============================
def main():
    global trading_active, last_buy_price

    send("🤖 Bot live 🚀")

    while True:
        try:
            messages = check_messages()

            # STOP LOSS
            if last_buy_price and get_price() < last_buy_price * (1 - STOP_LOSS_PERCENT):
                send("🚨 STOP-LOSS")
                sell_all()

            sol_price = get_price()
            sol_prices = get_history("solana", 30)
            btc_prices = get_history("bitcoin", 30)
            eth_prices = get_history("ethereum", 30)

            sol_trend = bepaal_trend(sol_prices)
            btc_trend = bepaal_trend(btc_prices)
            eth_trend = bepaal_trend(eth_prices)

            advies = bepaal_signaal(sol_prices, sol_trend, btc_trend)

            # ✅ AUTO TRADING (SELL FIXED)
            if trading_active:

                eur, sol = get_balances()
                # ✅ BUY (alleen als je geen positie hebt)
                if advies == "BUY" and sol == 0:

                    last_buy_price = sol_price
                    buy_all()
                
                # ✅ SELL (altijd als je SOL hebt)
                elif advies == "SELL" and sol > 0:
                    send("🔴 AUTO SELL")
                    sell_all()


            # COMMANDS
            for msg in messages:

                if "/sell" in msg:
                    send("⚡ Handmatige SELL")
                    sell_all()

                elif "/update" in msg:

                    now = time.strftime("%d-%m-%Y %H:%M")

                    support = min(sol_prices[-20:])
                    resistance = max(sol_prices[-20:])

                    eur, sol = get_balances()
                    totaal = eur + (sol * sol_price)

                    positie_status = "Status Bitvavo: BUY" if sol > 0.001 else "Status Bitvavo: SELL"

                    if advies == "BUY":
                        signaal = "🟢 BUY — Kans omhoog"
                    elif advies == "SELL":
                        signaal = "🔴 SELL — Zwakte"
                    else:
                        signaal = "⏸ WAIT — Onzeker"

                    bericht = (
                        f"Solana {now}:\n\n"
                        f"Koers: €{sol_price:.2f}\n"
                        f"{positie_status}\n"
                        f"Signaal: {signaal}\n\n"
                        f"SOL trend: {sol_trend}\n"
                        f"BTC trend: {btc_trend}\n"
                        f"ETH trend: {eth_trend}\n\n"
                        f"Support: €{support:.2f}\n"
                        f"Resistance: €{resistance:.2f}\n\n"
                        f"Saldo Bitvavo: €{totaal:.2f}"
                    )

                    send(bericht)

                elif "/stop" in msg:
                    trading_active = False
                    send("⏸ Bot gepauzeerd")

                elif "/start" in msg:
                    trading_active = True
                    send("▶️ Bot actief")

        except Exception as e:
            print("Fout:", e)

        time.sleep(30)

# =============================
# START
# =============================
if __name__ == "__main__":
    main()
