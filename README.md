# Stock Earnings Monitor

Poll the Finnhub earnings calendar for reports as they land, score each one's
EPS and revenue against the same company's actual results for the same
fiscal quarter one year ago (pulled fresh from SEC EDGAR, not locally
accumulated), and send a Telegram alert when growth clears the configured
threshold.

## What this repo does

This is a **data-centric** repo maintained by Hermes Agent.
The agent executes Python scripts directly. No pipeline framework or servers
are required.

## How scoring works

There are no analyst estimates or watchlists involved. When a ticker's actual
EPS shows up on the Finnhub earnings calendar:

1. Read the actual EPS and revenue directly from the Finnhub row.
2. Look up the ticker's SEC CIK (cached `data/cik_tickers.json`, refreshed
   from SEC's official ticker map on first use) and fetch its EDGAR
   `companyfacts`, picking out the single-quarter EPS and revenue figures
   whose period end is closest to 365 days before today (within a 45-day
   tolerance for fiscal-calendar drift) — i.e. the *same quarter, one year
   ago*, fetched live rather than read from anything we've stored ourselves.
3. Sanity-check the match (`scorer.is_plausible_pair`): reject it if it
   implies more than a 20x swing in either direction — that's almost always
   a mis-scaled/mis-tagged EDGAR fact, not real growth (see below).
4. Compute EPS growth % and revenue growth % vs. that year-ago quarter. A
   negative or zero prior is never trusted for a percentage (see below) —
   only `prior > 0` can produce `*_growth_ok`.
5. If both clear the thresholds in `config.yaml`, append an alert and send it
   to Telegram immediately. Separately, a company crossing from a prior-year
   loss to a current profit fires a distinct **turned-profitable** alert,
   since that's a real, notable event that isn't expressible as a percentage.
6. Record the new actual to `data/earnings_history.jsonl` either way, purely
   as a dedup/audit log so the same report isn't re-scored on a later run —
   it is **not** used as the growth baseline.

This replaced an earlier design that scored against whatever this system had
itself previously recorded locally. That meant a ticker needed to report
twice *after* the pipeline started running before it could ever be scored —
a full quarter's wait with almost nothing scoreable in the meantime. Sourcing
the year-ago quarter fresh from EDGAR means (almost) every report is
scoreable from day one.

Finnhub was chosen for the current-quarter actuals over NASDAQ's free
calendar endpoint because NASDAQ (a) has no revenue field at all and (b) lags
noticeably in posting actual EPS — verified live: on one test date NASDAQ
still showed 0/71 scheduled reports as posted, while Finnhub already had
actuals for 52 of them. Finnhub's own historical endpoints turned out to be
free-tier-limited in a different way (see below), which is why the year-ago
baseline comes from EDGAR instead.

### Known limitations (all found live, not hypothetical)

- **No year-ago baseline for some tickers.** EDGAR's `companyfacts` only has
  data for US GAAP filers; some foreign private issuers file IFRS instead
  (no `us-gaap` facts at all), and some filers only submit annual figures
  with no quarterly breakdown to compare against. In both cases, no alert
  fires for that report even if EPS looked strong — this is a deliberate
  "don't fabricate a mismatched comparison" choice, not a bug.
- **Revenue can be structurally missing for a whole sector.** EDGAR's
  ASC 606 contract-revenue tag doesn't apply to interest income, so banks
  and other financials (e.g. JPM) typically have no revenue figure at all —
  they can pass the EPS check but can never pass the revenue check.
- **EDGAR occasionally returns a mis-scaled figure.** Confirmed live on
  `PLXS`: its EDGAR revenue tag for the year-ago quarter came back
  **$1,018,308** against a true figure near **$1.3B** (~1280x off) — some
  filers tag a sub-segment or line item under the same concept the plain
  `companyfacts` API can't distinguish from consolidated revenue. The 20x
  plausibility guard rejects matches like this, logged as
  `monitor_abnormal_growth_detected`, and 118 more were caught this way in a
  20-day backtest. The guard doesn't catch a *genuine* but misleading
  low-base swing (e.g. EPS $0.02 -> $0.34 is a real +1600%, not a data bug,
  but also not durable 17x earnings growth) — that one needs a human glance.
- **A percentage from a negative baseline lies.** Confirmed live on `LFWD`:
  EPS went from -$0.58 to -$1.46 (the loss got *worse*), and the naive
  `(actual/prior - 1) * 100` formula gives **+151.7%** since
  negative-over-negative is positive. `score_growth` now requires
  `prior > 0` before trusting any percentage; a loss that widens, or that
  narrows without crossing into profit, never counts as growth. A prior-loss
  crossing into a current profit fires the separate turned-profitable alert
  instead.
- Check `logs/run.jsonl` for `monitor_no_cik_mapping`,
  `monitor_year_ago_quarter_not_found`, `monitor_abnormal_growth_detected`,
  and `monitor_revenue_unavailable` events to see which tickers this
  affected on a given run.

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

Two distinct alert types, both from `src/scorer.py`:

**Growth alert** — defaults in `config.yaml`:
- EPS actual grows >= 10% vs. the same quarter one year ago (year-ago EPS must be positive)
- Revenue actual grows >= 10% vs. the same quarter one year ago (year-ago revenue must be positive)

Both must pass. Tune via `config.yaml` (`scorer.min_eps_growth_pct`,
`scorer.min_revenue_growth_pct`).

**Turned-profitable alert** — fires instead of the growth alert when EPS
crosses from a year-ago loss (or exactly breakeven) to a current profit.
Not percentage-based, and not gated by the revenue check.

## Telegram delivery

`monitor.py` sends alerts directly as they're found. `src/alert_telegram.py`
is a standalone replay tool for resending everything currently in
`data/alerts.jsonl` (useful after an outage or for manual testing):

```bash
python3 src/alert_telegram.py --chat-id 8782198462
python3 src/alert_telegram.py send --chat-id 8782198462 --alerts data/alerts.jsonl
```

The same module can also send a **free-form message** to any Telegram chat,
so other scripts (or yourself) can publish a note without going through the
earnings pipeline:

```bash
python3 src/alert_telegram.py message --chat-id 8782198462 --message "System healthy"
python3 src/alert_telegram.py --message "System healthy"               # top-level shorthand
```

`--chat-id` defaults to the `TELEGRAM_CHAT_ID` env var, then to the module's
`DEFAULT_CHAT_ID`.

## Logs

Operational logs are written to `logs/run.jsonl`.

```bash
tail -n 50 logs/run.jsonl | python3 -m json.tool --no-ensure-ascii
```

Key events:
- `monitor_run_done`
- `monitor_no_baseline` — no EDGAR year-ago quarter was found for this report
- `monitor_no_cik_mapping` — ticker isn't in SEC's ticker->CIK map
- `monitor_year_ago_quarter_not_found` — has a CIK, but no matching quarter within tolerance
- `monitor_abnormal_growth_detected` — EDGAR match rejected by the 20x plausibility guard
- `monitor_revenue_unavailable` — Finnhub didn't have revenue for this report
- `alert_send_done` / `alert_send_failed`
- `message_send_done` / `message_send_failed`

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
