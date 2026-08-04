#!/usr/bin/env python3
"""
fetch_xg.py — pull World Cup performance data via soccerdata and write CSVs that
drop straight into the analysis pipeline's schema.

Sources (verified against soccerdata 1.9.1):
  * FBref  read_events()          -> goal/card/sub timing  => data/xg/events.csv
  * FBref  read_schedule()        -> fixtures (+ match xG if present) => schedule.csv
  * FBref  read_team_match_stats  -> match-level xG for/against => team_match_xg.csv
  * WhoScored read_events()  [--shots, needs a browser] -> per-shot xG w/ minute
                                                        => data/xg/shots.csv

WHAT NEEDS WHAT
  Study A core (jump/reversal overreaction)  : events.csv  (this file, no browser)
  Study B (results vs performance)           : team_match_xg.csv (no browser)
  Study A surprise proxy + quiet-min buckets : shots.csv  (--shots, WhoScored/Selenium)

I could not run this against the live sites from my sandbox, so it prints the
actual columns it receives and saves a raw copy alongside each normalized file.
If a normalization warns, open the *_raw.csv, see the real column names, and
adjust the small mapping functions below.

Usage:
  python fetch_xg.py                 # FBref events + match xG (fast, no browser)
  python fetch_xg.py --shots         # also pull per-shot xG from WhoScored (slow)
  python fetch_xg.py --force         # ignore soccerdata cache / re-normalize
"""
import argparse
import re
import sys
import pandas as pd

from config import XG_DIR, WC_LEAGUE, WC_SEASON, to_iso3


# ---------------------------------------------------------------------------
# Pure helper: FBref/WhoScored minute strings -> (half, minute, stoppage)
# "23" -> (1,23,0)   "45+2" -> (1,45,2)   "67" -> (2,67,0)   "90+3" -> (2,90,3)
# ---------------------------------------------------------------------------
def parse_minute(raw) -> tuple[int, int, int]:
    s = str(raw).strip()
    m = re.match(r"^(\d+)(?:\+(\d+))?", s)
    if not m:
        raise ValueError(f"unparseable minute: {raw!r}")
    minute = int(m.group(1))
    stoppage = int(m.group(2)) if m.group(2) else 0
    half = 1 if minute <= 45 else 2
    return half, minute, stoppage


def _find_col(df: pd.DataFrame, *candidates) -> str | None:
    """Case-insensitive search for the first column whose name contains any
    candidate substring. Handles FBref's occasional MultiIndex columns."""
    flat = {c: (" ".join(map(str, c)) if isinstance(c, tuple) else str(c)).lower()
            for c in df.columns}
    for cand in candidates:
        for col, name in flat.items():
            if cand in name:
                return col
    return None


# ---------------------------------------------------------------------------
# FBref: events (goal/card timing) -> pipeline `events` schema
# ---------------------------------------------------------------------------
def fetch_fbref(force: bool):
    import soccerdata as sd
    fb = sd.FBref(leagues=WC_LEAGUE, seasons=WC_SEASON, no_cache=force)

    print("[fbref] read_schedule() ...")
    schedule = fb.read_schedule().reset_index()
    schedule.to_csv(XG_DIR / "schedule.csv", index=False)
    print(f"  saved schedule.csv  ({len(schedule)} rows)  cols={list(schedule.columns)[:8]}...")

    print("[fbref] read_events() ...")
    ev_raw = fb.read_events().reset_index()
    ev_raw.to_csv(XG_DIR / "events_raw.csv", index=False)
    print(f"  saved events_raw.csv ({len(ev_raw)} rows)  cols={list(ev_raw.columns)}")

    # --- normalize to: match_id, half, minute, stoppage, event_type, team ---
    c_match = _find_col(ev_raw, "game", "match")
    c_min = _find_col(ev_raw, "minute", "time")
    c_type = _find_col(ev_raw, "event", "type")
    c_team = _find_col(ev_raw, "team")
    if not all([c_match, c_min, c_type, c_team]):
        print("  [WARN] could not auto-map events columns; leaving events_raw.csv "
              "for you to map. Found:",
              dict(match=c_match, minute=c_min, type=c_type, team=c_team))
    else:
        rows = []
        for _, r in ev_raw.iterrows():
            try:
                half, minute, stoppage = parse_minute(r[c_min])
            except ValueError:
                continue  # skip rows without a real minute (e.g. header/blank)
            etype = str(r[c_type]).lower()
            if "goal" in etype and "own" not in etype:
                std = "goal"
            elif "own" in etype:
                std = "own_goal"
            elif "red" in etype or "second yellow" in etype:
                std = "red_card"
            elif "penalty" in etype and "miss" not in etype:
                std = "penalty"
            else:
                continue  # subs, yellows, etc. — not needed for the studies
            rows.append(dict(match_id=r[c_match], half=half, minute=minute,
                             stoppage=stoppage, event_type=std,
                             team=to_iso3(str(r[c_team]))))
        events = pd.DataFrame(rows)
        events.to_csv(XG_DIR / "events.csv", index=False)
        print(f"  saved events.csv ({len(events)} goal/card/pen rows)")

    print("[fbref] read_team_match_stats(stat_type='schedule') for match xG ...")
    try:
        tms = fb.read_team_match_stats(stat_type="schedule").reset_index()
        tms.to_csv(XG_DIR / "team_match_xg.csv", index=False)
        print(f"  saved team_match_xg.csv ({len(tms)} rows).  If no xG column here, "
              "retry stat_type='shooting'.")
    except Exception as e:
        print(f"  [WARN] team match stats failed: {e}. Try stat_type='shooting' or "
              "'misc' in the soccerdata docs.")


# ---------------------------------------------------------------------------
# WhoScored (optional): per-shot xG with minute -> pipeline `shots` schema
# Requires a browser. `pip install selenium` and have Chrome/Chromium available.
# ---------------------------------------------------------------------------
def fetch_shots(force: bool):
    import soccerdata as sd
    print("[whoscored] read_events() — this launches a browser and is SLOW/fragile.")
    ws = sd.WhoScored(leagues=WC_LEAGUE, seasons=WC_SEASON, no_cache=force,
                      headless=True)
    ev = ws.read_events().reset_index()
    ev.to_csv(XG_DIR / "shots_raw.csv", index=False)
    print(f"  saved shots_raw.csv ({len(ev)} event rows)  cols={list(ev.columns)}")

    c_type = _find_col(ev, "type", "event")
    c_min = _find_col(ev, "minute", "expanded_minute", "time")
    c_team = _find_col(ev, "team")
    c_xg = _find_col(ev, "xg", "expected")
    if not all([c_type, c_min, c_team, c_xg]):
        print("  [WARN] shot columns not auto-mapped; inspect shots_raw.csv. Found:",
              dict(type=c_type, minute=c_min, team=c_team, xg=c_xg))
        return
    shots = ev[ev[c_type].astype(str).str.lower().str.contains("shot|goal", na=False)].copy()
    parsed = shots[c_min].map(parse_minute)
    shots["half"] = parsed.map(lambda t: t[0])
    shots["minute"] = parsed.map(lambda t: t[1])
    shots["team"] = shots[c_team].astype(str).map(to_iso3)
    shots["xg"] = pd.to_numeric(shots[c_xg], errors="coerce")
    out = shots[["half", "minute", "team", "xg"]].dropna(subset=["xg"])
    out.to_csv(XG_DIR / "shots.csv", index=False)
    print(f"  saved shots.csv ({len(out)} shots with xG)")


# ---------------------------------------------------------------------------
def _selftest():
    assert parse_minute("23") == (1, 23, 0)
    assert parse_minute("45+2") == (1, 45, 2)
    assert parse_minute("67") == (2, 67, 0)
    assert parse_minute("90+3") == (2, 90, 3)
    assert parse_minute("45") == (1, 45, 0)
    assert parse_minute("46") == (2, 46, 0)
    print("parse_minute self-test: OK")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--shots", action="store_true",
                    help="also pull per-shot xG from WhoScored (needs a browser)")
    ap.add_argument("--force", action="store_true", help="bypass soccerdata cache")
    ap.add_argument("--selftest", action="store_true", help="run offline unit tests only")
    args = ap.parse_args()

    if args.selftest:
        _selftest(); sys.exit(0)

    _selftest()
    fetch_fbref(force=args.force)
    if args.shots:
        fetch_shots(force=args.force)
    print("\nDone. CSVs in", XG_DIR)
