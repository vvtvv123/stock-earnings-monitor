# Stock Earnings Monitor

Monitor US stock earnings for NASDAQ and S&P 500 tickers, store earnings history, and send Telegram alerts when positive surprises meet objective criteria.

## What this repo does

This is a **data-centric** repo maintained by Hermes Agent.
The agent keeps watchlists and data files up to date, and executes Python scripts directly.
No pipeline framework or servers are required.

## Data files

- `watchlists/nasdaq_tickers.json` — NASDAQ watchlist with `ticker` and `next_earnings_ts`
- `watchlists/sp500_tickers.json` — S&P 500 watchlist with `ticker` and `next_earnings_ts`
- `data/earnings_history.jsonl` — append-only earnings records
- `data/upcoming_earnings.jsonl` — upcoming earnings cache
- `data/alerts.jsonl` — emitted alerts for Telegram delivery
- `logs/run.jsonl` — append-only operational log

## Data sources

### Calendar / next earnings date
- Primary: NASDAQ public earnings calendar API
- Existing helper: `python3 src/fetcher.py --populate`

### Actual earnings numbers
- Primary: SEC EDGAR filings via `data.sec.gov/api/xbrl/companyfacts`
- Secondary: NASDAQ earnings calendar rows (`epsActual`, `revenueActual`, `epsEstimate`, `revenueEstimate`) when available
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
# 1. Populate exact report datetimes from NASDAQ calendar
python3 src/fetcher.py --populate

# 2. Build upcoming cache using exact report datetimes
python3 src/fetcher.py --upcoming

# 3. Inspect earliest upcoming report target
python3 src/watcher.py --earliest

# 4. When report time arrives, fetch reported earnings for known due tickers and compare to history
python3 src/fetcher.py --fetch-reports

# 5. Score latest earnings and write alerts
python3 src/scorer.py --emit-alerts

# 6. Send Telegram alerts
python3 src/alert_telegram.py
```

## Fetcher commands

```bash
# Populate watchlists with exact next_earnings_ts + earnings_datetime_utc
python3 src/fetcher.py --populate

# Refresh existing entries with latest calendar datetimes
python3 src/fetcher.py --refresh --force

# Build exact upcoming earnings cache and show next scheduled run
python3 src/fetcher.py --upcoming

# Cache next exact report-arrival run target
python3 src/fetcher.py --schedule

# Fetch live reported earnings due now and compare to history
python3 src/fetcher.py --fetch-reports --tickers TSLA
```

## Watcher modes

```bash
# show the earliest upcoming ticker by exact report datetime
python3 src/watcher.py --once

# show only the single earliest upcoming ticker and suggested run time
python3 src/watcher.py --earliest
```

## Alert criteria

Default alerting criteria in `scorer.py`:
- EPS actual exceeds estimate by >= 5%
- Revenue actual exceeds estimate by >= 1%

Configure via `config.yaml` or extend `scorer.py` if needed.

## Telegram delivery

`src/alert_telegram.py` reads `data/alerts.jsonl` and sends concise messages.
Configure target chat ID via `--chat-id` or `TELEGRAM_CHAT_ID` env var.

Example:
```bash
python3 src/alert_telegram.py --chat-id 8782198462
```

## Logs

Operational logs are written to `logs/run.jsonl`.

```bash
tail -n 50 logs/run.jsonl | python3 -m json.tool --no-ensure-ascii
```

Key events:
- `watcher_once_done`
- `fetcher_backfill`
- `fetcher_populate_done`
- `scorer_emit_alerts`
- `alert_send_done`

## Requirements

`requirements.txt`:
- `requests`
- `beautifulsoup4`
- `lxml`
- `python-dotenv`

## Agent rules

- Edits go in `src/*.py` only.
- Data files live in `watchlists/`, `data/`, and `logs/`.
- Do not hardcode credentials; use `config.yaml` or environment variables.
- Prefer single-ticker operations inside the every-minute cron loop.
- Commit and push data updates with meaningful messages.
