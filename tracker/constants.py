"""Shared filesystem anchors, transport identifiers, and cache duration primitives."""

from pathlib import Path
from typing import Optional

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
REPO_PRICES_DIR: Path = REPO_ROOT / "prices"
REPO_CARDS_FILE: Path = REPO_ROOT / "cards.json"
REPO_DATA_DIR: Path = REPO_ROOT / "data"

USER_AGENT: str = "CyberpunkTCGMarketTracker/1.0"

CACHE_TTL_24H_SECONDS: int = 86400

CANONICAL_RARITIES = (
    "Common",
    "Uncommon",
    "Rare",
    "Epic",
    "Secret",
    "Iconic",
    "Nova",
)

RARITY_MAP: dict[str, str] = {
    "C": "Common",
    "COMMON": "Common",
    "UC": "Uncommon",
    "UNCOMMON": "Uncommon",
    "R": "Rare",
    "RARE": "Rare",
    "E": "Epic",
    "EPIC": "Epic",
    "S": "Secret",
    "SECRET": "Secret",
    "IL": "Iconic",
    "IO": "Iconic",
    "IS": "Iconic",
    "ICONIC": "Iconic",
    "ICONIC LEGEND": "Iconic",
    "ICONIC OTHER": "Iconic",
    "ICONIC SECRET": "Iconic",
    "NR": "Nova",
    "NOVA": "Nova",
    "NOVA RARE": "Nova",
}


def normalize_rarity(rarity: Optional[str]) -> str:
    """
    Normalizes card rarity strings to canonical game tiers.
    Preserves unrecognized non-empty strings as-is and returns 'Unknown' for None/blank.
    """
    if rarity is None:
        return "Unknown"
    stripped = rarity.strip()
    if not stripped:
        return "Unknown"
    upper = stripped.upper()
    if upper in RARITY_MAP:
        return RARITY_MAP[upper]
    if upper.startswith("ICONIC"):
        return "Iconic"
    if upper.startswith("NOVA"):
        return "Nova"
    return stripped
