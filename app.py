from flask import Flask, request, jsonify
import anthropic
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
import requests
import json
import os
import logging
import hmac
import hashlib
import time
from uuid import uuid4
from datetime import datetime

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

ANTHROPIC_API_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
ALPACA_API_KEY      = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY   = os.environ.get("ALPACA_SECRET_KEY", "")
COINBASE_API_KEY    = os.environ.get("COINBASE_API_KEY", "")
COINBASE_API_SECRET = os.environ.get("COINBASE_API_SECRET", "")
WEBHOOK_SECRET      = os.environ.get("WEBHOOK_SECRET", "claudebot2026")

claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
alpaca = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=False)

MAX_TRADE_USD = 10.0

def claude_confirms_trade(action, symbol, price):
    try:
        prompt = f"""You are a risk management AI for a small $50 trading account.
A trading bot wants to place this order:
- Action: {action.upper()}
- Symbol: {symbol}
- Price: ${price}
- Max risk: $5

Reply with only CONFIRM or REJECT followed by one sentence explaining why."""

        response = claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}]
        )
        reply = response.content[0].text.strip()
        log.info(f"Claude says: {reply}")
        return reply.upper().startswith("CONFIRM"), reply
    except Exception as e:
        log.error(f"Claude error: {e}")
        return False, str(e)

def place_alpaca_trade(action, symbol, price):
    try:
        clean_symbol = symbol.replace("USD", "").replace("/", "")
        side = OrderSide.BUY if action == "buy" else OrderSide.SELL
        qty = round(MAX_TRADE_USD / float(price), 4)

        order_data = MarketOrderRequest(
            symbol=clean_symbol,
            qty=qty,
            side=side,
            time_in_force=TimeInForce.GTC
        )
        order = alpaca.submit_order(order_data)
        return True, f"Alpaca {action.upper()} {qty} {clean_symbol} placed. Order ID: {order.id}"
    except Exception as e:
        return False, f"Alpaca error: {e}"

def place_coinbase_trade(action, symbol, price):
    try:
        if len(symbol) == 6 and symbol.endswith("USD"):
            product_id = symbol[:3] + "-USD"
        else:
            product_id = symbol + "-USD"

        timestamp = str(int(time.time()))
        path = "/api/v3/brokerage/orders"

        if action == "buy":
            body = json.dumps({
                "client_order_id": str(uuid4()),
                "product_id": product_id,
                "side": "BUY",
                "order_configuration": {
                    "market_market_ioc": {"quote_size": str(round(MAX_TRADE_USD, 2))}
                }
            })
        else:
            base_size = round(MAX_TRADE_USD / float(price), 8)
            body = json.dumps({
                "client_order_id": str(uuid4()),
                "product_id": product_id,
                "side": "SELL",
                "order_configuration": {
                    "market_market_ioc": {"base_size": str(base_size)}
                }
            })

        message = timestamp + "POST" + path + body
        signature = hmac.new(
            COINBASE_API_SECRET.encode("utf-8"),
            message.encode("utf-8"),
            digestmod=hashlib.sha256
        ).hexdigest()

        headers = {
            "CB-ACCESS-KEY": COINBASE_API_KEY,
            "CB-ACCESS-SIGN": signature,
            "CB-ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json"
        }

        response = requests.post("https://api.coinbase.com" + path, headers=headers, data=body)
        result = response.json()

        if response.status_code == 200 and result.get("success"):
            return True, f"Coinbase {action.upper()} {product_id} placed"
        else:
            return False, f"Coinbase error: {result}"
    except Exception as e:
        return False, f"Coinbase exception: {e}"

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        log.info(f"Signal received: {data}")

        if data.get("secret") != WEBHOOK_SECRET:
            return jsonify({"status": "error", "message": "Invalid secret"}), 401

        action = data.get("action", "").lower()
        symbol = data.get("symbol", "").upper()
        price  = data.get("price", 0)

        if not action or not symbol:
            return jsonify({"status": "error", "message": "Missing action or symbol"}), 400

        confirmed = True
        reason = "Auto-approved"

        is_crypto = any(symbol.startswith(c) for c in ["BTC", "ETH", "SOL", "DOGE", "XRP"])

        if is_crypto:
            success, message = place_coinbase_trade(action, symbol, price)
            exchange = "Coinbase"
        else:
            success, message = place_alpaca_trade(action, symbol, price)
            exchange = "Alpaca"

        return jsonify({
            "status": "executed" if success else "failed",
            "exchange": exchange,
            "action": action,
            "symbol": symbol,
            "price": price,
            "message": message,
            "claude": reason,
            "timestamp": datetime.utcnow().isoformat()
        })

    except Exception as e:
        log.error(f"Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "status": "ClaudeBot is running",
        "time": datetime.utcnow().isoformat(),
        "exchanges": ["Alpaca (stocks)", "Coinbase (crypto)"]
    })

@app.route("/positions", methods=["GET"])
def positions():
    try:
        positions = alpaca.get_all_positions()
        return jsonify({
            "alpaca": [{"symbol": p.symbol, "qty": p.qty, "market_value": p.market_value} for p in positions]
        })
    except Exception as e:
        return jsonify({"error": str(e)})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
