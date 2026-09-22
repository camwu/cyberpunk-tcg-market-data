"""
CardNexus Public API client and inventory synchronization module.
Fetches inventory lines, resolves card metadata from catalog feeds, and emits validated CSVs.
"""

import csv
import datetime
import gzip
import json
import os
from pathlib import Path
import shutil
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.error
import urllib.parse
import urllib.request

from tracker.validation import validate_collection_file, is_sealed_product

API_BASE_URL = "https://public-api.cardnexus.com/v1"
USER_AGENT = "CyberpunkTCGMarketTracker/1.0"
CATALOG_CACHE_TTL_SECONDS = 86400  # 24 hours
STEADY_STATE_SLEEP_SECONDS = 1.0  # CardNexus docs safe pattern: 1 req/s under 60 req/min cap


def resolve_cardnexus_api_key(explicit_key: Optional[str] = None) -> Optional[str]:
    """Resolves CardNexus API key from argument, environment, or Windows user registry fallback."""
    if explicit_key:
        return explicit_key.strip()
    env_key = os.getenv("CARDNEXUS_API_KEY")
    if env_key:
        return env_key.strip()
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as reg_key:
                val, _ = winreg.QueryValueEx(reg_key, "CARDNEXUS_API_KEY")
                if val:
                    return str(val).strip()
        except Exception:
            pass
    return None


class CardNexusAPIError(Exception):
    """Raised when CardNexus API returns an unrecoverable error."""
    pass


class CardNexusClient:
    """
    Authenticated client for CardNexus Public API.
    Interacts with inventory endpoints and catalog feeds while strictly firewalling internal productIds.
    """

    def __init__(self, api_key: Optional[str] = None, cache_dir: str = "data"):
        self.api_key = resolve_cardnexus_api_key(api_key)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _make_request(self, url: str, method: str = "GET", headers: Optional[Dict[str, str]] = None) -> Tuple[int, Dict[str, str], bytes]:
        req_headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
        if self.api_key and url.startswith(API_BASE_URL):
            req_headers["Authorization"] = f"Bearer {self.api_key}"
        if headers:
            req_headers.update(headers)

        req = urllib.request.Request(url, headers=req_headers, method=method)
        max_retries = 3

        for attempt in range(max_retries):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    status = resp.status
                    resp_headers = {k.lower(): v for k, v in resp.headers.items()}
                    body = resp.read()
                    return status, resp_headers, body
            except urllib.error.HTTPError as e:
                resp_headers = {k.lower(): v for k, v in e.headers.items()}
                body = e.read()
                if e.code == 429:
                    retry_after = resp_headers.get("retry-after")
                    sleep_time = float(retry_after) if retry_after else 2.0
                    print(f"CardNexus rate limit encountered (429). Retrying after {sleep_time}s...", file=sys.stderr)
                    time.sleep(sleep_time)
                    continue
                if e.code in (500, 502, 503, 504) and attempt < max_retries - 1:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                error_msg = f"HTTP {e.code}"
                try:
                    err_json = json.loads(body.decode("utf-8"))
                    error_msg = f"HTTP {e.code}: {err_json.get('message', err_json.get('code', error_msg))}"
                except Exception:
                    pass
                raise CardNexusAPIError(error_msg)
            except urllib.error.URLError as e:
                if attempt < max_retries - 1:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                raise CardNexusAPIError(f"Network connection failed: {e.reason}")

        raise CardNexusAPIError(f"Failed request after {max_retries} attempts.")

    def fetch_inventory(self, include_marketplace: bool = True, game: str = "cyberpunk") -> List[Dict[str, Any]]:
        """
        Fetches all inventory lines via cursor pagination.
        Enforces 1.0s steady-state pacing between requests to respect the 60 req/min global limit.
        """
        if not self.api_key:
            raise CardNexusAPIError("Missing CardNexus API key. Set CARDNEXUS_API_KEY environment variable.")

        items: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        page = 0

        while True:
            params = [f"game={game}", "limit=100"]
            if cursor:
                params.append(f"cursor={urllib.parse.quote(cursor)}")
            if not include_marketplace:
                params.append("forSale=false")

            url = f"{API_BASE_URL}/inventory?{'&'.join(params)}"
            page += 1

            status, headers, body = self._make_request(url)
            data = json.loads(body.decode("utf-8"))

            page_items = data.get("data", [])
            items.extend(page_items)

            next_cursor = (data.get("pagination") or {}).get("nextCursor")
            if not next_cursor or not page_items:
                break

            cursor = next_cursor
            time.sleep(STEADY_STATE_SLEEP_SECONDS)

        return items

    def fetch_catalog(self, refresh: bool = False, game: str = "cyberpunk") -> Dict[int, Dict[str, Any]]:
        """
        Retrieves and caches the Cyberpunk catalog feed to map internal productIds to metadata.
        Caches locally for 24 hours unless refresh=True.
        """
        cache_file = self.cache_dir / f"cardnexus_catalog_{game}.json"

        if not refresh and cache_file.is_file():
            age = time.time() - cache_file.stat().st_mtime
            if age < CATALOG_CACHE_TTL_SECONDS:
                try:
                    with open(cache_file, "r", encoding="utf-8") as f:
                        cached_raw = json.load(f)
                        return {int(k): v for k, v in cached_raw.items()}
                except Exception:
                    pass

        expansions_by_id: Dict[int, str] = {}
        expansions_by_slug: Dict[str, str] = {}
        try:
            exp_url = f"{API_BASE_URL}/games/{game}/expansions"
            status, headers, exp_body = self._make_request(exp_url)
            exp_json = json.loads(exp_body.decode("utf-8"))
            for exp in exp_json.get("data", []):
                eid = exp.get("id")
                ename = (exp.get("name") or "").strip()
                eslug = (exp.get("slug") or "").strip()
                if eid is not None and ename:
                    expansions_by_id[int(eid)] = ename
                if eslug and ename:
                    expansions_by_slug[eslug] = ename
        except Exception as e:
            print(f"Warning: Could not fetch expansions list for {game}: {e}", file=sys.stderr)

        url = f"{API_BASE_URL}/feeds/{game}/catalog"
        status, headers, body = self._make_request(url)
        feed_info = json.loads(body.decode("utf-8"))
        download_url = feed_info.get("url")
        if not download_url:
            raise CardNexusAPIError("Catalog feed response did not contain a download URL.")

        status, headers, compressed_data = self._make_request(download_url)
        decompressed = gzip.decompress(compressed_data).decode("utf-8")

        catalog_map: Dict[int, Dict[str, Any]] = {}
        for line in decompressed.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                pid = item.get("id") or item.get("productId")
                if pid is None:
                    continue

                exp_val = item.get("expansion")
                if isinstance(exp_val, dict) and exp_val.get("name"):
                    expansion_name = exp_val.get("name", "").strip()
                elif isinstance(exp_val, str) and exp_val.strip():
                    expansion_name = exp_val.strip()
                else:
                    eid = item.get("expansionId")
                    eslug = item.get("expansionSlug")
                    expansion_name = expansions_by_id.get(int(eid)) if eid is not None else None
                    if not expansion_name and eslug:
                        expansion_name = expansions_by_slug.get(str(eslug).strip())
                    if not expansion_name:
                        # Fallback guarantees expansion_name is always a string
                        expansion_name = str(exp_val or "").strip()

                catalog_map[int(pid)] = {
                    "name": (item.get("name") or "").strip(),
                    "expansion": expansion_name.strip(),
                    "printNumber": str(item.get("printNumber") or "").strip(),
                    "productType": str(item.get("productType") or "card").strip().lower(),
                }
            except Exception:
                continue

        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(catalog_map, f, indent=2)
        except Exception as e:
            print(f"Warning: Could not save catalog cache to {cache_file}: {e}", file=sys.stderr)

        return catalog_map

    def transform_inventory_rows(
        self,
        inventory_items: List[Dict[str, Any]],
        catalog_map: Dict[int, Dict[str, Any]],
    ) -> List[Dict[str, str]]:
        """
        Transforms raw inventory items into standard collection CSV rows.
        CRITICAL: Discards internal productId completely. Only (name, expansion, printNumber,
        finish, totalQtyOwned, notes) cross the boundary into downstream code.
        """
        output_rows: List[Dict[str, str]] = []

        for item in inventory_items:
            raw_pid = item.get("productId")
            if raw_pid is None:
                continue

            try:
                pid = int(raw_pid)
            except (ValueError, TypeError):
                continue

            meta = catalog_map.get(pid, {})
            name = meta.get("name") or str(item.get("name") or f"Product #{pid}")
            expansion = meta.get("expansion") or str(item.get("expansion") or "")
            product_type = meta.get("productType", "card")
            print_number = meta.get("printNumber", "")

            # Identify sealed products
            is_sealed = (product_type in ("sealed", "pack", "box", "case", "deck")) or is_sealed_product(name, expansion)
            if is_sealed:
                print_number = ""

            qty = int(item.get("quantity") or 0)
            if qty < 1:
                continue

            raw_finish = (item.get("finish") or "Standard").strip()
            finish = "Standard" if raw_finish.lower() in ("standard", "normal") else "Foil"
            notes = (item.get("notes") or "").strip()

            output_rows.append({
                "name": name,
                "expansion": expansion,
                "printNumber": print_number,
                "finish": finish,
                "totalQtyOwned": str(qty),
                "notes": notes,
            })

        return output_rows


def sync_cardnexus_collection(
    target_csv: str = "data/active_collection.csv",
    api_key: Optional[str] = None,
    include_marketplace: bool = True,
    refresh_catalog: bool = False,
) -> Tuple[bool, str, int]:
    """
    Orchestrates live CardNexus inventory sync:
    1. Fetches inventory lines from CardNexus API.
    2. Resolves card metadata via Cyberpunk catalog feed.
    3. Emits unified collection CSV snapshot.
    4. Validates snapshot via validate_collection_file().
    5. Only upon successful validation, promotes snapshot directly to target_csv.
    Returns (success, promoted_csv_path, total_physical_units).
    """
    cache_dir = os.path.dirname(target_csv) or "data"
    client = CardNexusClient(api_key=api_key, cache_dir=cache_dir)
    if not client.api_key:
        print("Error: CardNexus API key is not configured.", file=sys.stderr)
        return False, "", 0

    print("Fetching active inventory lines from CardNexus API...")
    inventory_items = client.fetch_inventory(include_marketplace=include_marketplace)
    print(f"Retrieved {len(inventory_items)} raw inventory line items.")

    print("Resolving catalog metadata...")
    catalog_map = client.fetch_catalog(refresh=refresh_catalog)
    print(f"Loaded {len(catalog_map)} catalog products from CardNexus feed.")

    rows = client.transform_inventory_rows(inventory_items, catalog_map)
    if not rows:
        print("Error: No valid Cyberpunk TCG items found in CardNexus inventory.", file=sys.stderr)
        return False, "", 0

    # Write timestamped snapshot
    os.makedirs(os.path.dirname(target_csv) or ".", exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot_path = os.path.join(os.path.dirname(target_csv) or ".", f"cardnexus_collection_{timestamp}.csv")

    fieldnames = ["name", "expansion", "printNumber", "finish", "totalQtyOwned", "notes"]
    with open(snapshot_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Saved authenticated API snapshot to {snapshot_path}")

    # Validate snapshot before promotion
    error_csv = os.path.join(cache_dir, "validation_errors.csv")
    is_valid, validation_errors, validated_rows = validate_collection_file(
        snapshot_path,
        error_export_path=error_csv,
    )
    if not is_valid:
        from tracker.validation import format_validation_report
        print(f"\nError: API collection validation failed for snapshot '{snapshot_path}':\n", file=sys.stderr)
        print(format_validation_report(validation_errors), file=sys.stderr)
        print("Promotion aborted: existing active collection was not modified.", file=sys.stderr)
        return False, snapshot_path, 0

    shutil.copyfile(snapshot_path, target_csv)
    try:
        os.remove(snapshot_path)
    except OSError as e:
        print(f"Warning: Could not remove temporary staging file '{snapshot_path}': {e}", file=sys.stderr)

    total_qty = sum(int(r.get("totalQtyOwned", 1)) for r in validated_rows)
    card_count = sum(1 for r in validated_rows if r.get("item_type") != "Sealed")
    sealed_count = sum(1 for r in validated_rows if r.get("item_type") == "Sealed")
    sealed_info = f" and {sealed_count} sealed items" if sealed_count else ""

    print(f"Promoted to {target_csv}: {len(validated_rows)} entries ({card_count} cards{sealed_info}, {total_qty} physical units).")
    return True, target_csv, total_qty
