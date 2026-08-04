#!/usr/bin/env python3
"""
fetch_kalshi.py — pull Kalshi World Cup price history (no auth needed for reads)
and write CSVs in the analysis pipeline's `ticks` schema.

Two price sources, on purpose:
  * candlesticks (1-min OHLC of yes_bid/yes_ask) -> midpoint prob, good for the
    minute-bucket / Study B table.            => data/kalshi/<ticker>_candles.csv
  * trades (individual fills, true timestamps) -> precise event-window timing for
    Study A.                                   => data/kalshi/<ticker>_trades.csv

Output schema (both files): ts, ticker, yes_bid, yes_ask, volume, prob, spread
(trades have no bid/ask, so yes_bid/yes_ask are NaN and prob = trade price.)

IMPORTANT — I could NOT reach Kalshi from my sandbox, so this is written to the
documented v2 API and marked where to confirm. On your first run, if a request
fails, print `r.text` and check field names against docs.kalshi.com. The three
things most likely to need a tweak are flagged with CONFIRM.

Usage:
  # 1) find the World Cup series tickers (browse them on kalshi.com too):
  python fetch_kalshi.py --discover soccer
  python fetch_kalshi.py --discover "world cup"

  # 2) pull everything under a series between two dates:
  python fetch_kalshi.py --series KXWORLDCUP \
         --start 2026-06-11 --end 2026-07-20 --interval 1

  # or pull specific market tickers directly:
  python fetch_kalshi.py --tickers KXWC-ARGWIN KXWC-BRAWIN --start ... --end ...
"""
import argparse
import time
import sys
from datetime import datetime, timezone

import requests
import pandas as pd

from config import (KALSHI_BASE, KALSHI_REQUEST_DELAY_S, KALSHI_MAX_RETRIES,
                    KALSHI_CANDLES_CAP, KALSHI_DIR)

SESSION = requests.Session()
SESSION.headers.update({"Accept": "application/json",
                        "User-Agent": "wc-market-study/0.1 (research)"})


def _get(path: str, params: dict | None = None) -> dict:
    """GET with polite throttle + backoff (Kalshi sends no Retry-After)."""
    url = path if path.startswith("http") else f"{KALSHI_BASE}{path}"
    delay = KALSHI_REQUEST_DELAY_S
    for attempt in range(KALSHI_MAX_RETRIES):
        time.sleep(KALSHI_REQUEST_DELAY_S)
        r = SESSION.get(url, params=params, timeout=30)
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503):
            time.sleep(delay); delay *= 2  # exponential backoff
            continue
        raise RuntimeError(f"GET {r.url} -> {r.status_code}: {r.text[:300]}")
    raise RuntimeError(f"GET {url} failed after {KALSHI_MAX_RETRIES} retries")


def to_epoch(date_str: str) -> int:
    return int(datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc).timestamp())


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def discover(keyword: str):
    """List series whose title/ticker matches a keyword, so you can grab the
    right series_ticker. CONFIRM: category discovery may instead need
    /search/tags_by_categories then /series?category=... — see docs if this is thin."""
    print(f"[discover] searching series for {keyword!r} ...")
    data = _get("/series", params={"limit": 1000})
    series = data.get("series", data.get("data", []))
    kw = keyword.lower()
    hits = [s for s in series
            if kw in str(s.get("title", "")).lower()
            or kw in str(s.get("ticker", "")).lower()
            or kw in str(s.get("category", "")).lower()]
    if not hits:
        print("  no matches. Browse markets on kalshi.com and pass --series/--tickers "
              "directly; also try a broader keyword.")
    for s in hits[:60]:
        print(f"  {s.get('ticker'):<24} {s.get('title','')[:70]}")
    return hits


def markets_in_series(series_ticker: str) -> list[str]:
    """Enumerate market tickers under a series. Tries live then historical."""
    tickers, cursor = [], None
    for base in ("/markets", "/historical/markets"):  # settled WC likely historical
        try:
            while True:
                params = {"series_ticker": series_ticker, "limit": 1000}
                if cursor:
                    params["cursor"] = cursor
                data = _get(base, params=params)
                mk = data.get("markets", [])
                tickers += [m["ticker"] for m in mk if "ticker" in m]
                cursor = data.get("cursor")
                if not cursor or not mk:
                    break
            if tickers:
                print(f"[markets] {len(tickers)} markets under {series_ticker} via {base}")
                return sorted(set(tickers))
        except RuntimeError as e:
            print(f"  ({base} not usable: {str(e)[:80]})")
            cursor = None
    return sorted(set(tickers))


# ---------------------------------------------------------------------------
# Candlesticks -> tick schema
# ---------------------------------------------------------------------------
def _price(block, key):
    """Candlestick prices come as decimal-dollar strings ('0.5600'). Return cents
    int, or NaN. CONFIRM the nesting (yes_bid.close) against a live response."""
    try:
        return round(float(block[key]["close"]) * 100)
    except (KeyError, TypeError, ValueError):
        return float("nan")


def fetch_candlesticks(ticker, start_ts, end_ts, interval_min):
    rows = []
    window = KALSHI_CANDLES_CAP * interval_min * 60 - 60  # stay under the 10k cap
    t0 = start_ts
    while t0 < end_ts:
        t1 = min(t0 + window, end_ts)
        # CONFIRM path: docs show /historical/markets/{ticker}/candlesticks
        data = _get(f"/historical/markets/{ticker}/candlesticks",
                    params={"start_ts": t0, "end_ts": t1, "period_interval": interval_min})
        for c in data.get("candlesticks", []):
            yb, ya = _price(c, "yes_bid"), _price(c, "yes_ask")
            mid = (yb + ya) / 2 if pd.notna(yb) and pd.notna(ya) else float("nan")
            rows.append(dict(
                ts=pd.to_datetime(c["end_period_ts"], unit="s", utc=True),
                ticker=ticker, yes_bid=yb, yes_ask=ya,
                volume=float(c.get("volume", "nan") or "nan"),
                prob=mid / 100 if pd.notna(mid) else float("nan"),
                spread=(ya - yb) if pd.notna(yb) and pd.notna(ya) else float("nan"),
            ))
        t0 = t1
    return pd.DataFrame(rows)


def fetch_trades(ticker, start_ts, end_ts):
    rows, cursor = [], None
    while True:
        params = {"ticker": ticker, "limit": 1000,
                  "min_ts": start_ts, "max_ts": end_ts}  # CONFIRM param names
        if cursor:
            params["cursor"] = cursor
        data = _get("/historical/trades", params=params)  # CONFIRM historical vs /markets/trades
        trades = data.get("trades", [])
        for t in trades:
            yp = t.get("yes_price")  # cents; CONFIRM field name
            rows.append(dict(
                ts=pd.to_datetime(t.get("created_time"), utc=True),
                ticker=ticker, yes_bid=float("nan"), yes_ask=float("nan"),
                volume=float(t.get("count", "nan") or "nan"),
                prob=(yp / 100) if yp is not None else float("nan"),
                spread=float("nan"),
            ))
        cursor = data.get("cursor")
        if not cursor or not trades:
            break
    return pd.DataFrame(rows)


def save(df, path, force):
    if path.exists() and not force:
        print(f"  cached, skip {path.name} (use --force to refetch)")
        return
    df.sort_values("ts").to_csv(path, index=False)
    print(f"  saved {path.name} ({len(df)} rows)")


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--discover", metavar="KEYWORD",
                    help="list series matching a keyword, then exit")
    ap.add_argument("--series", help="pull all markets under this series ticker")
    ap.add_argument("--tickers", nargs="+", help="pull these specific market tickers")
    ap.add_argument("--start", help="YYYY-MM-DD (UTC)")
    ap.add_argument("--end", help="YYYY-MM-DD (UTC)")
    ap.add_argument("--interval", type=int, default=1, help="candle minutes (default 1)")
    ap.add_argument("--no-trades", action="store_true", help="candlesticks only")
    ap.add_argument("--force", action="store_true", help="refetch even if cached")
    args = ap.parse_args()

    if args.discover:
        discover(args.discover); sys.exit(0)

    if not (args.series or args.tickers):
        ap.error("give --discover, or --series, or --tickers")
    if not (args.start and args.end):
        ap.error("--start and --end are required when fetching")

    start_ts, end_ts = to_epoch(args.start), to_epoch(args.end)
    tickers = list(args.tickers or [])
    if args.series:
        tickers += markets_in_series(args.series)
    tickers = sorted(set(tickers))
    if not tickers:
        print("No tickers resolved. Browse kalshi.com to find the series/market "
              "tickers and pass them with --tickers."); sys.exit(1)
    print(f"Fetching {len(tickers)} market(s) from {args.start} to {args.end}\n")

    for i, tk in enumerate(tickers, 1):
        print(f"[{i}/{len(tickers)}] {tk}")
        try:
            save(fetch_candlesticks(tk, start_ts, end_ts, args.interval),
                 KALSHI_DIR / f"{tk}_candles.csv", args.force)
            if not args.no_trades:
                save(fetch_trades(tk, start_ts, end_ts),
                     KALSHI_DIR / f"{tk}_trades.csv", args.force)
        except RuntimeError as e:
            print(f"  [ERROR] {e}")

    print("\nDone. CSVs in", KALSHI_DIR)
