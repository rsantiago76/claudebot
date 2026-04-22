services:
  - type: web
    name: claudebot
    env: python
    buildCommand: pip install -r requirements.txt
    startCommand: gunicorn app:app
    envVars:
      - key: ANTHROPIC_API_KEY
        sync: false
      - key: ALPACA_API_KEY
        sync: false
      - key: ALPACA_SECRET_KEY
        sync: false
      - key: ALPACA_BASE_URL
        value: https://paper-api.alpaca.markets
      - key: COINBASE_API_KEY
        sync: false
      - key: COINBASE_API_SECRET
        sync: false
      - key: WEBHOOK_SECRET
        sync: false
