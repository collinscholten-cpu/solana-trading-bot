import requests
import time
import os
import hmac
import hashlib
import json
from datetime import datetime
from pycoingecko import CoinGeckoAPI
from dotenv import load_dotenv

# =============================
# ✅ LOAD ENV (lokaal + Railway)
# =============================
load_dotenv()

API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("CHAT_ID")

cg = CoinGeckoAPI()

last_update_id = None
trading_active = True
trade_log = []

# =============================
# 📩 TELEGRAM SEND
# =============================
def send(message):
    url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': message,
        'parse_mode': 'Markdown'
    }
    requests.post(url, data=payload)

# =============================
# 📥 TELEGRAM MESSAGES
# =============================
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
# ✅ BITVAVO API (FIXED)
# =============================
def bitvavo_request(method, endpoint, body=None):
    timestamp = str(int(time.time() * 1000))
    body_str = json.dumps(body) if body else ""

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
# 💰 PRIJS
# =============================
def get_price():
    data = cg.get_price(ids='solana', vs_currencies='eur')
    return data['solana']['eur']

# =============================
# 📊 HISTORIE
# =============================
def get_history(coin, days):
    data = cg.get_coin_market_chart_by_id(id=coin, vs_currency='eur', days=days)
    return [p[1] for p in data['prices']]

# =============================
# 📈 TREND
# =============================
def bepaal_trend(prices):
    change = (prices[-1] - prices[0]) / prices[0] * 100

    if change > 3:
        return "stijgend"
    elif change < -3:
        return "dalend"
    return "neutraal"

# =============================
# 🧠 SIGNALEN
# =============================
def bepaal_signaal(prices, sol_trend, btc_trend):
    support = min(prices[-20:])
    resistance = max(prices[-20:])

    if sol_trend == "stijgend" and btc_trend == "stijgend":
        return "BUY", "Sterke markt", support, resistance

    if btc_trend == "dalend":
        return "SELL", "Markt zwak", support, resistance

    return "SELL", "Onzeker", support, resistance

# =============================
# ✅ BUY
# =============================
def buy_all():
    balances = bitvavo_request("GET", "/v2/balance")
    eur = next((float(b['available']) for b in balances if b['symbol'] == 'EUR'), 0)

    if eur > 5:
        body = {
            "market": "SOL-EUR",
            "side": "buy",
            "orderType": "market",
            "amountQuote": str(eur)
        }
        bitvavo_request("POST", "/v2/order", body)
        trade_log.append(f"BUY €{eur} @ {datetime.now().strftime('%H:%M')}")
        send("✅ BUY uitgevoerd")

# =============================
# ✅ SELL
# =============================
def sell_all():
    balances = bitvavo_request("GET", "/v2/balance")
    sol = next((float(b['available']) for b in balances if b['symbol'] == 'SOL'), 0)

    if sol > 0.01:
        body = {
            "market": "SOL-EUR",
            "side": "sell",
            "orderType": "market",
            "amount": str(sol)
        }
        bitvavo_request("POST", "/v2/order", body)
        trade_log.append(f"SELL {sol} SOL @ {datetime.now().strftime('%H:%M')}")
        send("✅ SELL uitgevoerd")

# =============================
# 🔁 MAIN LOOP
# =============================
def main():
    global trading_active

    send(
        "🤖 Auto-trading bot actief!\n\n"
        "Commando's:\n"
        "/update — stand van zaken\n"
        "/saldo — check saldo\n"
        "/log — trades\n"
        "/stop — pauze\n"
        "/start — hervatten"
    )

    while True:
        try:
            msg = check_messages()

            if msg:

                if msg == "/stop":
                    trading_active = False
                    send("⏸ Bot gepauzeerd")

                elif msg == "/start":
                    trading_active = True
                    send("▶️ Bot actief")

                elif msg == "/saldo":
                    balances = bitvavo_request("GET", "/v2/balance")
                    text = "\n".join(
                        [f"{b['symbol']}: {b['available']}" for b in balances if float(b['available']) > 0]
                    )
                    send(text)

                elif msg == "/log":
                    send("\n".join(trade_log[-5:]) or "Geen trades")

                elif msg == "/update":

                    sol_price = get_price()
                    sol_prices = get_history('solana', 30)
                    btc_prices = get_history('bitcoin', 30)
                    eth_prices = get_history('ethereum', 30)

                    sol_trend = bepaal_trend(sol_prices)
                    btc_trend = bepaal_trend(btc_prices)
                    eth_trend = bepaal_trend(eth_prices)

                    advies, uitleg, support, resistance = bepaal_signaal(
                        sol_prices, sol_trend, btc_trend
                    )

                    signaal = "🟢 BUY" if advies == "BUY" else "🔴 SELL"

                    afstand_support = (sol_price - support) / support * 100
                    afstand_resistance = (resistance - sol_price) / sol_price * 100

                    now = datetime.now().strftime("%d-%m-%Y %H:%M")
                    status = "▶️ actief" if trading_active else "⏸ gepauzeerd"

                    bericht = (
    f"*Solana {now}:*\n\n"
    f"*Koers:* €{sol_price:.2f}\n"
    f"*Signaal:* {signaal} — {uitleg}\n\n"
    f"*SOL trend:* {sol_trend}\n"
    f"*BTC trend:* {btc_trend}\n"
    f"*ETH trend:* {eth_trend}\n\n"
    f"*Support:* €{support:.2f} ({afstand_support:+.2f}%)\n"
    f"*Resistance:* €{resistance:.2f} ({afstand_resistance:+.2f}%)\n\n"
    f"*Bot is {status}*"
)


                    send(bericht)

                elif msg == "/buy":
                    buy_all()

                elif msg == "/sell":
                    sell_all()

        except Exception as e:
            print("Fout:", e)

        time.sleep(10)

# =============================
# ▶️ START
# =============================
if __name__ == "__main__":
    main()
