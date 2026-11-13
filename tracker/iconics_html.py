"""
Welcome to Night City Iconic Cards HTML Dashboard Generator.
Renders a self-contained interactive HTML report with dark-mode SVG price trajectories
and paired Beta vs. Retail market comparison tables.
"""

import datetime
import html
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any, Dict, List, Optional, Tuple

from tracker.iconics_common import (
    ValidatedCollectionRow,
    compute_quantiles,
    extract_finish_price,
    load_validated_collection_rows,
    normalize_finish,
)

EARLIEST_BETA_DATE = "2026-09-13"
EARLIEST_RETAIL_DATE = "2026-10-30"
WNC_BETA_GROUP_NAME = "Welcome to Night City - Beta"
WNC_RETAIL_GROUP_NAME = "Welcome to Night City - Retail"

_ALLOWED_COLORS = {"Yellow", "Green", "Blue", "Red", "Neutral"}


def serialize_json_for_script(data: Any) -> str:
    """Serializes JSON safely for embedding inside an inline HTML <script> block."""
    return (
        json.dumps(data)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def load_daily_prices_chronological(
    prices_dir: Path,
) -> Tuple[List[str], Dict[str, Dict[str, Dict[str, Any]]]]:
    """
    Loads daily market price snapshots chronologically from prices_dir.
    Uses dated YYYY-MM-DD.json files and includes latest.json if dated files are empty
    or latest.json carries a newer snapshot date not already present.
    """
    if not prices_dir.is_dir():
        raise FileNotFoundError(f"Prices directory not found at {prices_dir}")

    dated_files = [
        f
        for f in prices_dir.glob("*.json")
        if f.name != "latest.json" and len(f.stem) == 10 and f.stem.replace("-", "").isdigit()
    ]
    dated_files.sort(key=lambda p: p.stem)

    all_dates: List[str] = []
    price_by_date: Dict[str, Dict[str, Dict[str, Any]]] = {}

    for pf in dated_files:
        d_str = pf.stem
        try:
            with open(pf, "r", encoding="utf-8") as f:
                data = json.load(f)
            prices = data.get("prices", {})
            if isinstance(prices, dict):
                all_dates.append(d_str)
                price_by_date[d_str] = prices
        except (OSError, json.JSONDecodeError):
            continue

    latest_file = prices_dir / "latest.json"
    if latest_file.is_file():
        try:
            with open(latest_file, "r", encoding="utf-8") as f:
                latest_data = json.load(f)
            latest_date = str(latest_data.get("date") or "").strip()
            latest_prices = latest_data.get("prices", {})
            if latest_date and isinstance(latest_prices, dict) and latest_date not in price_by_date:
                all_dates.append(latest_date)
                price_by_date[latest_date] = latest_prices
                all_dates.sort()
        except (OSError, json.JSONDecodeError):
            pass

    return all_dates, price_by_date


def _filter_dates_from(all_dates: List[str], earliest_date: Optional[str]) -> List[str]:
    """Filters snapshot dates on or after earliest_date when provided."""
    if not earliest_date:
        return list(all_dates)
    return [d for d in all_dates if d >= earliest_date]


def _clamp_date(raw_date: Optional[str], min_date: Optional[str]) -> Optional[str]:
    if not raw_date:
        return None
    if min_date and raw_date < min_date:
        return min_date
    return raw_date


def load_collection_acquisitions(
    collection_path: Optional[str],
    database_path: Optional[str],
    earliest_beta_date: Optional[str],
    earliest_retail_date: Optional[str],
    validated_rows: Optional[List[ValidatedCollectionRow]] = None,
) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    """
    Loads card acquisition lots and baselines from card_metadata SQLite (when available)
    and active collection CSV rows keyed by (expansion, printNumber, normalized_finish).
    """
    db_lot_baselines: Dict[Tuple[str, str, str, str], float] = {}
    db_clamped_baselines: Dict[Tuple[str, str, str, str], float] = {}
    if database_path and os.path.isfile(database_path):
        conn = None
        try:
            conn = sqlite3.connect(database_path)
            cur = conn.cursor()
            cur.execute(
                "SELECT expansion, print_number, finish, first_seen_date, baseline_market_price "
                "FROM card_metadata ORDER BY first_seen_date ASC"
            )
            for exp, pn, fin, fsd, bmp in cur.fetchall():
                exp_s = str(exp or "")
                pn_s = str(pn or "")
                norm_fin = normalize_finish(str(fin or ""), default="Standard")
                raw_fsd = str(fsd or "").strip()
                bmp_val = float(bmp or 0.0)
                if raw_fsd and bmp_val > 0.0:
                    db_lot_baselines.setdefault((exp_s, pn_s, norm_fin, raw_fsd), bmp_val)
                    min_ed = (
                        earliest_beta_date
                        if exp_s == WNC_BETA_GROUP_NAME
                        else (earliest_retail_date if exp_s == WNC_RETAIL_GROUP_NAME else earliest_beta_date)
                    )
                    clamped_fsd = _clamp_date(raw_fsd, min_ed)
                    if clamped_fsd:
                        db_clamped_baselines.setdefault((exp_s, pn_s, norm_fin, clamped_fsd), bmp_val)
        except sqlite3.Error as e:
            print(f"Warning: Could not read card_metadata from '{database_path}': {e}", file=sys.stderr)
        finally:
            if conn is not None:
                conn.close()

    acquisitions: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    if validated_rows is None:
        if not collection_path or not os.path.isfile(collection_path):
            return acquisitions
        validated_rows = load_validated_collection_rows(collection_path)

    for vrow in validated_rows:
        if vrow.qty <= 0:
            continue

        exp = vrow.expansion
        pn = vrow.print_number
        fin = vrow.norm_finish or "Standard"
        lots = vrow.lots

        min_edition_date = (
            earliest_beta_date
            if exp == WNC_BETA_GROUP_NAME
            else (earliest_retail_date if exp == WNC_RETAIL_GROUP_NAME else earliest_beta_date)
        )
        clamped_lots: List[Tuple[str, int]] = []
        lot_entries: List[Tuple[str, int, float]] = []
        for lot_date, lot_qty in lots:
            if lot_date:
                c_date = _clamp_date(lot_date, min_edition_date)
                if c_date:
                    clamped_lots.append((c_date, lot_qty))
                    lot_db_cost = (
                        db_lot_baselines.get((exp, pn, fin, lot_date))
                        or db_lot_baselines.get((exp, pn, fin, c_date))
                        or db_clamped_baselines.get((exp, pn, fin, c_date))
                        or 0.0
                    )
                    lot_entries.append((c_date, lot_qty, lot_db_cost))

        dated_candidates = sorted(d for d, _ in clamped_lots)
        raw_earliest = sorted(d for d, _ in lots if d)[0] if any(d for d, _ in lots) else None
        acq_date = dated_candidates[0] if dated_candidates else None

        acq_key = (exp, pn, fin)
        existing = acquisitions.get(acq_key)
        if existing:
            existing["qty"] += vrow.qty
            existing["lots"].extend(clamped_lots)
            existing["lot_entries"].extend(lot_entries)
            if acq_date and (not existing["date"] or acq_date < existing["date"]):
                existing["date"] = acq_date
            merged_entries = existing["lot_entries"]
            merged_qty = sum(q for _, q, _ in merged_entries)
            if merged_qty > 0 and all(c > 0.0 for _, _, c in merged_entries):
                existing["cost"] = sum(c * q for _, q, c in merged_entries) / merged_qty
            else:
                existing["cost"] = 0.0
        else:
            row_lot_qty = sum(q for _, q, _ in lot_entries)
            summary_cost = (
                sum(c * q for _, q, c in lot_entries) / row_lot_qty
                if (row_lot_qty > 0 and all(c > 0.0 for _, _, c in lot_entries))
                else 0.0
            )
            acquisitions[acq_key] = {
                "date": acq_date,
                "rawDate": raw_earliest,
                "cost": summary_cost,
                "qty": vrow.qty,
                "finish": fin,
                "lots": clamped_lots,
                "lot_entries": lot_entries,
            }

    return acquisitions


def _lookup_point_price(points: List[Tuple[str, float]], target_date: str) -> float:
    """Finds carried-forward price on target_date, clamping earlier dates to the first snapshot."""
    if not points:
        return 0.0
    if target_date <= points[0][0]:
        return points[0][1]
    resolved = points[0][1]
    for d, val in points:
        if d <= target_date:
            resolved = val
        else:
            break
    return resolved


def build_edition_trajectories(
    pairs: List[Any],
    edition: str,
    dates: List[str],
    price_by_date: Dict[str, Dict[str, Dict[str, Any]]],
    acquisitions: Dict[Tuple[str, str, str], Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Builds chronological daily price series and return metrics for one WNC Iconic edition.
    Treats $0.00/missing scrapes as absent, establishes baseline on first non-zero price,
    carries the last non-zero price forward, and clamps acquisition markers to priced history.
    """
    is_beta = edition == "beta"
    expansion_name = WNC_BETA_GROUP_NAME if is_beta else WNC_RETAIL_GROUP_NAME
    reverse_tier_map = {"Legends": "IL", "Others": "IO", "Secrets": "IS"}
    trajectories: List[Dict[str, Any]] = []

    sorted_pairs = sorted(
        pairs,
        key=lambda p: (int(p.print_number) if str(p.print_number).isdigit() else 9999, str(p.print_number)),
    )

    for pair in sorted_pairs:
        pid = pair.beta_product_id if is_beta else pair.retail_product_id
        pid_str = str(pid) if pid is not None else ""
        display_pnum = f"B{pair.print_number}" if (is_beta and not str(pair.print_number).startswith("B")) else str(pair.print_number)
        rarity_code = reverse_tier_map.get(pair.tier, "IL")
        card_color = pair.color if pair.color in _ALLOWED_COLORS else "Neutral"
        card_type = pair.card_type or "Unknown"

        clean_points: List[Tuple[str, float]] = []
        last_known_price: Optional[float] = None
        baseline_price: Optional[float] = None
        baseline_date: Optional[str] = None

        for d in dates:
            p_data = price_by_date.get(d, {}).get(pid_str) if pid_str else None
            mp = extract_finish_price(p_data, pair.valid_finish) if p_data is not None else None
            if mp is not None and mp > 0.0:
                if baseline_price is None:
                    baseline_price = mp
                    baseline_date = d
                last_known_price = mp
                clean_points.append((d, mp))
            elif last_known_price is not None:
                clean_points.append((d, last_known_price))

        if not clean_points:
            current_price = 0.0
            price_change_1d = 0.0
            price_change_1d_pct = 0.0
            price_change_7d = 0.0
            price_change_7d_pct = 0.0
            price_change_all = 0.0
            price_change_all_pct = 0.0
            min_price = 0.0
            max_price = 0.0
        else:
            current_price = clean_points[-1][1]
            min_price = min(pt[1] for pt in clean_points)
            max_price = max(pt[1] for pt in clean_points)

            if len(clean_points) >= 2:
                prev_1d = clean_points[-2][1]
                price_change_1d = current_price - prev_1d
                price_change_1d_pct = (price_change_1d / prev_1d * 100.0) if prev_1d > 0 else 0.0
            else:
                price_change_1d = 0.0
                price_change_1d_pct = 0.0

            target_7d_date = (
                datetime.date.fromisoformat(clean_points[-1][0]) - datetime.timedelta(days=7)
            ).isoformat()
            prev_7d = _lookup_point_price(clean_points, target_7d_date)
            price_change_7d = current_price - prev_7d
            price_change_7d_pct = (price_change_7d / prev_7d * 100.0) if prev_7d > 0 else 0.0

            base = baseline_price or current_price
            price_change_all = current_price - base
            price_change_all_pct = (price_change_all / base * 100.0) if base > 0 else 0.0

        owned_qty = pair.beta_owned if is_beta else pair.retail_owned
        acq = acquisitions.get((expansion_name, display_pnum, pair.valid_finish))
        if acq and acq.get("date") and owned_qty > 0:
            earliest_card_date = clean_points[0][0] if clean_points else None
            acq_date = _clamp_date(str(acq["date"]), earliest_card_date)
            lot_entries = acq.get("lot_entries")
            if lot_entries:
                lot_total_qty = sum(q for _, q, _ in lot_entries)
                if lot_total_qty > 0:
                    weighted = 0.0
                    resolved_qty = 0
                    for d, q, lot_db_cost in lot_entries:
                        lot_price = lot_db_cost if lot_db_cost > 0.0 else (_lookup_point_price(clean_points, d) if clean_points else 0.0)
                        if lot_price > 0.0:
                            weighted += lot_price * q
                            resolved_qty += q
                    acq_cost = (weighted / resolved_qty) if resolved_qty > 0 else 0.0
                else:
                    acq_cost = _lookup_point_price(clean_points, acq_date or "") if clean_points else 0.0
            elif acq.get("lots") and clean_points:
                lot_total_qty = sum(q for _, q in acq["lots"])
                if lot_total_qty > 0:
                    weighted = sum(_lookup_point_price(clean_points, d) * q for d, q in acq["lots"])
                    acq_cost = weighted / lot_total_qty
                else:
                    acq_cost = _lookup_point_price(clean_points, acq_date or "")
            else:
                db_cost = float(acq.get("cost") or 0.0)
                acq_cost = db_cost if db_cost > 0.0 else (_lookup_point_price(clean_points, acq_date or "") if clean_points else 0.0)

            acq_delta_dollar = current_price - acq_cost if current_price > 0 else 0.0
            acq_delta_pct = (acq_delta_dollar / acq_cost * 100.0) if (acq_cost > 0 and current_price > 0) else 0.0
            has_acq = True
        else:
            acq_date = None
            acq_cost = 0.0
            acq_delta_dollar = 0.0
            acq_delta_pct = 0.0
            has_acq = False

        trajectories.append({
            "productId": pid_str,
            "name": pair.card_name,
            "printNumber": display_pnum,
            "cleanNumber": str(pair.print_number),
            "rarity": rarity_code,
            "color": card_color,
            "cardType": card_type,
            "finish": pair.valid_finish,
            "currentPrice": current_price,
            "hasPrice": bool(clean_points) and current_price > 0.0,
            "ownedQty": owned_qty,
            "ownedValue": round(owned_qty * current_price, 2) if current_price > 0.0 else 0.0,
            "baselinePrice": baseline_price or 0.0,
            "baselineDate": baseline_date or "",
            "change1d": price_change_1d,
            "change1dPct": price_change_1d_pct,
            "change7d": price_change_7d,
            "change7dPct": price_change_7d_pct,
            "changeAll": price_change_all,
            "changeAllPct": price_change_all_pct,
            "hasAcquisition": has_acq,
            "acqDate": acq_date,
            "acqCost": acq_cost,
            "acqDeltaDollar": acq_delta_dollar,
            "acqDeltaPct": acq_delta_pct,
            "minPrice": min_price,
            "maxPrice": max_price,
            "points": clean_points,
        })

    return trajectories


def generate_svg_chart(
    points: List[Tuple[str, float]],
    card_id: str = "",
    width: int = 340,
    height: int = 120,
    acq_info: Optional[Dict[str, Any]] = None,
) -> str:
    """Renders a responsive standalone SVG line chart for a series of (date, price) points."""
    if not points or len(points) < 2:
        return (
            f'<svg viewBox="0 0 {width} {height}" class="chart-svg" xmlns="http://www.w3.org/2000/svg">'
            f'<text x="{width/2}" y="{height/2}" text-anchor="middle" fill="#6e7681" font-size="12">'
            'Insufficient historical data</text></svg>'
        )

    padding_top = 16
    padding_bottom = 22
    padding_left = 42
    padding_right = 16

    plot_w = width - padding_left - padding_right
    plot_h = height - padding_top - padding_bottom

    prices = [p[1] for p in points]
    p_min = min(prices)
    p_max = max(prices)

    if p_max == p_min:
        y_min = max(0.0, p_min - 1.0)
        y_max = p_max + 1.0
    else:
        pad = (p_max - p_min) * 0.12
        y_min = max(0.0, p_min - pad)
        y_max = p_max + pad

    y_range = y_max - y_min if y_max > y_min else 1.0
    n = len(points)

    def get_coords(idx: int, val: float) -> Tuple[float, float]:
        x = padding_left + (idx / (n - 1)) * plot_w
        y = padding_top + (1.0 - ((val - y_min) / y_range)) * plot_h
        return round(x, 1), round(y, 1)

    coords = [get_coords(i, pt[1]) for i, pt in enumerate(points)]
    polyline_pts = " ".join(f"{x},{y}" for x, y in coords)

    first_p = points[0][1]
    last_p = points[-1][1]
    if last_p > first_p * 1.01:
        stroke_color = "#3fb950"
        gradient_stop = "#3fb950"
    elif last_p < first_p * 0.99:
        stroke_color = "#f85149"
        gradient_stop = "#f85149"
    else:
        stroke_color = "#58a6ff"
        gradient_stop = "#58a6ff"

    first_x, _ = coords[0]
    last_x, _ = coords[-1]
    bottom_y = height - padding_bottom
    area_pts = f"{polyline_pts} {last_x},{bottom_y} {first_x},{bottom_y}"

    start_year = points[0][0][:4]
    end_year = points[-1][0][:4]
    multi_year = start_year != end_year

    def fmt_date(d_str: str) -> str:
        parts = d_str.split("-")
        if len(parts) == 3:
            if multi_year:
                return f"{parts[1]}/{parts[2]}/{parts[0][2:4]}"
            return f"{parts[1]}/{parts[2]}"
        return d_str

    start_date_label = fmt_date(points[0][0])
    end_date_label = fmt_date(points[-1][0])

    acq_idx: Optional[int] = None
    acq_d: Optional[str] = None
    if acq_info and acq_info.get("date"):
        acq_d = str(acq_info["date"])
        for i, (d, _) in enumerate(points):
            if d == acq_d:
                acq_idx = i
                break
            elif d > acq_d and i > 0 and acq_idx is None:
                acq_idx = i - 1
        if acq_idx is None and acq_d <= points[0][0]:
            acq_idx = 0

    safe_card_id = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in card_id)
    grad_id = f"grad_{safe_card_id}" if safe_card_id else f"grad_{abs(hash(polyline_pts)) % 1000000}"
    is_dense = len(points) > 20
    svg_class = "chart-svg is-dense" if is_dense else "chart-svg"

    y_top_label = math.ceil(p_max)
    y_bot_label = math.floor(p_min)
    if y_top_label == y_bot_label:
        y_top_label += 1
        y_bot_label = max(0, y_bot_label - 1)

    svg_parts = [
        f'<svg viewBox="0 0 {width} {height}" class="{svg_class}" xmlns="http://www.w3.org/2000/svg">',
        "<defs>",
        f'  <linearGradient id="{grad_id}" x1="0" y1="0" x2="0" y2="1">',
        f'    <stop class="chart-grad-stop" offset="0%" stop-color="{gradient_stop}" stop-opacity="0.15" />',
        f'    <stop class="chart-grad-stop" offset="100%" stop-color="{gradient_stop}" stop-opacity="0.0" />',
        "  </linearGradient>",
        "</defs>",
        f'<line x1="{padding_left}" y1="{padding_top}" x2="{width - padding_right}" y2="{padding_top}" stroke="#21262d" stroke-dasharray="3,3" />',
        f'<line x1="{padding_left}" y1="{bottom_y}" x2="{width - padding_right}" y2="{bottom_y}" stroke="#30363d" />',
        f'<text x="{padding_left - 6}" y="{padding_top + 4}" fill="#8b949e" font-size="10" text-anchor="end">${y_top_label}</text>',
        f'<text x="{padding_left - 6}" y="{bottom_y - 2}" fill="#8b949e" font-size="10" text-anchor="end">${y_bot_label}</text>',
    ]

    acq_x = None
    if acq_idx is not None and 0 <= acq_idx < len(coords):
        acq_x, _ = coords[acq_idx]
        svg_parts.append(
            f'<line x1="{acq_x}" y1="{padding_top}" x2="{acq_x}" y2="{bottom_y}" stroke="#d29922" stroke-width="1" stroke-dasharray="2,2" opacity="0.45" />'
        )

    if acq_idx is not None and acq_x is not None and acq_d:
        acq_label = f"{acq_d[5:7]}/{acq_d[8:10]}/{acq_d[2:4]}" if len(acq_d) == 10 else html.escape(acq_d)
        left_bound = padding_left
        right_bound = width - padding_right
        min_clearance = 64

        if (acq_x - left_bound) < min_clearance:
            if acq_x < left_bound + 26:
                svg_parts.append(
                    f'<text x="{left_bound}" y="{height - 6}" fill="#d29922" font-size="10" font-family="monospace" font-weight="600" text-anchor="start">{acq_label}</text>'
                )
            else:
                svg_parts.append(
                    f'<text x="{acq_x}" y="{height - 6}" fill="#d29922" font-size="10" font-family="monospace" font-weight="600" text-anchor="middle">{acq_label}</text>'
                )
            svg_parts.append(
                f'<text x="{right_bound}" y="{height - 6}" fill="#8b949e" font-size="10" text-anchor="end">{html.escape(end_date_label)}</text>'
            )
        elif (right_bound - acq_x) < min_clearance:
            svg_parts.append(
                f'<text x="{left_bound}" y="{height - 6}" fill="#8b949e" font-size="10" text-anchor="start">{html.escape(start_date_label)}</text>'
            )
            if acq_x > right_bound - 26:
                svg_parts.append(
                    f'<text x="{right_bound}" y="{height - 6}" fill="#d29922" font-size="10" font-family="monospace" font-weight="600" text-anchor="end">{acq_label}</text>'
                )
            else:
                svg_parts.append(
                    f'<text x="{acq_x}" y="{height - 6}" fill="#d29922" font-size="10" font-family="monospace" font-weight="600" text-anchor="middle">{acq_label}</text>'
                )
        else:
            svg_parts.append(
                f'<text x="{left_bound}" y="{height - 6}" fill="#8b949e" font-size="10" text-anchor="start">{html.escape(start_date_label)}</text>'
            )
            svg_parts.append(
                f'<text x="{acq_x}" y="{height - 6}" fill="#d29922" font-size="10" font-family="monospace" font-weight="600" text-anchor="middle">{acq_label}</text>'
            )
            svg_parts.append(
                f'<text x="{right_bound}" y="{height - 6}" fill="#8b949e" font-size="10" text-anchor="end">{html.escape(end_date_label)}</text>'
            )
    else:
        svg_parts.append(
            f'<text x="{padding_left}" y="{height - 6}" fill="#8b949e" font-size="10" text-anchor="start">{html.escape(start_date_label)}</text>'
        )
        svg_parts.append(
            f'<text x="{width - padding_right}" y="{height - 6}" fill="#8b949e" font-size="10" text-anchor="end">{html.escape(end_date_label)}</text>'
        )

    svg_parts.append(f'<polygon points="{area_pts}" fill="url(#{grad_id})" />')
    svg_parts.append(
        f'<polyline class="chart-line" points="{polyline_pts}" fill="none" stroke="{stroke_color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />'
    )

    for i, ((x, y), (d, val)) in enumerate(zip(coords, points)):
        is_acq = i == acq_idx
        if is_acq and acq_info:
            acq_cost_val = acq_info.get("cost", 0.0)
            acq_attr = f' data-acq="true" data-acq-cost="${acq_cost_val:.2f}"'
            svg_parts.append(
                f'<circle cx="{x}" cy="{y}" r="5.5" fill="#d29922" fill-opacity="0.25" class="acq-dot-halo" />'
                f'<circle cx="{x}" cy="{y}" r="3.5" fill="#d29922" stroke="#0d1117" stroke-width="1.5" class="acq-dot-core" />'
            )
        else:
            acq_attr = ""
            svg_parts.append(
                f'<circle cx="{x}" cy="{y}" r="2.5" class="chart-dot" fill="#0d1117" stroke="{stroke_color}" stroke-width="1.5" />'
            )
        svg_parts.append(
            f'<circle cx="{x}" cy="{y}" r="9" class="chart-hit-point" fill="transparent" data-date="{html.escape(d)}" data-price="${val:.2f}"{acq_attr} style="cursor: crosshair; pointer-events: all;" />'
        )

    svg_parts.append("</svg>")
    return "".join(svg_parts)


def _fmt_currency_delta(val: float, include_plus: bool = True, decimals: int = 2) -> str:
    if val > 0:
        return f"+${val:,.{decimals}f}" if include_plus else f"${val:,.{decimals}f}"
    if val < 0:
        return f"-${abs(val):,.{decimals}f}"
    return f"${0:.{decimals}f}"


def _fmt_quant_stat(val: Optional[float]) -> str:
    return f"${val:,.2f}" if val is not None else "N/A"


def _compute_tier_stats(card_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    prices = [c["currentPrice"] for c in card_list if c["currentPrice"] > 0]
    q = compute_quantiles(prices)
    owned_count = sum(1 for c in card_list if c.get("ownedQty", 0) > 0)
    owned_qty = sum(c.get("ownedQty", 0) for c in card_list)
    owned_val = sum(c.get("ownedValue", 0.0) for c in card_list)
    return {
        "min": _fmt_quant_stat(q[0]),
        "p25": _fmt_quant_stat(q[1]),
        "med": _fmt_quant_stat(q[2]),
        "p75": _fmt_quant_stat(q[3]),
        "max": _fmt_quant_stat(q[4]),
        "owned_count": owned_count,
        "owned_qty": owned_qty,
        "owned_val": owned_val,
    }


_DASHBOARD_CSS = """    :root {
      --bg: #0d1117;
      --card-bg: #161b22;
      --card-border: #30363d;
      --card-hover: #1f242c;
      --text: #d0d7de;
      --text-muted: #8b949e;
      --accent: #58a6ff;
      --green: #3fb950;
      --red: #f85149;
      --yellow: #d29922;
      --purple: #bc8cff;
      --font-mono: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, Courier, monospace;
      --sticky-nav-height: 128px;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      padding: 24px;
      line-height: 1.5;
    }
    .sticky-nav {
      position: sticky;
      top: 0;
      z-index: 100;
      background: rgba(13, 17, 23, 0.94);
      backdrop-filter: blur(10px);
      -webkit-backdrop-filter: blur(10px);
      padding-top: 16px;
      padding-bottom: 16px;
      margin-top: -24px;
      margin-left: -24px;
      margin-right: -24px;
      padding-left: 24px;
      padding-right: 24px;
      border-bottom: 1px solid var(--card-border);
      margin-bottom: 0;
    }
    .header {
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      padding-bottom: 12px;
      margin-bottom: 12px;
    }
    .title-group h1 {
      font-size: 24px;
      font-weight: 600;
      color: var(--text);
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .header-meta {
      text-align: right;
      font-size: 12px;
      color: var(--text-muted);
      line-height: 1.5;
      font-family: var(--font-mono);
      white-space: nowrap;
      min-height: 36px;
      display: flex;
      flex-direction: column;
      justify-content: flex-end;
      align-items: flex-end;
    }
    .edition-toggle {
      display: inline-flex;
      background: #0d1117;
      border: 1px solid var(--card-border);
      border-radius: 6px;
      padding: 2px;
      gap: 2px;
      align-items: center;
      box-sizing: border-box;
    }
    .toggle-btn {
      position: relative;
      background: transparent;
      border: 1px solid transparent;
      color: var(--text-muted);
      font-family: inherit;
      font-size: 13px;
      font-weight: 600;
      padding: 5px 14px;
      border-radius: 4px;
      cursor: pointer;
      line-height: 1.2;
      transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
    }
    .toggle-btn:hover { color: var(--text); }
    .toggle-btn.active {
      background: var(--card-bg);
      color: var(--accent);
      border-color: var(--card-border);
    }
    .toggle-btn[data-tooltip]::after {
      content: attr(data-tooltip);
      position: absolute;
      bottom: calc(100% + 6px);
      left: 50%;
      transform: translateX(-50%);
      background: #1f242c;
      color: #f0f6fc;
      border: 1px solid #30363d;
      border-radius: 6px;
      padding: 6px 10px;
      font-size: 11px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-weight: normal;
      line-height: 1.35;
      white-space: normal;
      width: 200px;
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5);
      pointer-events: none;
      opacity: 0;
      visibility: hidden;
      transition: opacity 0.1s ease, visibility 0.1s ease;
      z-index: 200;
    }
    .toggle-btn[data-tooltip]:hover::after {
      opacity: 1;
      visibility: visible;
    }
    .tier-section { margin-bottom: 36px; }
    .tier-header {
      position: sticky;
      top: var(--sticky-nav-height, 128px);
      z-index: 50;
      display: flex;
      align-items: center;
      gap: 14px;
      flex-wrap: wrap;
      background: rgba(13, 17, 23, 0.95);
      backdrop-filter: blur(8px);
      -webkit-backdrop-filter: blur(8px);
      margin-left: -24px;
      margin-right: -24px;
      padding-left: 24px;
      padding-right: 24px;
      padding-top: 12px;
      padding-bottom: 12px;
      min-height: 64px;
      box-sizing: border-box;
      margin-bottom: 12px;
      border-bottom: 1px solid var(--card-border);
      will-change: transform;
    }
    .tier-title {
      font-size: 18px;
      font-weight: 600;
      color: var(--text);
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .text-green { color: var(--green); }
    .text-red { color: var(--red); }
    .controls {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      padding: 12px 16px;
      margin-bottom: 0;
      display: flex;
      flex-wrap: wrap;
      gap: 16px;
      align-items: center;
    }
    .control-cluster {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }
    .control-cluster.flex-grow { flex: 1 1 auto; }
    .cluster-label {
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      color: var(--text-muted);
      letter-spacing: 0.5px;
      user-select: none;
    }
    .cluster-divider {
      width: 1px;
      height: 24px;
      background: var(--card-border);
      align-self: center;
    }
    .count-badge {
      font-size: 13px;
      color: #8b949e;
      white-space: nowrap;
      margin-left: 4px;
    }
    .reset-btn {
      background: transparent;
      border: 1px solid var(--card-border);
      color: var(--text-muted);
      font-size: 12px;
      font-family: var(--font-mono);
      padding: 4px 10px;
      border-radius: 6px;
      cursor: pointer;
      transition: opacity 0.15s ease, visibility 0.15s ease, background 0.15s ease, border-color 0.15s ease;
      display: inline-flex;
      align-items: center;
      height: 28px;
      margin-left: 6px;
      opacity: 0;
      visibility: hidden;
      pointer-events: none;
    }
    .reset-btn.is-active {
      opacity: 1;
      visibility: visible;
      pointer-events: auto;
    }
    .reset-btn.is-active:hover {
      background: var(--card-hover);
      color: var(--text);
      border-color: var(--text-muted);
    }
    .search-box {
      flex: 1 1 240px;
      background: #0d1117;
      border: 1px solid var(--card-border);
      border-radius: 6px;
      padding: 7px 12px;
      color: var(--text);
      font-family: inherit;
      font-size: 13px;
      line-height: 1.4;
      outline: none;
      transition: border-color 0.15s ease, box-shadow 0.15s ease;
    }
    .search-box:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 2px rgba(88, 166, 255, 0.2);
    }
    .filter-select {
      appearance: none;
      -webkit-appearance: none;
      -moz-appearance: none;
      background-color: #0d1117;
      background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12' fill='none'%3E%3Cpath d='M2.5 4.5L6 8L9.5 4.5' stroke='%238b949e' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
      background-repeat: no-repeat;
      background-position: right 10px center;
      border: 1px solid var(--card-border);
      border-radius: 6px;
      padding: 7px 30px 7px 12px;
      color: var(--text);
      font-family: inherit;
      font-size: 13px;
      line-height: 1.4;
      outline: none;
      cursor: pointer;
      transition: border-color 0.15s ease, box-shadow 0.15s ease;
    }
    .filter-select:hover { border-color: #3b4252; }
    .filter-select:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 2px rgba(88, 166, 255, 0.2);
    }
    .multiselect { position: relative; display: inline-block; }
    .multiselect-btn {
      appearance: none;
      -webkit-appearance: none;
      -moz-appearance: none;
      background-color: #0d1117;
      background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12' fill='none'%3E%3Cpath d='M2.5 4.5L6 8L9.5 4.5' stroke='%238b949e' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
      background-repeat: no-repeat;
      background-position: right 10px center;
      border: 1px solid var(--card-border);
      border-radius: 6px;
      padding: 7px 30px 7px 12px;
      color: var(--text);
      font-family: inherit;
      font-size: 13px;
      line-height: 1.4;
      cursor: pointer;
      text-align: left;
      white-space: nowrap;
      transition: border-color 0.15s ease, box-shadow 0.15s ease;
      display: inline-flex;
      align-items: center;
    }
    .multiselect-btn:hover { border-color: #3b4252; }
    .multiselect-btn.open, .multiselect-btn:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 2px rgba(88, 166, 255, 0.2);
    }
    .filter-count {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      background: #30363d;
      color: #f0f6fc;
      font-family: var(--font-mono);
      font-size: 11px;
      font-weight: 600;
      border-radius: 9px;
      padding: 0 6px;
      height: 18px;
      line-height: 1;
      margin-left: 6px;
    }
    .multiselect-menu {
      display: none;
      position: absolute;
      top: calc(100% + 4px);
      left: 0;
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 6px;
      padding: 6px 0;
      min-width: 170px;
      box-shadow: 0 4px 14px rgba(0, 0, 0, 0.6);
      z-index: 1000;
      user-select: none;
    }
    .multiselect-menu.open { display: block; }
    .multiselect-item {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 6px 12px;
      font-size: 13px;
      color: var(--text);
      cursor: pointer;
      transition: background 0.12s ease;
    }
    .multiselect-item:hover { background: var(--card-hover); }
    .multiselect-item input[type="checkbox"] {
      cursor: pointer;
      accent-color: var(--accent);
      margin: 0;
    }
    .multiselect-item-label {
      flex: 1;
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
      gap: 16px;
    }
    .card-tile {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      padding: 16px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      transition: border-color 0.15s ease, background 0.15s ease;
    }
    .card-tile:hover {
      border-color: #3b4252;
      background: var(--card-hover);
    }
    .card-header {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      margin-bottom: 8px;
    }
    .card-num {
      font-family: var(--font-mono);
      font-size: 12px;
      font-weight: 600;
      color: var(--text-muted);
    }
    .card-name {
      font-size: 15px;
      font-weight: 600;
      color: var(--text);
      margin-bottom: 12px;
      display: flex;
      align-items: center;
      gap: 8px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .card-link {
      color: var(--text);
      text-decoration: none;
      transition: color 0.15s ease;
    }
    .card-link:hover { color: var(--accent); text-decoration: underline; }
    .ext-icon { font-size: 11px; color: var(--text-muted); margin-left: 3px; }
    .card-link:hover .ext-icon { color: var(--accent); }
    .price-block { margin-bottom: 12px; font-family: var(--font-mono); }
    .price-row-main {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
    }
    .price-row-sub {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      margin-top: 5px;
      font-size: 12px;
      color: var(--text-muted);
    }
    .price-sub-val { color: #c9d1d9; font-weight: 500; }
    .acq-base-label {
      position: relative;
      text-decoration: underline dotted var(--text-muted);
      cursor: help;
    }
    .acq-base-label::after {
      content: attr(data-tooltip);
      position: absolute;
      bottom: calc(100% + 6px);
      left: 0;
      background: #1f242c;
      color: #f0f6fc;
      border: 1px solid #30363d;
      border-radius: 6px;
      padding: 6px 10px;
      font-size: 11px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-weight: normal;
      line-height: 1.35;
      white-space: normal;
      width: 220px;
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5);
      pointer-events: none;
      opacity: 0;
      visibility: hidden;
      transition: opacity 0.1s ease, visibility 0.1s ease;
      z-index: 200;
    }
    .acq-base-label:hover::after { opacity: 1; visibility: visible; }
    .acq-dot-halo, .acq-dot-core { pointer-events: none; }
    .price-main { font-size: 22px; font-weight: 600; font-family: var(--font-mono); }
    .price-change { font-size: 12px; font-family: var(--font-mono); font-weight: 600; }
    .metric-l7d, .metric-sub {
      padding: 1px 5px;
      border-radius: 4px;
      display: inline-block;
      line-height: 1.3;
      transition: background 0.12s ease;
    }
    .metric-active { background: rgba(255, 255, 255, 0.08); }
    .chart-container {
      width: 100%;
      height: 120px;
      background: #0d1117;
      border: 1px solid var(--card-border);
      border-radius: 6px;
      overflow: hidden;
      margin-top: auto;
    }
    .chart-svg { width: 100%; height: 100%; display: block; }
    .color-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      display: inline-block;
      flex-shrink: 0;
    }
    .color-Yellow { background: #d29922; }
    .color-Green { background: #3fb950; }
    .color-Blue { background: #58a6ff; }
    .color-Red { background: #f85149; }
    .color-Neutral { background: #8b949e; }
    .chart-tooltip {
      position: fixed;
      display: none;
      pointer-events: none;
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 6px;
      padding: 5px 9px;
      font-size: 11px;
      font-family: var(--font-mono);
      font-weight: 600;
      color: var(--text);
      box-shadow: 0 4px 14px rgba(0, 0, 0, 0.6);
      z-index: 10000;
      white-space: nowrap;
    }
    .chart-dot { transition: r 0.12s ease, opacity 0.12s ease; }
    .chart-svg.is-dense .chart-dot { opacity: 0; }
    .chart-svg.is-dense .chart-dot.is-active { opacity: 1; }
    .chart-hit-point { cursor: crosshair; }
    .tier-stats-group {
      display: inline-flex;
      align-items: stretch;
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 6px;
      height: 40px;
      box-sizing: border-box;
    }
    .tier-stats-tab {
      background: transparent;
      border: none;
      color: var(--text-muted);
      font-family: inherit;
      font-size: 11px;
      font-weight: 600;
      padding: 0 12px;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 5px;
      user-select: none;
      transition: background 0.15s ease, color 0.15s ease;
      white-space: nowrap;
      height: 100%;
      border-radius: 5px;
    }
    .tier-stats-tab:hover, .tier-stats-group:not(.is-collapsed) .tier-stats-tab {
      color: var(--accent);
      background: #21262d;
    }
    .tier-stats-group:not(.is-collapsed) .tier-stats-tab {
      border-right: 1px solid var(--card-border);
      border-top-right-radius: 0;
      border-bottom-right-radius: 0;
    }
    .tier-stats-group.is-collapsed .tier-stats-tab { border-radius: 5px; }
    .tier-stats-content {
      display: flex;
      align-items: center;
      gap: 16px;
      padding: 0 14px;
      height: 100%;
    }
    .stat-cell {
      display: flex;
      flex-direction: column;
      gap: 2px;
      line-height: 1.15;
    }
    .stat-label {
      font-size: 9px;
      font-weight: 700;
      text-transform: uppercase;
      color: var(--text-muted);
      letter-spacing: 0.5px;
    }
    .stat-val {
      font-family: var(--font-mono);
      font-size: 12px;
      font-weight: 600;
      color: var(--text);
    }
    .stat-cell-owned { padding-left: 12px; border-left: 1px solid var(--card-border); }
    .stat-cell-owned .stat-val { color: var(--accent); }
    @media (max-width: 768px) {
      .tier-header {
        flex-direction: column;
        align-items: flex-start;
        gap: 8px;
        min-height: auto;
      }
    }
    .comparison-view {
      max-width: 1180px;
      margin-top: 16px;
      margin-left: auto;
      margin-right: auto;
      margin-bottom: 36px;
    }
    .comp-overview-grid {
      display: grid;
      grid-template-columns: 1.1fr 1.6fr 0.95fr;
      gap: 16px;
      margin-bottom: 24px;
    }
    @media (max-width: 960px) {
      .comp-overview-grid { grid-template-columns: 1fr; }
    }
    .comp-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      padding: 16px;
    }
    .comp-card-distro {
      display: flex;
      flex-direction: column;
      justify-content: center;
    }
    .comp-card-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 6px;
    }
    .comp-card-header .comp-card-title { margin-bottom: 0; }
    .comp-pill-group {
      display: inline-flex;
      background: #0d1117;
      border: 1px solid var(--card-border);
      border-radius: 6px;
      padding: 2px;
      gap: 2px;
    }
    .comp-pill-btn {
      background: transparent;
      border: none;
      color: var(--text-muted);
      font-family: inherit;
      font-size: 10px;
      font-weight: 600;
      padding: 2px 7px;
      border-radius: 4px;
      cursor: pointer;
      transition: all 0.15s ease;
      user-select: none;
      line-height: 1.4;
    }
    .comp-pill-btn:hover { color: var(--text); }
    .comp-pill-btn.is-active { background: #21262d; color: var(--accent); }
    .comp-card-title {
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      color: var(--text-muted);
      margin-bottom: 6px;
      letter-spacing: 0.5px;
    }
    .comp-card-val {
      font-family: var(--font-mono);
      font-size: 18px;
      font-weight: 600;
      color: var(--text);
      margin-bottom: 4px;
    }
    .comp-card-sub {
      font-size: 12px;
      color: var(--text-muted);
      font-family: var(--font-mono);
      line-height: 1.4;
    }
    .comp-distro-table {
      display: flex;
      flex-direction: column;
      gap: 7px;
      margin-top: 0;
    }
    .comp-distro-header, .comp-distro-row {
      display: grid;
      grid-template-columns: 58px repeat(5, 1fr);
      gap: 4px 10px;
      align-items: baseline;
      font-family: var(--font-mono);
      font-size: 13px;
      text-align: right;
    }
    .comp-distro-header {
      font-size: 11px;
      font-weight: 600;
      color: var(--text-muted);
      border-bottom: 1px solid rgba(255, 255, 255, 0.08);
      padding-bottom: 4px;
    }
    .comp-distro-header span:first-child,
    .comp-distro-row span:first-child { text-align: left; }
    .comp-distro-edition { font-weight: 600; font-size: 13px; }
    .comp-distro-beta { color: var(--accent); }
    .comp-distro-retail { color: var(--text-muted); }
    .comp-distro-med-header { color: var(--text); font-weight: 700; }
    .comp-distro-med-val { font-weight: 700; color: #fff; }
    .comp-sub-section { margin-bottom: 28px; }
    .comp-sub-title {
      font-size: 16px;
      font-weight: 600;
      color: var(--text);
      margin-bottom: 12px;
      padding-bottom: 6px;
      border-bottom: 1px solid var(--card-border);
    }
    .comp-table-container {
      overflow-x: auto;
      border-radius: 6px;
      border: 1px solid var(--card-border);
      background: var(--card-bg);
    }
    .comp-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
      text-align: left;
    }
    .comp-table th {
      background: #0d1117;
      padding: 10px 12px;
      font-weight: 600;
      color: var(--text-muted);
      border-bottom: 1px solid var(--card-border);
      white-space: nowrap;
      user-select: none;
    }
    .comp-table th.sortable { cursor: pointer; }
    .comp-table th.sortable:hover { color: var(--text); }
    .comp-table th.sort-asc::after { content: " ▲"; font-size: 10px; }
    .comp-table th.sort-desc::after { content: " ▼"; font-size: 10px; }
    .comp-table td {
      padding: 8px 12px;
      border-bottom: 1px solid #21262d;
      color: var(--text);
      white-space: nowrap;
    }
    .comp-table tr:hover td { background: #1f242c; }
    .comp-table tr:last-child td { border-bottom: none; }
    .comp-table-mono { font-family: var(--font-mono); }
    .comp-table td.comp-premium-pos, .comp-premium-pos { color: var(--green); }
    .comp-table td.comp-premium-neg, .comp-premium-neg { color: var(--red); }
    .comp-card-name { display: inline-flex; align-items: center; gap: 8px; }
    .comp-price-link {
      color: var(--text);
      text-decoration: underline dotted var(--text-muted);
      text-underline-offset: 3px;
      display: inline-flex;
      align-items: center;
      gap: 3px;
      transition: color 0.15s ease, text-decoration-color 0.15s ease;
    }
    .comp-price-link:hover {
      color: var(--accent);
      text-decoration: underline dotted var(--accent);
    }
    .comp-price-link .ext-icon {
      font-size: 10px;
      color: var(--text-muted);
      margin-left: 1px;
      transition: color 0.15s ease;
    }
    .comp-price-link:hover .ext-icon { color: var(--accent); }
    .comp-table tfoot tr {
      background: #11151c;
      border-top: 2px solid #30363d;
      font-weight: 600;
    }
    .comp-table tfoot td {
      padding: 10px 12px;
      border-bottom: none;
      color: var(--text);
      white-space: nowrap;
    }
    html.preload-comparison .tier-section { display: none !important; }
    html.preload-comparison #view-comparison { display: block !important; }
    html.preload-comparison #cluster-basis,
    html.preload-comparison #cluster-basis + .cluster-divider { display: none !important; }
    html.preload-comparison #btn-beta { background: transparent !important; color: var(--text-muted) !important; border-color: transparent !important; }
    html.preload-comparison #btn-comparison { background: var(--card-bg) !important; color: var(--accent) !important; border-color: var(--card-border) !important; }
    html.preload-comparison #meta-snapshots { display: none !important; }
    html.preload-retail .card-tile[data-edition="beta"] { display: none !important; }
    html.preload-retail .card-tile[data-edition="retail"] { display: flex !important; }
    html.preload-retail .edition-stats-beta { display: none !important; }
    html.preload-retail .edition-stats-retail { display: flex !important; }
    html.preload-retail #btn-beta { background: transparent !important; color: var(--text-muted) !important; border-color: transparent !important; }
    html.preload-retail #btn-retail { background: var(--card-bg) !important; color: var(--accent) !important; border-color: var(--card-border) !important; }"""


_DASHBOARD_JS_BODY = r"""    let currentEdition = 'beta';
    let currentAnchor = 'acq';
    let activePricedSetTier = 'all';
    const btnRetail = document.getElementById('btn-retail');
    const btnBeta = document.getElementById('btn-beta');
    const btnComparison = document.getElementById('btn-comparison');
    const compView = document.getElementById('view-comparison');
    const clusterBasis = document.getElementById('cluster-basis');
    const dividerBasis = clusterBasis ? clusterBasis.nextElementSibling : null;
    const btnAnchorLifetime = document.getElementById('btn-anchor-lifetime');
    const btnAnchorAcq = document.getElementById('btn-anchor-acq');
    const btnAnchorL7d = document.getElementById('btn-anchor-l7d');
    const metaSnapshots = document.getElementById('meta-snapshots');
    const searchInput = document.getElementById('search-input');
    const sortSelect = document.getElementById('sort-select');
    const countDisplay = document.getElementById('count-display');
    const tierCodes = ['IL', 'IO', 'IS'];
    const tooltip = document.getElementById('chart-tooltip');
    const STORAGE_KEY = 'wnc_iconics_filter_state';

    function parseNum(raw, fallback) {
      if (raw === undefined || raw === null || raw === '') return fallback;
      const n = Number(raw);
      return Number.isFinite(n) ? n : fallback;
    }

    function compareNullableNums(rawA, rawB, dir) {
      const a = parseNum(rawA, null);
      const b = parseNum(rawB, null);
      if (a === null && b === null) return 0;
      if (a === null) return 1;
      if (b === null) return -1;
      return dir * (a - b);
    }

    function fmtCurrency(val, withCommas = true) {
      return '$' + (withCommas
        ? val.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
        : val.toFixed(2));
    }

    function computeQuantilesJS(values) {
      const n = values.length;
      if (n === 0) return ['N/A', 'N/A', 'N/A', 'N/A', 'N/A'];
      const sorted = [...values].sort((a, b) => a - b);
      const vMin = sorted[0];
      const vMax = sorted[n - 1];
      const vMed = (n % 2 === 1) ? sorted[(n - 1) >> 1] : (sorted[n / 2 - 1] + sorted[n / 2]) / 2;
      if (n < 4) {
        return [fmtCurrency(vMin), 'N/A', fmtCurrency(vMed), 'N/A', fmtCurrency(vMax)];
      }
      const interp = (q) => {
        const idx = q * (n - 1);
        const lo = Math.floor(idx);
        const hi = Math.ceil(idx);
        return sorted[lo] + (idx - lo) * (sorted[hi] - sorted[lo]);
      };
      return [
        fmtCurrency(vMin),
        fmtCurrency(interp(0.25)),
        fmtCurrency(vMed),
        fmtCurrency(interp(0.75)),
        fmtCurrency(vMax)
      ];
    }

    function syncComparisonOverview() {
      const visibleTierRows = Array.from(document.querySelectorAll('tr.comp-row')).filter(row => {
        if (row.style.display === 'none') return false;
        return activePricedSetTier === 'all' || row.dataset.rarity === activePricedSetTier;
      });
      const pairedRows = visibleTierRows.filter(row => row.dataset.paired === '1');
      const titleEl = document.getElementById('set-val-title');
      if (titleEl) {
        titleEl.textContent = (pairedRows.length < visibleTierRows.length)
          ? `Iconics Set Value (${pairedRows.length}/${visibleTierRows.length} pairs)`
          : 'Iconics Set Value';
      }

      const bEl = document.getElementById('set-val-beta');
      const rEl = document.getElementById('set-val-retail');
      const premEl = document.getElementById('set-val-premium');
      const bPrices = [];
      const rPrices = [];

      if (pairedRows.length > 0) {
        let bSum = 0;
        let rSum = 0;
        pairedRows.forEach(row => {
          const bp = parseNum(row.dataset.bPrice, 0);
          const rp = parseNum(row.dataset.rPrice, 0);
          bSum += bp;
          rSum += rp;
          bPrices.push(bp);
          rPrices.push(rp);
        });
        const spDlr = bSum - rSum;
        const spPct = rSum > 0 ? (spDlr / rSum * 100) : 0;
        const sign = spDlr > 0 ? '+' : '';
        const dlrTxt = spDlr > 0 ? `+${fmtCurrency(spDlr)}` : (spDlr < 0 ? `-${fmtCurrency(Math.abs(spDlr))}` : '$0.00');
        const cls = spDlr > 0 ? 'comp-premium-pos' : (spDlr < 0 ? 'comp-premium-neg' : '');
        if (bEl) bEl.textContent = fmtCurrency(bSum);
        if (rEl) rEl.textContent = fmtCurrency(rSum);
        if (premEl) {
          premEl.textContent = `${dlrTxt} (${sign}${spPct.toFixed(1)}%)`;
          premEl.className = cls;
        }
      } else {
        if (bEl) bEl.textContent = 'N/A';
        if (rEl) rEl.textContent = 'N/A';
        if (premEl) {
          premEl.textContent = 'N/A';
          premEl.className = '';
        }
      }

      const bDist = computeQuantilesJS(bPrices);
      const rDist = computeQuantilesJS(rPrices);
      const ids = ['min', 'p25', 'med', 'p75', 'max'];
      ids.forEach((k, idx) => {
        const be = document.getElementById(`dist-b-${k}`);
        const re = document.getElementById(`dist-r-${k}`);
        if (be) be.textContent = bDist[idx];
        if (re) re.textContent = rDist[idx];
      });
    }

    function setPricedSetTier(tier) {
      activePricedSetTier = tier;
      document.querySelectorAll('.comp-pill-btn').forEach(btn => btn.classList.remove('is-active'));
      const activeBtn = document.getElementById(`pill-tier-${tier}`);
      if (activeBtn) activeBtn.classList.add('is-active');
      syncComparisonOverview();
    }

    function toggleTierStats(code) {
      const group = document.getElementById(`stats-group-${code}`);
      const activeBox = (currentEdition === 'retail')
        ? document.getElementById(`stats-retail-${code}`)
        : document.getElementById(`stats-beta-${code}`);
      const btn = document.getElementById(`btn-stats-${code}`);
      if (!activeBox || !group) return;
      const isHidden = activeBox.style.display === 'none' || !activeBox.style.display;
      activeBox.style.display = isHidden ? 'flex' : 'none';
      group.classList.toggle('is-collapsed', !isHidden);
      if (btn) {
        btn.innerHTML = isHidden ? 'Stats <span class="stats-arrow">&#x25C2;</span>' : 'Stats <span class="stats-arrow">&#x25B8;</span>';
      }
    }

    const multiselectCategories = ['color', 'type', 'rarity', 'ownership'];

    multiselectCategories.forEach(cat => {
      const btn = document.getElementById(`btn-${cat}`);
      const menu = document.getElementById(`menu-${cat}`);
      if (!btn || !menu) return;
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const isOpen = menu.classList.contains('open');
        closeAllMenus();
        if (!isOpen) {
          menu.classList.add('open');
          btn.classList.add('open');
        }
      });
      menu.addEventListener('click', (e) => {
        e.stopPropagation();
      });
    });

    function closeAllMenus() {
      document.querySelectorAll('.multiselect-menu').forEach(m => m.classList.remove('open'));
      document.querySelectorAll('.multiselect-btn').forEach(b => b.classList.remove('open'));
    }

    document.addEventListener('click', closeAllMenus);

    function getSelected(cat) {
      const menu = document.getElementById(`menu-${cat}`);
      if (!menu) return [];
      const checked = Array.from(menu.querySelectorAll('input[type="checkbox"]:checked'));
      return checked.map(c => c.value);
    }

    function updateBadge(cat) {
      const vals = getSelected(cat);
      const badge = document.getElementById(`badge-${cat}`);
      if (badge) {
        if (vals.length > 0) {
          badge.textContent = vals.length;
          badge.style.display = 'inline-flex';
        } else {
          badge.style.display = 'none';
        }
      }
    }

    function saveState() {
      try {
        const state = {
          edition: currentEdition,
          anchor: currentAnchor,
          search: searchInput.value,
          rarity: getSelected('rarity'),
          color: getSelected('color'),
          type: getSelected('type'),
          ownership: getSelected('ownership'),
          sort: sortSelect.value
        };
        localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
      } catch (err) {
      }
    }

    function loadState() {
      try {
        const raw = localStorage.getItem(STORAGE_KEY);
        return raw ? JSON.parse(raw) : null;
      } catch (err) {
        return null;
      }
    }

    function setAnchor(anchor, save = true) {
      currentAnchor = anchor;
      if (btnAnchorLifetime) btnAnchorLifetime.classList.toggle('active', anchor === 'lifetime');
      if (btnAnchorAcq) btnAnchorAcq.classList.toggle('active', anchor === 'acq');
      if (btnAnchorL7d) btnAnchorL7d.classList.toggle('active', anchor === 'l7d');

      const allTiles = document.querySelectorAll('.card-tile');
      allTiles.forEach(tile => {
        let val = 0;
        if (anchor === 'lifetime') {
          val = parseFloat(tile.dataset.call) || 0;
        } else if (anchor === 'acq') {
          val = (tile.dataset.hasAcq === '1')
            ? (parseFloat(tile.dataset.cacq) || 0)
            : (parseFloat(tile.dataset.call) || 0);
        } else if (anchor === 'l7d') {
          val = parseFloat(tile.dataset.c7d) || 0;
        }

        const color = val > 0 ? '#3fb950' : (val < 0 ? '#f85149' : '#58a6ff');
        const line = tile.querySelector('.chart-line');
        if (line) line.setAttribute('stroke', color);
        tile.querySelectorAll('.chart-grad-stop').forEach(s => s.setAttribute('stop-color', color));
        tile.querySelectorAll('.chart-dot').forEach(d => d.setAttribute('stroke', color));

        const metricL7d = tile.querySelector('.metric-l7d');
        const metricSub = tile.querySelector('.metric-sub');
        if (metricL7d) metricL7d.classList.toggle('metric-active', anchor === 'l7d');
        if (metricSub) metricSub.classList.toggle('metric-active', anchor === 'acq' || anchor === 'lifetime');
      });

      if (save) saveState();
    }

    function setEdition(edition, save = true) {
      currentEdition = edition;
      const sortNumOption = sortSelect ? sortSelect.querySelector('option[value="num-asc"]') : null;

      if (btnComparison) btnComparison.classList.toggle('active', edition === 'comparison');
      if (btnRetail) btnRetail.classList.toggle('active', edition === 'retail');
      if (btnBeta) btnBeta.classList.toggle('active', edition === 'beta');

      if (edition === 'comparison') {
        if (clusterBasis) clusterBasis.style.display = 'none';
        if (dividerBasis) dividerBasis.style.display = 'none';
        if (metaSnapshots) metaSnapshots.style.display = 'none';
        if (sortNumOption) sortNumOption.innerHTML = 'Print # (141 &rarr; 171)';
        searchInput.placeholder = 'Search by card name or number (e.g. 141, Panam, Judy, Smasher)...';
        if (compView) compView.style.display = 'block';
        tierCodes.forEach(code => {
          const s = document.getElementById(`section-${code}`);
          if (s) s.style.display = 'none';
        });
      } else {
        if (clusterBasis) clusterBasis.style.display = 'inline-flex';
        if (dividerBasis) dividerBasis.style.display = 'block';
        if (metaSnapshots) metaSnapshots.style.display = 'block';
        if (compView) compView.style.display = 'none';
        tierCodes.forEach(code => {
          const s = document.getElementById(`section-${code}`);
          if (s) s.style.display = 'block';
        });

        if (edition === 'retail') {
          if (metaSnapshots) metaSnapshots.textContent = snapshotTemplates.retail;
          if (sortNumOption) sortNumOption.innerHTML = 'Print # (141 &rarr; 171)';
          searchInput.placeholder = 'Search by card name or number (e.g. 141, Panam, Judy, Smasher)...';
          tierCodes.forEach(code => {
            const group = document.getElementById(`stats-group-${code}`);
            const isExpanded = group && !group.classList.contains('is-collapsed');
            const dr = document.getElementById(`stats-retail-${code}`);
            const db = document.getElementById(`stats-beta-${code}`);
            if (dr) dr.style.display = isExpanded ? 'flex' : 'none';
            if (db) db.style.display = 'none';
          });
        } else {
          if (metaSnapshots) metaSnapshots.textContent = snapshotTemplates.beta;
          if (sortNumOption) sortNumOption.innerHTML = 'Print # (B141 &rarr; B171)';
          searchInput.placeholder = 'Search by card name or number (e.g. B141, Panam, Judy, Smasher)...';
          tierCodes.forEach(code => {
            const group = document.getElementById(`stats-group-${code}`);
            const isExpanded = group && !group.classList.contains('is-collapsed');
            const dr = document.getElementById(`stats-retail-${code}`);
            const db = document.getElementById(`stats-beta-${code}`);
            if (dr) dr.style.display = 'none';
            if (db) db.style.display = isExpanded ? 'flex' : 'none';
          });
        }
      }

      applyFilters();
      if (typeof syncClusterDividers === 'function') syncClusterDividers();
      if (save) saveState();
    }

    document.addEventListener('mouseover', (e) => {
      const hit = e.target.closest('.chart-hit-point');
      if (hit) {
        if (hit.dataset.acq) {
          tooltip.textContent = `${hit.dataset.date} (Acq base: ${hit.dataset.acqCost}): ${hit.dataset.price}`;
        } else {
          tooltip.textContent = `${hit.dataset.date}: ${hit.dataset.price}`;
        }
        tooltip.style.display = 'block';
        const dot = hit.previousElementSibling;
        if (dot && dot.classList.contains('chart-dot')) {
          dot.setAttribute('r', '4.5');
          dot.classList.add('is-active');
        }
      }
    });

    document.addEventListener('mousemove', (e) => {
      if (tooltip && tooltip.style.display === 'block') {
        let left = e.clientX + 12;
        let top = e.clientY - 32;
        if (top < 10) top = e.clientY + 16;
        if (left + 160 > window.innerWidth) left = e.clientX - 150;
        tooltip.style.left = left + 'px';
        tooltip.style.top = top + 'px';
      }
    });

    document.addEventListener('mouseout', (e) => {
      const hit = e.target.closest('.chart-hit-point');
      if (hit) {
        if (tooltip) tooltip.style.display = 'none';
        const dot = hit.previousElementSibling;
        if (dot && dot.classList.contains('chart-dot')) {
          dot.setAttribute('r', '2.5');
          dot.classList.remove('is-active');
        }
      }
    });

    function applyFilters() {
      const q = searchInput.value.trim().toLowerCase();
      const r = getSelected('rarity');
      const c = getSelected('color');
      const t = getSelected('type');
      const o = getSelected('ownership');
      const sort = sortSelect.value;

      multiselectCategories.forEach(cat => updateBadge(cat));

      let totalVisible = 0;

      if (currentEdition === 'comparison') {
        const rows = document.querySelectorAll('tr.comp-row');
        const sectionCounts = { 'IL': 0, 'IO': 0, 'IS': 0 };
        const sectionTotals = {
          'IL': { bPrice: 0, rPrice: 0, pairedCount: 0, visibleCount: 0, bOwned: 0, rOwned: 0 },
          'IO': { bPrice: 0, rPrice: 0, pairedCount: 0, visibleCount: 0, bOwned: 0, rOwned: 0 },
          'IS': { bPrice: 0, rPrice: 0, pairedCount: 0, visibleCount: 0, bOwned: 0, rOwned: 0 }
        };

        rows.forEach(row => {
          const name = row.dataset.name;
          const num = row.dataset.num.toLowerCase();
          const cleanNum = num.replace(/^b/, '');
          const rarity = row.dataset.rarity;
          const color = row.dataset.color;
          const cardType = row.dataset.type;
          const isOwned = (parseInt(row.dataset.ownedB) || 0) > 0 || (parseInt(row.dataset.ownedR) || 0) > 0;

          const matchesQ = !q || name.includes(q) || num.includes(q) || cleanNum.includes(q);
          const matchesR = r.length === 0 || r.includes(rarity);
          const matchesC = c.length === 0 || c.includes(color);
          const matchesT = t.length === 0 || t.includes(cardType);
          const matchesO = o.length === 0 || (o.includes('owned') && isOwned) || (o.includes('unowned') && !isOwned);

          if (matchesQ && matchesR && matchesC && matchesT && matchesO) {
            row.style.display = '';
            totalVisible++;
            if (sectionCounts[rarity] !== undefined) {
              sectionCounts[rarity]++;
              sectionTotals[rarity].visibleCount++;
              if (row.dataset.paired === '1') {
                sectionTotals[rarity].bPrice += parseFloat(row.dataset.bPrice) || 0;
                sectionTotals[rarity].rPrice += parseFloat(row.dataset.rPrice) || 0;
                sectionTotals[rarity].pairedCount++;
              }
              sectionTotals[rarity].bOwned += parseInt(row.dataset.ownedB) || 0;
              sectionTotals[rarity].rOwned += parseInt(row.dataset.ownedR) || 0;
            }
          } else {
            row.style.display = 'none';
          }
        });

        tierCodes.forEach(code => {
          const sub = document.getElementById(`comp-sub-${code}`);
          if (sub) sub.style.display = (sectionCounts[code] > 0) ? 'block' : 'none';
          const tot = sectionTotals[code];
          const labelEl = document.getElementById(`total-label-${code}`);
          const bPriceEl = document.getElementById(`total-b-price-${code}`);
          const rPriceEl = document.getElementById(`total-r-price-${code}`);
          const spDlrEl = document.getElementById(`total-sp-dlr-${code}`);
          const spPctEl = document.getElementById(`total-sp-pct-${code}`);
          const bOwnedEl = document.getElementById(`total-b-owned-${code}`);
          const rOwnedEl = document.getElementById(`total-r-owned-${code}`);

          if (tot && bPriceEl && rPriceEl && spDlrEl && spPctEl && bOwnedEl && rOwnedEl) {
            if (labelEl) {
              labelEl.textContent = (tot.pairedCount < tot.visibleCount)
                ? `Total (${tot.pairedCount}/${tot.visibleCount} pairs)`
                : 'Total';
            }
            spDlrEl.classList.remove('comp-premium-pos', 'comp-premium-neg');
            spPctEl.classList.remove('comp-premium-pos', 'comp-premium-neg');
            if (tot.pairedCount > 0) {
              bPriceEl.textContent = `$${tot.bPrice.toFixed(2)}`;
              rPriceEl.textContent = `$${tot.rPrice.toFixed(2)}`;
              const spDlr = tot.bPrice - tot.rPrice;
              const spPct = tot.rPrice > 0 ? (spDlr / tot.rPrice * 100) : 0;
              let dlrStr = '$0.00';
              if (spDlr > 0) dlrStr = `+$${spDlr.toFixed(2)}`;
              else if (spDlr < 0) dlrStr = `-$${Math.abs(spDlr).toFixed(2)}`;
              spDlrEl.textContent = dlrStr;
              spPctEl.textContent = `${spDlr > 0 ? '+' : ''}${spPct.toFixed(1)}%`;
              if (spDlr > 0) {
                spDlrEl.classList.add('comp-premium-pos');
                spPctEl.classList.add('comp-premium-pos');
              } else if (spDlr < 0) {
                spDlrEl.classList.add('comp-premium-neg');
                spPctEl.classList.add('comp-premium-neg');
              }
            } else {
              bPriceEl.textContent = 'N/A';
              rPriceEl.textContent = 'N/A';
              spDlrEl.textContent = 'N/A';
              spPctEl.textContent = 'N/A';
            }
            bOwnedEl.textContent = tot.bOwned;
            rOwnedEl.textContent = tot.rOwned;
          }
        });

        tierCodes.forEach(code => {
          const table = document.getElementById(`table-${code}`);
          if (!table) return;
          const tbody = table.querySelector('tbody');
          if (!tbody) return;
          const tableRows = Array.from(tbody.querySelectorAll('tr.comp-row'));
          tableRows.sort((a, b) => {
            if (sort === 'price-desc') return compareNullableNums(a.dataset.bPrice, b.dataset.bPrice, -1);
            if (sort === 'price-asc') return compareNullableNums(a.dataset.bPrice, b.dataset.bPrice, 1);
            if (sort === 'gain-desc' || sort === 'gain-all-desc' || sort === 'gain-acq-desc') {
              return compareNullableNums(a.dataset.spreadPct, b.dataset.spreadPct, -1);
            }
            return (parseInt(a.dataset.num) || 0) - (parseInt(b.dataset.num) || 0);
          });
          tableRows.forEach(row => tbody.appendChild(row));
        });

        syncComparisonOverview();
        countDisplay.textContent = `Showing ${totalVisible} of ${totalCardCount} cards`;
        const hasActiveFilters = Boolean(q || r.length > 0 || c.length > 0 || t.length > 0 || o.length > 0);
        const resetBtn = document.getElementById('btn-reset-filters');
        if (resetBtn) resetBtn.classList.toggle('is-active', hasActiveFilters);
        if (typeof syncTierHeaderStickyBounds === 'function') syncTierHeaderStickyBounds();
        return;
      }

      tierCodes.forEach(code => {
        const sectionElem = document.getElementById(`section-${code}`);
        const gridElem = document.getElementById(`grid-${code}`);
        if (!sectionElem || !gridElem) return;

        const tiles = Array.from(gridElem.getElementsByClassName('card-tile'));
        let sectionVisible = 0;

        tiles.forEach(tile => {
          const edition = tile.dataset.edition;
          if (edition !== currentEdition) {
            tile.style.display = 'none';
            return;
          }

          const name = tile.dataset.name;
          const num = tile.dataset.num.toLowerCase();
          const cleanNum = num.replace(/^b/, '');
          const rarity = tile.dataset.rarity;
          const color = tile.dataset.color;
          const cardType = tile.dataset.type;
          const isOwned = (parseInt(tile.dataset.ownedQty, 10) || 0) > 0;

          const matchesQ = !q || name.includes(q) || num.includes(q) || cleanNum.includes(q);
          const matchesR = r.length === 0 || r.includes(rarity);
          const matchesC = c.length === 0 || c.includes(color);
          const matchesT = t.length === 0 || t.includes(cardType);
          const matchesO = o.length === 0 || (o.includes('owned') && isOwned) || (o.includes('unowned') && !isOwned);

          if (matchesQ && matchesR && matchesC && matchesT && matchesO) {
            tile.style.display = 'flex';
            sectionVisible++;
            totalVisible++;
          } else {
            tile.style.display = 'none';
          }
        });

        sectionElem.style.display = sectionVisible > 0 ? 'block' : 'none';

        const activeTiles = tiles.filter(tile => tile.dataset.edition === currentEdition);
        activeTiles.sort((a, b) => {
          if (sort === 'price-desc') {
            const cmp = compareNullableNums(a.dataset.price, b.dataset.price, -1);
            if (cmp !== 0) return cmp;
          } else if (sort === 'price-asc') {
            const cmp = compareNullableNums(a.dataset.price, b.dataset.price, 1);
            if (cmp !== 0) return cmp;
          } else if (sort === 'gain-desc') {
            const cmp = compareNullableNums(a.dataset.c7d, b.dataset.c7d, -1);
            if (cmp !== 0) return cmp;
          } else if (sort === 'gain-all-desc') {
            const cmp = compareNullableNums(a.dataset.call, b.dataset.call, -1);
            if (cmp !== 0) return cmp;
          } else if (sort === 'gain-acq-desc') {
            const hasA = a.dataset.hasAcq === '1';
            const hasB = b.dataset.hasAcq === '1';
            if (hasA && !hasB) return -1;
            if (!hasA && hasB) return 1;
            if (hasA && hasB) {
              const cmp = compareNullableNums(a.dataset.cacq, b.dataset.cacq, -1);
              if (cmp !== 0) return cmp;
            }
          }
          return (parseInt(a.dataset.num.replace(/\D/g, '')) || 0) - (parseInt(b.dataset.num.replace(/\D/g, '')) || 0);
        });

        activeTiles.forEach(tile => gridElem.appendChild(tile));
      });

      countDisplay.textContent = `Showing ${totalVisible} of ${totalCardCount} cards`;
      const hasActiveFilters = Boolean(q || r.length > 0 || c.length > 0 || t.length > 0 || o.length > 0);
      const resetBtn = document.getElementById('btn-reset-filters');
      if (resetBtn) resetBtn.classList.toggle('is-active', hasActiveFilters);
      if (typeof syncTierHeaderStickyBounds === 'function') syncTierHeaderStickyBounds();
    }

    function resetAllFilters() {
      searchInput.value = '';
      multiselectCategories.forEach(cat => {
        const menu = document.getElementById(`menu-${cat}`);
        if (menu) menu.querySelectorAll('input[type="checkbox"]').forEach(box => { box.checked = false; });
      });
      applyFilters();
      saveState();
    }

    function onFilterChange() {
      applyFilters();
      saveState();
    }

    searchInput.addEventListener('input', onFilterChange);
    document.querySelectorAll('.multiselect-item input[type="checkbox"]').forEach(box => {
      box.addEventListener('change', onFilterChange);
    });
    sortSelect.addEventListener('change', onFilterChange);

    function initFilters() {
      const saved = loadState();
      if (saved) {
        if (typeof saved.search === 'string') searchInput.value = saved.search;
        multiselectCategories.forEach(cat => {
          const vals = Array.isArray(saved[cat]) ? saved[cat] : (typeof saved[cat] === 'string' && saved[cat] ? [saved[cat]] : []);
          const menu = document.getElementById(`menu-${cat}`);
          if (menu) {
            menu.querySelectorAll('input[type="checkbox"]').forEach(box => {
              box.checked = vals.includes(box.value);
            });
          }
          updateBadge(cat);
        });
        if (typeof saved.sort === 'string') sortSelect.value = saved.sort;
        const targetEdition = (saved.edition === 'retail' || saved.edition === 'beta' || saved.edition === 'comparison') ? saved.edition : 'beta';
        setEdition(targetEdition, false);
        const targetAnchor = (saved.anchor === 'lifetime' || saved.anchor === 'acq' || saved.anchor === 'l7d') ? saved.anchor : 'acq';
        setAnchor(targetAnchor, false);
      } else {
        setEdition('beta', false);
        setAnchor('acq', false);
      }
      const isWideViewport = window.innerWidth >= 768;
      tierCodes.forEach(code => {
        const group = document.getElementById(`stats-group-${code}`);
        const btn = document.getElementById(`btn-stats-${code}`);
        const activeBox = (currentEdition === 'retail')
          ? document.getElementById(`stats-retail-${code}`)
          : document.getElementById(`stats-beta-${code}`);
        const otherBox = (currentEdition === 'retail')
          ? document.getElementById(`stats-beta-${code}`)
          : document.getElementById(`stats-retail-${code}`);
        if (otherBox) otherBox.style.display = 'none';
        if (activeBox) activeBox.style.display = isWideViewport ? 'flex' : 'none';
        if (group) group.classList.toggle('is-collapsed', !isWideViewport);
        if (btn) {
          btn.innerHTML = isWideViewport ? 'Stats <span class="stats-arrow">&#x25C2;</span>' : 'Stats <span class="stats-arrow">&#x25B8;</span>';
        }
      });
      document.documentElement.classList.remove('preload-comparison', 'preload-retail');
    }

    document.querySelectorAll('.comp-table th.sortable').forEach(th => {
      th.addEventListener('click', () => {
        const table = th.closest('table');
        const tbody = table.querySelector('tbody');
        const rows = Array.from(tbody.querySelectorAll('tr.comp-row'));
        const col = th.dataset.col;
        const isAsc = th.classList.contains('sort-asc');

        table.querySelectorAll('th.sortable').forEach(h => h.classList.remove('sort-asc', 'sort-desc'));
        th.classList.toggle('sort-asc', !isAsc);
        th.classList.toggle('sort-desc', isAsc);
        const dir = isAsc ? -1 : 1;

        rows.sort((a, b) => {
          if (col === 'num') return dir * ((parseInt(a.dataset.num) || 0) - (parseInt(b.dataset.num) || 0));
          if (col === 'name') return dir * a.dataset.name.localeCompare(b.dataset.name);
          if (col === 'type') return dir * a.dataset.type.localeCompare(b.dataset.type);
          if (col === 'b_price') return compareNullableNums(a.dataset.bPrice, b.dataset.bPrice, dir);
          if (col === 'r_price') return compareNullableNums(a.dataset.rPrice, b.dataset.rPrice, dir);
          if (col === 'spread_dlr') return compareNullableNums(a.dataset.spreadDlr, b.dataset.spreadDlr, dir);
          if (col === 'spread_pct') return compareNullableNums(a.dataset.spreadPct, b.dataset.spreadPct, dir);
          if (col === 'b_owned') return dir * ((parseInt(a.dataset.ownedB) || 0) - (parseInt(b.dataset.ownedB) || 0));
          if (col === 'r_owned') return dir * ((parseInt(a.dataset.ownedR) || 0) - (parseInt(b.dataset.ownedR) || 0));
          return 0;
        });

        rows.forEach(r => tbody.appendChild(r));
      });
    });

    function syncClusterDividers() {
      const dividers = document.querySelectorAll('.cluster-divider');
      dividers.forEach(d => { d.style.display = 'block'; });
      dividers.forEach(d => {
        const prev = d.previousElementSibling;
        const next = d.nextElementSibling;
        if (prev && next) {
          const pRect = prev.getBoundingClientRect();
          const dRect = d.getBoundingClientRect();
          const nRect = next.getBoundingClientRect();
          if (Math.abs(pRect.top - dRect.top) > 8 || Math.abs(dRect.top - nRect.top) > 8) {
            d.style.display = 'none';
          }
        }
      });
    }

    function syncTierHeaderStickyBounds() {
      if (currentEdition === 'comparison') {
        tierCodes.forEach(code => {
          const h = document.getElementById(`header-${code}`);
          if (h) h.style.transform = '';
        });
        return;
      }
      const nav = document.querySelector('.sticky-nav');
      const navHeight = nav ? nav.offsetHeight : 128;
      const headerMarginBottom = 12;

      tierCodes.forEach(code => {
        const header = document.getElementById(`header-${code}`);
        const grid = document.getElementById(`grid-${code}`);
        if (!header || !grid) return;

        const visibleCards = Array.from(grid.querySelectorAll('.card-tile')).filter(c => c.style.display !== 'none');
        if (visibleCards.length === 0) {
          header.style.transform = '';
          return;
        }

        const lastCardRect = visibleCards[visibleCards.length - 1].getBoundingClientRect();
        const headerHeight = header.offsetHeight;
        const unpinThreshold = navHeight + headerHeight + headerMarginBottom;
        const diff = unpinThreshold - lastCardRect.top;
        if (diff > 0) {
          const clampDiff = Math.min(headerHeight + 30, diff);
          header.style.transform = `translateY(-${clampDiff}px)`;
        } else {
          header.style.transform = '';
        }
      });
    }

    function syncStickyNavHeight() {
      syncClusterDividers();
      const nav = document.querySelector('.sticky-nav');
      if (nav) document.documentElement.style.setProperty('--sticky-nav-height', `${nav.offsetHeight}px`);
      syncTierHeaderStickyBounds();
    }
    window.addEventListener('resize', syncStickyNavHeight);
    window.addEventListener('DOMContentLoaded', syncStickyNavHeight);
    if (window.ResizeObserver) {
      const nav = document.querySelector('.sticky-nav');
      if (nav) new ResizeObserver(syncStickyNavHeight).observe(nav);
    }
    syncStickyNavHeight();

    let isHeaderTicking = false;
    window.addEventListener('scroll', () => {
      if (!isHeaderTicking) {
        window.requestAnimationFrame(() => {
          syncTierHeaderStickyBounds();
          isHeaderTicking = false;
        });
        isHeaderTicking = true;
      }
    }, { passive: true });

    initFilters();
  </script>
</body>
</html>
"""


def _render_tier_sections(
    retail_trajectories: List[Dict[str, Any]],
    beta_trajectories: List[Dict[str, Any]],
    r_stats: Dict[str, Dict[str, Any]],
    b_stats: Dict[str, Dict[str, Any]],
) -> str:
    tier_definitions = [
        ("IL", "Iconic Legends"),
        ("IO", "Iconic Others"),
        ("IS", "Iconic Secret Rares"),
    ]
    parts: List[str] = []

    for code, base_title in tier_definitions:
        r_cards = [t for t in retail_trajectories if t["rarity"] == code]
        b_cards = [t for t in beta_trajectories if t["rarity"] == code]
        tier_count = len(b_cards) or len(r_cards)
        title = f"{base_title} ({tier_count})"

        parts.append(f"""  <div class="tier-section" id="section-{code}">
    <div class="tier-header" id="header-{code}">
      <div class="tier-title">
        <span>{html.escape(title)}</span>
      </div>
      <div class="tier-stats-group" id="stats-group-{code}">
        <button type="button" class="tier-stats-tab" onclick="toggleTierStats('{code}')" id="btn-stats-{code}">Stats <span class="stats-arrow">&#x25C2;</span></button>
        <div class="tier-stats-content edition-stats-beta" id="stats-beta-{code}">
          <div class="stat-cell"><span class="stat-label">Min</span><span class="stat-val">{b_stats[code]['min']}</span></div>
          <div class="stat-cell"><span class="stat-label">p25</span><span class="stat-val">{b_stats[code]['p25']}</span></div>
          <div class="stat-cell"><span class="stat-label">Med</span><span class="stat-val">{b_stats[code]['med']}</span></div>
          <div class="stat-cell"><span class="stat-label">p75</span><span class="stat-val">{b_stats[code]['p75']}</span></div>
          <div class="stat-cell"><span class="stat-label">Max</span><span class="stat-val">{b_stats[code]['max']}</span></div>
          <div class="stat-cell stat-cell-owned"><span class="stat-label">Owned</span><span class="stat-val">{b_stats[code]['owned_qty']} cards (${b_stats[code]['owned_val']:,.2f})</span></div>
        </div>
        <div class="tier-stats-content edition-stats-retail" id="stats-retail-{code}" style="display:none;">
          <div class="stat-cell"><span class="stat-label">Min</span><span class="stat-val">{r_stats[code]['min']}</span></div>
          <div class="stat-cell"><span class="stat-label">p25</span><span class="stat-val">{r_stats[code]['p25']}</span></div>
          <div class="stat-cell"><span class="stat-label">Med</span><span class="stat-val">{r_stats[code]['med']}</span></div>
          <div class="stat-cell"><span class="stat-label">p75</span><span class="stat-val">{r_stats[code]['p75']}</span></div>
          <div class="stat-cell"><span class="stat-label">Max</span><span class="stat-val">{r_stats[code]['max']}</span></div>
          <div class="stat-cell stat-cell-owned"><span class="stat-label">Owned</span><span class="stat-val">{r_stats[code]['owned_qty']} cards (${r_stats[code]['owned_val']:,.2f})</span></div>
        </div>
      </div>
    </div>
    <div class="grid tier-grid" id="grid-{code}">
""")
        for edition_name, card_list, initial_display in [
            ("beta", b_cards, "flex"),
            ("retail", r_cards, "none"),
        ]:
            for t in card_list:
                chg_class = "text-green" if t["change7d"] > 0 else ("text-red" if t["change7d"] < 0 else "text-muted")
                chg_sign = "+" if t["change7d"] > 0 else ""
                disp_style = f' style="display:{initial_display};"' if initial_display == "none" else ""
                price_display = f"${t['currentPrice']:.2f}" if t.get("hasPrice") else "N/A"
                chg_display = (
                    f"{_fmt_currency_delta(t['change7d'])} ({chg_sign}{t['change7dPct']:.1f}%)"
                    if t.get("hasPrice")
                    else "N/A"
                )

                if t.get("hasAcquisition"):
                    acq_class = "text-green" if t["acqDeltaDollar"] > 0 else ("text-red" if t["acqDeltaDollar"] < 0 else "text-muted")
                    acq_sign = "+" if t["acqDeltaDollar"] > 0 else ""
                    acq_base_txt = f"${t['acqCost']:.2f}" if t["acqCost"] > 0 else "N/A"
                    row2_left = f'<span class="acq-base-label" data-tooltip="Acquisition Baseline: Fair market price on the date the card was opened or purchased">Acq base:</span> <span class="price-sub-val">{acq_base_txt}</span>'
                    row2_right = (
                        f'Acq: <span class="{acq_class}">{_fmt_currency_delta(t["acqDeltaDollar"])} ({acq_sign}{t["acqDeltaPct"]:.1f}%)</span>'
                        if t.get("hasPrice") and t["acqCost"] > 0
                        else 'Acq: <span class="text-muted">N/A</span>'
                    )
                else:
                    all_class = "text-green" if t["changeAll"] > 0 else ("text-red" if t["changeAll"] < 0 else "text-muted")
                    all_sign = "+" if t["changeAll"] > 0 else ""
                    row2_left = '<span class="acq-base-label" data-tooltip="Acquisition Baseline: Fair market price on the date the card was opened or purchased">Acq base:</span> <span class="price-sub-val">N/A</span>'
                    row2_right = (
                        f'Lifetime: <span class="{all_class}">{_fmt_currency_delta(t["changeAll"])} ({all_sign}{t["changeAllPct"]:.1f}%)</span>'
                        if t.get("hasPrice")
                        else 'Lifetime: <span class="text-muted">N/A</span>'
                    )

                has_price = bool(t.get("hasPrice"))
                price_attr = f"{t['currentPrice']}" if has_price else ""
                c7d_attr = f"{t['change7dPct']}" if has_price else ""
                call_attr = f"{t['changeAllPct']}" if has_price else ""
                has_acq_attr = "1" if t.get("hasAcquisition") else "0"
                cacq_attr = (
                    f"{t.get('acqDeltaPct', 0.0):.2f}"
                    if (t.get("hasAcquisition") and has_price and t.get("acqCost", 0.0) > 0)
                    else ""
                )
                safe_name_attr = html.escape(t["name"].lower(), quote=True)
                safe_name_title = html.escape(t["name"], quote=True)
                safe_name_body = html.escape(t["name"])
                safe_pid = html.escape(str(t["productId"]), quote=True)
                safe_pnum = html.escape(str(t["printNumber"]), quote=True)
                safe_color = html.escape(str(t["color"]), quote=True)
                safe_type = html.escape(str(t["cardType"]), quote=True)
                card_title_el = (
                    f'<a href="https://www.tcgplayer.com/product/{safe_pid}" target="_blank" rel="noopener noreferrer" class="card-link truncate">{safe_name_body} <span class="ext-icon">&#x2197;</span></a>'
                    if safe_pid
                    else f'<span class="truncate">{safe_name_body}</span>'
                )

                parts.append(f"""      <div class="card-tile" data-edition="{edition_name}" data-pid="{safe_pid}" data-num="{safe_pnum}" data-name="{safe_name_attr}" data-rarity="{t['rarity']}" data-color="{safe_color}" data-type="{safe_type}" data-price="{price_attr}" data-c7d="{c7d_attr}" data-call="{call_attr}" data-has-acq="{has_acq_attr}" data-cacq="{cacq_attr}" data-owned-qty="{t.get('ownedQty', 0)}"{disp_style}>
        <div>
          <div class="card-header">
            <span class="card-num">#{safe_pnum}</span>
          </div>
          <div class="card-name" title="{safe_name_title}">
            <span class="color-dot color-{safe_color}"></span>
            {card_title_el}
          </div>
        </div>
        <div>
          <div class="price-block">
            <div class="price-row-main">
              <span class="price-main">{price_display}</span>
              <div class="price-change metric-l7d"><span style="color:var(--text-muted);">7d:</span> <span class="{chg_class}">{chg_display}</span></div>
            </div>
            <div class="price-row-sub">
              <div>{row2_left}</div>
              <div class="text-right metric-sub metric-active">{row2_right}</div>
            </div>
          </div>
          <div class="chart-container">
            {t['svgChart']}
          </div>
        </div>
      </div>
""")
        parts.append("    </div>\n  </div>\n")

    return "".join(parts)


def _render_comparison_view(comp_pairs: List[Dict[str, Any]]) -> str:
    b_total_owned_cards = sum(p["beta_owned"] for p in comp_pairs)
    b_total_owned_val = sum(p["beta_owned"] * (p["beta_price"] or 0.0) for p in comp_pairs)
    r_total_owned_cards = sum(p["retail_owned"] for p in comp_pairs)
    r_total_owned_val = sum(p["retail_owned"] * (p["retail_price"] or 0.0) for p in comp_pairs)

    paired_all = [p for p in comp_pairs if p["is_paired"]]
    title_str = (
        f"Iconics Set Value ({len(paired_all)}/{len(comp_pairs)} pairs)"
        if len(paired_all) < len(comp_pairs)
        else "Iconics Set Value"
    )
    if paired_all:
        b_sum = sum(p["beta_price"] for p in paired_all)
        r_sum = sum(p["retail_price"] for p in paired_all)
        sp_dlr = b_sum - r_sum
        sp_pct = (sp_dlr / r_sum * 100.0) if r_sum > 0 else 0.0
        s_cls = "comp-premium-pos" if sp_dlr > 0 else ("comp-premium-neg" if sp_dlr < 0 else "")
        s_sign = "+" if sp_dlr > 0 else ""

        b_q = compute_quantiles([p["beta_price"] for p in paired_all])
        r_q = compute_quantiles([p["retail_price"] for p in paired_all])

        all_info = {
            "title": title_str,
            "beta": f"${b_sum:,.2f}",
            "retail": f"${r_sum:,.2f}",
            "premium": f"{_fmt_currency_delta(sp_dlr)} ({s_sign}{sp_pct:.1f}%)",
            "cls": s_cls,
            "b_dist": [_fmt_quant_stat(x) for x in b_q],
            "r_dist": [_fmt_quant_stat(x) for x in r_q],
        }
    else:
        all_info = {
            "title": title_str,
            "beta": "N/A",
            "retail": "N/A",
            "premium": "N/A",
            "cls": "",
            "b_dist": ["N/A"] * 5,
            "r_dist": ["N/A"] * 5,
        }
    b_all_dist = all_info["b_dist"]
    r_all_dist = all_info["r_dist"]

    parts: List[str] = [f"""  <div class="comparison-view" id="view-comparison" style="display:none;">
    <div class="comp-overview-grid">
      <div class="comp-card">
        <div class="comp-card-header">
          <div class="comp-card-title" id="set-val-title">{html.escape(all_info['title'])}</div>
          <div class="comp-pill-group">
            <button type="button" class="comp-pill-btn is-active" onclick="setPricedSetTier('all')" id="pill-tier-all">All</button>
            <button type="button" class="comp-pill-btn" onclick="setPricedSetTier('IL')" id="pill-tier-IL">IL</button>
            <button type="button" class="comp-pill-btn" onclick="setPricedSetTier('IO')" id="pill-tier-IO">IO</button>
            <button type="button" class="comp-pill-btn" onclick="setPricedSetTier('IS')" id="pill-tier-IS">ISR</button>
          </div>
        </div>
        <div class="comp-card-val"><span style="color:#58a6ff;" id="set-val-beta">{all_info['beta']}</span> <span style="font-size:13px;color:var(--text-muted);font-weight:normal;">vs</span> <span style="color:#8b949e;" id="set-val-retail">{all_info['retail']}</span></div>
        <div class="comp-card-sub">Beta Premium: <strong class="{all_info['cls']}" id="set-val-premium">{all_info['premium']}</strong></div>
      </div>
      <div class="comp-card comp-card-distro">
        <div class="comp-distro-table">
          <div class="comp-distro-header">
            <span></span>
            <span>MIN</span>
            <span>P25</span>
            <span class="comp-distro-med-header">MED</span>
            <span>P75</span>
            <span>MAX</span>
          </div>
          <div class="comp-distro-row">
            <span class="comp-distro-edition comp-distro-beta">Beta</span>
            <span id="dist-b-min">{b_all_dist[0]}</span>
            <span id="dist-b-p25">{b_all_dist[1]}</span>
            <span id="dist-b-med" class="comp-distro-med-val">{b_all_dist[2]}</span>
            <span id="dist-b-p75">{b_all_dist[3]}</span>
            <span id="dist-b-max">{b_all_dist[4]}</span>
          </div>
          <div class="comp-distro-row">
            <span class="comp-distro-edition comp-distro-retail">Retail</span>
            <span id="dist-r-min">{r_all_dist[0]}</span>
            <span id="dist-r-p25">{r_all_dist[1]}</span>
            <span id="dist-r-med" class="comp-distro-med-val">{r_all_dist[2]}</span>
            <span id="dist-r-p75">{r_all_dist[3]}</span>
            <span id="dist-r-max">{r_all_dist[4]}</span>
          </div>
        </div>
      </div>
      <div class="comp-card">
        <div class="comp-card-title">Owned Iconics Value</div>
        <div class="comp-card-val">${b_total_owned_val + r_total_owned_val:,.2f} <span style="font-size:13px;color:var(--text-muted);font-weight:normal;">({b_total_owned_cards + r_total_owned_cards} total)</span></div>
        <div class="comp-card-sub">
          <div>Beta: {b_total_owned_cards} cards (${b_total_owned_val:,.2f})</div>
          <div>Retail: {r_total_owned_cards} cards (${r_total_owned_val:,.2f})</div>
        </div>
      </div>
    </div>
"""]

    tier_order = [
        ("IL", "Iconic Legends"),
        ("IO", "Iconic Others"),
        ("IS", "Iconic Secret Rares"),
    ]

    for t_code, t_base in tier_order:
        t_pairs = [p for p in comp_pairs if p["rarity"] == t_code]
        t_label = f"{t_base} ({len(t_pairs)})"
        parts.append(f"""    <div class="comp-sub-section" id="comp-sub-{t_code}">
      <h2 class="comp-sub-title">{html.escape(t_label)}</h2>
      <div class="comp-table-container">
        <table class="comp-table" id="table-{t_code}">
          <thead>
            <tr>
              <th style="text-align:center;width:44px;" class="sortable" data-col="num">#</th>
              <th style="text-align:left;" class="sortable" data-col="name">Card Name</th>
              <th style="text-align:center;width:90px;" class="sortable" data-col="type">Type</th>
              <th style="text-align:right;width:95px;" class="sortable" data-col="b_price">Beta Price</th>
              <th style="text-align:right;width:95px;" class="sortable" data-col="r_price">Retail Price</th>
              <th style="text-align:right;width:125px;" class="sortable" data-col="spread_dlr">Beta Premium ($)</th>
              <th style="text-align:right;width:125px;" class="sortable" data-col="spread_pct">Beta Premium (%)</th>
              <th style="text-align:center;width:80px;" class="sortable" data-col="b_owned">Beta Owned</th>
              <th style="text-align:center;width:85px;" class="sortable" data-col="r_owned">Retail Owned</th>
            </tr>
          </thead>
          <tbody>
""")
        for p in t_pairs:
            b_p = p["beta_price"]
            r_p = p["retail_price"]
            b_p_str = (
                f'<a href="https://www.tcgplayer.com/product/{html.escape(p["beta_pid"], quote=True)}" target="_blank" rel="noopener noreferrer" class="comp-price-link">${b_p:.2f}<span class="ext-icon">&#x2197;</span></a>'
                if (b_p is not None and b_p > 0 and p["beta_pid"])
                else (f"${b_p:.2f}" if (b_p is not None and b_p > 0) else "N/A")
            )
            r_p_str = (
                f'<a href="https://www.tcgplayer.com/product/{html.escape(p["retail_pid"], quote=True)}" target="_blank" rel="noopener noreferrer" class="comp-price-link">${r_p:.2f}<span class="ext-icon">&#x2197;</span></a>'
                if (r_p is not None and r_p > 0 and p["retail_pid"])
                else (f"${r_p:.2f}" if (r_p is not None and r_p > 0) else "N/A")
            )

            if p["is_paired"]:
                sp_dlr = p["spread_dlr"]
                sp_pct = p["spread_pct"]
                sp_sign = "+" if sp_dlr > 0 else ""
                sp_cls = "comp-premium-pos" if sp_dlr > 0 else ("comp-premium-neg" if sp_dlr < 0 else "")
                sp_dlr_str = _fmt_currency_delta(sp_dlr)
                sp_pct_str = f"{sp_sign}{sp_pct:.1f}%"
            else:
                sp_cls = ""
                sp_dlr_str = "N/A"
                sp_pct_str = "N/A"

            parts.append(f"""            <tr class="comp-row" data-paired="{'1' if p['is_paired'] else '0'}" data-name="{html.escape(p['name'].lower(), quote=True)}" data-num="{html.escape(p['num'], quote=True)}" data-rarity="{p['rarity']}" data-color="{html.escape(p['color'], quote=True)}" data-type="{html.escape(p['cardType'], quote=True)}" data-b-price="{b_p if b_p is not None else ''}" data-r-price="{r_p if r_p is not None else ''}" data-spread-dlr="{p['spread_dlr'] if p['spread_dlr'] is not None else ''}" data-spread-pct="{p['spread_pct'] if p['spread_pct'] is not None else ''}" data-owned-b="{p['beta_owned']}" data-owned-r="{p['retail_owned']}">
              <td style="text-align:center;" class="comp-table-mono">{html.escape(p['num'])}</td>
              <td><div class="comp-card-name"><span class="color-dot color-{html.escape(p['color'], quote=True)}"></span><span>{html.escape(p['name'])}</span></div></td>
              <td style="text-align:center;">{html.escape(p['cardType'])}</td>
              <td style="text-align:right;" class="comp-table-mono">{b_p_str}</td>
              <td style="text-align:right;" class="comp-table-mono">{r_p_str}</td>
              <td style="text-align:right;" class="comp-table-mono {sp_cls}">{sp_dlr_str}</td>
              <td style="text-align:right;" class="comp-table-mono {sp_cls}">{sp_pct_str}</td>
              <td style="text-align:center;" class="comp-table-mono">{p['beta_owned']}</td>
              <td style="text-align:center;" class="comp-table-mono">{p['retail_owned']}</td>
            </tr>
""")

        paired_t = [p for p in t_pairs if p["is_paired"]]
        t_b_owned = sum(p["beta_owned"] for p in t_pairs)
        t_r_owned = sum(p["retail_owned"] for p in t_pairs)
        t_total_label = f"Total ({len(paired_t)}/{len(t_pairs)} pairs)" if len(paired_t) < len(t_pairs) else "Total"

        if paired_t:
            t_b_sum = sum(p["beta_price"] for p in paired_t)
            t_r_sum = sum(p["retail_price"] for p in paired_t)
            t_sp_dlr = t_b_sum - t_r_sum
            t_sp_pct = (t_sp_dlr / t_r_sum * 100.0) if t_r_sum > 0 else 0.0
            t_sp_cls = "comp-premium-pos" if t_sp_dlr > 0 else ("comp-premium-neg" if t_sp_dlr < 0 else "")
            t_sp_sign = "+" if t_sp_dlr > 0 else ""
            tot_b_txt = f"${t_b_sum:.2f}"
            tot_r_txt = f"${t_r_sum:.2f}"
            tot_dlr_txt = _fmt_currency_delta(t_sp_dlr)
            tot_pct_txt = f"{t_sp_sign}{t_sp_pct:.1f}%"
        else:
            t_sp_cls = ""
            tot_b_txt = "N/A"
            tot_r_txt = "N/A"
            tot_dlr_txt = "N/A"
            tot_pct_txt = "N/A"

        parts.append(f"""          </tbody>
          <tfoot>
            <tr class="comp-total-row" id="total-row-{t_code}">
              <td style="text-align:center;" class="comp-table-mono">—</td>
              <td id="total-label-{t_code}">{html.escape(t_total_label)}</td>
              <td style="text-align:center;">—</td>
              <td style="text-align:right;" class="comp-table-mono" id="total-b-price-{t_code}">{tot_b_txt}</td>
              <td style="text-align:right;" class="comp-table-mono" id="total-r-price-{t_code}">{tot_r_txt}</td>
              <td style="text-align:right;" class="comp-table-mono {t_sp_cls}" id="total-sp-dlr-{t_code}">{tot_dlr_txt}</td>
              <td style="text-align:right;" class="comp-table-mono {t_sp_cls}" id="total-sp-pct-{t_code}">{tot_pct_txt}</td>
              <td style="text-align:center;" class="comp-table-mono" id="total-b-owned-{t_code}">{t_b_owned}</td>
              <td style="text-align:center;" class="comp-table-mono" id="total-r-owned-{t_code}">{t_r_owned}</td>
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
""")

    parts.append("  </div>\n")
    return "".join(parts)


def generate_html_dashboard(
    pairs: List[Any],
    prices_dir: Path,
    collection_path: Optional[str],
    output_path: str,
    database_path: Optional[str] = None,
    earliest_beta_date: Optional[str] = EARLIEST_BETA_DATE,
    earliest_retail_date: Optional[str] = EARLIEST_RETAIL_DATE,
    validated_rows: Optional[List[ValidatedCollectionRow]] = None,
) -> None:
    """
    Generates the interactive standalone HTML comparison and trajectory dashboard for WNC Iconics.
    """
    all_dates, price_by_date = load_daily_prices_chronological(prices_dir)

    beta_dates = _filter_dates_from(all_dates, earliest_beta_date)
    retail_dates = _filter_dates_from(all_dates, earliest_retail_date)

    earliest_beta = beta_dates[0] if beta_dates else (all_dates[0] if all_dates else None)
    earliest_retail = retail_dates[0] if retail_dates else (all_dates[0] if all_dates else None)

    acquisitions = load_collection_acquisitions(
        collection_path=collection_path,
        database_path=database_path,
        earliest_beta_date=earliest_beta,
        earliest_retail_date=earliest_retail,
        validated_rows=validated_rows,
    )

    beta_trajectories = build_edition_trajectories(
        pairs=pairs,
        edition="beta",
        dates=beta_dates,
        price_by_date=price_by_date,
        acquisitions=acquisitions,
    )
    retail_trajectories = build_edition_trajectories(
        pairs=pairs,
        edition="retail",
        dates=retail_dates,
        price_by_date=price_by_date,
        acquisitions=acquisitions,
    )

    for t in retail_trajectories:
        acq_info = {"date": t["acqDate"], "cost": t["acqCost"]} if t.get("hasAcquisition") else None
        t["svgChart"] = generate_svg_chart(t["points"], card_id=f"r_{t['productId']}", acq_info=acq_info)
    for t in beta_trajectories:
        acq_info = {"date": t["acqDate"], "cost": t["acqCost"]} if t.get("hasAcquisition") else None
        t["svgChart"] = generate_svg_chart(t["points"], card_id=f"b_{t['productId']}", acq_info=acq_info)

    r_stats = {
        code: _compute_tier_stats([t for t in retail_trajectories if t["rarity"] == code])
        for code in ("IL", "IO", "IS")
    }
    b_stats = {
        code: _compute_tier_stats([t for t in beta_trajectories if t["rarity"] == code])
        for code in ("IL", "IO", "IS")
    }

    pair_by_num = {str(p.print_number): p for p in pairs}
    r_by_num = {t["cleanNumber"]: t for t in retail_trajectories}
    comp_pairs: List[Dict[str, Any]] = []
    for b in beta_trajectories:
        cnum = b["cleanNumber"]
        r = r_by_num.get(cnum)
        pair_obj = pair_by_num.get(cnum)
        b_p = pair_obj.beta_price if pair_obj is not None else (b["currentPrice"] if b.get("hasPrice") else None)
        r_p = pair_obj.retail_price if pair_obj is not None else (r["currentPrice"] if (r and r.get("hasPrice")) else None)
        is_paired = (b_p is not None and r_p is not None)
        if is_paired:
            spread_dlr: Optional[float] = b_p - r_p
            spread_pct: Optional[float] = ((spread_dlr / r_p) * 100.0) if r_p > 0 else 0.0
        else:
            spread_dlr = None
            spread_pct = None
        comp_pairs.append({
            "num": cnum,
            "name": b["name"],
            "rarity": b["rarity"],
            "color": b["color"],
            "cardType": b["cardType"],
            "beta_pid": b["productId"],
            "retail_pid": r["productId"] if r else "",
            "beta_price": b_p,
            "retail_price": r_p,
            "is_paired": is_paired,
            "spread_dlr": spread_dlr,
            "spread_pct": spread_pct,
            "beta_owned": b.get("ownedQty", 0),
            "retail_owned": r.get("ownedQty", 0) if r else 0,
        })

    generated_at = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    retail_date_range = f"{retail_dates[0]} to {retail_dates[-1]}" if retail_dates else "N/A"
    beta_date_range = f"{beta_dates[0]} to {beta_dates[-1]}" if beta_dates else "N/A"
    retail_count = len(retail_dates)
    beta_count = len(beta_dates)
    snapshot_templates = {
        "retail": f"{retail_count} market snapshots ({retail_date_range})",
        "beta": f"{beta_count} market snapshots ({beta_date_range})",
    }

    tier_sections_html = _render_tier_sections(retail_trajectories, beta_trajectories, r_stats, b_stats)
    comp_view_html = _render_comparison_view(comp_pairs)
    total_card_count = len(beta_trajectories)

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Welcome to Night City &ndash; Iconic Price Trajectories</title>
  <script>
    (function() {{
      try {{
        var raw = localStorage.getItem('wnc_iconics_filter_state');
        if (raw) {{
          var s = JSON.parse(raw);
          if (s && s.edition === 'comparison') {{
            document.documentElement.classList.add('preload-comparison');
          }} else if (s && s.edition === 'retail') {{
            document.documentElement.classList.add('preload-retail');
          }}
        }}
      }} catch (e) {{}}
    }})();
  </script>
  <style>
{_DASHBOARD_CSS}
  </style>
</head>
<body>
  <header class="sticky-nav">
  <div class="header">
    <div class="title-group">
      <h1>Welcome to Night City &ndash; Iconic Price Trajectories</h1>
    </div>
    <div class="header-meta">
      <div id="meta-snapshots">{html.escape(snapshot_templates['beta'])}</div>
      <div>Generated {html.escape(generated_at)}</div>
    </div>
  </div>

  <div class="controls">
    <div class="control-cluster">
      <span class="cluster-label">View</span>
      <div class="edition-toggle" id="edition-toggle">
        <button type="button" class="toggle-btn active" id="btn-beta" onclick="setEdition('beta')">Beta</button>
        <button type="button" class="toggle-btn" id="btn-retail" onclick="setEdition('retail')">Retail</button>
        <button type="button" class="toggle-btn" id="btn-comparison" onclick="setEdition('comparison')">Comparison</button>
      </div>
    </div>

    <div class="cluster-divider"></div>

    <div class="control-cluster" id="cluster-basis">
      <span class="cluster-label">Basis</span>
      <div class="edition-toggle" id="anchor-toggle">
        <button type="button" class="toggle-btn" id="btn-anchor-lifetime" onclick="setAnchor('lifetime')" data-tooltip="Measures return from first tracked market price">Lifetime</button>
        <button type="button" class="toggle-btn active" id="btn-anchor-acq" onclick="setAnchor('acq')" data-tooltip="Measures return relative to fair market price on acquisition date">Acquisition</button>
        <button type="button" class="toggle-btn" id="btn-anchor-l7d" onclick="setAnchor('l7d')" data-tooltip="Measures price change over the last 7 calendar days">7-Day</button>
      </div>
    </div>

    <div class="cluster-divider"></div>

    <div class="control-cluster flex-grow">
      <span class="cluster-label">Filter</span>
      <input type="text" id="search-input" class="search-box" placeholder="Search by name or number (e.g. B141, Panam, Judy)..." />
      <div class="multiselect" id="ms-color">
        <button type="button" class="multiselect-btn" id="btn-color">
          <span>Color</span>
          <span class="filter-count" id="badge-color" style="display: none;">0</span>
        </button>
        <div class="multiselect-menu" id="menu-color">
          <label class="multiselect-item"><input type="checkbox" value="Red" /><span class="multiselect-item-label">&#128308; Red</span></label>
          <label class="multiselect-item"><input type="checkbox" value="Yellow" /><span class="multiselect-item-label">&#128993; Yellow</span></label>
          <label class="multiselect-item"><input type="checkbox" value="Green" /><span class="multiselect-item-label">&#128994; Green</span></label>
          <label class="multiselect-item"><input type="checkbox" value="Blue" /><span class="multiselect-item-label">&#128309; Blue</span></label>
        </div>
      </div>

      <div class="multiselect" id="ms-type">
        <button type="button" class="multiselect-btn" id="btn-type">
          <span>Type</span>
          <span class="filter-count" id="badge-type" style="display: none;">0</span>
        </button>
        <div class="multiselect-menu" id="menu-type">
          <label class="multiselect-item"><input type="checkbox" value="Legend" /><span class="multiselect-item-label">Legend</span></label>
          <label class="multiselect-item"><input type="checkbox" value="Unit" /><span class="multiselect-item-label">Unit</span></label>
          <label class="multiselect-item"><input type="checkbox" value="Gear" /><span class="multiselect-item-label">Gear</span></label>
          <label class="multiselect-item"><input type="checkbox" value="Program" /><span class="multiselect-item-label">Program</span></label>
        </div>
      </div>

      <div class="multiselect" id="ms-rarity">
        <button type="button" class="multiselect-btn" id="btn-rarity">
          <span>Rarity</span>
          <span class="filter-count" id="badge-rarity" style="display: none;">0</span>
        </button>
        <div class="multiselect-menu" id="menu-rarity">
          <label class="multiselect-item"><input type="checkbox" value="IL" /><span class="multiselect-item-label">Iconic Legend (IL)</span></label>
          <label class="multiselect-item"><input type="checkbox" value="IO" /><span class="multiselect-item-label">Iconic Other (IO)</span></label>
          <label class="multiselect-item"><input type="checkbox" value="IS" /><span class="multiselect-item-label">Iconic Secret (IS)</span></label>
        </div>
      </div>

      <div class="multiselect" id="ms-ownership">
        <button type="button" class="multiselect-btn" id="btn-ownership">
          <span>Ownership</span>
          <span class="filter-count" id="badge-ownership" style="display: none;">0</span>
        </button>
        <div class="multiselect-menu" id="menu-ownership">
          <label class="multiselect-item"><input type="checkbox" value="owned" /><span class="multiselect-item-label">Owned</span></label>
          <label class="multiselect-item"><input type="checkbox" value="unowned" /><span class="multiselect-item-label">Unowned</span></label>
        </div>
      </div>
    </div>

    <div class="cluster-divider"></div>

    <div class="control-cluster">
      <span class="cluster-label">Sort</span>
      <select id="sort-select" class="filter-select">
        <option value="num-asc">Print # (B141 &rarr; B171)</option>
        <option value="price-desc">Price: High to Low</option>
        <option value="price-asc">Price: Low to High</option>
        <option value="gain-desc">7-Day Change %</option>
        <option value="gain-all-desc">All-Time Change %</option>
        <option value="gain-acq-desc">Acquisition Change %</option>
      </select>
      <div id="count-display" class="count-badge">Showing {total_card_count} cards</div>
      <button type="button" id="btn-reset-filters" class="reset-btn" onclick="resetAllFilters()">Reset</button>
    </div>
  </div>
  </header>
{tier_sections_html}{comp_view_html}  <div id="chart-tooltip" class="chart-tooltip"></div>

  <script>
    const totalCardCount = {total_card_count};
    const snapshotTemplates = {serialize_json_for_script(snapshot_templates)};
{_DASHBOARD_JS_BODY}"""

    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(html_content)
