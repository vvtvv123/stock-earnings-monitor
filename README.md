# Stock Earnings Monitor

Poll the Finnhub earnings calendar for reports as they land, score each one's
EPS and revenue against that same company's own last reported quarter, and
send a Telegram alert when growth clears the configured threshold.

## What this repo does

This is a **data-centric** repo maintained by Hermes Agent.
The agent executes Python scripts directly. No pipeline framework or servers
are required.

## How scoring works

There are no analyst estimates or watchlists involved. When a ticker's actual
EPS shows up on the Finnhub earnings calendar:

1. Look up that ticker's most recently recorded actual (from
   `data/earnings_history.jsonl`) as the growth baseline.
2. Read the actual EPS and revenue directly from the same Finnhub row.
3. Compute EPS growth % and revenue growth % vs. that prior period.
4. If both clear the thresholds in `config.yaml`, append an alert and send it
   to Telegram immediately.
5. Always record the new actual to `data/earnings_history.jsonl`, whether or
   not it triggered an alert, so it becomes the baseline for next time.

A ticker's very first recorded report has no baseline, so it's stored but
never triggers an alert.

Finnhub was chosen over NASDAQ's free calendar endpoint because NASDAQ (a)
has no revenue field at all and (b) lags noticeably in posting actual EPS —
verified live: on one test date NASDAQ still showed 0/71 scheduled reports
as posted, while Finnhub already had actuals for 52 of them.

### Known limitation: revenue can still be missing

Finnhub doesn't have revenue for every filer (e.g. some foreign private
issuers). When revenue is unavailable, the revenue-growth check simply can't
pass, so no alert fires for that report even if EPS looked strong. Check
`logs/run.jsonl` for `monitor_revenue_unavailable` events.

## Setup

```bash
cd ~/stock-earnings-monitor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export FINNHUB_API_KEY=your_key_here   # free tier at finnhub.io -- never commit this
```

## Usage

```bash
# Run one check now (this is what cron calls every minute)
python3 src/monitor.py

# Re-check a specific past date (backfill / debugging)
python3 src/monitor.py --date 2026-07-23
```

Cron:

```
* * * * * cd ~/stock-earnings-monitor && python3 src/monitor.py >> logs/cron.log 2>&1
```

## Visibility helper (not part of alerting)

```bash
# list upcoming reports over the next N days, purely for a human to glance at
# (still reads NASDAQ's calendar -- fine for a schedule preview, just not for actuals)
python3 src/watcher.py --once --lookahead-days 7
```

## Alert criteria

Defaults in `config.yaml`:
- EPS actual grows >= 5% vs. this ticker's last recorded actual
- Revenue actual grows >= 1% vs. this ticker's last recorded actual

Both must pass. Tune via `config.yaml` (`scorer.min_eps_growth_pct`,
`scorer.min_revenue_growth_pct`).

## Telegram delivery

`monitor.py` sends alerts directly as they're found. `src/alert_telegram.py`
is a standalone replay tool for resending everything currently in
`data/alerts.jsonl` (useful after an outage or for manual testing):

```bash
python3 src/alert_telegram.py --chat-id 8782198462
```

## Logs

Operational logs are written to `logs/run.jsonl`.

```bash
tail -n 50 logs/run.jsonl | python3 -m json.tool --no-ensure-ascii
```

Key events:
- `monitor_run_done`
- `monitor_first_record`
- `monitor_revenue_unavailable`
- `alert_send_done` / `alert_send_failed`

## Requirements

`requirements.txt`:
- `pyyaml`

Also requires the `FINNHUB_API_KEY` environment variable (free tier at
finnhub.io).

## Agent rules

- Edits go in `src/*.py` only.
- Data files live in `data/` and `logs/`.
- Do not hardcode credentials; use `config.yaml` or environment variables.
- Commit and push data updates with meaningful messages.
