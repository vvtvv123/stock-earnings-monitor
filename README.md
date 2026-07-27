# Stock Earnings Monitor

Poll the NASDAQ earnings calendar for reports as they land, score each one's
EPS and revenue against that same company's own last reported quarter, and
send a Telegram alert when growth clears the configured threshold.

## What this repo does

This is a **data-centric** repo maintained by Hermes Agent.
The agent executes Python scripts directly. No pipeline framework or servers
are required.

## How scoring works

There are no analyst estimates or watchlists involved. When a ticker's actual
EPS shows up on the NASDAQ calendar:

1. Look up that ticker's most recently recorded actual (from
   `data/earnings_history.jsonl`) as the growth baseline.
2. Pull the matching quarterly revenue figure from SEC EDGAR (NASDAQ's free
   calendar endpoint doesn't provide revenue at all).
3. Compute EPS growth % and revenue growth % vs. that prior period.
4. If both clear the thresholds in `config.yaml`, append an alert and send it
   to Telegram immediately.
5. Always record the new actual to `data/earnings_history.jsonl`, whether or
   not it triggered an alert, so it becomes the baseline for next time.

A ticker's very first recorded report has no baseline, so it's stored but
never triggers an alert.

### Known limitation: revenue can lag or be missing

EDGAR filings sometimes post a day or two after the earnings release, and
foreign private issuers (e.g. SAP, TotalEnergies) file 20-F, not 10-Q/10-K,
so they never get a revenue figure from this source. When revenue is
unavailable, the revenue-growth check simply can't pass, so no alert fires
for that report even if EPS looked strong. Check `logs/run.jsonl` for
`monitor_revenue_unavailable` events.

## Setup

```bash
cd ~/stock-earnings-monitor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
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

## Agent rules

- Edits go in `src/*.py` only.
- Data files live in `data/` and `logs/`.
- Do not hardcode credentials; use `config.yaml` or environment variables.
- Commit and push data updates with meaningful messages.
