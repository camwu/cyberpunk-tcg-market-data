"""
Welcome to Night City Iconic card comparison engine.
Analyzes market price spreads and collection ownership between Beta and Retail
printings across alternate-art Iconic cards (#141 - #171).
"""

import csv
from dataclasses import dataclass
import datetime
import json
import os
from pathlib import Path
import statistics
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

from tracker.constants import (
    REPO_ROOT,
    REPO_CARDS_FILE,
    REPO_PRICES_DIR,
    REPO_DATA_DIR,
)
from tracker.config import load_config
from tracker.iconics_html import generate_html_dashboard
from tracker.validation import CollectionValidationError, parse_multi_lot_notes
from tracker.valuation import sanitize_card_name

TIER_MAP: Dict[str, str] = {
    "IL": "Legends",
    "IO": "Others",
    "IS": "Secrets",
}


@dataclass
class IconicCardPair:
    print_number: str
    card_name: str
    tier: str
    valid_finish: str
    beta_product_id: Optional[int]
    retail_product_id: Optional[int]
    beta_price: Optional[float]
    retail_price: Optional[float]
    spread: Optional[float]
    beta_premium_pct: Optional[float]
    beta_owned: int
    retail_owned: int
    color: Optional[str] = None
    card_type: Optional[str] = None

    @property
    def pair_complete(self) -> bool:
        """Indicates whether at least one copy is owned in both Beta and Retail printings."""
        return self.beta_owned > 0 and self.retail_owned > 0


@dataclass
class TierSummary:
    name: str
    total_cards: int
    priced_pairs_count: int
    beta_owned_count: int
    retail_owned_count: int
    beta_median: Optional[float]
    retail_median: Optional[float]
    spread: Optional[float]
    beta_min: Optional[float] = None
    beta_p25: Optional[float] = None
    beta_p75: Optional[float] = None
    beta_max: Optional[float] = None
    retail_min: Optional[float] = None
    retail_p25: Optional[float] = None
    retail_p75: Optional[float] = None
    retail_max: Optional[float] = None
    beta_set_total: Optional[float] = None
    retail_set_total: Optional[float] = None
    set_premium_dlr: Optional[float] = None
    set_premium_pct: Optional[float] = None
    beta_physical_owned: int = 0
    retail_physical_owned: int = 0


def compute_quantiles(
    values: List[float],
) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float], Optional[float]]:
    """
    Computes (min, p25, median, p75, max) for a numeric sequence.
    Returns (None, None, None, None, None) when empty.
    Returns (min, None, median, None, max) when 1 <= len < 4 so uninvented quartiles render N/A.
    """
    if not values:
        return None, None, None, None, None
    sorted_vals = sorted(values)
    v_min = sorted_vals[0]
    v_max = sorted_vals[-1]
    v_med = statistics.median(sorted_vals)
    if len(sorted_vals) >= 4:
        q1, _, q3 = statistics.quantiles(sorted_vals, n=4, method="inclusive")
        return v_min, q1, v_med, q3, v_max
    return v_min, None, v_med, None, v_max


def resolve_latest_price_file(prices_dir: Path = REPO_PRICES_DIR) -> Optional[Path]:
    """Finds prices/latest.json or the newest dated price JSON file."""
    latest = prices_dir / "latest.json"
    if latest.is_file():
        return latest
    dated_files = sorted(prices_dir.glob("*.json"))
    if dated_files:
        return dated_files[-1]
    return None


def get_historical_price_files(prices_dir: Path = REPO_PRICES_DIR) -> List[Path]:
    """Returns dated price files (YYYY-MM-DD.json) sorted descending chronologically."""
    files = [
        f for f in prices_dir.glob("*.json")
        if f.name != "latest.json" and len(f.stem) == 10 and f.stem.replace("-", "").isdigit()
    ]
    files.sort(key=lambda p: p.stem, reverse=True)
    return files


def extract_finish_price(p_data: Any, preferred_finish: str) -> Optional[float]:
    """
    Extracts market price strictly for the specified finish, accepting Normal as Standard alias.
    Never falls back to a different finish.
    """
    if not isinstance(p_data, dict):
        return None
    finish_variants = [preferred_finish]
    if preferred_finish == "Standard":
        finish_variants.append("Normal")
    elif preferred_finish == "Normal":
        finish_variants.append("Standard")

    for f in finish_variants:
        sub = p_data.get(f)
        if isinstance(sub, dict):
            mp = sub.get("marketPrice")
            if mp is not None and isinstance(mp, (int, float)) and float(mp) > 0.0:
                return float(mp)

    return None


def resolve_product_finish(
    product_id: Optional[int],
    card_label: str,
    latest_prices: Dict[str, Any],
    historical_files: List[Path],
    snapshot_cache: Dict[Path, Dict[str, Any]],
) -> str:
    """
    Derives the valid normalized finish for a product from price records.
    Normalizes 'Normal' -> 'Standard'.
    Raises ValueError if product has zero records across all history, or multiple distinct finishes.
    """
    if not product_id:
        raise ValueError(f"Missing productId for {card_label}")

    pid_str = str(product_id)

    # Check latest prices
    p_data = latest_prices.get(pid_str)
    if isinstance(p_data, dict) and p_data:
        raw_keys = list(p_data.keys())
    else:
        raw_keys = []

    # If absent in latest, scan historical snapshots until found
    if not raw_keys:
        for fpath in historical_files:
            if fpath not in snapshot_cache:
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        snapshot_cache[fpath] = json.load(f).get("prices", {})
                except Exception:
                    snapshot_cache[fpath] = {}
            hist_pdata = snapshot_cache[fpath].get(pid_str)
            if isinstance(hist_pdata, dict) and hist_pdata:
                raw_keys = list(hist_pdata.keys())
                break

    if not raw_keys:
        raise ValueError(
            f"No price records or finish keys found across catalog history for {card_label} (productId={product_id})"
        )

    normalized_finishes: Set[str] = set()
    for k in raw_keys:
        k_upper = str(k).strip().upper()
        if k_upper in ("NORMAL", "STANDARD"):
            normalized_finishes.add("Standard")
        elif k_upper == "FOIL":
            normalized_finishes.add("Foil")
        else:
            normalized_finishes.add(str(k).strip())

    if len(normalized_finishes) > 1:
        raise ValueError(
            f"Ambiguous multiple finishes for {card_label} (productId={product_id}): {sorted(normalized_finishes)}"
        )

    return next(iter(normalized_finishes))


def resolve_card_price_unbounded(
    product_id: Optional[int],
    preferred_finish: str,
    latest_prices: Dict[str, Any],
    historical_files: List[Path],
    snapshot_cache: Dict[Path, Dict[str, Any]],
) -> Optional[float]:
    """
    Resolves card market price for preferred_finish from latest prices or carries forward from historical snapshots.
    Lazily loads historical price files into snapshot_cache.
    Returns None (N/A) if unpriced across all history.
    """
    if not product_id:
        return None

    pid_str = str(product_id)
    latest_data = latest_prices.get(pid_str)
    price = extract_finish_price(latest_data, preferred_finish)
    if price is not None and price > 0.0:
        return price

    # Scan historical files descending
    for fpath in historical_files:
        if fpath not in snapshot_cache:
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    snapshot_cache[fpath] = json.load(f).get("prices", {})
            except Exception:
                snapshot_cache[fpath] = {}
        hist_prices = snapshot_cache[fpath]
        h_data = hist_prices.get(pid_str)
        hist_price = extract_finish_price(h_data, preferred_finish)
        if hist_price is not None and hist_price > 0.0:
            return hist_price

    return None


def load_iconic_pairs(
    cards_path: Path = REPO_CARDS_FILE,
    prices_dir: Path = REPO_PRICES_DIR,
    collection_path: Optional[str] = None,
) -> Tuple[List[IconicCardPair], Optional[str], Optional[str]]:
    """
    Loads, dynamically validates, and pairs Welcome to Night City Iconic cards.
    Derives card finishes strictly from price records and enforces Beta/Retail consistency.
    Returns (pairs, collection_file_name, collection_mtime_date).
    """
    if not cards_path.is_file():
        raise FileNotFoundError(f"Missing cards catalog at {cards_path}")

    with open(cards_path, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    # Filter catalog for WNC Iconic cards
    card_items = [v for k, v in catalog.items() if k != "_groups"]
    beta_cards: Dict[str, dict] = {}
    retail_cards: Dict[str, dict] = {}

    for c in card_items:
        grp = c.get("groupName") or ""
        raw_rarity = c.get("rarity") or ""
        if raw_rarity not in TIER_MAP:
            continue
        pnum = str(c.get("printNumber") or "").strip()
        if not pnum:
            continue

        if grp == "Welcome to Night City - Beta":
            clean_num = pnum.removeprefix("B")
            beta_cards[clean_num] = c
        elif grp == "Welcome to Night City - Retail":
            retail_cards[pnum] = c

    # Dynamic orphan validation
    beta_keys = set(beta_cards.keys())
    retail_keys = set(retail_cards.keys())

    if not beta_keys and not retail_keys:
        raise ValueError("No Welcome to Night City Iconic cards found in catalog.")

    orphaned_beta = sorted(beta_keys - retail_keys)
    orphaned_retail = sorted(retail_keys - beta_keys)
    if orphaned_beta or orphaned_retail:
        err_msg = []
        if orphaned_beta:
            err_msg.append(f"Orphaned Beta print numbers without Retail pair: {', '.join(orphaned_beta)}")
        if orphaned_retail:
            err_msg.append(f"Orphaned Retail print numbers without Beta pair: {', '.join(orphaned_retail)}")
        raise ValueError("Iconic card pairing mismatch: " + "; ".join(err_msg))

    matched_keys = sorted(beta_keys, key=lambda k: int(k) if k.isdigit() else k)

    # Price resolution setup
    latest_path = resolve_latest_price_file(prices_dir)
    latest_prices: Dict[str, Any] = {}
    if latest_path and latest_path.is_file():
        with open(latest_path, "r", encoding="utf-8") as f:
            latest_prices = json.load(f).get("prices", {})

    historical_files = get_historical_price_files(prices_dir)
    snapshot_cache: Dict[Path, Dict[str, Any]] = {}

    # Strict Beta/Retail Rarity & Finish Agreement
    valid_finish_by_key: Dict[str, str] = {}
    for k in matched_keys:
        b_card = beta_cards[k]
        r_card = retail_cards[k]

        b_rarity = b_card.get("rarity")
        r_rarity = r_card.get("rarity")
        card_name = sanitize_card_name(b_card.get("name") or r_card.get("name") or "")

        if b_rarity != r_rarity:
            raise ValueError(
                f"Rarity mismatch between Beta ({b_rarity}) and Retail ({r_rarity}) for #{k} '{card_name}'"
            )
        if b_rarity not in TIER_MAP:
            raise ValueError(f"Unrecognized Iconic rarity code '{b_rarity}' for #{k} '{card_name}'")

        b_pid = b_card.get("productId")
        r_pid = r_card.get("productId")

        b_finish = resolve_product_finish(
            b_pid, f"Beta #{b_card.get('printNumber')} '{card_name}'", latest_prices, historical_files, snapshot_cache
        )
        r_finish = resolve_product_finish(
            r_pid, f"Retail #{r_card.get('printNumber')} '{card_name}'", latest_prices, historical_files, snapshot_cache
        )

        if b_finish != r_finish:
            raise ValueError(
                f"Finish mismatch between Beta ({b_finish}) and Retail ({r_finish}) for #{k} '{card_name}'"
            )

        valid_finish_by_key[k] = b_finish

    # Collection ingestion & auditing
    owned_counts: Dict[Tuple[str, str, str], int] = {}
    col_name: Optional[str] = None
    col_mtime_date: Optional[str] = None

    if collection_path:
        if not os.path.isfile(collection_path):
            raise FileNotFoundError(
                f"Collection file not found: '{collection_path}'. "
                "Run with --sync-collection to download active collection, or provide --collection."
            )
        col_name = Path(collection_path).name
        mtime_ts = os.path.getmtime(collection_path)
        col_mtime_date = datetime.date.fromtimestamp(mtime_ts).isoformat()

        total_rows = 0
        wnc_rows = 0

        with open(collection_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                total_rows += 1
                exp = (row.get("expansion") or "").strip()
                pnum = (row.get("printNumber") or "").strip()
                finish = (row.get("finish") or "").strip()
                qty_str = (row.get("totalQtyOwned") or "0").strip()

                if "Welcome to Night City" in exp:
                    wnc_rows += 1

                # Normalize finish
                if finish.upper() in ("NORMAL", "STANDARD"):
                    norm_finish = "Standard"
                elif finish.upper() == "FOIL":
                    norm_finish = "Foil"
                else:
                    norm_finish = finish

                try:
                    qty = int(qty_str)
                except ValueError:
                    qty = 0

                if qty > 0:
                    notes = (row.get("notes") or "").strip()
                    _, lot_err = parse_multi_lot_notes(notes, qty)
                    if lot_err:
                        raise CollectionValidationError(f"Row {exp} #{pnum}: {lot_err}")

                # Audit finish mismatch against derived card finish
                clean_num = pnum.removeprefix("B")
                if exp in ("Welcome to Night City - Beta", "Welcome to Night City - Retail") and clean_num in valid_finish_by_key:
                    card_valid_finish = valid_finish_by_key[clean_num]
                    if norm_finish != card_valid_finish:
                        print(
                            f"Warning: Collection row for {exp} #{pnum} has finish '{finish}' but card is {card_valid_finish}-only. Excluding.",
                            file=sys.stderr,
                        )
                        continue

                key = (exp, pnum, norm_finish)
                owned_counts[key] = owned_counts.get(key, 0) + qty

        if total_rows > 0 and wnc_rows == 0:
            print(
                "Warning: Collection contains records but zero rows matched 'Welcome to Night City - Beta' or 'Welcome to Night City - Retail'.",
                file=sys.stderr,
            )

    # Build pairs
    pairs: List[IconicCardPair] = []
    for k in matched_keys:
        b_card = beta_cards[k]
        r_card = retail_cards[k]

        raw_name = b_card.get("name") or r_card.get("name") or ""
        card_name = sanitize_card_name(raw_name)
        tier = TIER_MAP[b_card.get("rarity")]
        valid_finish = valid_finish_by_key[k]
        color = b_card.get("color") or r_card.get("color")
        card_type = b_card.get("cardType") or r_card.get("cardType")

        b_pid = b_card.get("productId")
        r_pid = r_card.get("productId")

        b_price = resolve_card_price_unbounded(b_pid, valid_finish, latest_prices, historical_files, snapshot_cache)
        r_price = resolve_card_price_unbounded(r_pid, valid_finish, latest_prices, historical_files, snapshot_cache)

        if b_price is not None and r_price is not None:
            spread = b_price - r_price
            beta_premium_pct = ((b_price - r_price) / r_price) * 100.0 if r_price > 0.0 else None
        else:
            spread = None
            beta_premium_pct = None

        b_pnum = str(b_card.get("printNumber") or f"B{k}").strip()
        r_pnum = str(r_card.get("printNumber") or k).strip()

        b_owned = owned_counts.get(("Welcome to Night City - Beta", b_pnum, valid_finish), 0)
        r_owned = owned_counts.get(("Welcome to Night City - Retail", r_pnum, valid_finish), 0)

        pairs.append(
            IconicCardPair(
                print_number=k,
                card_name=card_name,
                tier=tier,
                valid_finish=valid_finish,
                beta_product_id=b_pid,
                retail_product_id=r_pid,
                beta_price=b_price,
                retail_price=r_price,
                spread=spread,
                beta_premium_pct=beta_premium_pct,
                beta_owned=b_owned,
                retail_owned=r_owned,
                color=color,
                card_type=card_type,
            )
        )

    return pairs, col_name, col_mtime_date


def build_tier_summary(name: str, pairs_subset: List[IconicCardPair]) -> TierSummary:
    """
    Builds paired-subset summary metrics for a tier.
    Medians, quantiles, and set values are strictly computed over pairs where both Beta and Retail prices exist.
    """
    total_cards = len(pairs_subset)
    b_owned_count = sum(1 for p in pairs_subset if p.beta_owned > 0)
    r_owned_count = sum(1 for p in pairs_subset if p.retail_owned > 0)
    b_physical_owned = sum(p.beta_owned for p in pairs_subset)
    r_physical_owned = sum(p.retail_owned for p in pairs_subset)

    # Paired subset: both prices exist
    priced_pairs = [p for p in pairs_subset if p.beta_price is not None and p.retail_price is not None]
    priced_count = len(priced_pairs)

    b_prices = [p.beta_price for p in priced_pairs if p.beta_price is not None]
    r_prices = [p.retail_price for p in priced_pairs if p.retail_price is not None]

    if priced_count > 0:
        b_med = statistics.median(b_prices)
        r_med = statistics.median(r_prices)
        reconciled_spread = b_med - r_med
        b_set_total = sum(b_prices)
        r_set_total = sum(r_prices)
        set_prem_dlr = b_set_total - r_set_total
        set_prem_pct = ((set_prem_dlr) / r_set_total) * 100.0 if r_set_total > 0 else None
    else:
        b_med = None
        r_med = None
        reconciled_spread = None
        b_set_total = None
        r_set_total = None
        set_prem_dlr = None
        set_prem_pct = None

    b_min, b_p25, _, b_p75, b_max = compute_quantiles(b_prices)
    r_min, r_p25, _, r_p75, r_max = compute_quantiles(r_prices)

    return TierSummary(
        name=name,
        total_cards=total_cards,
        priced_pairs_count=priced_count,
        beta_owned_count=b_owned_count,
        retail_owned_count=r_owned_count,
        beta_median=b_med,
        retail_median=r_med,
        spread=reconciled_spread,
        beta_min=b_min,
        beta_p25=b_p25,
        beta_p75=b_p75,
        beta_max=b_max,
        retail_min=r_min,
        retail_p25=r_p25,
        retail_p75=r_p75,
        retail_max=r_max,
        beta_set_total=b_set_total,
        retail_set_total=r_set_total,
        set_premium_dlr=set_prem_dlr,
        set_premium_pct=set_prem_pct,
        beta_physical_owned=b_physical_owned,
        retail_physical_owned=r_physical_owned,
    )


def format_currency_diff(val: Optional[float]) -> str:
    """Formats signed currency difference string. Returns N/A when None."""
    if val is None:
        return "N/A"
    sign = "+" if val >= 0 else "-"
    return f"{sign}${abs(val):,.2f}"


def format_console_summary(
    tier_summaries: List[TierSummary],
    pairs: List[IconicCardPair],
    output_path: str,
    collection_name: Optional[str] = None,
    collection_mtime_date: Optional[str] = None,
) -> str:
    """Renders the approved uncrowded 76-character console summary."""
    total_pairs_count = len(pairs)
    total_beta_owned_distinct = sum(1 for p in pairs if p.beta_owned > 0)
    total_retail_owned_distinct = sum(1 for p in pairs if p.retail_owned > 0)

    total_beta_cards = sum(p.beta_owned for p in pairs)
    total_retail_cards = sum(p.retail_owned for p in pairs)

    beta_owned_val = 0.0
    retail_owned_val = 0.0
    unpriced_owned_count = 0

    for p in pairs:
        if p.beta_owned > 0:
            if p.beta_price is not None:
                beta_owned_val += p.beta_price * p.beta_owned
            else:
                unpriced_owned_count += p.beta_owned
        if p.retail_owned > 0:
            if p.retail_price is not None:
                retail_owned_val += p.retail_price * p.retail_owned
            else:
                unpriced_owned_count += p.retail_owned

    total_owned_val = beta_owned_val + retail_owned_val
    out_name = Path(output_path).name

    lines = [
        "===================== Welcome to Night City: Iconics =====================",
        "",
        "Category       Beta Owned  Retail Owned   Beta Med  Retail Med   Med Spread",
        "----------------------------------------------------------------------------",
    ]

    for ts in tier_summaries:
        b_ratio = f"{ts.beta_owned_count:>2}/{ts.total_cards}"
        r_ratio = f"{ts.retail_owned_count:>2}/{ts.total_cards}"
        b_med_str = f"${ts.beta_median:>6.2f}" if ts.beta_median is not None else f"{'N/A':>7}"
        r_med_str = f"${ts.retail_median:>6.2f}" if ts.retail_median is not None else f"{'N/A':>7}"
        spread_str = format_currency_diff(ts.spread)
        cat_label = f"{ts.name} ({ts.total_cards})"

        lines.append(
            f"{cat_label:<14}     {b_ratio}         {r_ratio}    {b_med_str}     {r_med_str}     {spread_str:>8}"
        )

    lines.append("----------------------------------------------------------------------------")
    tot_b_str = f"{total_beta_owned_distinct:>2}/{total_pairs_count}"
    tot_r_str = f"{total_retail_owned_distinct:>2}/{total_pairs_count}"
    lines.append(f"Total ({total_pairs_count})         {tot_b_str}         {tot_r_str}")
    lines.append("")
    lines.append("Owned Collection Value:")
    b_val_str = f"${beta_owned_val:,.2f}"
    r_val_str = f"${retail_owned_val:,.2f}"
    tot_val_str = f"${total_owned_val:,.2f}"
    lines.append(f"{f'  Beta ({total_beta_cards} cards):':<22}{b_val_str:>11}")
    lines.append(f"{f'  Retail ({total_retail_cards} cards):':<22}{r_val_str:>11}")
    tot_line = f"{'  Total:':<22}{tot_val_str:>11}"
    if unpriced_owned_count > 0:
        tot_line += f" (excludes {unpriced_owned_count} unpriced card(s))"
    lines.append(tot_line)

    lines.append("")
    if collection_name and collection_mtime_date:
        lines.append(f"Collection: {collection_name} (modified {collection_mtime_date})")
    elif collection_name:
        lines.append(f"Collection: {collection_name}")
    lines.append(f"Report saved: {out_name}")
    lines.append("============================================================================")

    return "\n".join(lines)


def run_iconics_comparison(
    output_path: Optional[str] = None,
    collection_path: Optional[str] = None,
    cards_path: Path = REPO_CARDS_FILE,
    prices_dir: Path = REPO_PRICES_DIR,
    database_path: Optional[str] = None,
) -> Tuple[int, str]:
    """Executes the WNC Iconic cards comparison pipeline end-to-end."""
    cfg = None
    if not collection_path or not output_path:
        cfg = load_config()

    if not collection_path:
        collection_path = cfg.collection_csv

    target_output = output_path
    if not target_output:
        if cfg and getattr(cfg, "output_report", None):
            parent = Path(cfg.output_report).parent
            target_output = str(parent / "wnc_iconic_price_trajectories.html")
        else:
            target_output = str(REPO_DATA_DIR / "wnc_iconic_price_trajectories.html")

    target_db = database_path if database_path is not None else (getattr(cfg, "database_path", None) if cfg else None)

    pairs, col_name, col_mtime = load_iconic_pairs(
        cards_path=cards_path,
        prices_dir=prices_dir,
        collection_path=collection_path,
    )

    tier_order = ["Legends", "Others", "Secrets"]
    tier_summaries: List[TierSummary] = []
    for tname in tier_order:
        subset = [p for p in pairs if p.tier == tname]
        if subset:
            tier_summaries.append(build_tier_summary(tname, subset))

    generate_html_dashboard(
        pairs=pairs,
        prices_dir=prices_dir,
        collection_path=collection_path,
        output_path=target_output,
        database_path=target_db,
        extract_price_fn=extract_finish_price,
        compute_quantiles_fn=compute_quantiles,
    )

    summary_text = format_console_summary(
        tier_summaries=tier_summaries,
        pairs=pairs,
        output_path=target_output,
        collection_name=col_name,
        collection_mtime_date=col_mtime,
    )

    pointer_file = os.path.join(str(REPO_ROOT), ".latest_iconics_report")
    try:
        with open(pointer_file, "w", encoding="utf-8") as f:
            f.write(os.path.abspath(target_output))
    except OSError as e:
        print(f"Warning: Could not write latest iconics report pointer to '{pointer_file}': {e}", file=sys.stderr)

    return 0, summary_text
