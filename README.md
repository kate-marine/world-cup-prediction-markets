# World Cup 2026 × Kalshi — prediction-market study

Data fetchers for a study of how Kalshi prices reacted during the 2026 World Cup:
whether the market overweights scoreboard events (goals, cards, upsets) and
underweights underlying performance (xG, dominance).

Two studies share one pipeline:
- **Study A** — in-play overreaction: do goal/card price *jumps* reverse?
- **Study B** — results vs. performance: does *how* a team won move its title odds,
  beyond *whether* it won?

## What's here

| file | what it does | needs a browser? |
|------|--------------|------------------|
| `fetch_kalshi.py` | discovers WC market tickers, pulls candlesticks + trades | no |
| `fetch_xg.py`     | FBref goal/card timing + match-level xG (and optional per-shot xG) | only with `--shots` |
| `config.py`       | paths, Kalshi base URL, throttle, team crosswalk | — |

Outputs land in `data/` (gitignored) in the analysis pipeline's schema:
`ticks` (ts, ticker, yes_bid, yes_ask, volume, prob, spread), `events`
(match_id, half, minute, stoppage, event_type, team), `shots` (half, minute, team, xg).

## Setup

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
# 1) find the World Cup series ticker(s) on Kalshi (also browsable on kalshi.com)
python fetch_kalshi.py --discover "world cup"

# 2) pull prices for that series across the tournament
python fetch_kalshi.py --series <SERIES_TICKER> --start 2026-06-11 --end 2026-07-20 --interval 1

# 3) pull performance data (fast path — no browser)
python fetch_xg.py

# 4) OPTIONAL: per-shot xG for the Study A surprise proxy (slow, launches a browser)
pip install selenium            # and have Chrome/Chromium installed
python fetch_xg.py --shots
```

## What needs what (so you can stage the work)

- **Study A core (jump/reversal):** Kalshi trades + `events.csv`. **No shot xG needed** —
  you can produce the main overreaction result without WhoScored.
- **Study B:** Kalshi candlesticks + `team_match_xg.csv`.
- **Study A surprise proxy + quiet-minute buckets:** adds `shots.csv` (the `--shots` path).

## Heads-up (things I couldn't verify from a sandbox)

These scripts were written against the documented APIs but **not run against live
Kalshi/FBref**. A few spots are marked `CONFIRM` in the code — most likely to need a
small tweak on first run:

- **Kalshi base host** (`config.py`): Kalshi has changed hosts before; verify at
  docs.kalshi.com if requests fail.
- **Kalshi field names**: `yes_bid.close` nesting in candlesticks; `created_time` /
  `yes_price` in trades; whether trades live at `/historical/trades` or `/markets/trades`.
  The code prints the raw response on error so you can check.
- **FBref column names**: `fetch_xg.py` saves a `*_raw.csv` next to each normalized
  file and warns if it can't auto-map a column — open the raw file, read the real
  names, adjust the small mapping in `fetch_xg.py`.
- **Team names**: fill in `TEAM_TO_ISO3` in `config.py` as merges surface mismatches.
  Unmapped teams appear with a leading `?` so they never vanish silently.

Reading Kalshi market data needs **no** API key. Only actual trading does.
