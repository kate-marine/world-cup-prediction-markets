"""
Shared configuration for the World Cup / Kalshi prediction-market study.

Nothing here hits the network. It's constants + a couple of pure helpers the
fetch scripts import.
"""
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths (everything under data/ is gitignored; created on first run)
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
KALSHI_DIR = DATA / "kalshi"
XG_DIR = DATA / "xg"
for d in (KALSHI_DIR, XG_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Kalshi
# ---------------------------------------------------------------------------
# CONFIRM ON FIRST RUN: Kalshi has changed hosts before. The official docs
# examples currently use this host for the historical endpoints. If you get
# connection errors or 404s, check docs.kalshi.com for the current base URL
# (other hosts seen in the wild: api.elections.kalshi.com, trading-api.kalshi.com).
KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"

# Reading market data (markets, candlesticks, trades) needs NO auth.
# Be polite + dodge the token-bucket limiter (no Retry-After header is sent).
KALSHI_REQUEST_DELAY_S = 0.4      # min seconds between requests
KALSHI_MAX_RETRIES = 5
KALSHI_CANDLES_CAP = 10_000       # documented per-response cap; we chunk under it

# ---------------------------------------------------------------------------
# FBref / soccerdata  (verified against soccerdata 1.9.1)
# ---------------------------------------------------------------------------
WC_LEAGUE = "INT-World Cup"       # exact code soccerdata expects
WC_SEASON = "2026"

# ---------------------------------------------------------------------------
# Team-name crosswalk  ***fill this in as you discover mismatches***
# FBref/WhoScored team strings will NOT match Kalshi's country labels. Join on a
# stable key (ISO-3166 alpha-3 is safest). Add rows the first time a merge drops
# a team. Left = source string as it appears, Right = ISO3 you standardize on.
# ---------------------------------------------------------------------------
TEAM_TO_ISO3 = {
    "Argentina": "ARG", "Brazil": "BRA", "France": "FRA", "Spain": "ESP",
    "England": "ENG", "Germany": "GER", "Netherlands": "NED", "Portugal": "POR",
    "United States": "USA", "USA": "USA", "Mexico": "MEX", "Canada": "CAN",
    # ... extend as needed
}


def to_iso3(name: str) -> str:
    """Best-effort team -> ISO3; returns the raw name (uppercased) if unmapped
    so nothing silently vanishes — unmapped names are visible in the output."""
    if name in TEAM_TO_ISO3:
        return TEAM_TO_ISO3[name]
    return f"?{name}"  # leading '?' flags an unmapped team for you to add above
