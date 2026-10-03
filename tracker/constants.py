"""Shared filesystem anchors, transport identifiers, and cache duration primitives."""

from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
REPO_PRICES_DIR: Path = REPO_ROOT / "prices"
REPO_CARDS_FILE: Path = REPO_ROOT / "cards.json"

USER_AGENT: str = "CyberpunkTCGMarketTracker/1.0"

CACHE_TTL_24H_SECONDS: int = 86400
