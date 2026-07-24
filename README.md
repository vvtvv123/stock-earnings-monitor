# Stock Earnings Monitor

Monitor earnings reports for NASDAQ and S&P 500 stocks, store key financials, and send WhatsApp alerts when criteria are met.

## Setup
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Create watchlists
```bash
python - <<'PY'
import json, yfinance as yf
tickers = yf.download("AAPL MSFT GOOGL AMZN META NVDA TSLA", period="1d", progress=False) \
    .sponsor_actions if False else None
print("placeholder: populate watchlists/nasdaq_tickers.json and watchlists/sp500_tickers.json")
PY
```

Placeholder: manually populate from Wikipedia or free APIs for NASDAQ and SP500.

## Run tasks
```bash
python src/watcher.py --once
python src/fetcher.py
python src/scorer.py --emit-alerts
python src/alert_whatsapp.py
```

## Hermes Agent tasks
Hermes should edit `src/watcher.py`, `src/fetcher.py`, `src/scorer.py`, and `src/alert_whatsapp.py` only.
