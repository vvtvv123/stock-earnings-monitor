# Stock Earnings Monitor

Monitor US stock earnings for NASDAQ and S&P 500 tickers, store earnings history, and send WhatsApp alerts when positive surprises meet objective criteria.

## What this repo does

This is a **data-centric** repo maintained by Hermes Agent.
The agent keeps watchlists and data files up to date, and executes Python scripts directly.
No pipeline framework or servers are required.

## Data files

- `watchlists/nasdaq_tickers.json` — NASDAQ watchlist with `ticker` and `next_earnings_ts`
- `watchlists/sp500_tickers.json` — S&P 500 watchlist with `ticker` and `next_earnings_ts`
- `data/earnings_history.jsonl` — append-only earnings records
- `data/upcoming_earnings.jsonl` — upcoming earnings cache
- `data/alerts.jsonl` — emitted alerts for WhatsApp delivery

## Data sources

### Calendar / next earnings date
- Primary: NASDAQ public earnings calendar API
- Existing helper: `python3 src/fetcher.py --populate`

### Actual earnings numbers
- Primary: SEC EDGAR filings via `data.sec.gov/api/xbrl/companyfacts`
- Secondary: NASDAQ calendar actuals when available
- Existing helper: `python3 src/fetcher.py --ingest`

## Setup

```bash
cd ~/stock-earnings-monitor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run order

```bash
# 1. Inspect upcoming earnings from watchlists
python3 src/watcher.py --once

# 2. Refresh next_earnings_ts from NASDAQ calendar (requires environment with outbound HTTPS)
python3 src/fetcher.py --populate

# 3. Ingest latest available actuals from SEC EDGAR
python3 src/fetcher.py --ingest

# 4. Score latest earnings and write alerts
python3 src/scorer.py --emit-alerts

# 5. Send WhatsApp alerts
python3 src/alert_whatsapp.py
```

## Watchlist management

Watchlists are already populated.
If `next_earnings_ts` is missing, populate it with realistic sample dates for testing:

```bash
python3 src/fetcher.py --backfill
```

To refresh from source, update the relevant watcher script in your environment.

## Alert criteria

Default alerting criteria in `scorer.py`:
- EPS actual exceeds estimate by >= 5%
- Revenue actual exceeds estimate by >= 1%

Configure via `config.yaml` or extend `scorer.py` if needed.

## WhatsApp delivery

`src/alert_whatsapp.py` reads `data/alerts.jsonl` and sends concise callback-free messages.
Configure target chat/phone in `config.yaml` before running in production.

## Requirements

`requirements.txt`:
- `requests`
- `beautifulsoup4`
- `lxml`
- `python-dotenv`

## Agent rules

- Edits go in `src/*.py` only.
- Data files live in `watchlists/` and `data/`.
- Do not hardcode credentials; use `config.yaml` or environment variables.
- Prefer executable scripts over notebooks.
- Commit and push data updates with meaningful messages.
