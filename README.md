# Stock Earnings Monitor

Monitor US stock earnings by exact report time, fetch live reported data, and score alerts for Telegram delivery.

## What this repo does

This is a **data-centric** repo maintained by Hermes Agent.
The agent executes Python scripts directly. No pipeline framework or servers are required.

## Workflow

For a given date `X` and time `Y`:

1. Fetch the NASDAQ earnings calendar for `X`.
2. Find tickers reporting at exact time `Y` within `window_minutes`.
3. Fetch live earnings data for those tickers.
4. Score them against alert criteria.
5. Emit alerts for downstream delivery.

## Setup

```bash
cd ~/stock-earnings-monitor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
# Inspect NASDAQ calendar for a date
python3 src/fetcher.py --calendar --date 2026-07-27

# Find tickers at exact time and score them
python3 src/fetcher.py --at --date 2026-07-27 --time 16:00 --window 30

# Get earliest upcoming report from calendar
python3 src/watcher.py --once --lookahead-days 7

# Fetch live data for specific tickers
python3 src/fetcher.py --tickers AAPL,TSLA
```

## Fetcher commands

- `--calendar --date YYYY-MM-DD`: print NASDAQ calendar for a date
- `--at --date YYYY-MM-DD --time HH:MM [--window N]`: find tickers at exact time and score them
- `--schedule`: suggest next exact report-arrival run target
- `--tickers TICKER1,TICKER2`: fetch live data for specific tickers without watchlists

## Watcher commands

- `--once --lookahead-days N`: scan NASDAQ calendar for next N days and print earliest upcoming ticker
- `--earliest`: print only the earliest upcoming ticker

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

## Requirements

`requirements.txt`:
- `requests`
- `beautifulsoup4`
- `lxml`
- `python-dotenv`

## Agent rules

- Edits go in `src/*.py` only.
- Data files live in `data/` and `logs/`.
- Do not hardcode credentials; use `config.yaml` or environment variables.
- Prefer exact-time operations in the every-minute cron loop.
- Commit and push data updates with meaningful messages.
