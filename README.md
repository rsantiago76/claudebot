"""
ClaudeBot Webhook Receiver
Receives TradingView signals → Claude AI confirms → Places trades on Alpaca/Coinbase
"""

from flask import Flask, request, jsonify
import anthropic
import alpaca_trade_api as tradeapi
import requests
import json
import os
import logging
from datetime import datetime

# ─────────────────────────────────────────
# SETUP
# ─────────────────────────────────────────
app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ─────────────────────────────────────────
# YOUR KEYS — fill these in from your .env file
# ─────────────────────────────────────────
ANTHROPIC_API_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
ALPACA_API_KEY      = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY   = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL     = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
COINBASE_API_KEY    = os.environ.get("COINBASE_API_KEY", "")
COINBASE_API_SECRET = os.environ.get("COINBASE_API_SECRET", "")
WEBHOOK_SECRET      = os.environ.get("WEBHOOK_SECRET", "claudebot123")

# ─────────────────────────────────────────
# CLIENTS
# ─────────────────────────────────────────
claude  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
alpaca  = tradeapi.REST(ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL)

# ─────────────────────────────────────────
# CRYPTO SYMBOLS — these go to Coinbase
# ─────────────────────────────────────────
CRYPTO_SYMBOLS = ["BTCUSD", "ETHUSD", "SOLUSD", "BTC", "ETH", "SOL"]

# ─────────────────────────────────────────
# POSITION SIZING — max % of account per trade
# ─────────────────────────────────────────
MAX_TRADE_PCT = 0.10   # 10% of account per trade (max $5 on $50)

# ─────────────────────────────────────────
# CLAUDE AI CONFIRMATION
# ─────────────────────────────────────────
def claude_confirms_trade(action, symbol, price):
    """Ask Claude AI if this trade makes sense before executing"""
    try:
        prompt = f"""You are a risk management AI for a small $50 trading account.
A trading bot wants to place this order:
- Action: {action.upper()}
- Symbol: {symbol}
- Price: ${price}
- Account size: $50
- Max risk per trade: $5 (10%)

Should this trade be executed? Reply with only:
CONFIRM - if the trade is reasonable
REJECT - if the trade seems risky or unusual

Then one sentence explaining why."""

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
        return False, f"Claude error: {e}"

# ─────────────────────────────────────────
# ALPACA STOCK TRADING
# ─────────────────────────────────────────
def place_alpaca_trade(action, symbol, price):
    """Place a stock trade on Alpaca"""
    try:
        account = alpaca.get_account()
        buying_power = float(account.buying_power)
        trade_amount = min(buying_power * MAX_TRADE_PCT, 5.0)

        if action == "buy":
            qty = trade_amount / float(price)
            qty = round(qty, 4)
            if qty <= 0:
                return False, "Insufficient buying power"

            order = alpaca.submit_order(
                symbol=symbol.replace("USD", ""),
                qty=qty,
                side="buy",
                type="market",
                time_in_force="gtc"
            )
            return True, f"Alpaca BUY order placed: {qty} {symbol} @ ~${price} (Order ID: {order.id})"

        elif action == "sell":
            try:
                position = alpaca.get_position(symbol.replace("USD", ""))
                order = alpaca.submit_order(
                    symbol=symbol.replace("USD", ""),
                    qty=position.qty,
                    side="sell",
                    type="market",
                    time_in_force="gtc"
                )
                return True, f"Alpaca SELL order placed: {position.qty} {symbol} (Order ID: {order.id})"
            except Exception:
                return False, f"No position to sell for {symbol}"

    except Exception as e:
        return False, f"Alpaca error: {e}"

# ─────────────────────────────────────────
# COINBASE CRYPTO TRADING
# ─────────────────────────────────────────
def place_coinbase_trade(action, symbol, price):
    """Place a crypto trade on Coinbase Advanced Trade API"""
    try:
        import hmac, hashlib, time
        from uuid import uuid4

        base_url = "https://api.coinbase.com"
        timestamp = str(int(time.time()))

        # Format product ID for Coinbase (BTCUSD → BTC-USD)
        if len(symbol) == 6 and symbol.endswith("USD"):
            product_id = symbol[:3] + "-USD"
        else:
            product_id = symbol + "-USD"

        # Calculate trade size — $5 max
        trade_usd = 5.0

        if action == "buy":
            body = json.dumps({
                "client_order_id": str(uuid4()),
                "product_id": product_id,
                "side": "BUY",
                "order_configuration": {
                    "market_market_ioc": {
                        "quote_size": str(round(trade_usd, 2))
                    }
                }
            })
        elif action == "sell":
            # For sell we need the base size — estimate from price
            base_size = round(trade_usd / float(price), 8)
            body = json.dumps({
                "client_order_id": str(uuid4()),
                "product_id": product_id,
                "side": "SELL",
                "order_configuration": {
                    "market_market_ioc": {
                        "base_size": str(base_size)
                    }
                }
            })
        else:
            return False, "Unknown action"

        path = "/api/v3/brokerage/orders"
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

        response = requests.post(base_url + path, headers=headers, data=body)
        result = response.json()

        if response.status_code == 200 and result.get("success"):
            return True, f"Coinbase {action.upper()} order placed for {product_id}"
        else:
            return False, f"Coinbase error: {result.get('error_response', result)}"

    except Exception as e:
        return False, f"Coinbase exception: {e}"

# ─────────────────────────────────────────
# MAIN WEBHOOK ENDPOINT
# ─────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        log.info(f"Received signal: {data}")

        # Validate webhook secret
        if data.get("secret") != WEBHOOK_SECRET:
            log.warning("Invalid webhook secret")
            return jsonify({"status": "error", "message": "Invalid secret"}), 401

        action = data.get("action", "").lower()
        symbol = data.get("symbol", "").upper()
        price  = data.get("price", 0)

        if not action or not symbol:
            return jsonify({"status": "error", "message": "Missing action or symbol"}), 400

        log.info(f"Signal: {action.upper()} {symbol} @ ${price}")

        # Step 1 — Claude AI confirmation
        confirmed, claude_reason = claude_confirms_trade(action, symbol, price)
        if not confirmed:
            log.info(f"Claude REJECTED trade: {claude_reason}")
            return jsonify({
                "status": "rejected",
                "reason": claude_reason,
                "symbol": symbol,
                "action": action
            })

        # Step 2 — Route to correct exchange
        is_crypto = any(symbol.startswith(c) for c in ["BTC", "ETH", "SOL", "DOGE", "XRP"])

        if is_crypto:
            success, message = place_coinbase_trade(action, symbol, price)
            exchange = "Coinbase"
        else:
            success, message = place_alpaca_trade(action, symbol, price)
            exchange = "Alpaca"

        # Step 3 — Log and respond
        result = {
            "status": "executed" if success else "failed",
            "exchange": exchange,
            "action": action,
            "symbol": symbol,
            "price": price,
            "message": message,
            "claude_confirmation": claude_reason,
            "timestamp": datetime.utcnow().isoformat()
        }

        log.info(f"Result: {result}")
        return jsonify(result)

    except Exception as e:
        log.error(f"Webhook error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# ─────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "status": "ClaudeBot is running",
        "time": datetime.utcnow().isoformat(),
        "exchanges": ["Alpaca (stocks)", "Coinbase (crypto)"]
    })

@app.route("/positions", methods=["GET"])
def positions():
    """Check current open positions"""
    try:
        alpaca_positions = alpaca.list_positions()
        return jsonify({
            "alpaca": [{"symbol": p.symbol, "qty": p.qty, "market_value": p.market_value, "unrealized_pl": p.unrealized_pl} for p in alpaca_positions]
        })
    except Exception as e:
        return jsonify({"error": str(e)})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
