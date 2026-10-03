"""
Scrapes Cyberpunk TCG market prices dynamically across all groups from TCGCSV.
Adheres to rate limits using custom User-Agent and throttled requests.
"""

import argparse
import datetime
import json
import os
import ssl
import sys
import time
from typing import Optional
import urllib.request
import urllib.error

CATEGORY_ID = 92
BASE_URL = "https://tcgcsv.com/tcgplayer"
LAST_UPDATED_URL = "https://tcgcsv.com/last-updated.txt"
USER_AGENT = "CyberpunkTCGMarketTracker/1.0 (contact: github-actions-collector)"
_SSL_CONTEXT: ssl.SSLContext = ssl.create_default_context()
RATE_LIMIT_DELAY = float(os.getenv("TCGCSV_RATE_LIMIT_DELAY", "0.2"))
MAX_RETRIES = 3
RETRY_INITIAL_DELAY = 1.0
RETRY_BACKOFF_FACTOR = 2.0
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def fetch_text(url: str, max_retries: int = MAX_RETRIES) -> Optional[str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(1, max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30, context=_SSL_CONTEXT) as resp:
                return resp.read().decode("utf-8").strip()
        except urllib.error.HTTPError as e:
            if e.code in RETRYABLE_STATUS_CODES and attempt < max_retries:
                delay = RETRY_INITIAL_DELAY * (RETRY_BACKOFF_FACTOR ** (attempt - 1))
                print(f"HTTP {e.code} fetching {url} (attempt {attempt}/{max_retries}). Retrying in {delay:.1f}s...", file=sys.stderr)
                time.sleep(delay)
                continue
            print(f"Error fetching {url}: {e}", file=sys.stderr)
            return None
        except urllib.error.URLError as e:
            if attempt < max_retries:
                delay = RETRY_INITIAL_DELAY * (RETRY_BACKOFF_FACTOR ** (attempt - 1))
                print(f"Network error fetching {url}: {e} (attempt {attempt}/{max_retries}). Retrying in {delay:.1f}s...", file=sys.stderr)
                time.sleep(delay)
                continue
            print(f"Error fetching {url}: {e}", file=sys.stderr)
            return None
    return None


def fetch_json(endpoint: str, max_retries: int = MAX_RETRIES):
    url = f"{BASE_URL}/{endpoint}" if not endpoint.startswith("http") else endpoint
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(1, max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30, context=_SSL_CONTEXT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in RETRYABLE_STATUS_CODES and attempt < max_retries:
                delay = RETRY_INITIAL_DELAY * (RETRY_BACKOFF_FACTOR ** (attempt - 1))
                print(f"HTTP {e.code} fetching {url} (attempt {attempt}/{max_retries}). Retrying in {delay:.1f}s...", file=sys.stderr)
                time.sleep(delay)
                continue
            print(f"Error fetching {url}: {e}", file=sys.stderr)
            return None
        except urllib.error.URLError as e:
            if attempt < max_retries:
                delay = RETRY_INITIAL_DELAY * (RETRY_BACKOFF_FACTOR ** (attempt - 1))
                print(f"Network error fetching {url}: {e} (attempt {attempt}/{max_retries}). Retrying in {delay:.1f}s...", file=sys.stderr)
                time.sleep(delay)
                continue
            print(f"Error fetching {url}: {e}", file=sys.stderr)
            return None
    return None


def is_valid_snapshot(file_path: str) -> bool:
    """Verifies that an existing snapshot is non-empty and contains valid JSON with price data."""
    if not os.path.isfile(file_path) or os.path.getsize(file_path) == 0:
        return False
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return isinstance(data, dict) and bool(data.get("prices"))
    except (OSError, json.JSONDecodeError):
        return False


def extract_date_from_build(build_timestamp: Optional[str]) -> Optional[str]:
    """Extracts YYYY-MM-DD from an upstream build timestamp string if valid."""
    if not build_timestamp or len(build_timestamp) < 10:
        return None
    candidate = build_timestamp[:10]
    try:
        datetime.datetime.strptime(candidate, "%Y-%m-%d")
        return candidate
    except ValueError:
        return None


def run_scraper(output_dir: str = "prices", force: bool = False, target_date: Optional[str] = None):
    os.makedirs(output_dir, exist_ok=True)

    last_updated = fetch_text(LAST_UPDATED_URL)
    if last_updated:
        print(f"TCGCSV upstream build timestamp: {last_updated}")

    upstream_date = extract_date_from_build(last_updated)
    today = target_date or upstream_date or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    dated_file = os.path.join(output_dir, f"{today}.json")

    if not force and is_valid_snapshot(dated_file):
        print(f"Daily price snapshot for {today} already exists at {dated_file}. Skipping scrape to preserve original timestamp (use --force to overwrite).")
        return True

    print(f"Starting TCGCSV scrape for Cyberpunk TCG (Category {CATEGORY_ID}) on {today}...")

    groups_data = fetch_json(f"{CATEGORY_ID}/groups")
    if not groups_data or "results" not in groups_data:
        print("Failed to fetch groups list.", file=sys.stderr)
        return False

    groups = groups_data["results"]
    print(f"Discovered {len(groups)} set groups.")

    cards_file = os.path.join(os.path.dirname(os.path.abspath(output_dir)), "cards.json") if output_dir != "." else "cards.json"
    existing_cards = {}
    if os.path.exists(cards_file):
        try:
            with open(cards_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    existing_cards = loaded
        except (OSError, json.JSONDecodeError):
            pass

    existing_groups = existing_cards.get("_groups", {})
    existing_group_ids = {
        card["groupId"]
        for k, card in existing_cards.items()
        if not k.startswith("_") and isinstance(card, dict) and "groupId" in card
    }

    catalog = {}
    all_prices = {}
    updated_groups = dict(existing_groups)

    for grp in groups:
        group_id = grp["groupId"]
        group_name = grp["name"]
        group_modified = grp.get("modifiedOn")
        cached_group = existing_groups.get(str(group_id))

        need_products = (
            force
            or group_id not in existing_group_ids
            or not cached_group
            or (group_modified and cached_group.get("modifiedOn") != group_modified)
        )

        time.sleep(RATE_LIMIT_DELAY)
        price_data = fetch_json(f"{CATEGORY_ID}/{group_id}/prices")

        if price_data and "results" in price_data:
            for pr in price_data["results"]:
                pid_str = str(pr["productId"])
                sub_type = pr.get("subTypeName", "Normal")
                if pid_str not in all_prices:
                    all_prices[pid_str] = {}
                all_prices[pid_str][sub_type] = {
                    "marketPrice": pr.get("marketPrice"),
                    "lowPrice": pr.get("lowPrice"),
                    "midPrice": pr.get("midPrice"),
                    "highPrice": pr.get("highPrice"),
                    "directLowPrice": pr.get("directLowPrice"),
                }

        if need_products:
            print(f"Fetching group {group_id}: {group_name} (metadata updated/new)...")
            time.sleep(RATE_LIMIT_DELAY)
            prod_data = fetch_json(f"{CATEGORY_ID}/{group_id}/products")
            if prod_data and "results" in prod_data:
                for p in prod_data["results"]:
                    pid = p["productId"]
                    print_number = None
                    rarity = None
                    color = None
                    card_type = None
                    for ext in p.get("extendedData", []):
                        if ext.get("name") == "Number":
                            print_number = ext.get("value")
                        elif ext.get("name") == "Rarity":
                            rarity = ext.get("value")
                        elif ext.get("name") == "Color":
                            color = ext.get("value")
                        elif ext.get("name") == "CardType":
                            card_type = ext.get("value")

                    catalog[str(pid)] = {
                        "productId": pid,
                        "name": p.get("name"),
                        "cleanName": p.get("cleanName"),
                        "groupId": group_id,
                        "groupName": group_name,
                        "printNumber": print_number,
                        "rarity": rarity,
                        "color": color,
                        "cardType": card_type,
                    }
        else:
            print(f"Fetching group {group_id}: {group_name} (prices only, metadata unchanged)...")

        updated_groups[str(group_id)] = {
            "name": group_name,
            "modifiedOn": group_modified,
        }

    if not all_prices:
        print(f"Error: No price records fetched from TCGCSV for Category {CATEGORY_ID}. Aborting scrape to protect existing snapshots.", file=sys.stderr)
        return False

    # 1. Update static cards.json catalog
    if catalog or updated_groups != existing_groups:
        existing_cards["_groups"] = updated_groups
        existing_cards.update(catalog)
        with open(cards_file, "w", encoding="utf-8") as f:
            json.dump(existing_cards, f, indent=2)

    total_products = len([k for k in existing_cards if not k.startswith("_")])

    # 2. Write slim daily price snapshots
    daily_payload = {
        "date": today,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "tcgcsvBuild": last_updated,
        "category": "Cyberpunk TCG",
        "categoryId": CATEGORY_ID,
        "productCount": total_products,
        "prices": all_prices,
    }

    dated_file = os.path.join(output_dir, f"{today}.json")
    latest_file = os.path.join(output_dir, "latest.json")

    with open(dated_file, "w", encoding="utf-8") as f:
        json.dump(daily_payload, f, indent=2)

    with open(latest_file, "w", encoding="utf-8") as f:
        json.dump(daily_payload, f, indent=2)

    print(f"Scrape completed successfully. Catalog ({total_products} cards) saved to {cards_file}.")
    print(f"Daily price snapshot saved to {dated_file} and {latest_file}.")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrapes Cyberpunk TCG market prices from TCGCSV")
    parser.add_argument("output_dir", nargs="?", default="prices", help="Directory to save price snapshots (default: prices)")
    parser.add_argument("--force", action="store_true", help="Force fresh scrape even if today's snapshot exists")
    parser.add_argument("--date", dest="target_date", default=None, help="Optional target date string (YYYY-MM-DD)")

    args = parser.parse_args()
    success = run_scraper(output_dir=args.output_dir, force=args.force, target_date=args.target_date)
    if not success:
        sys.exit(1)
