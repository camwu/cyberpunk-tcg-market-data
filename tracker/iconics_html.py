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
EARLIEST_NCB_DATE = "2026-11-06"
EARLIEST_NCB_CHAMP_DATE = "2026-11-17"
WNC_BETA_GROUP_NAME = "Welcome to Night City - Beta"
WNC_RETAIL_GROUP_NAME = "Welcome to Night City - Retail"
WNC_BT_BETA_GROUP_NAME = "Box Toppers - Beta"
WNC_BT_RETAIL_GROUP_NAME = "Box Toppers - Retail"
NCB_S1_GROUP_NAME = "Night City Brawl Season 1"

# Pure JS state, migration, and view-descriptor helpers embedded verbatim in <head> and tested under Node vm.
_SET_STATE_JS = r"""    function defaultSetFilters() {
      return { q: '', o: [], r: [], c: [], t: [], s: 'num', b: 'all', statsOpen: {} };
    }

    function _cloneStrArray(val) {
      if (Array.isArray(val)) return val.map(v => String(v));
      if (typeof val === 'string' && val) return [val];
      return [];
    }

    function _normalizeStatsOpenMap(raw) {
      var out = {};
      if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return out;
      Object.keys(raw).forEach(function(k) {
        if (typeof raw[k] === 'boolean') out[k] = raw[k];
      });
      return out;
    }

    function resolveTierStatsOpen(statsOpenMap, code, isWideViewport) {
      if (statsOpenMap && typeof statsOpenMap === 'object' && !Array.isArray(statsOpenMap) && typeof statsOpenMap[code] === 'boolean') {
        return statsOpenMap[code];
      }
      return Boolean(isWideViewport);
    }

    function updateTierStatsOpenMap(statsOpenMap, code, isOpen) {
      var out = _normalizeStatsOpenMap(statsOpenMap);
      if (typeof code === 'string' && code) {
        out[code] = Boolean(isOpen);
      }
      return out;
    }

    function _normalizeFilterObj(src) {
      var def = defaultSetFilters();
      if (!src || typeof src !== 'object') return def;
      var q = typeof src.q === 'string' ? src.q : (typeof src.search === 'string' ? src.search : '');
      var o = _cloneStrArray(src.o !== undefined ? src.o : src.ownership);
      var r = _cloneStrArray(src.r !== undefined ? src.r : src.rarity);
      var c = _cloneStrArray(src.c !== undefined ? src.c : src.color);
      var t = _cloneStrArray(src.t !== undefined ? src.t : src.type);
      var rawSort = typeof src.s === 'string' ? src.s : (typeof src.sort === 'string' ? src.sort : 'num');
      var s = rawSort || 'num';
      var b = typeof src.b === 'string' && src.b ? src.b : 'all';
      var statsOpen = _normalizeStatsOpenMap(src.statsOpen);
      return { q: q, o: o, r: r, c: c, t: t, s: s, b: b, statsOpen: statsOpen };
    }

    function _normalizeLowMode(raw) {
      return (raw === 'all' || raw === 'hide' || raw === 'current') ? raw : 'current';
    }

    function normalizeSavedFilterState(saved, hasNcb) {
      var allowNcb = hasNcb !== false;
      var base = {
        set: 'wnc',
        edition: 'beta',
        ncbView: 'charts',
        anchor: 'acq',
        lowMode: 'current',
        filtersBySet: {
          wnc: defaultSetFilters(),
          ncb: defaultSetFilters()
        }
      };
      if (!saved || typeof saved !== 'object') return base;
      var ed = (saved.edition === 'retail' || saved.edition === 'beta' || saved.edition === 'comparison') ? saved.edition : 'beta';
      var ncbV = (saved.ncbView === 'table' || saved.ncbView === 'charts') ? saved.ncbView : 'charts';
      var anc = (saved.anchor === 'lifetime' || saved.anchor === 'acq' || saved.anchor === 'l7d') ? saved.anchor : 'acq';
      var lm = _normalizeLowMode(saved.lowMode);
      if (saved.filtersBySet && typeof saved.filtersBySet === 'object') {
        var activeSet = (allowNcb && saved.set === 'ncb') ? 'ncb' : 'wnc';
        return {
          set: activeSet,
          edition: ed,
          ncbView: ncbV,
          anchor: anc,
          lowMode: lm,
          filtersBySet: {
            wnc: _normalizeFilterObj(saved.filtersBySet.wnc),
            ncb: _normalizeFilterObj(saved.filtersBySet.ncb)
          }
        };
      }
      return {
        set: 'wnc',
        edition: ed,
        ncbView: ncbV,
        anchor: anc,
        lowMode: lm,
        filtersBySet: {
          wnc: _normalizeFilterObj(saved),
          ncb: defaultSetFilters()
        }
      };
    }

    function switchSetFilterState(state, nextSet, outgoingControls, hasNcb) {
      var allowNcb = hasNcb !== false;
      var normalized = normalizeSavedFilterState(state, allowNcb);
      var prevSet = normalized.set === 'ncb' ? 'ncb' : 'wnc';
      var targetSet = (allowNcb && nextSet === 'ncb') ? 'ncb' : 'wnc';
      var nextWnc = _normalizeFilterObj(normalized.filtersBySet.wnc);
      var nextNcb = _normalizeFilterObj(normalized.filtersBySet.ncb);
      if (outgoingControls && typeof outgoingControls === 'object') {
        if (prevSet === 'wnc') {
          nextWnc = _normalizeFilterObj(outgoingControls);
        } else {
          nextNcb = _normalizeFilterObj(outgoingControls);
        }
      }
      return {
        set: targetSet,
        edition: normalized.edition,
        ncbView: normalized.ncbView,
        anchor: normalized.anchor,
        lowMode: normalized.lowMode,
        filtersBySet: {
          wnc: nextWnc,
          ncb: nextNcb
        }
      };
    }

    function applySetTierStatsToggle(state, activeSet, code, isOpen, hasNcb) {
      var normalized = normalizeSavedFilterState(state, hasNcb);
      var targetSet = (hasNcb !== false && activeSet === 'ncb') ? 'ncb' : 'wnc';
      var nextWnc = _normalizeFilterObj(normalized.filtersBySet.wnc);
      var nextNcb = _normalizeFilterObj(normalized.filtersBySet.ncb);
      if (targetSet === 'ncb') {
        nextNcb.statsOpen = updateTierStatsOpenMap(nextNcb.statsOpen, code, isOpen);
      } else {
        nextWnc.statsOpen = updateTierStatsOpenMap(nextWnc.statsOpen, code, isOpen);
      }
      return {
        set: targetSet,
        edition: normalized.edition,
        ncbView: normalized.ncbView,
        anchor: normalized.anchor,
        lowMode: normalized.lowMode,
        filtersBySet: {
          wnc: nextWnc,
          ncb: nextNcb
        }
      };
    }

    function computeSetViewState(activeSet, activeEdition, meta, activeNcbView) {
      var m = (meta && typeof meta === 'object') ? meta : {};
      if (activeSet === 'ncb') {
        var ncbRange = m.ncbRangeLabel || '';
        var ncbChampCount = Number(m.ncbChampCardCount || 0);
        var hasNcbChamp = Boolean(m.hasNcbChamp || ncbChampCount > 0);
        var isNcbTable = activeNcbView === 'table';
        return {
          title: 'Night City Brawl Season 1 \u2013 Nova Rare Price Trajectories',
          showClusterView: true,
          showEditionToggle: false,
          showNcbViewToggle: true,
          showMsRarity: false,
          showMsVariant: hasNcbChamp,
          showLegendTypeItem: false,
          showSectionNR: !isNcbTable,
          showSectionNRC: !isNcbTable && hasNcbChamp,
          showWncTiers: false,
          showComparisonView: false,
          showNcbTableView: isNcbTable,
          showBasisCluster: !isNcbTable,
          sortNumLabel: ncbRange ? ('Print # (' + ncbRange + ')') : 'Print #',
          countDenominator: Number(m.ncbCardCount || 0) + ncbChampCount
        };
      }
      var isComparison = activeEdition === 'comparison';
      var isRetail = activeEdition === 'retail';
      var wncRange = (isComparison || isRetail) ? (m.wncRetailRangeLabel || '') : (m.wncBetaRangeLabel || '');
      return {
        title: 'Welcome to Night City \u2013 Iconic & Promo Price Trajectories',
        showClusterView: true,
        showEditionToggle: true,
        showNcbViewToggle: false,
        showMsRarity: true,
        showMsVariant: false,
        showLegendTypeItem: true,
        showSectionNR: false,
        showSectionNRC: false,
        showWncTiers: !isComparison,
        showComparisonView: isComparison,
        showNcbTableView: false,
        showBasisCluster: !isComparison,
        sortNumLabel: wncRange ? ('Print # (' + wncRange + ')') : 'Print #',
        countDenominator: Number(m.totalCardCount || 0)
      };
    }

    function hasActiveFilterValues(filterObj) {
      if (!filterObj || typeof filterObj !== 'object') return false;
      var q = typeof filterObj.q === 'string' ? filterObj.q.trim() : '';
      return Boolean(
        q ||
        (Array.isArray(filterObj.o) && filterObj.o.length > 0) ||
        (Array.isArray(filterObj.r) && filterObj.r.length > 0) ||
        (Array.isArray(filterObj.c) && filterObj.c.length > 0) ||
        (Array.isArray(filterObj.t) && filterObj.t.length > 0)
      );
    }

    function resolveSearchPlaceholder(activeSet, activeEdition) {
      if (activeSet === 'ncb') {
        return 'Search by card name or number (e.g. 001, Chrome, Brawl)...';
      }
      if (activeEdition === 'retail' || activeEdition === 'comparison') {
        return 'Search by card name or number (e.g. 141, Panam, Judy, Smasher)...';
      }
      return 'Search by card name or number (e.g. B141, Panam, Judy, Smasher)...';
    }"""

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


def _resolve_earliest_date_for_expansion(
    exp: str,
    earliest_beta_date: Optional[str],
    earliest_retail_date: Optional[str],
    earliest_ncb_date: Optional[str],
    variant: Optional[str] = None,
    earliest_ncb_champ_date: Optional[str] = EARLIEST_NCB_CHAMP_DATE,
) -> Optional[str]:
    """Resolves the active release-date cutoff for an expansion name and optional variant."""
    if exp in (WNC_BETA_GROUP_NAME, WNC_BT_BETA_GROUP_NAME):
        return earliest_beta_date
    if exp in (WNC_RETAIL_GROUP_NAME, WNC_BT_RETAIL_GROUP_NAME):
        return earliest_retail_date
    if exp == NCB_S1_GROUP_NAME:
        if variant == "Brawl Champ":
            return earliest_ncb_champ_date
        return earliest_ncb_date
    return earliest_beta_date


def load_collection_acquisitions(
    collection_path: Optional[str],
    database_path: Optional[str],
    earliest_beta_date: Optional[str],
    earliest_retail_date: Optional[str],
    validated_rows: Optional[List[ValidatedCollectionRow]] = None,
    earliest_ncb_date: Optional[str] = EARLIEST_NCB_DATE,
    earliest_ncb_champ_date: Optional[str] = EARLIEST_NCB_CHAMP_DATE,
) -> Dict[Tuple[Any, ...], Dict[str, Any]]:
    """
    Loads card acquisition lots and baselines from card_metadata SQLite (when available)
    and active collection CSV rows keyed by (expansion, printNumber, normalized_finish)
    for regular lots or (expansion, printNumber, normalized_finish, variant) for stamped lots.
    """
    db_lot_baselines: Dict[Tuple[str, str, str, str, str], float] = {}
    db_clamped_baselines: Dict[Tuple[str, str, str, str, str], float] = {}
    if database_path and os.path.isfile(database_path):
        conn = None
        try:
            conn = sqlite3.connect(database_path)
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(card_metadata)")
            meta_cols = {col[1] for col in cur.fetchall()}
            if "variant" in meta_cols:
                cur.execute(
                    "SELECT expansion, print_number, finish, COALESCE(variant, ''), first_seen_date, baseline_market_price "
                    "FROM card_metadata ORDER BY first_seen_date ASC"
                )
                db_rows = cur.fetchall()
            else:
                cur.execute(
                    "SELECT expansion, print_number, finish, first_seen_date, baseline_market_price "
                    "FROM card_metadata ORDER BY first_seen_date ASC"
                )
                db_rows = [(exp, pn, fin, "", fsd, bmp) for exp, pn, fin, fsd, bmp in cur.fetchall()]

            for exp, pn, fin, var, fsd, bmp in db_rows:
                exp_s = str(exp or "")
                pn_s = str(pn or "")
                var_s = str(var or "").strip()
                norm_fin = normalize_finish(str(fin or ""), default="Standard")
                raw_fsd = str(fsd or "").strip()
                bmp_val = float(bmp or 0.0)
                if raw_fsd and bmp_val > 0.0:
                    db_lot_baselines.setdefault((exp_s, pn_s, norm_fin, var_s, raw_fsd), bmp_val)
                    min_ed = _resolve_earliest_date_for_expansion(
                        exp_s,
                        earliest_beta_date,
                        earliest_retail_date,
                        earliest_ncb_date,
                        variant=var_s or None,
                        earliest_ncb_champ_date=earliest_ncb_champ_date,
                    )
                    clamped_fsd = _clamp_date(raw_fsd, min_ed)
                    if clamped_fsd:
                        db_clamped_baselines.setdefault((exp_s, pn_s, norm_fin, var_s, clamped_fsd), bmp_val)
        except sqlite3.Error as e:
            print(f"Warning: Could not read card_metadata from '{database_path}': {e}", file=sys.stderr)
        finally:
            if conn is not None:
                conn.close()

    acquisitions: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    if validated_rows is None:
        if not collection_path or not os.path.isfile(collection_path):
            return acquisitions
        validated_rows = load_validated_collection_rows(collection_path)

    for vrow in validated_rows:
        if vrow.qty <= 0:
            continue

        lots_by_var: Dict[str, List[Tuple[Optional[str], int]]] = {}
        qty_by_var: Dict[str, int] = {}
        if vrow.lot_variants is not None:
            for d, q, v in vrow.lot_variants:
                var_key = (v or "").strip()
                lots_by_var.setdefault(var_key, []).append((d, q))
                qty_by_var[var_key] = qty_by_var.get(var_key, 0) + q
        else:
            lots_by_var[""] = vrow.lots
            qty_by_var[""] = vrow.qty

        exp = vrow.expansion
        pn = vrow.print_number
        fin = vrow.norm_finish or "Standard"

        for var_key, var_qty in qty_by_var.items():
            if var_qty <= 0:
                continue

            lots = lots_by_var[var_key]
            min_edition_date = _resolve_earliest_date_for_expansion(
                exp,
                earliest_beta_date,
                earliest_retail_date,
                earliest_ncb_date,
                variant=var_key or None,
                earliest_ncb_champ_date=earliest_ncb_champ_date,
            )
            clamped_lots: List[Tuple[str, int]] = []
            lot_entries: List[Tuple[str, int, float, str]] = []
            for lot_date, lot_qty in lots:
                if lot_date:
                    c_date = _clamp_date(lot_date, min_edition_date)
                    if c_date:
                        clamped_lots.append((c_date, lot_qty))
                        lot_db_cost = (
                            db_lot_baselines.get((exp, pn, fin, var_key, lot_date))
                            or db_lot_baselines.get((exp, pn, fin, var_key, c_date))
                            or db_clamped_baselines.get((exp, pn, fin, var_key, c_date))
                            or 0.0
                        )
                        lot_entries.append((c_date, lot_qty, lot_db_cost, lot_date))

            dated_candidates = sorted(d for d, _ in clamped_lots)
            raw_earliest = sorted(d for d, _ in lots if d)[0] if any(d for d, _ in lots) else None
            acq_date = dated_candidates[0] if dated_candidates else None

            acq_key: Tuple[Any, ...] = (exp, pn, fin, var_key) if var_key else (exp, pn, fin)
            existing = acquisitions.get(acq_key)
            if existing:
                existing["qty"] += var_qty
                existing["lots"].extend(clamped_lots)
                existing["lot_entries"].extend(lot_entries)
                if acq_date and (not existing["date"] or acq_date < existing["date"]):
                    existing["date"] = acq_date
                if raw_earliest and (not existing.get("rawDate") or raw_earliest < existing["rawDate"]):
                    existing["rawDate"] = raw_earliest
                merged_entries = existing["lot_entries"]
                merged_qty = sum(q for _, q, *_ in merged_entries)
                if merged_qty > 0 and all(c > 0.0 for _, _, c, *_ in merged_entries):
                    existing["cost"] = sum(c * q for _, q, c, *_ in merged_entries) / merged_qty
                else:
                    existing["cost"] = 0.0
            else:
                row_lot_qty = sum(q for _, q, *_ in lot_entries)
                summary_cost = (
                    sum(c * q for _, q, c, *_ in lot_entries) / row_lot_qty
                    if (row_lot_qty > 0 and all(c > 0.0 for _, _, c, *_ in lot_entries))
                    else 0.0
                )
                acquisitions[acq_key] = {
                    "date": acq_date,
                    "rawDate": raw_earliest,
                    "cost": summary_cost,
                    "qty": var_qty,
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


def _build_card_trajectory(
    pid: Optional[int],
    name: str,
    display_pnum: str,
    clean_pnum: str,
    rarity_code: str,
    color: Optional[str],
    card_type: Optional[str],
    valid_finish: str,
    owned_qty: int,
    expansion_name: str,
    dates: List[str],
    price_by_date: Dict[str, Dict[str, Dict[str, Any]]],
    acquisitions: Dict[Tuple[Any, ...], Dict[str, Any]],
    variant: Optional[str] = None,
) -> Dict[str, Any]:
    """Computes delayed-discovery trajectory metrics and acquisition baselines for one card."""
    pid_str = str(pid) if pid is not None else ""
    card_color = color if color in _ALLOWED_COLORS else "Neutral"
    resolved_type = card_type or "Unknown"

    clean_points: List[Tuple[str, float]] = []
    last_known_price: Optional[float] = None
    baseline_price: Optional[float] = None
    baseline_date: Optional[str] = None

    for d in dates:
        p_data = price_by_date.get(d, {}).get(pid_str) if pid_str else None
        mp = extract_finish_price(p_data, valid_finish) if p_data is not None else None
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

    current_low_price: Optional[float] = None
    if clean_points and current_price > 0.0 and dates and pid_str:
        latest_p_data = price_by_date.get(dates[-1], {}).get(pid_str)
        if latest_p_data is not None:
            current_low_price = extract_finish_price(latest_p_data, valid_finish, field="lowPrice")

    low_points: List[Optional[float]] = (
        [
            extract_finish_price(price_by_date.get(d, {}).get(pid_str), valid_finish, field="lowPrice")
            for d, _ in clean_points
        ]
        if (clean_points and current_price > 0.0 and pid_str)
        else []
    )

    acq_key: Tuple[Any, ...] = (
        (expansion_name, display_pnum, valid_finish, variant)
        if variant
        else (expansion_name, display_pnum, valid_finish)
    )
    acq = acquisitions.get(acq_key)
    first_priced_date = clean_points[0][0] if clean_points else None
    if acq and acq.get("date") and owned_qty > 0:
        acq_date = _clamp_date(str(acq["date"]), first_priced_date)
        lot_entries = acq.get("lot_entries")
        if lot_entries:
            lot_total_qty = sum(entry[1] for entry in lot_entries)
            if lot_total_qty > 0:
                weighted = 0.0
                resolved_qty = 0
                for entry in lot_entries:
                    d, q, lot_db_cost = entry[0], entry[1], entry[2]
                    orig_d = entry[3] if len(entry) >= 4 else d
                    use_db_cost = lot_db_cost > 0.0 and first_priced_date is not None and first_priced_date <= orig_d
                    lot_price = lot_db_cost if use_db_cost else (_lookup_point_price(clean_points, d) if clean_points else 0.0)
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
            orig_acq_date = str(acq.get("rawDate") or acq_date or "")
            use_acq_db_cost = (
                db_cost > 0.0
                and first_priced_date is not None
                and bool(orig_acq_date)
                and first_priced_date <= orig_acq_date
            )
            acq_cost = db_cost if use_acq_db_cost else (_lookup_point_price(clean_points, acq_date or "") if clean_points else 0.0)

        acq_delta_dollar = current_price - acq_cost if current_price > 0 else 0.0
        acq_delta_pct = (acq_delta_dollar / acq_cost * 100.0) if (acq_cost > 0 and current_price > 0) else 0.0
        has_acq = True
    else:
        acq_date = None
        acq_cost = 0.0
        acq_delta_dollar = 0.0
        acq_delta_pct = 0.0
        has_acq = False

    return {
        "productId": pid_str,
        "name": name,
        "printNumber": display_pnum,
        "cleanNumber": clean_pnum,
        "rarity": rarity_code,
        "color": card_color,
        "cardType": resolved_type,
        "finish": valid_finish,
        "currentPrice": current_price,
        "currentLowPrice": current_low_price,
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
        "lowPoints": low_points,
    }


def build_edition_trajectories(
    pairs: List[Any],
    edition: str,
    dates: List[str],
    price_by_date: Dict[str, Dict[str, Dict[str, Any]]],
    acquisitions: Dict[Tuple[str, str, str], Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Builds chronological daily price series and return metrics for one WNC Iconic or Box Topper edition.
    Establishes lifetime baselines from the first non-zero ($0.00+) market price, carries forward the
    latest known price across missing or $0.00 scrapes, and clamps pre-history acquisition dates to
    the earliest available market date for each card.
    """
    is_beta = edition == "beta"
    reverse_tier_map = {"Legends": "IL", "Others": "IO", "Secrets": "IS", "Box Toppers": "BT"}
    trajectories: List[Dict[str, Any]] = []

    sorted_pairs = sorted(
        pairs,
        key=lambda p: (int(p.print_number) if str(p.print_number).isdigit() else 9999, str(p.print_number)),
    )

    for pair in sorted_pairs:
        pid = pair.beta_product_id if is_beta else pair.retail_product_id
        display_pnum = f"B{pair.print_number}" if (is_beta and not str(pair.print_number).startswith("B")) else str(pair.print_number)
        rarity_code = reverse_tier_map.get(pair.tier, "IL")
        owned_qty = pair.beta_owned if is_beta else pair.retail_owned
        expansion_name = pair.beta_expansion if is_beta else pair.retail_expansion

        trajectories.append(
            _build_card_trajectory(
                pid=pid,
                name=pair.card_name,
                display_pnum=display_pnum,
                clean_pnum=str(pair.print_number),
                rarity_code=rarity_code,
                color=pair.color,
                card_type=pair.card_type,
                valid_finish=pair.valid_finish,
                owned_qty=owned_qty,
                expansion_name=expansion_name,
                dates=dates,
                price_by_date=price_by_date,
                acquisitions=acquisitions,
            )
        )

    return trajectories


def build_ncb_trajectories(
    ncb_cards: List[Any],
    dates: List[str],
    price_by_date: Dict[str, Dict[str, Dict[str, Any]]],
    acquisitions: Dict[Tuple[Any, ...], Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Builds chronological daily price series and return metrics for Night City Brawl Season 1 cards.
    """
    sorted_cards = sorted(
        ncb_cards,
        key=lambda c: (int(c.print_number) if str(c.print_number).isdigit() else 9999, str(c.print_number)),
    )
    trajectories: List[Dict[str, Any]] = []
    for card in sorted_cards:
        pnum = str(card.print_number)
        expansion_name = card.expansion
        variant = getattr(card, "variant", None)
        rarity_code = "NRC" if variant == "Brawl Champ" else "NR"
        trajectories.append(
            _build_card_trajectory(
                pid=card.product_id,
                name=card.card_name,
                display_pnum=pnum,
                clean_pnum=pnum,
                rarity_code=rarity_code,
                color=card.color,
                card_type=card.card_type,
                valid_finish=card.valid_finish,
                owned_qty=card.owned,
                expansion_name=expansion_name,
                dates=dates,
                price_by_date=price_by_date,
                acquisitions=acquisitions,
                variant=variant,
            )
        )
    return trajectories


def generate_svg_chart(
    points: List[Tuple[str, float]],
    card_id: str = "",
    width: int = 340,
    height: int = 120,
    acq_info: Optional[Dict[str, Any]] = None,
    low_price: Optional[float] = None,
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

    valid_low: Optional[float] = float(low_price) if (low_price is not None and low_price > 0.0) else None
    prices = [p[1] for p in points]
    p_max = max(max(prices), valid_low) if valid_low is not None else max(prices)

    y_min = 0.0
    y_max = float(max(1, math.ceil(p_max)))

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
    if last_p > first_p:
        stroke_color = "#3fb950"
        gradient_stop = "#3fb950"
    elif last_p < first_p:
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

    y_top_label = int(y_max)
    y_bot_label = 0

    svg_parts = [
        f'<svg viewBox="0 0 {width} {height}" class="{svg_class}" data-card-id="{safe_card_id}" xmlns="http://www.w3.org/2000/svg">',
        "<defs>",
        f'  <linearGradient id="{grad_id}" x1="0" y1="0" x2="0" y2="1">',
        f'    <stop class="chart-grad-stop" offset="0%" stop-color="{gradient_stop}" stop-opacity="0.15" />',
        f'    <stop class="chart-grad-stop" offset="100%" stop-color="{gradient_stop}" stop-opacity="0.0" />',
        "  </linearGradient>",
        "</defs>",
        f'<line x1="{padding_left}" y1="{padding_top}" x2="{width - padding_right}" y2="{padding_top}" stroke="#21262d" stroke-dasharray="3,3" />',
        f'<line x1="{padding_left}" y1="{bottom_y}" x2="{width - padding_right}" y2="{bottom_y}" stroke="#30363d" />',
        f'<text x="{padding_left - 6}" y="{padding_top + 4}" class="chart-y-max" fill="#8b949e" font-size="10" text-anchor="end">${y_top_label}</text>',
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

    low_y: Optional[float] = None
    low_gap: float = 0.0
    if valid_low is not None:
        _, low_y = get_coords(n - 1, valid_low)
        _, last_y = coords[-1]
        low_gap = abs(low_y - last_y)

    svg_parts.append('<g class="low-price-line-layer">')
    if valid_low is not None and low_y is not None and low_gap >= 5.0:
        _, last_y = coords[-1]
        svg_parts.append(
            f'<line x1="{last_x}" y1="{last_y}" x2="{last_x}" y2="{low_y}" '
            f'stroke="#bc8cff" stroke-width="1.2" stroke-dasharray="2,2" opacity="0.85" class="low-price-connector" />'
        )
    svg_parts.append("</g>")

    svg_parts.append(
        f'<polyline class="chart-line" points="{polyline_pts}" fill="none" stroke="{stroke_color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />'
    )

    for i, ((x, y), (d, val)) in enumerate(zip(coords, points)):
        is_acq = i == acq_idx
        is_last = i == n - 1
        low_attr = f' data-low-price="${valid_low:.2f}"' if (is_last and valid_low is not None) else ""
        hit_r: float = min(6.0, round(low_gap / 2.0, 1)) if (is_last and valid_low is not None and low_gap >= 8.0) else 9
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
            f'<circle cx="{x}" cy="{y}" r="{hit_r}" class="chart-hit-point" fill="transparent" data-date="{html.escape(d)}" data-price="${val:.2f}"{acq_attr}{low_attr} style="cursor: crosshair; pointer-events: all;" />'
        )

    svg_parts.append('<g class="low-price-dot-layer">')
    if valid_low is not None and low_y is not None and not (acq_idx == n - 1 and low_gap < 5.0):
        svg_parts.append(
            f'<circle cx="{last_x}" cy="{low_y}" r="3.0" class="low-price-dot" fill="#0d1117" stroke="#bc8cff" stroke-width="1.5" style="pointer-events: none;" />'
        )
        if low_gap >= 8.0:
            last_date = points[-1][0]
            low_hit_r = min(6.0, round(low_gap / 2.0, 1))
            svg_parts.append(
                f'<circle cx="{last_x}" cy="{low_y}" r="{low_hit_r}" class="chart-hit-point low-hit-point" fill="transparent" '
                f'data-date="{html.escape(last_date)}" data-price="${valid_low:.2f}" data-low="true" '
                f'style="cursor: crosshair; pointer-events: all;" />'
            )
    svg_parts.append("</g>")

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
    set_total = sum(prices) if prices else None
    return {
        "min": _fmt_quant_stat(q[0]),
        "p25": _fmt_quant_stat(q[1]),
        "med": _fmt_quant_stat(q[2]),
        "p75": _fmt_quant_stat(q[3]),
        "max": _fmt_quant_stat(q[4]),
        "set_total": _fmt_quant_stat(set_total),
        "priced_count": len(prices),
        "total_count": len(card_list),
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
      padding-bottom: 12px;
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
    .set-switcher-row {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 12px;
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
    .acq-base-label,
    .price-low-tooltip {
      position: relative;
      text-decoration: underline dotted var(--text-muted);
      cursor: help;
    }
    .price-low-tooltip { text-underline-offset: 4px; }
    .acq-base-label::after,
    .price-low-tooltip::after {
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
    .acq-base-label:hover::after,
    .price-low-tooltip:hover::after { opacity: 1; visibility: visible; }
    .chart-legend {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 6px 14px;
      margin-top: 12px;
      padding: 0 17px;
      font-size: 12px;
      font-weight: 500;
      color: #c9d1d9;
    }
    .legend-item {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      white-space: nowrap;
    }
    .legend-line {
      display: inline-block;
      width: 12px;
      height: 2px;
      border-radius: 1px;
    }
    .legend-dot {
      display: inline-block;
      width: 8px;
      height: 8px;
      border-radius: 50%;
      box-sizing: border-box;
    }
    .legend-dashed-line {
      display: inline-block;
      width: 14px;
      height: 0;
      border-top: 2px dashed #bc8cff;
    }
    .acq-dot-halo, .acq-dot-core, .low-price-dot { pointer-events: none; }
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
    .chart-dot, .low-price-dot { transition: r 0.12s ease, opacity 0.12s ease; }
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
    html.preload-comparison #cluster-basis + .cluster-divider,
    html.preload-comparison #cluster-low,
    html.preload-comparison #divider-low { display: none !important; }
    html.preload-comparison #btn-beta { background: transparent !important; color: var(--text-muted) !important; border-color: transparent !important; }
    html.preload-comparison #btn-comparison { background: var(--card-bg) !important; color: var(--accent) !important; border-color: var(--card-border) !important; }
    html.preload-comparison #meta-snapshots,
    html.preload-comparison #chart-legend { display: none !important; }
    html.preload-retail .card-tile[data-edition="beta"] { display: none !important; }
    html.preload-retail .card-tile[data-edition="retail"] { display: flex !important; }
    html.preload-retail .edition-stats-beta { display: none !important; }
    html.preload-retail .edition-stats-retail { display: flex !important; }
    html.preload-retail #btn-beta { background: transparent !important; color: var(--text-muted) !important; border-color: transparent !important; }
    html.preload-retail #btn-retail { background: var(--card-bg) !important; color: var(--accent) !important; border-color: var(--card-border) !important; }
    html.preload-ncb #edition-toggle,
    html.preload-ncb #view-comparison,
    html.preload-ncb #section-IL,
    html.preload-ncb #section-IO,
    html.preload-ncb #section-IS,
    html.preload-ncb #section-BT,
    html.preload-ncb #ms-rarity,
    html.preload-ncb #type-item-legend { display: none !important; }
    html.preload-ncb #cluster-view,
    html.preload-ncb #ncb-view-toggle { display: inline-flex !important; }
    html.preload-ncb #divider-view,
    html.preload-ncb #divider-low,
    html.preload-ncb #section-NR,
    html.preload-ncb #section-NRC { display: block !important; }
    html.preload-ncb #ms-variant { display: inline-block !important; }
    html.preload-ncb #cluster-basis,
    html.preload-ncb #cluster-low { display: inline-flex !important; }
    html.preload-ncb #meta-snapshots { display: block !important; }
    html.preload-ncb #chart-legend { display: flex !important; }
    html.preload-ncb #btn-set-wnc { background: transparent !important; color: var(--text-muted) !important; border-color: transparent !important; }
    html.preload-ncb #btn-set-ncb { background: var(--card-bg) !important; color: var(--accent) !important; border-color: var(--card-border) !important; }
    html.preload-ncb-table #section-NR,
    html.preload-ncb-table #section-NRC,
    html.preload-ncb-table #cluster-basis,
    html.preload-ncb-table #cluster-basis + .cluster-divider,
    html.preload-ncb-table #cluster-low,
    html.preload-ncb-table #divider-low,
    html.preload-ncb-table #meta-snapshots,
    html.preload-ncb-table #chart-legend { display: none !important; }
    html.preload-ncb-table #view-ncb-table { display: block !important; }
    html.preload-ncb-table #btn-ncb-charts { background: transparent !important; color: var(--text-muted) !important; border-color: transparent !important; }
    html.preload-ncb-table #btn-ncb-table { background: var(--card-bg) !important; color: var(--accent) !important; border-color: var(--card-border) !important; }
    html.preload-anchor-lifetime #btn-anchor-acq,
    html.preload-anchor-l7d #btn-anchor-acq { background: transparent !important; color: var(--text-muted) !important; border-color: transparent !important; }
    html.preload-anchor-lifetime #btn-anchor-lifetime,
    html.preload-anchor-l7d #btn-anchor-l7d { background: var(--card-bg) !important; color: var(--accent) !important; border-color: var(--card-border) !important; }
    html.preload-hydrating *,
    html.preload-hydrating *::before,
    html.preload-hydrating *::after { transition: none !important; }
    html.preload-header-pending .sticky-nav { visibility: hidden; }
    html.preload-count-pending #count-display { visibility: hidden; }"""


_HEADER_HYDRATION_JS = r"""    const multiselectCategories = ['color', 'type', 'rarity', 'variant', 'ownership'];

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

    function syncClusterDividers() {
      const dividers = document.querySelectorAll('.cluster-divider');
      dividers.forEach(d => {
        const prev = d.previousElementSibling;
        const next = d.nextElementSibling;
        if ((prev && prev.style.display === 'none') || (next && next.style.display === 'none')) {
          d.style.display = 'none';
          return;
        }
        d.style.display = 'block';
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

    function syncStickyNavHeight() {
      syncClusterDividers();
      const nav = document.querySelector('.sticky-nav');
      if (nav) document.documentElement.style.setProperty('--sticky-nav-height', `${nav.offsetHeight}px`);
      if (typeof syncTierHeaderStickyBounds === 'function') syncTierHeaderStickyBounds();
    }

    function applyAnchorButtons(anchor) {
      const btnLife = document.getElementById('btn-anchor-lifetime');
      const btnAcq = document.getElementById('btn-anchor-acq');
      const btnL7d = document.getElementById('btn-anchor-l7d');
      if (btnLife) btnLife.classList.toggle('active', anchor === 'lifetime');
      if (btnAcq) btnAcq.classList.toggle('active', anchor === 'acq');
      if (btnL7d) btnL7d.classList.toggle('active', anchor === 'l7d');
    }

    function applyLowModeControls(lowMode) {
      const mode = _normalizeLowMode(lowMode);
      const lowSel = document.getElementById('low-price-select');
      if (lowSel) lowSel.value = mode;
      const legendLow = document.getElementById('legend-low-price');
      if (!legendLow) return;
      if (mode === 'hide') {
        legendLow.style.display = 'none';
        return;
      }
      legendLow.style.display = '';
      const swatch = legendLow.querySelector('.legend-low-swatch');
      const label = legendLow.querySelector('.legend-low-label');
      if (swatch) {
        if (mode === 'all') {
          swatch.className = 'legend-dashed-line legend-low-swatch';
          swatch.removeAttribute('style');
        } else {
          swatch.className = 'legend-dot legend-low-swatch';
          swatch.setAttribute('style', 'background:#0d1117;border:1.5px solid #bc8cff;');
        }
      }
      if (label) {
        label.textContent = mode === 'all'
          ? 'Historical TCG Low (excl. shipping)'
          : 'Current TCG Low (excl. shipping)';
      }
    }

    function applyHeaderFromDescriptor(desc, activeSet, activeEdition, activeNcbView) {
      const titleEl = document.getElementById('dashboard-title');
      if (titleEl) titleEl.textContent = desc.title;
      document.title = desc.title;

      const setWnc = document.getElementById('btn-set-wnc');
      const setNcb = document.getElementById('btn-set-ncb');
      if (setWnc) setWnc.classList.toggle('active', activeSet === 'wnc');
      if (setNcb) setNcb.classList.toggle('active', activeSet === 'ncb');

      const betaBtn = document.getElementById('btn-beta');
      const retailBtn = document.getElementById('btn-retail');
      const compBtn = document.getElementById('btn-comparison');
      if (betaBtn) betaBtn.classList.toggle('active', activeEdition === 'beta');
      if (retailBtn) retailBtn.classList.toggle('active', activeEdition === 'retail');
      if (compBtn) compBtn.classList.toggle('active', activeEdition === 'comparison');

      const ncbChartsBtn = document.getElementById('btn-ncb-charts');
      const ncbTableBtn = document.getElementById('btn-ncb-table');
      const targetNcbView = activeNcbView === 'table' ? 'table' : 'charts';
      if (ncbChartsBtn) ncbChartsBtn.classList.toggle('active', targetNcbView === 'charts');
      if (ncbTableBtn) ncbTableBtn.classList.toggle('active', targetNcbView === 'table');

      const cView = document.getElementById('cluster-view');
      const dView = document.getElementById('divider-view');
      const edToggle = document.getElementById('edition-toggle');
      const ncbToggle = document.getElementById('ncb-view-toggle');
      const rarityMs = document.getElementById('ms-rarity');
      const variantMs = document.getElementById('ms-variant');
      const legendType = document.getElementById('type-item-legend');
      const cBasis = document.getElementById('cluster-basis');
      const dBasis = cBasis ? cBasis.nextElementSibling : null;
      const cLow = document.getElementById('cluster-low');
      const dLow = document.getElementById('divider-low');

      if (cView) cView.style.display = desc.showClusterView ? 'inline-flex' : 'none';
      if (dView) dView.style.display = desc.showClusterView ? 'block' : 'none';
      if (edToggle) edToggle.style.display = desc.showEditionToggle ? 'inline-flex' : 'none';
      if (ncbToggle) ncbToggle.style.display = desc.showNcbViewToggle ? 'inline-flex' : 'none';
      if (rarityMs) rarityMs.style.display = desc.showMsRarity ? '' : 'none';
      if (variantMs) variantMs.style.display = desc.showMsVariant ? 'inline-block' : 'none';
      if (legendType) legendType.style.display = desc.showLegendTypeItem ? '' : 'none';
      if (cBasis) cBasis.style.display = desc.showBasisCluster ? 'inline-flex' : 'none';
      if (dBasis) dBasis.style.display = desc.showBasisCluster ? 'block' : 'none';
      if (cLow) cLow.style.display = desc.showBasisCluster ? 'inline-flex' : 'none';
      if (dLow) dLow.style.display = desc.showBasisCluster ? 'block' : 'none';

      const sortEl = document.getElementById('sort-select');
      const sortNumOption = sortEl ? sortEl.querySelector('option[value="num-asc"]') : null;
      if (sortNumOption) sortNumOption.textContent = desc.sortNumLabel;

      const searchEl = document.getElementById('search-input');
      if (searchEl) searchEl.placeholder = resolveSearchPlaceholder(activeSet, activeEdition);

      const metaEl = document.getElementById('meta-snapshots');
      const legendEl = document.getElementById('chart-legend');
      const templates = (typeof snapshotTemplates !== 'undefined' && snapshotTemplates) ? snapshotTemplates : {};
      if (activeSet === 'ncb') {
        if (targetNcbView === 'table') {
          if (metaEl) metaEl.style.display = 'none';
          if (legendEl) legendEl.style.display = 'none';
        } else {
          if (metaEl) {
            metaEl.style.display = 'block';
            metaEl.textContent = templates.ncb || '';
          }
          if (legendEl) legendEl.style.display = 'flex';
        }
      } else if (activeEdition === 'comparison') {
        if (metaEl) metaEl.style.display = 'none';
        if (legendEl) legendEl.style.display = 'none';
      } else {
        if (metaEl) {
          metaEl.style.display = 'block';
          metaEl.textContent = activeEdition === 'retail' ? (templates.retail || '') : (templates.beta || '');
        }
        if (legendEl) legendEl.style.display = 'flex';
      }
    }

    function applyFilterControlValues(activeSet, filterObj) {
      const f = filterObj || defaultSetFilters();
      const searchEl = document.getElementById('search-input');
      if (searchEl) searchEl.value = typeof f.q === 'string' ? f.q : '';
      const mapByCategory = {
        ownership: f.o || [],
        rarity: activeSet === 'wnc' ? (f.r || []) : [],
        variant: activeSet === 'ncb' ? (f.r || []) : [],
        color: f.c || [],
        type: f.t || []
      };
      multiselectCategories.forEach(cat => {
        const vals = Array.isArray(mapByCategory[cat]) ? mapByCategory[cat] : [];
        const menu = document.getElementById(`menu-${cat}`);
        if (menu) {
          menu.querySelectorAll('input[type="checkbox"]').forEach(box => {
            box.checked = vals.includes(box.value);
          });
        }
        updateBadge(cat);
      });
      const sortEl = document.getElementById('sort-select');
      if (sortEl) {
        sortEl.value = (!f.s || f.s === 'num') ? 'num-asc' : f.s;
      }
      const resetBtn = document.getElementById('btn-reset-filters');
      if (resetBtn) resetBtn.classList.toggle('is-active', hasActiveFilterValues(f));
    }

    function hydrateHeaderControls() {
      const state = (typeof initialFilterState !== 'undefined' && initialFilterState)
        ? initialFilterState
        : normalizeSavedFilterState(null, typeof hasNcb !== 'undefined' ? hasNcb : true);
      const meta = (typeof setViewMeta !== 'undefined' && setViewMeta) ? setViewMeta : {};
      const desc = computeSetViewState(state.set, state.edition, meta, state.ncbView);
      applyHeaderFromDescriptor(desc, state.set, state.edition, state.ncbView);
      applyAnchorButtons(state.anchor);
      applyLowModeControls(state.lowMode);
      const filterObj = (state.filtersBySet && state.filtersBySet[state.set]) || defaultSetFilters();
      applyFilterControlValues(state.set, filterObj);

      const countEl = document.getElementById('count-display');
      const hasActive = hasActiveFilterValues(filterObj);
      if (countEl) {
        const unfilteredVisible = (state.set === 'wnc' && state.edition === 'retail' && meta.wncRetailCardCount !== undefined)
          ? Number(meta.wncRetailCardCount)
          : desc.countDenominator;
        countEl.textContent = `Showing ${unfilteredVisible} of ${desc.countDenominator} cards`;
      }
      document.documentElement.classList.remove('preload-header-pending');
      if (!hasActive) {
        document.documentElement.classList.remove('preload-count-pending');
      }
      syncStickyNavHeight();
    }"""


_CHART_GEOMETRY_JS = r"""    function computeLowModeChartGeometry(marketPrices, lowPoints, acqIdx, mode) {
      const width = 340;
      const height = 120;
      const paddingTop = 16;
      const paddingBottom = 22;
      const paddingLeft = 42;
      const paddingRight = 16;
      const plotW = width - paddingLeft - paddingRight;
      const plotH = height - paddingTop - paddingBottom;
      const bottomY = height - paddingBottom;
      const n = Array.isArray(marketPrices) ? marketPrices.length : 0;
      if (n < 2) return null;

      const resolvedMode = (mode === 'all' || mode === 'hide') ? mode : 'current';
      const lows = Array.isArray(lowPoints) && lowPoints.length === n ? lowPoints : [];
      const validLastLow = (resolvedMode !== 'hide' && lows.length === n && lows[n - 1] !== null && lows[n - 1] !== undefined && Number(lows[n - 1]) > 0)
        ? Number(lows[n - 1])
        : null;

      let pMax = Math.max(...marketPrices);
      if (resolvedMode === 'current' && validLastLow !== null) {
        pMax = Math.max(pMax, validLastLow);
      } else if (resolvedMode === 'all') {
        for (let i = 0; i < lows.length; i++) {
          const lv = lows[i];
          if (lv !== null && lv !== undefined && Number(lv) > 0) {
            pMax = Math.max(pMax, Number(lv));
          }
        }
      }

      const yMax = Math.max(1, Math.ceil(pMax));
      const yRange = yMax > 0 ? yMax : 1.0;
      const round1 = (v) => Math.round(v * 10) / 10;
      const getCoords = (idx, val) => ({
        x: round1(paddingLeft + (idx / (n - 1)) * plotW),
        y: round1(paddingTop + (1.0 - (val / yRange)) * plotH)
      });

      const marketCoords = marketPrices.map((val, i) => getCoords(i, val));
      const lowSegments = [];
      if (resolvedMode === 'all' && lows.length === n) {
        let seg = [];
        for (let i = 0; i < n; i++) {
          const lv = lows[i];
          if (lv !== null && lv !== undefined && Number(lv) > 0) {
            const pt = getCoords(i, Number(lv));
            seg.push({ idx: i, x: pt.x, y: pt.y });
          } else if (seg.length > 0) {
            lowSegments.push(seg);
            seg = [];
          }
        }
        if (seg.length > 0) lowSegments.push(seg);
      }

      const lastPt = marketCoords[n - 1];
      let lowY = null;
      let lowGap = 0;
      let showConnector = false;
      let lastHitR = 9;
      let showLowDot = false;
      let lowHitR = null;

      if (validLastLow !== null) {
        lowY = getCoords(n - 1, validLastLow).y;
        lowGap = Math.abs(lowY - lastPt.y);
        showConnector = (resolvedMode === 'current') && (lowGap >= 5.0);
        lastHitR = (lowGap >= 8.0) ? Math.min(6.0, round1(lowGap / 2.0)) : 9;
        showLowDot = !(acqIdx === n - 1 && lowGap < 5.0);
        lowHitR = (showLowDot && lowGap >= 8.0) ? Math.min(6.0, round1(lowGap / 2.0)) : null;
      }

      return {
        yMax,
        bottomY,
        marketCoords,
        lowSegments,
        validLastLow,
        lowY,
        lowGap,
        showConnector,
        lastHitR,
        showLowDot,
        lowHitR
      };
    }"""


_DASHBOARD_JS_BODY = r"""    const allowNcb = typeof hasNcb !== 'undefined' ? hasNcb : true;
    let activeSetState = (typeof initialFilterState !== 'undefined' && initialFilterState)
      ? normalizeSavedFilterState(initialFilterState, allowNcb)
      : normalizeSavedFilterState(null, allowNcb);
    let currentSet = activeSetState.set;
    let currentEdition = activeSetState.edition;
    let currentNcbView = activeSetState.ncbView || 'charts';
    let currentAnchor = activeSetState.anchor;
    let currentLowMode = activeSetState.lowMode || 'current';
    let activePricedSetTier = (activeSetState.filtersBySet[currentSet] && activeSetState.filtersBySet[currentSet].b) || 'all';
    const btnSetWnc = document.getElementById('btn-set-wnc');
    const btnSetNcb = document.getElementById('btn-set-ncb');
    const dashboardTitle = document.getElementById('dashboard-title');
    const clusterView = document.getElementById('cluster-view');
    const dividerView = document.getElementById('divider-view');
    const editionToggle = document.getElementById('edition-toggle');
    const ncbViewToggle = document.getElementById('ncb-view-toggle');
    const btnRetail = document.getElementById('btn-retail');
    const btnBeta = document.getElementById('btn-beta');
    const btnComparison = document.getElementById('btn-comparison');
    const btnNcbCharts = document.getElementById('btn-ncb-charts');
    const btnNcbTable = document.getElementById('btn-ncb-table');
    const compView = document.getElementById('view-comparison');
    const ncbTableView = document.getElementById('view-ncb-table');
    const clusterBasis = document.getElementById('cluster-basis');
    const dividerBasis = clusterBasis ? clusterBasis.nextElementSibling : null;
    const msRarity = document.getElementById('ms-rarity');
    const msVariant = document.getElementById('ms-variant');
    const typeItemLegend = document.getElementById('type-item-legend');
    const sectionNR = document.getElementById('section-NR');
    const sectionNRC = document.getElementById('section-NRC');
    const btnAnchorLifetime = document.getElementById('btn-anchor-lifetime');
    const btnAnchorAcq = document.getElementById('btn-anchor-acq');
    const btnAnchorL7d = document.getElementById('btn-anchor-l7d');
    const metaSnapshots = document.getElementById('meta-snapshots');
    const chartLegend = document.getElementById('chart-legend');
    const searchInput = document.getElementById('search-input');
    const sortSelect = document.getElementById('sort-select');
    const lowPriceSelect = document.getElementById('low-price-select');
    const countDisplay = document.getElementById('count-display');
    const tierCodes = Array.isArray(renderedTierCodes) && renderedTierCodes.length > 0 ? renderedTierCodes : ['IL', 'IO', 'IS'];
    const ncbSectionCodes = (typeof renderedNcbCodes !== 'undefined' && Array.isArray(renderedNcbCodes) && renderedNcbCodes.length > 0) ? renderedNcbCodes : ['NR'];
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
          ? `WNC Set Value (${pairedRows.length}/${visibleTierRows.length} pairs)`
          : 'WNC Set Value';
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

    function setPricedSetTier(tier, save = true) {
      activePricedSetTier = tier;
      document.querySelectorAll('.comp-pill-btn').forEach(btn => btn.classList.remove('is-active'));
      const activeBtn = document.getElementById(`pill-tier-${tier}`);
      if (activeBtn) activeBtn.classList.add('is-active');
      syncComparisonOverview();
      if (save) saveState();
    }

    const SVG_NS = 'http://www.w3.org/2000/svg';

    function renderChartForLowMode(svgEl, lowPoints, mode) {
      if (!svgEl) return;
      const hitPoints = Array.from(svgEl.querySelectorAll('.chart-hit-point:not(.low-hit-point)'));
      const n = hitPoints.length;
      if (n < 2) return;

      const marketPrices = hitPoints.map(hp => parseFloat(String(hp.dataset.price || '').replace(/[$,]/g, '')) || 0);
      const acqIdx = hitPoints.findIndex(hp => hp.dataset.acq === 'true');
      const geom = computeLowModeChartGeometry(marketPrices, lowPoints, acqIdx, mode);
      if (!geom) return;

      const yMaxEl = svgEl.querySelector('.chart-y-max');
      if (yMaxEl) yMaxEl.textContent = `$${geom.yMax}`;

      const polyPts = geom.marketCoords.map(pt => `${pt.x.toFixed(1)},${pt.y.toFixed(1)}`).join(' ');
      const firstX = geom.marketCoords[0].x.toFixed(1);
      const lastX = geom.marketCoords[n - 1].x.toFixed(1);
      const lastMktY = geom.marketCoords[n - 1].y.toFixed(1);
      const bottomY = geom.bottomY;

      const polyEl = svgEl.querySelector('polygon');
      if (polyEl) polyEl.setAttribute('points', `${polyPts} ${lastX},${bottomY} ${firstX},${bottomY}`);

      const lineEl = svgEl.querySelector('.chart-line');
      if (lineEl) lineEl.setAttribute('points', polyPts);

      const resolvedMode = _normalizeLowMode(mode);
      const lows = Array.isArray(lowPoints) && lowPoints.length === n ? lowPoints : [];

      for (let i = 0; i < n; i++) {
        const hp = hitPoints[i];
        const cx = geom.marketCoords[i].x.toFixed(1);
        const cy = geom.marketCoords[i].y.toFixed(1);
        hp.setAttribute('cx', cx);
        hp.setAttribute('cy', cy);
        hp.setAttribute('r', i === n - 1 ? String(geom.lastHitR) : '9');

        if (resolvedMode === 'hide') {
          hp.removeAttribute('data-low-price');
        } else if (resolvedMode === 'current') {
          if (i === n - 1 && geom.validLastLow !== null) {
            hp.setAttribute('data-low-price', `$${geom.validLastLow.toFixed(2)}`);
          } else {
            hp.removeAttribute('data-low-price');
          }
        } else {
          const lv = lows[i];
          if (lv !== null && lv !== undefined && Number(lv) > 0) {
            hp.setAttribute('data-low-price', `$${Number(lv).toFixed(2)}`);
          } else {
            hp.removeAttribute('data-low-price');
          }
        }

        const prev = hp.previousElementSibling;
        if (prev) {
          prev.setAttribute('cx', cx);
          prev.setAttribute('cy', cy);
          if (prev.classList.contains('acq-dot-core')) {
            const halo = prev.previousElementSibling;
            if (halo && halo.classList.contains('acq-dot-halo')) {
              halo.setAttribute('cx', cx);
              halo.setAttribute('cy', cy);
            }
          }
        }
      }

      const lineLayer = svgEl.querySelector('.low-price-line-layer');
      if (lineLayer) {
        lineLayer.textContent = '';
        if (geom.showConnector && geom.lowY !== null) {
          const conn = document.createElementNS(SVG_NS, 'line');
          conn.setAttribute('x1', lastX);
          conn.setAttribute('y1', lastMktY);
          conn.setAttribute('x2', lastX);
          conn.setAttribute('y2', geom.lowY.toFixed(1));
          conn.setAttribute('stroke', '#bc8cff');
          conn.setAttribute('stroke-width', '1.2');
          conn.setAttribute('stroke-dasharray', '2,2');
          conn.setAttribute('opacity', '0.85');
          conn.setAttribute('class', 'low-price-connector');
          lineLayer.appendChild(conn);
        } else if (resolvedMode === 'all') {
          geom.lowSegments.forEach(seg => {
            if (seg.length >= 2) {
              const pl = document.createElementNS(SVG_NS, 'polyline');
              pl.setAttribute('class', 'low-price-line');
              pl.setAttribute('points', seg.map(pt => `${pt.x.toFixed(1)},${pt.y.toFixed(1)}`).join(' '));
              pl.setAttribute('fill', 'none');
              pl.setAttribute('stroke', '#bc8cff');
              pl.setAttribute('stroke-width', '1.4');
              pl.setAttribute('stroke-dasharray', '3,2.5');
              pl.setAttribute('stroke-linecap', 'round');
              pl.setAttribute('stroke-linejoin', 'round');
              pl.setAttribute('opacity', '0.92');
              lineLayer.appendChild(pl);
            } else if (seg.length === 1 && !(seg[0].idx === n - 1 && geom.showLowDot)) {
              const iso = document.createElementNS(SVG_NS, 'circle');
              iso.setAttribute('cx', seg[0].x.toFixed(1));
              iso.setAttribute('cy', seg[0].y.toFixed(1));
              iso.setAttribute('r', '2.0');
              iso.setAttribute('class', 'low-price-isolated-dot');
              iso.setAttribute('fill', '#0d1117');
              iso.setAttribute('stroke', '#bc8cff');
              iso.setAttribute('stroke-width', '1.5');
              lineLayer.appendChild(iso);
            }
          });
        }
      }

      const dotLayer = svgEl.querySelector('.low-price-dot-layer');
      if (dotLayer) {
        dotLayer.textContent = '';
        if (geom.showLowDot && geom.lowY !== null && geom.validLastLow !== null) {
          const lowDot = document.createElementNS(SVG_NS, 'circle');
          lowDot.setAttribute('cx', lastX);
          lowDot.setAttribute('cy', geom.lowY.toFixed(1));
          lowDot.setAttribute('r', '3.0');
          lowDot.setAttribute('class', 'low-price-dot');
          lowDot.setAttribute('fill', '#0d1117');
          lowDot.setAttribute('stroke', '#bc8cff');
          lowDot.setAttribute('stroke-width', '1.5');
          lowDot.setAttribute('style', 'pointer-events: none;');
          dotLayer.appendChild(lowDot);

          if (geom.lowHitR !== null) {
            const lastDate = hitPoints[n - 1].dataset.date || '';
            const lowHit = document.createElementNS(SVG_NS, 'circle');
            lowHit.setAttribute('cx', lastX);
            lowHit.setAttribute('cy', geom.lowY.toFixed(1));
            lowHit.setAttribute('r', String(geom.lowHitR));
            lowHit.setAttribute('class', 'chart-hit-point low-hit-point');
            lowHit.setAttribute('fill', 'transparent');
            lowHit.setAttribute('data-date', lastDate);
            lowHit.setAttribute('data-price', `$${geom.validLastLow.toFixed(2)}`);
            lowHit.setAttribute('data-low', 'true');
            lowHit.setAttribute('style', 'cursor: crosshair; pointer-events: all;');
            dotLayer.appendChild(lowHit);
          }
        }
      }
    }

    function setLowMode(mode, save = true) {
      const normalized = _normalizeLowMode(mode);
      currentLowMode = normalized;
      activeSetState.lowMode = normalized;
      applyLowModeControls(normalized);

      const seriesMap = (typeof chartLowSeriesById !== 'undefined' && chartLowSeriesById) ? chartLowSeriesById : {};
      document.querySelectorAll('svg.chart-svg[data-card-id]').forEach(svgEl => {
        const cardId = svgEl.getAttribute('data-card-id');
        if (!cardId || !Object.prototype.hasOwnProperty.call(seriesMap, cardId)) return;
        renderChartForLowMode(svgEl, seriesMap[cardId], normalized);
      });

      if (save) saveState();
    }

    function applyTierStatsOpen(code, isOpen) {
      const group = document.getElementById(`stats-group-${code}`);
      const btn = document.getElementById(`btn-stats-${code}`);
      if (code === 'NR' || code === 'NRC') {
        const nrBox = document.getElementById(`stats-ncb-${code}`);
        if (nrBox) nrBox.style.display = isOpen ? 'flex' : 'none';
      } else {
        const activeBox = (currentEdition === 'retail')
          ? document.getElementById(`stats-retail-${code}`)
          : document.getElementById(`stats-beta-${code}`);
        const otherBox = (currentEdition === 'retail')
          ? document.getElementById(`stats-beta-${code}`)
          : document.getElementById(`stats-retail-${code}`);
        if (otherBox) otherBox.style.display = 'none';
        if (activeBox) activeBox.style.display = isOpen ? 'flex' : 'none';
      }
      if (group) group.classList.toggle('is-collapsed', !isOpen);
      if (btn) {
        btn.innerHTML = isOpen ? 'Stats <span class="stats-arrow">&#x25C2;</span>' : 'Stats <span class="stats-arrow">&#x25B8;</span>';
      }
    }

    function applyStatsOpenForCurrentSet(statsOpenMap) {
      const targetCodes = currentSet === 'ncb' ? ncbSectionCodes : tierCodes;
      const isWideViewport = window.innerWidth >= 768;
      targetCodes.forEach(code => applyTierStatsOpen(code, resolveTierStatsOpen(statsOpenMap, code, isWideViewport)));
    }

    function toggleTierStats(code) {
      const group = document.getElementById(`stats-group-${code}`);
      const activeBox = (code === 'NR' || code === 'NRC')
        ? document.getElementById(`stats-ncb-${code}`)
        : ((currentEdition === 'retail')
            ? document.getElementById(`stats-retail-${code}`)
            : document.getElementById(`stats-beta-${code}`));
      if (!activeBox || !group) return;
      const isHidden = activeBox.style.display === 'none' || !activeBox.style.display;
      applyTierStatsOpen(code, isHidden);
      // Persist statsOpen before saveState(), which merges live DOM controls via readOutgoingControls().
      activeSetState = applySetTierStatsToggle(activeSetState, currentSet, code, isHidden, allowNcb);
      saveState();
    }

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

    function readOutgoingControls() {
      const rawSort = sortSelect ? sortSelect.value : 'num-asc';
      const currentFilters = activeSetState.filtersBySet && activeSetState.filtersBySet[currentSet];
      return {
        q: searchInput ? searchInput.value : '',
        o: getSelected('ownership'),
        r: currentSet === 'ncb' ? getSelected('variant') : getSelected('rarity'),
        c: getSelected('color'),
        t: getSelected('type'),
        s: rawSort === 'num-asc' ? 'num' : rawSort,
        b: activePricedSetTier || 'all',
        statsOpen: _normalizeStatsOpenMap(currentFilters ? currentFilters.statsOpen : null)
      };
    }

    function applyControlsFromFilterObj(f) {
      applyFilterControlValues(currentSet, f);
      clearTableHeaderSorts(currentSet === 'ncb' ? ncbSectionCodes : tierCodes);
      setPricedSetTier((f && f.b) || 'all', false);
    }

    function saveState() {
      try {
        activeSetState.set = currentSet;
        activeSetState.edition = currentEdition;
        activeSetState.ncbView = currentNcbView;
        activeSetState.anchor = currentAnchor;
        activeSetState.lowMode = currentLowMode;
        activeSetState.filtersBySet[currentSet] = readOutgoingControls();
        localStorage.setItem(STORAGE_KEY, JSON.stringify(activeSetState));
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
      applyAnchorButtons(anchor);

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

    function applyViewDescriptor(desc) {
      applyHeaderFromDescriptor(desc, currentSet, currentEdition, currentNcbView);
      if (compView) compView.style.display = desc.showComparisonView ? 'block' : 'none';
      if (ncbTableView) ncbTableView.style.display = desc.showNcbTableView ? 'block' : 'none';
      if (sectionNR) sectionNR.style.display = desc.showSectionNR ? 'block' : 'none';
      if (sectionNRC) sectionNRC.style.display = desc.showSectionNRC ? 'block' : 'none';
    }

    function setNcbView(view, save = true) {
      const targetView = view === 'table' ? 'table' : 'charts';
      currentNcbView = targetView;
      activeSetState.ncbView = targetView;

      if (btnNcbCharts) btnNcbCharts.classList.toggle('active', targetView === 'charts');
      if (btnNcbTable) btnNcbTable.classList.toggle('active', targetView === 'table');

      if (currentSet !== 'ncb') {
        if (save) saveState();
        return;
      }

      const desc = computeSetViewState(currentSet, currentEdition, setViewMeta, currentNcbView);
      applyViewDescriptor(desc);

      tierCodes.forEach(code => {
        const s = document.getElementById(`section-${code}`);
        if (s) s.style.display = 'none';
      });

      applyFilters();
      if (typeof syncStickyNavHeight === 'function') syncStickyNavHeight();
      if (save) saveState();
    }

    function setActiveSet(nextSet, save = true) {
      const targetSet = (allowNcb && nextSet === 'ncb') ? 'ncb' : 'wnc';
      if (targetSet !== currentSet) {
        const outgoing = readOutgoingControls();
        activeSetState = switchSetFilterState(activeSetState, targetSet, outgoing, allowNcb);
        currentSet = activeSetState.set;
        applyControlsFromFilterObj(activeSetState.filtersBySet[currentSet]);
      } else {
        currentSet = targetSet;
        activeSetState.set = targetSet;
      }

      const desc = computeSetViewState(currentSet, currentEdition, setViewMeta, currentNcbView);
      applyViewDescriptor(desc);

      if (currentSet === 'ncb') {
        const ncbFilters = activeSetState.filtersBySet.ncb || defaultSetFilters();
        applyStatsOpenForCurrentSet(ncbFilters.statsOpen);
        setNcbView(currentNcbView, false);
      } else {
        setEdition(currentEdition, false);
        const wncFilters = activeSetState.filtersBySet.wnc || defaultSetFilters();
        applyStatsOpenForCurrentSet(wncFilters.statsOpen);
      }

      if (typeof syncStickyNavHeight === 'function') syncStickyNavHeight();
      if (save) saveState();
    }

    function setEdition(edition, save = true) {
      currentEdition = edition;
      activeSetState.edition = edition;

      if (btnComparison) btnComparison.classList.toggle('active', edition === 'comparison');
      if (btnRetail) btnRetail.classList.toggle('active', edition === 'retail');
      if (btnBeta) btnBeta.classList.toggle('active', edition === 'beta');

      if (currentSet === 'ncb') {
        if (save) saveState();
        return;
      }

      const desc = computeSetViewState(currentSet, currentEdition, setViewMeta, currentNcbView);
      applyViewDescriptor(desc);

      if (edition === 'comparison') {
        tierCodes.forEach(code => {
          const s = document.getElementById(`section-${code}`);
          if (s) s.style.display = 'none';
        });
      } else {
        tierCodes.forEach(code => {
          const s = document.getElementById(`section-${code}`);
          if (s) s.style.display = 'block';
        });

        const showRetailStats = edition === 'retail';
        tierCodes.forEach(code => {
          const group = document.getElementById(`stats-group-${code}`);
          const isExpanded = group && !group.classList.contains('is-collapsed');
          const dr = document.getElementById(`stats-retail-${code}`);
          const db = document.getElementById(`stats-beta-${code}`);
          if (dr) dr.style.display = (showRetailStats && isExpanded) ? 'flex' : 'none';
          if (db) db.style.display = (!showRetailStats && isExpanded) ? 'flex' : 'none';
        });
      }

      applyFilters();
      if (typeof syncStickyNavHeight === 'function') syncStickyNavHeight();
      if (save) saveState();
    }

    function positionChartTooltip(e) {
      if (!tooltip || tooltip.style.display !== 'block') return;
      const tipW = tooltip.offsetWidth || 220;
      const tipH = tooltip.offsetHeight || 28;
      let left = e.clientX + 12;
      let top = e.clientY - tipH - 8;
      if (top < 8) top = e.clientY + 16;
      if (left + tipW + 8 > window.innerWidth) {
        left = Math.max(8, e.clientX - tipW - 12);
      }
      tooltip.style.left = left + 'px';
      tooltip.style.top = top + 'px';
    }

    document.addEventListener('mouseover', (e) => {
      const hit = e.target.closest('.chart-hit-point');
      if (hit) {
        if (hit.dataset.low === 'true') {
          tooltip.textContent = `${hit.dataset.date} (TCG Low): ${hit.dataset.price}`;
        } else if (hit.dataset.acq) {
          const suffix = hit.dataset.lowPrice ? ` | Low: ${hit.dataset.lowPrice}` : '';
          tooltip.textContent = `${hit.dataset.date} (Acq base: ${hit.dataset.acqCost}): ${hit.dataset.price}${suffix}`;
        } else if (hit.dataset.lowPrice) {
          tooltip.textContent = `${hit.dataset.date}: ${hit.dataset.price} | Low: ${hit.dataset.lowPrice}`;
        } else {
          tooltip.textContent = `${hit.dataset.date}: ${hit.dataset.price}`;
        }
        tooltip.style.display = 'block';
        positionChartTooltip(e);
        const dot = hit.previousElementSibling;
        if (dot && dot.classList.contains('chart-dot')) {
          dot.setAttribute('r', '4.5');
          dot.classList.add('is-active');
        } else if (dot && dot.classList.contains('low-price-dot')) {
          dot.setAttribute('r', '4.5');
        }
      }
    });

    document.addEventListener('mousemove', positionChartTooltip);

    document.addEventListener('mouseout', (e) => {
      const hit = e.target.closest('.chart-hit-point');
      if (hit) {
        if (tooltip) tooltip.style.display = 'none';
        const dot = hit.previousElementSibling;
        if (dot && dot.classList.contains('chart-dot')) {
          dot.setAttribute('r', '2.5');
          dot.classList.remove('is-active');
        } else if (dot && dot.classList.contains('low-price-dot')) {
          dot.setAttribute('r', '3.0');
        }
      }
    });

    function tileMatchesFilters(tile, q, r, c, t, o) {
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
      return matchesQ && matchesR && matchesC && matchesT && matchesO;
    }

    function sortCardTiles(tiles, sort) {
      tiles.sort((a, b) => {
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
    }

    function applyFilters() {
      const q = searchInput.value.trim().toLowerCase();
      const r = currentSet === 'ncb' ? getSelected('variant') : getSelected('rarity');
      const c = getSelected('color');
      const t = getSelected('type');
      const o = getSelected('ownership');
      const sort = sortSelect.value;

      multiselectCategories.forEach(cat => updateBadge(cat));

      const viewDesc = computeSetViewState(currentSet, currentEdition, setViewMeta, currentNcbView);
      let totalVisible = 0;

      if (currentSet === 'ncb') {
        if (currentNcbView === 'table') {
          if (sectionNR) sectionNR.style.display = 'none';
          if (sectionNRC) sectionNRC.style.display = 'none';

          const rows = document.querySelectorAll('tr.ncb-comp-row');
          const sectionCounts = {};
          const sectionTotals = {};
          ncbSectionCodes.forEach(code => {
            sectionCounts[code] = 0;
            sectionTotals[code] = { mktPrice: 0, lowPrice: 0, c7dDlr: 0, pricedCount: 0, lowCount: 0, visibleCount: 0, owned: 0 };
          });

          rows.forEach(row => {
            const name = row.dataset.name;
            const num = row.dataset.num.toLowerCase();
            const cleanNum = num.replace(/^b/, '');
            const rarity = row.dataset.rarity;
            const color = row.dataset.color;
            const cardType = row.dataset.type;
            const isOwned = (parseInt(row.dataset.owned, 10) || 0) > 0;

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
                if (row.dataset.priced === '1') {
                  sectionTotals[rarity].mktPrice += parseFloat(row.dataset.mktPrice) || 0;
                  sectionTotals[rarity].c7dDlr += parseFloat(row.dataset.c7dDlr) || 0;
                  sectionTotals[rarity].pricedCount++;
                }
                if (row.dataset.hasLow === '1') {
                  sectionTotals[rarity].lowPrice += parseFloat(row.dataset.lowPrice) || 0;
                  sectionTotals[rarity].lowCount++;
                }
                sectionTotals[rarity].owned += parseInt(row.dataset.owned, 10) || 0;
              }
            } else {
              row.style.display = 'none';
            }
          });

          ncbSectionCodes.forEach(code => {
            const sub = document.getElementById(`ncb-sub-${code}`);
            if (sub) sub.style.display = (sectionCounts[code] > 0) ? 'block' : 'none';
            const tot = sectionTotals[code];
            const labelEl = document.getElementById(`total-label-${code}`);
            const mktEl = document.getElementById(`total-mkt-price-${code}`);
            const lowEl = document.getElementById(`total-low-price-${code}`);
            const c7dDlrEl = document.getElementById(`total-c7d-dlr-${code}`);
            const c7dPctEl = document.getElementById(`total-c7d-pct-${code}`);
            const ownedEl = document.getElementById(`total-owned-${code}`);

            if (tot && mktEl && lowEl && c7dDlrEl && c7dPctEl && ownedEl) {
              if (labelEl) {
                labelEl.textContent = (tot.pricedCount < tot.visibleCount)
                  ? `Total (${tot.pricedCount}/${tot.visibleCount} priced)`
                  : 'Total';
              }
              c7dDlrEl.classList.remove('comp-premium-pos', 'comp-premium-neg');
              c7dPctEl.classList.remove('comp-premium-pos', 'comp-premium-neg');
              if (tot.pricedCount > 0) {
                mktEl.textContent = `$${tot.mktPrice.toFixed(2)}`;
                const prev7dSum = tot.mktPrice - tot.c7dDlr;
                const c7dPct = prev7dSum > 0 ? (tot.c7dDlr / prev7dSum * 100) : 0;
                let dlrStr = '$0.00';
                if (tot.c7dDlr > 0) dlrStr = `+$${tot.c7dDlr.toFixed(2)}`;
                else if (tot.c7dDlr < 0) dlrStr = `-$${Math.abs(tot.c7dDlr).toFixed(2)}`;
                c7dDlrEl.textContent = dlrStr;
                c7dPctEl.textContent = `${tot.c7dDlr > 0 ? '+' : ''}${c7dPct.toFixed(1)}%`;
                if (tot.c7dDlr > 0) {
                  c7dDlrEl.classList.add('comp-premium-pos');
                  c7dPctEl.classList.add('comp-premium-pos');
                } else if (tot.c7dDlr < 0) {
                  c7dDlrEl.classList.add('comp-premium-neg');
                  c7dPctEl.classList.add('comp-premium-neg');
                }
              } else {
                mktEl.textContent = 'N/A';
                c7dDlrEl.textContent = 'N/A';
                c7dPctEl.textContent = 'N/A';
              }
              if (tot.lowCount > 0) {
                lowEl.textContent = (tot.lowCount < tot.pricedCount)
                  ? `$${tot.lowPrice.toFixed(2)} (${tot.lowCount}/${tot.pricedCount})`
                  : `$${tot.lowPrice.toFixed(2)}`;
              } else {
                lowEl.textContent = 'N/A';
              }
              ownedEl.textContent = tot.owned;
            }

            const table = document.getElementById(`table-${code}`);
            if (!table) return;
            const tbody = table.querySelector('tbody');
            if (!tbody) return;
            const activeTh = table.querySelector('th.sortable.sort-asc, th.sortable.sort-desc');
            const activeCol = activeTh ? activeTh.dataset.col : null;
            const activeDir = activeTh && activeTh.classList.contains('sort-asc') ? 1 : -1;
            const tableRows = Array.from(tbody.querySelectorAll('tr.ncb-comp-row'));
            tableRows.sort((a, b) => {
              if (activeCol) {
                return compareTableRowsByCol(a, b, activeCol, activeDir);
              }
              if (sort === 'price-desc') {
                const cmp = compareNullableNums(a.dataset.mktPrice, b.dataset.mktPrice, -1);
                if (cmp !== 0) return cmp;
              } else if (sort === 'price-asc') {
                const cmp = compareNullableNums(a.dataset.mktPrice, b.dataset.mktPrice, 1);
                if (cmp !== 0) return cmp;
              } else if (sort === 'gain-desc' || sort === 'gain-all-desc' || sort === 'gain-acq-desc') {
                const cmp = compareNullableNums(a.dataset.c7dPct, b.dataset.c7dPct, -1);
                if (cmp !== 0) return cmp;
              }
              return (parseInt(a.dataset.num.replace(/\D/g, ''), 10) || 0) - (parseInt(b.dataset.num.replace(/\D/g, ''), 10) || 0);
            });
            tableRows.forEach(row => tbody.appendChild(row));
          });

          countDisplay.textContent = `Showing ${totalVisible} of ${viewDesc.countDenominator} cards`;
          const hasActiveFilters = Boolean(q || r.length > 0 || c.length > 0 || t.length > 0 || o.length > 0);
          const resetBtn = document.getElementById('btn-reset-filters');
          if (resetBtn) resetBtn.classList.toggle('is-active', hasActiveFilters);
          if (typeof syncTierHeaderStickyBounds === 'function') syncTierHeaderStickyBounds();
          return;
        }

        ncbSectionCodes.forEach(code => {
          const sectionElem = document.getElementById(`section-${code}`);
          const gridElem = document.getElementById(`grid-${code}`);
          if (!sectionElem || !gridElem) return;

          const tiles = Array.from(gridElem.getElementsByClassName('card-tile'));
          let sectionVisible = 0;
          tiles.forEach(tile => {
            if (tileMatchesFilters(tile, q, r, c, t, o)) {
              tile.style.display = 'flex';
              sectionVisible++;
              totalVisible++;
            } else {
              tile.style.display = 'none';
            }
          });
          sectionElem.style.display = sectionVisible > 0 ? 'block' : 'none';
          sortCardTiles(tiles, sort);
          tiles.forEach(tile => gridElem.appendChild(tile));
        });

        countDisplay.textContent = `Showing ${totalVisible} of ${viewDesc.countDenominator} cards`;
        const hasActiveFilters = Boolean(q || r.length > 0 || c.length > 0 || t.length > 0 || o.length > 0);
        const resetBtn = document.getElementById('btn-reset-filters');
        if (resetBtn) resetBtn.classList.toggle('is-active', hasActiveFilters);
        if (typeof syncTierHeaderStickyBounds === 'function') syncTierHeaderStickyBounds();
        return;
      }

      if (sectionNR) sectionNR.style.display = 'none';
      if (sectionNRC) sectionNRC.style.display = 'none';
      if (ncbTableView) ncbTableView.style.display = 'none';

      if (currentEdition === 'comparison') {
        const rows = document.querySelectorAll('tr.comp-row');
        const sectionCounts = {};
        const sectionTotals = {};
        tierCodes.forEach(code => {
          sectionCounts[code] = 0;
          sectionTotals[code] = { bPrice: 0, rPrice: 0, pairedCount: 0, visibleCount: 0, bOwned: 0, rOwned: 0 };
        });

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
          const activeTh = table.querySelector('th.sortable.sort-asc, th.sortable.sort-desc');
          const activeCol = activeTh ? activeTh.dataset.col : null;
          const activeDir = activeTh && activeTh.classList.contains('sort-asc') ? 1 : -1;
          const tableRows = Array.from(tbody.querySelectorAll('tr.comp-row'));
          tableRows.sort((a, b) => {
            if (activeCol) {
              return compareTableRowsByCol(a, b, activeCol, activeDir);
            }
            if (sort === 'price-desc') {
              const cmp = compareNullableNums(a.dataset.bPrice, b.dataset.bPrice, -1);
              if (cmp !== 0) return cmp;
            } else if (sort === 'price-asc') {
              const cmp = compareNullableNums(a.dataset.bPrice, b.dataset.bPrice, 1);
              if (cmp !== 0) return cmp;
            } else if (sort === 'gain-desc' || sort === 'gain-all-desc' || sort === 'gain-acq-desc') {
              const cmp = compareNullableNums(a.dataset.spreadPct, b.dataset.spreadPct, -1);
              if (cmp !== 0) return cmp;
            }
            return (parseInt(a.dataset.num.replace(/\D/g, ''), 10) || 0) - (parseInt(b.dataset.num.replace(/\D/g, ''), 10) || 0);
          });
          tableRows.forEach(row => tbody.appendChild(row));
        });

        syncComparisonOverview();
        countDisplay.textContent = `Showing ${totalVisible} of ${viewDesc.countDenominator} cards`;
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

          if (tileMatchesFilters(tile, q, r, c, t, o)) {
            tile.style.display = 'flex';
            sectionVisible++;
            totalVisible++;
          } else {
            tile.style.display = 'none';
          }
        });

        sectionElem.style.display = sectionVisible > 0 ? 'block' : 'none';

        const activeTiles = tiles.filter(tile => tile.dataset.edition === currentEdition);
        sortCardTiles(activeTiles, sort);
        activeTiles.forEach(tile => gridElem.appendChild(tile));
      });

      countDisplay.textContent = `Showing ${totalVisible} of ${viewDesc.countDenominator} cards`;
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
    sortSelect.addEventListener('change', () => {
      clearTableHeaderSorts(currentSet === 'ncb' ? ncbSectionCodes : tierCodes);
      onFilterChange();
    });
    if (lowPriceSelect) {
      lowPriceSelect.addEventListener('change', () => {
        setLowMode(lowPriceSelect.value, true);
      });
    }

    function clearTableHeaderSorts(codes) {
      const targetCodes = Array.isArray(codes) ? codes : tierCodes.concat(ncbSectionCodes);
      targetCodes.forEach(code => {
        const table = document.getElementById(`table-${code}`);
        if (table) {
          table.querySelectorAll('th.sortable').forEach(h => h.classList.remove('sort-asc', 'sort-desc'));
        }
      });
    }

    function compareTableRowsByCol(a, b, col, dir) {
      let cmp = 0;
      if (col === 'num') cmp = dir * ((parseInt(a.dataset.num.replace(/\D/g, ''), 10) || 0) - (parseInt(b.dataset.num.replace(/\D/g, ''), 10) || 0));
      else if (col === 'name') cmp = dir * a.dataset.name.localeCompare(b.dataset.name);
      else if (col === 'type') cmp = dir * a.dataset.type.localeCompare(b.dataset.type);
      else if (col === 'b_price') cmp = compareNullableNums(a.dataset.bPrice, b.dataset.bPrice, dir);
      else if (col === 'r_price') cmp = compareNullableNums(a.dataset.rPrice, b.dataset.rPrice, dir);
      else if (col === 'spread_dlr') cmp = compareNullableNums(a.dataset.spreadDlr, b.dataset.spreadDlr, dir);
      else if (col === 'spread_pct') cmp = compareNullableNums(a.dataset.spreadPct, b.dataset.spreadPct, dir);
      else if (col === 'b_owned') cmp = dir * ((parseInt(a.dataset.ownedB, 10) || 0) - (parseInt(b.dataset.ownedB, 10) || 0));
      else if (col === 'r_owned') cmp = dir * ((parseInt(a.dataset.ownedR, 10) || 0) - (parseInt(b.dataset.ownedR, 10) || 0));
      else if (col === 'mkt_price') cmp = compareNullableNums(a.dataset.mktPrice, b.dataset.mktPrice, dir);
      else if (col === 'low_price') cmp = compareNullableNums(a.dataset.lowPrice, b.dataset.lowPrice, dir);
      else if (col === 'c7d_dlr') cmp = compareNullableNums(a.dataset.c7dDlr, b.dataset.c7dDlr, dir);
      else if (col === 'c7d_pct') cmp = compareNullableNums(a.dataset.c7dPct, b.dataset.c7dPct, dir);
      else if (col === 'owned') cmp = dir * ((parseInt(a.dataset.owned, 10) || 0) - (parseInt(b.dataset.owned, 10) || 0));
      if (cmp !== 0) return cmp;
      return (parseInt(a.dataset.num.replace(/\D/g, ''), 10) || 0) - (parseInt(b.dataset.num.replace(/\D/g, ''), 10) || 0);
    }

    function initFilters() {
      const rawSaved = loadState();
      activeSetState = normalizeSavedFilterState(rawSaved, allowNcb);
      currentSet = activeSetState.set;
      currentEdition = activeSetState.edition;
      currentNcbView = activeSetState.ncbView || 'charts';
      currentAnchor = activeSetState.anchor;
      currentLowMode = activeSetState.lowMode || 'current';
      applyControlsFromFilterObj(activeSetState.filtersBySet[currentSet]);
      setActiveSet(currentSet, false);
      setAnchor(currentAnchor, false);
      if (currentLowMode !== 'current') {
        setLowMode(currentLowMode, false);
      } else {
        applyLowModeControls('current');
      }
      const root = document.documentElement;
      root.classList.remove(
        'preload-comparison',
        'preload-retail',
        'preload-ncb',
        'preload-ncb-table',
        'preload-anchor-lifetime',
        'preload-anchor-l7d',
        'preload-header-pending',
        'preload-count-pending'
      );
      void root.offsetHeight;
      root.classList.remove('preload-hydrating');
    }

    document.querySelectorAll('.comp-table th.sortable').forEach(th => {
      th.addEventListener('click', () => {
        const table = th.closest('table');
        const tbody = table.querySelector('tbody');
        const rows = Array.from(tbody.querySelectorAll('tr.comp-row, tr.ncb-comp-row'));
        const col = th.dataset.col;
        const isAsc = th.classList.contains('sort-asc');

        table.querySelectorAll('th.sortable').forEach(h => h.classList.remove('sort-asc', 'sort-desc'));
        th.classList.toggle('sort-asc', !isAsc);
        th.classList.toggle('sort-desc', isAsc);
        const dir = isAsc ? -1 : 1;

        rows.sort((a, b) => compareTableRowsByCol(a, b, col, dir));

        rows.forEach(r => tbody.appendChild(r));
      });
    });

    function syncTierHeaderStickyBounds() {
      const allHeaders = tierCodes.concat(ncbSectionCodes);
      if ((currentSet === 'wnc' && currentEdition === 'comparison') || (currentSet === 'ncb' && currentNcbView === 'table')) {
        allHeaders.forEach(code => {
          const h = document.getElementById(`header-${code}`);
          if (h) h.style.transform = '';
        });
        return;
      }
      const nav = document.querySelector('.sticky-nav');
      const navHeight = nav ? nav.offsetHeight : 128;
      const headerMarginBottom = 12;
      const activeCodes = currentSet === 'ncb' ? ncbSectionCodes : tierCodes;

      allHeaders.forEach(code => {
        const header = document.getElementById(`header-${code}`);
        if (!header) return;
        if (!activeCodes.includes(code)) {
          header.style.transform = '';
          return;
        }
        const grid = document.getElementById(`grid-${code}`);
        if (!grid) return;

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


def _render_card_tile(t: Dict[str, Any], edition_name: str, initial_display: str) -> str:
    """Renders a single interactive card trajectory tile."""
    chg_class = "text-green" if t["change7d"] > 0 else ("text-red" if t["change7d"] < 0 else "text-muted")
    chg_sign = "+" if t["change7d"] > 0 else ""
    disp_style = f' style="display:{initial_display};"' if initial_display == "none" else ""
    price_display = f"${t['currentPrice']:.2f}" if t.get("hasPrice") else "N/A"
    low_val = t.get("currentLowPrice")
    if low_val is not None and low_val > 0.0:
        price_main_html = (
            f'<span class="price-main price-low-tooltip" '
            f'data-tooltip="Current TCG Low (excl. shipping): ${low_val:.2f}">{price_display}</span>'
        )
    else:
        price_main_html = f'<span class="price-main">{price_display}</span>'
    chg_display = (
        f"{_fmt_currency_delta(t['change7d'])} ({chg_sign}{t['change7dPct']:.1f}%)"
        if t.get("hasPrice")
        else "N/A"
    )

    if t.get("hasAcquisition"):
        acq_class = "text-green" if t["acqDeltaDollar"] > 0 else ("text-red" if t["acqDeltaDollar"] < 0 else "text-muted")
        acq_sign = "+" if t["acqDeltaDollar"] > 0 else ""
        acq_base_txt = f"${t['acqCost']:.2f}" if t["acqCost"] > 0 else "N/A"
        row2_left = '<span class="acq-base-label" data-tooltip="Acquisition Baseline: Fair market price on the date the card was opened or purchased">Acq base:</span> <span class="price-sub-val">' + acq_base_txt + "</span>"
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

    return f"""      <div class="card-tile" data-edition="{edition_name}" data-pid="{safe_pid}" data-num="{safe_pnum}" data-name="{safe_name_attr}" data-rarity="{t['rarity']}" data-color="{safe_color}" data-type="{safe_type}" data-price="{price_attr}" data-c7d="{c7d_attr}" data-call="{call_attr}" data-has-acq="{has_acq_attr}" data-cacq="{cacq_attr}" data-owned-qty="{t.get('ownedQty', 0)}"{disp_style}>
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
              {price_main_html}
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
    if any(t["rarity"] == "BT" for t in beta_trajectories):
        tier_definitions.append(("BT", "Nova Box Toppers"))

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
                parts.append(_render_card_tile(t, edition_name, initial_display))
        parts.append("    </div>\n  </div>\n")

    return "".join(parts)


def _render_ncb_subsection(
    code: str,
    title: str,
    trajectories: List[Dict[str, Any]],
    stats: Dict[str, Any],
) -> str:
    """Renders a single Night City Brawl Season 1 tier subsection."""
    priced_count = stats.get("priced_count", 0)
    total_count = stats.get("total_count", len(trajectories))
    set_total_label = (
        f"Set Total ({priced_count}/{total_count})"
        if priced_count < total_count
        else "Set Total"
    )
    parts: List[str] = [f"""  <div class="tier-section" id="section-{code}" style="display:none;">
    <div class="tier-header" id="header-{code}">
      <div class="tier-title">
        <span>{html.escape(title)}</span>
      </div>
      <div class="tier-stats-group" id="stats-group-{code}">
        <button type="button" class="tier-stats-tab" onclick="toggleTierStats('{code}')" id="btn-stats-{code}">Stats <span class="stats-arrow">&#x25C2;</span></button>
        <div class="tier-stats-content edition-stats-ncb" id="stats-ncb-{code}">
          <div class="stat-cell"><span class="stat-label">Min</span><span class="stat-val">{stats['min']}</span></div>
          <div class="stat-cell"><span class="stat-label">p25</span><span class="stat-val">{stats['p25']}</span></div>
          <div class="stat-cell"><span class="stat-label">Med</span><span class="stat-val">{stats['med']}</span></div>
          <div class="stat-cell"><span class="stat-label">p75</span><span class="stat-val">{stats['p75']}</span></div>
          <div class="stat-cell"><span class="stat-label">Max</span><span class="stat-val">{stats['max']}</span></div>
          <div class="stat-cell"><span class="stat-label">{html.escape(set_total_label)}</span><span class="stat-val">{stats['set_total']}</span></div>
          <div class="stat-cell stat-cell-owned"><span class="stat-label">Owned</span><span class="stat-val">{stats['owned_qty']} cards (${stats['owned_val']:,.2f})</span></div>
        </div>
      </div>
    </div>
    <div class="grid tier-grid" id="grid-{code}">
"""]
    for t in trajectories:
        parts.append(_render_card_tile(t, "ncb", "flex"))
    parts.append("    </div>\n  </div>\n")
    return "".join(parts)


def _render_ncb_section(
    ncb_trajectories: List[Dict[str, Any]],
    nr_stats: Dict[str, Any],
    ncb_champ_trajectories: Optional[List[Dict[str, Any]]] = None,
    nrc_stats: Optional[Dict[str, Any]] = None,
) -> str:
    """Renders Night City Brawl Season 1 regular Nova Rares and optional Brawl Champ sections."""
    sections: List[str] = []
    if ncb_trajectories:
        sections.append(
            _render_ncb_subsection(
                code="NR",
                title=f"Nova Rares ({len(ncb_trajectories)})",
                trajectories=ncb_trajectories,
                stats=nr_stats,
            )
        )
    if ncb_champ_trajectories and nrc_stats is not None:
        sections.append(
            _render_ncb_subsection(
                code="NRC",
                title=f"Nova Rares \u2013 Brawl Champ ({len(ncb_champ_trajectories)})",
                trajectories=ncb_champ_trajectories,
                stats=nrc_stats,
            )
        )
    return "".join(sections)


def _render_ncb_table_view(
    ncb_trajectories: List[Dict[str, Any]],
    ncb_champ_trajectories: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Renders the tabular view for Night City Brawl Season 1 regular Nova Rares and Brawl Champ sections."""
    subsections: List[Tuple[str, str, List[Dict[str, Any]]]] = []
    if ncb_trajectories:
        subsections.append(("NR", f"Nova Rares ({len(ncb_trajectories)})", ncb_trajectories))
    if ncb_champ_trajectories:
        subsections.append(("NRC", f"Nova Rares \u2013 Brawl Champ ({len(ncb_champ_trajectories)})", ncb_champ_trajectories))
    if not subsections:
        return ""

    parts: List[str] = ['  <div class="comparison-view" id="view-ncb-table" style="display:none;">\n']
    for code, title, trajs in subsections:
        parts.append(f"""    <div class="comp-sub-section" id="ncb-sub-{code}">
      <h2 class="comp-sub-title">{html.escape(title)}</h2>
      <div class="comp-table-container">
        <table class="comp-table" id="table-{code}">
          <thead>
            <tr>
              <th style="text-align:center;width:44px;" class="sortable" data-col="num">#</th>
              <th style="text-align:left;" class="sortable" data-col="name">Card Name</th>
              <th style="text-align:center;width:90px;" class="sortable" data-col="type">Type</th>
              <th style="text-align:right;width:105px;" class="sortable" data-col="mkt_price">Market Price</th>
              <th style="text-align:right;width:95px;" class="sortable" data-col="low_price">Low Price</th>
              <th style="text-align:right;width:120px;" class="sortable" data-col="c7d_dlr">L7D Change ($)</th>
              <th style="text-align:right;width:120px;" class="sortable" data-col="c7d_pct">L7D Change (%)</th>
              <th style="text-align:center;width:80px;" class="sortable" data-col="owned">Owned</th>
            </tr>
          </thead>
          <tbody>
""")
        for t in trajs:
            has_price = bool(t.get("hasPrice")) and float(t.get("currentPrice") or 0.0) > 0.0
            mkt_p = float(t["currentPrice"]) if has_price else None
            raw_low = t.get("currentLowPrice")
            has_low = has_price and raw_low is not None and float(raw_low) > 0.0
            low_p = float(raw_low) if has_low else None
            pid = str(t.get("productId") or "")

            mkt_str = (
                f'<a href="https://www.tcgplayer.com/product/{html.escape(pid, quote=True)}" target="_blank" rel="noopener noreferrer" class="comp-price-link">${mkt_p:.2f}<span class="ext-icon">&#x2197;</span></a>'
                if (mkt_p is not None and pid)
                else (f"${mkt_p:.2f}" if mkt_p is not None else "N/A")
            )
            low_str = f"${low_p:.2f}" if low_p is not None else "N/A"

            if has_price:
                c7d_dlr = float(t.get("change7d", 0.0))
                c7d_pct = float(t.get("change7dPct", 0.0))
                c7d_sign = "+" if c7d_dlr > 0 else ""
                c7d_cls = "comp-premium-pos" if c7d_dlr > 0 else ("comp-premium-neg" if c7d_dlr < 0 else "")
                c7d_dlr_str = _fmt_currency_delta(c7d_dlr)
                c7d_pct_str = f"{c7d_sign}{c7d_pct:.1f}%"
                c7d_dlr_attr = f"{c7d_dlr}"
                c7d_pct_attr = f"{c7d_pct}"
            else:
                c7d_cls = ""
                c7d_dlr_str = "N/A"
                c7d_pct_str = "N/A"
                c7d_dlr_attr = ""
                c7d_pct_attr = ""

            owned_qty = int(t.get("ownedQty", 0))
            num_str = str(t.get("printNumber") or "")
            name_str = str(t.get("name") or "")
            color_str = str(t.get("color") or "Neutral")
            type_str = str(t.get("cardType") or "Unknown")

            parts.append(f"""            <tr class="ncb-comp-row" data-priced="{'1' if has_price else '0'}" data-has-low="{'1' if has_low else '0'}" data-name="{html.escape(name_str.lower(), quote=True)}" data-num="{html.escape(num_str, quote=True)}" data-rarity="{code}" data-color="{html.escape(color_str, quote=True)}" data-type="{html.escape(type_str, quote=True)}" data-mkt-price="{mkt_p if mkt_p is not None else ''}" data-low-price="{low_p if low_p is not None else ''}" data-c7d-dlr="{c7d_dlr_attr}" data-c7d-pct="{c7d_pct_attr}" data-owned="{owned_qty}">
              <td style="text-align:center;" class="comp-table-mono">{html.escape(num_str)}</td>
              <td><div class="comp-card-name"><span class="color-dot color-{html.escape(color_str, quote=True)}"></span><span>{html.escape(name_str)}</span></div></td>
              <td style="text-align:center;">{html.escape(type_str)}</td>
              <td style="text-align:right;" class="comp-table-mono">{mkt_str}</td>
              <td style="text-align:right;" class="comp-table-mono">{low_str}</td>
              <td style="text-align:right;" class="comp-table-mono {c7d_cls}">{c7d_dlr_str}</td>
              <td style="text-align:right;" class="comp-table-mono {c7d_cls}">{c7d_pct_str}</td>
              <td style="text-align:center;" class="comp-table-mono">{owned_qty}</td>
            </tr>
""")

        priced_t = [t for t in trajs if bool(t.get("hasPrice")) and float(t.get("currentPrice") or 0.0) > 0.0]
        low_t = [t for t in priced_t if t.get("currentLowPrice") is not None and float(t["currentLowPrice"]) > 0.0]
        tot_owned = sum(int(t.get("ownedQty", 0)) for t in trajs)
        total_label = f"Total ({len(priced_t)}/{len(trajs)} priced)" if len(priced_t) < len(trajs) else "Total"

        if priced_t:
            mkt_sum = sum(float(t["currentPrice"]) for t in priced_t)
            c7d_sum = sum(float(t.get("change7d", 0.0)) for t in priced_t)
            prev_7d_sum = mkt_sum - c7d_sum
            c7d_pct_tot = (c7d_sum / prev_7d_sum * 100.0) if prev_7d_sum > 0.0 else 0.0
            tot_c7d_cls = "comp-premium-pos" if c7d_sum > 0 else ("comp-premium-neg" if c7d_sum < 0 else "")
            tot_c7d_sign = "+" if c7d_sum > 0 else ""
            tot_mkt_txt = f"${mkt_sum:.2f}"
            tot_c7d_dlr_txt = _fmt_currency_delta(c7d_sum)
            tot_c7d_pct_txt = f"{tot_c7d_sign}{c7d_pct_tot:.1f}%"
        else:
            tot_c7d_cls = ""
            tot_mkt_txt = "N/A"
            tot_c7d_dlr_txt = "N/A"
            tot_c7d_pct_txt = "N/A"

        if low_t:
            low_sum = sum(float(t["currentLowPrice"]) for t in low_t)
            tot_low_txt = (
                f"${low_sum:.2f} ({len(low_t)}/{len(priced_t)})"
                if len(low_t) < len(priced_t)
                else f"${low_sum:.2f}"
            )
        else:
            tot_low_txt = "N/A"

        parts.append(f"""          </tbody>
          <tfoot>
            <tr class="comp-total-row" id="total-row-{code}">
              <td style="text-align:center;" class="comp-table-mono">—</td>
              <td id="total-label-{code}">{html.escape(total_label)}</td>
              <td style="text-align:center;">—</td>
              <td style="text-align:right;" class="comp-table-mono" id="total-mkt-price-{code}">{tot_mkt_txt}</td>
              <td style="text-align:right;" class="comp-table-mono" id="total-low-price-{code}">{tot_low_txt}</td>
              <td style="text-align:right;" class="comp-table-mono {tot_c7d_cls}" id="total-c7d-dlr-{code}">{tot_c7d_dlr_txt}</td>
              <td style="text-align:right;" class="comp-table-mono {tot_c7d_cls}" id="total-c7d-pct-{code}">{tot_c7d_pct_txt}</td>
              <td style="text-align:center;" class="comp-table-mono" id="total-owned-{code}">{tot_owned}</td>
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
""")

    parts.append("  </div>\n")
    return "".join(parts)


def _render_comparison_view(comp_pairs: List[Dict[str, Any]]) -> str:
    b_total_owned_cards = sum(p["beta_owned"] for p in comp_pairs)
    b_total_owned_val = sum(p["beta_owned"] * (p["beta_price"] or 0.0) for p in comp_pairs)
    r_total_owned_cards = sum(p["retail_owned"] for p in comp_pairs)
    r_total_owned_val = sum(p["retail_owned"] * (p["retail_price"] or 0.0) for p in comp_pairs)
    has_box_toppers = any(p["rarity"] == "BT" for p in comp_pairs)

    paired_all = [p for p in comp_pairs if p["is_paired"]]
    title_str = (
        f"WNC Set Value ({len(paired_all)}/{len(comp_pairs)} pairs)"
        if len(paired_all) < len(comp_pairs)
        else "WNC Set Value"
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
    bt_pill_html = (
        '\n            <button type="button" class="comp-pill-btn" onclick="setPricedSetTier(\'BT\')" id="pill-tier-BT">BT</button>'
        if has_box_toppers
        else ""
    )

    parts: List[str] = [f"""  <div class="comparison-view" id="view-comparison" style="display:none;">
    <div class="comp-overview-grid">
      <div class="comp-card">
        <div class="comp-card-header">
          <div class="comp-card-title" id="set-val-title">{html.escape(all_info['title'])}</div>
          <div class="comp-pill-group">
            <button type="button" class="comp-pill-btn is-active" onclick="setPricedSetTier('all')" id="pill-tier-all">All</button>
            <button type="button" class="comp-pill-btn" onclick="setPricedSetTier('IL')" id="pill-tier-IL">IL</button>
            <button type="button" class="comp-pill-btn" onclick="setPricedSetTier('IO')" id="pill-tier-IO">IO</button>
            <button type="button" class="comp-pill-btn" onclick="setPricedSetTier('IS')" id="pill-tier-IS">ISR</button>{bt_pill_html}
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
        <div class="comp-card-title">Owned WNC Value</div>
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
    if has_box_toppers:
        tier_order.append(("BT", "Nova Box Toppers"))

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


def _format_print_range(trajectories: List[Dict[str, Any]]) -> str:
    """Builds 'first → last' print number label sorted numerically."""
    if not trajectories:
        return ""
    ordered = sorted(
        trajectories,
        key=lambda t: (
            int("".join(ch for ch in str(t.get("printNumber") or "") if ch.isdigit()) or 0),
            str(t.get("printNumber") or ""),
        ),
    )
    first = str(ordered[0].get("printNumber") or "")
    last = str(ordered[-1].get("printNumber") or "")
    if not first:
        return ""
    return f"{first} \u2192 {last}" if first != last else first


def generate_html_dashboard(
    pairs: List[Any],
    prices_dir: Path,
    collection_path: Optional[str],
    output_path: str,
    database_path: Optional[str] = None,
    earliest_beta_date: Optional[str] = EARLIEST_BETA_DATE,
    earliest_retail_date: Optional[str] = EARLIEST_RETAIL_DATE,
    validated_rows: Optional[List[ValidatedCollectionRow]] = None,
    earliest_ncb_date: Optional[str] = EARLIEST_NCB_DATE,
    ncb_cards: Optional[List[Any]] = None,
    earliest_ncb_champ_date: Optional[str] = EARLIEST_NCB_CHAMP_DATE,
    ncb_champ_cards: Optional[List[Any]] = None,
) -> None:
    """
    Generates the interactive standalone HTML comparison and trajectory dashboard for WNC & NCB S1.
    """
    all_dates, price_by_date = load_daily_prices_chronological(prices_dir)

    beta_dates = _filter_dates_from(all_dates, earliest_beta_date)
    retail_dates = _filter_dates_from(all_dates, earliest_retail_date)
    ncb_dates = _filter_dates_from(all_dates, earliest_ncb_date)
    ncb_champ_dates = _filter_dates_from(all_dates, earliest_ncb_champ_date)

    earliest_beta = beta_dates[0] if beta_dates else (all_dates[0] if all_dates else None)
    earliest_retail = retail_dates[0] if retail_dates else (all_dates[0] if all_dates else None)
    earliest_ncb = ncb_dates[0] if ncb_dates else (all_dates[0] if all_dates else None)
    earliest_ncb_champ = ncb_champ_dates[0] if ncb_champ_dates else earliest_ncb_champ_date

    acquisitions = load_collection_acquisitions(
        collection_path=collection_path,
        database_path=database_path,
        earliest_beta_date=earliest_beta,
        earliest_retail_date=earliest_retail,
        validated_rows=validated_rows,
        earliest_ncb_date=earliest_ncb,
        earliest_ncb_champ_date=earliest_ncb_champ,
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
    ncb_trajectories = build_ncb_trajectories(
        ncb_cards=ncb_cards or [],
        dates=ncb_dates,
        price_by_date=price_by_date,
        acquisitions=acquisitions,
    )
    ncb_champ_trajectories = build_ncb_trajectories(
        ncb_cards=ncb_champ_cards or [],
        dates=ncb_champ_dates,
        price_by_date=price_by_date,
        acquisitions=acquisitions,
    )

    chart_low_series_by_id: Dict[str, List[Optional[float]]] = {}
    for prefix, series in [
        ("r", retail_trajectories),
        ("b", beta_trajectories),
        ("n", ncb_trajectories),
        ("nc", ncb_champ_trajectories),
    ]:
        for t in series:
            raw_pid = str(t.get("productId") or "").strip()
            raw_card_id = f"{prefix}_{raw_pid}"
            safe_card_id = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in raw_card_id)
            acq_info = {"date": t["acqDate"], "cost": t["acqCost"]} if t.get("hasAcquisition") else None
            t["svgChart"] = generate_svg_chart(
                t["points"], card_id=raw_card_id, acq_info=acq_info, low_price=t.get("currentLowPrice")
            )
            if raw_pid and len(t["points"]) >= 2 and t.get("hasPrice"):
                chart_low_series_by_id[safe_card_id] = t.get("lowPoints") or []

    has_box_toppers = any(t["rarity"] == "BT" for t in beta_trajectories)
    wnc_tier_codes = ["IL", "IO", "IS", "BT"] if has_box_toppers else ["IL", "IO", "IS"]

    r_stats = {
        code: _compute_tier_stats([t for t in retail_trajectories if t["rarity"] == code])
        for code in wnc_tier_codes
    }
    b_stats = {
        code: _compute_tier_stats([t for t in beta_trajectories if t["rarity"] == code])
        for code in wnc_tier_codes
    }
    nr_stats = _compute_tier_stats(ncb_trajectories)
    nrc_stats = _compute_tier_stats(ncb_champ_trajectories)

    reverse_tier_map = {"Legends": "IL", "Others": "IO", "Secrets": "IS", "Box Toppers": "BT"}
    pair_by_key = {(reverse_tier_map.get(p.tier, "IL"), str(p.print_number)): p for p in pairs}
    r_by_key = {(t["rarity"], t["cleanNumber"]): t for t in retail_trajectories}
    comp_pairs: List[Dict[str, Any]] = []
    for b in beta_trajectories:
        cnum = b["cleanNumber"]
        comp_key = (b["rarity"], cnum)
        r = r_by_key.get(comp_key)
        pair_obj = pair_by_key.get(comp_key)
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
    active_ncb_dates = ncb_dates if ncb_trajectories else ncb_champ_dates
    retail_date_range = f"{retail_dates[0]} to {retail_dates[-1]}" if retail_dates else "N/A"
    beta_date_range = f"{beta_dates[0]} to {beta_dates[-1]}" if beta_dates else "N/A"
    ncb_date_range = f"{active_ncb_dates[0]} to {active_ncb_dates[-1]}" if active_ncb_dates else "N/A"
    retail_count = len(retail_dates)
    beta_count = len(beta_dates)
    ncb_count = len(active_ncb_dates)
    snapshot_templates = {
        "retail": f"{retail_count} market snapshots ({retail_date_range})",
        "beta": f"{beta_count} market snapshots ({beta_date_range})",
        "ncb": f"{ncb_count} market snapshots ({ncb_date_range})",
    }

    has_ncb = len(ncb_trajectories) > 0 or len(ncb_champ_trajectories) > 0
    has_ncb_champ = len(ncb_champ_trajectories) > 0
    ncb_section_codes = (
        ["NR", "NRC"]
        if (len(ncb_trajectories) > 0 and has_ncb_champ)
        else (["NRC"] if has_ncb_champ else ["NR"])
    )
    tier_sections_html = _render_tier_sections(retail_trajectories, beta_trajectories, r_stats, b_stats)
    ncb_section_html = (
        _render_ncb_section(ncb_trajectories, nr_stats, ncb_champ_trajectories, nrc_stats)
        if has_ncb
        else ""
    )
    ncb_table_view_html = (
        _render_ncb_table_view(ncb_trajectories, ncb_champ_trajectories)
        if has_ncb
        else ""
    )
    comp_view_html = _render_comparison_view(comp_pairs)
    total_card_count = len(beta_trajectories)
    ncb_card_count = len(ncb_trajectories)
    ncb_champ_card_count = len(ncb_champ_trajectories)
    wnc_beta_range_label = _format_print_range(beta_trajectories)
    wnc_retail_range_label = _format_print_range(retail_trajectories)
    ncb_range_label = _format_print_range(ncb_trajectories or ncb_champ_trajectories)
    initial_sort_num_label = f"Print # ({wnc_beta_range_label})" if wnc_beta_range_label else "Print #"
    set_view_meta = {
        "totalCardCount": total_card_count,
        "wncRetailCardCount": len(retail_trajectories),
        "ncbCardCount": ncb_card_count,
        "ncbChampCardCount": ncb_champ_card_count,
        "hasNcbChamp": has_ncb_champ,
        "wncBetaRangeLabel": wnc_beta_range_label,
        "wncRetailRangeLabel": wnc_retail_range_label,
        "ncbRangeLabel": ncb_range_label,
    }
    set_toggle_html = (
        """        <div class="edition-toggle" id="set-toggle" role="tablist" aria-label="Set">
          <button type="button" class="toggle-btn active" id="btn-set-wnc" onclick="setActiveSet('wnc')">WNC</button>
          <button type="button" class="toggle-btn" id="btn-set-ncb" onclick="setActiveSet('ncb')">NCB S1</button>
        </div>\n"""
        if has_ncb
        else ""
    )
    ncb_view_toggle_html = (
        """
      <div class="edition-toggle" id="ncb-view-toggle" style="display:none;">
        <button type="button" class="toggle-btn active" id="btn-ncb-charts" onclick="setNcbView('charts')">Charts</button>
        <button type="button" class="toggle-btn" id="btn-ncb-table" onclick="setNcbView('table')">Table</button>
      </div>"""
        if has_ncb
        else ""
    )
    bt_rarity_option_html = (
        '\n          <label class="multiselect-item"><input type="checkbox" value="BT" /><span class="multiselect-item-label">Nova Box Topper (BT)</span></label>'
        if has_box_toppers
        else ""
    )
    ms_variant_html = (
        """
      <div class="multiselect" id="ms-variant" style="display: none;">
        <button type="button" class="multiselect-btn" id="btn-variant">
          <span>Variant</span>
          <span class="filter-count" id="badge-variant" style="display: none;">0</span>
        </button>
        <div class="multiselect-menu" id="menu-variant">
          <label class="multiselect-item"><input type="checkbox" value="NR" /><span class="multiselect-item-label">Regular (NR)</span></label>
          <label class="multiselect-item"><input type="checkbox" value="NRC" /><span class="multiselect-item-label">Brawl Champ (NRC)</span></label>
        </div>
      </div>"""
        if has_ncb_champ
        else ""
    )

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Welcome to Night City &ndash; Iconic &amp; Promo Price Trajectories</title>
  <script>
{_SET_STATE_JS}
    var hasNcb = {'true' if has_ncb else 'false'};
    const setViewMeta = {serialize_json_for_script(set_view_meta)};
    const snapshotTemplates = {serialize_json_for_script(snapshot_templates)};
    var initialFilterState = (function() {{
      try {{
        var raw = localStorage.getItem('wnc_iconics_filter_state');
        var parsed = raw ? JSON.parse(raw) : null;
        return normalizeSavedFilterState(parsed, hasNcb);
      }} catch (e) {{
        return normalizeSavedFilterState(null, hasNcb);
      }}
    }})();
    (function() {{
      var root = document.documentElement;
      root.classList.add('preload-hydrating', 'preload-header-pending', 'preload-count-pending');
      if (initialFilterState.set === 'ncb') {{
        root.classList.add('preload-ncb');
        if (initialFilterState.ncbView === 'table') {{
          root.classList.add('preload-ncb-table');
        }}
      }} else if (initialFilterState.edition === 'comparison') {{
        root.classList.add('preload-comparison');
      }} else if (initialFilterState.edition === 'retail') {{
        root.classList.add('preload-retail');
      }}
      if (initialFilterState.anchor === 'lifetime') {{
        root.classList.add('preload-anchor-lifetime');
      }} else if (initialFilterState.anchor === 'l7d') {{
        root.classList.add('preload-anchor-l7d');
      }}
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
      <div class="set-switcher-row">
{set_toggle_html}        <h1 id="dashboard-title">Welcome to Night City &ndash; Iconic &amp; Promo Price Trajectories</h1>
      </div>
    </div>
    <div class="header-meta">
      <div id="meta-snapshots">{html.escape(snapshot_templates['beta'])}</div>
      <div>Generated {html.escape(generated_at)}</div>
    </div>
  </div>

  <div class="controls">
    <div class="control-cluster" id="cluster-view">
      <span class="cluster-label">View</span>
      <div class="edition-toggle" id="edition-toggle">
        <button type="button" class="toggle-btn active" id="btn-beta" onclick="setEdition('beta')">Beta</button>
        <button type="button" class="toggle-btn" id="btn-retail" onclick="setEdition('retail')">Retail</button>
        <button type="button" class="toggle-btn" id="btn-comparison" onclick="setEdition('comparison')">Comparison</button>
      </div>{ncb_view_toggle_html}
    </div>

    <div class="cluster-divider" id="divider-view"></div>

    <div class="control-cluster" id="cluster-basis">
      <span class="cluster-label">Basis</span>
      <div class="edition-toggle" id="anchor-toggle">
        <button type="button" class="toggle-btn" id="btn-anchor-lifetime" onclick="setAnchor('lifetime')" data-tooltip="Measures return from first tracked market price">Lifetime</button>
        <button type="button" class="toggle-btn active" id="btn-anchor-acq" onclick="setAnchor('acq')" data-tooltip="Measures return relative to fair market price on acquisition date">Acquisition</button>
        <button type="button" class="toggle-btn" id="btn-anchor-l7d" onclick="setAnchor('l7d')" data-tooltip="Measures price change over the last 7 calendar days">7-Day</button>
      </div>
    </div>

    <div class="cluster-divider"></div>

    <div class="control-cluster" id="cluster-low">
      <span class="cluster-label">Low</span>
      <select id="low-price-select" class="filter-select" aria-label="TCG Low display mode">
        <option value="current">Current</option>
        <option value="all">All</option>
        <option value="hide">Hide</option>
      </select>
    </div>

    <div class="cluster-divider" id="divider-low"></div>

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
          <label class="multiselect-item" id="type-item-legend"><input type="checkbox" value="Legend" /><span class="multiselect-item-label">Legend</span></label>
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
          <label class="multiselect-item"><input type="checkbox" value="IS" /><span class="multiselect-item-label">Iconic Secret (IS)</span></label>{bt_rarity_option_html}
        </div>
      </div>{ms_variant_html}

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
        <option value="num-asc">{html.escape(initial_sort_num_label)}</option>
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
  <div class="chart-legend" id="chart-legend">
    <span class="legend-item"><span class="legend-line" style="background:#3fb950;"></span>Up</span>
    <span class="legend-item"><span class="legend-line" style="background:#f85149;"></span>Down</span>
    <span class="legend-item"><span class="legend-line" style="background:#58a6ff;"></span>Flat (0%)</span>
    <span class="legend-item"><span class="legend-dot" style="background:#d29922;"></span>Acquisition date</span>
    <span class="legend-item" id="legend-low-price"><span class="legend-dot legend-low-swatch" style="background:#0d1117;border:1.5px solid #bc8cff;"></span><span class="legend-low-label">Current TCG Low (excl. shipping)</span></span>
  </div>
  </header>
  <script>
{_HEADER_HYDRATION_JS}
    hydrateHeaderControls();
  </script>
{tier_sections_html}{ncb_section_html}{comp_view_html}{ncb_table_view_html}  <div id="chart-tooltip" class="chart-tooltip"></div>

  <script>
    const totalCardCount = {total_card_count};
    const ncbCardCount = {ncb_card_count};
    const hasNcbChamp = {'true' if has_ncb_champ else 'false'};
    const renderedTierCodes = {serialize_json_for_script(wnc_tier_codes)};
    const renderedNcbCodes = {serialize_json_for_script(ncb_section_codes)};
    const chartLowSeriesById = {serialize_json_for_script(chart_low_series_by_id)};
{_CHART_GEOMETRY_JS}
{_DASHBOARD_JS_BODY}"""

    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(html_content)
