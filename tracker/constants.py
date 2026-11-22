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


COLOR_DOTS: dict[str, str] = {
    "Green": "🟢",
    "Blue": "🔵",
    "Red": "🔴",
    "Yellow": "🟡",
}

STAMP_VARIANTS: tuple[str, ...] = (
    "Brawl Champ",
    "Showdown Champ",
    "Top 4",
)

STAMP_VARIANT_MAP: dict[str, str] = {
    v.lower(): v for v in STAMP_VARIANTS
}

EXPANSION_ALIASES: dict[str, str] = {
    "night city showdown s1": "Set 1 Promos",
}


def normalize_stamp_variant(tag: Optional[str]) -> Optional[str]:
    """Normalizes recognized tournament stamp tags to canonical casing, returning None otherwise."""
    if not tag:
        return None
    cleaned = tag.strip().lower()
    return STAMP_VARIANT_MAP.get(cleaned)


def resolve_expansion_alias(expansion: str) -> str:
    """Maps CardNexus expansion names to TCGplayer catalog group names when an alias exists."""
    cleaned = (expansion or "").strip()
    if not cleaned:
        return cleaned
    return EXPANSION_ALIASES.get(cleaned.lower(), cleaned)


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
