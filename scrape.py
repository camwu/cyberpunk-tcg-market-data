"""
Scrapes Cyberpunk TCG market prices dynamically across all groups from TCGCSV.
Adheres to rate limits using custom User-Agent and throttled requests.
"""

import datetime
import json
import os
import sys
import time
import urllib.request
import urllib.error

CATEGORY_ID = 92
BASE_URL = "https://tcgcsv.com/tcgplayer"
USER_AGENT = "CyberpunkTCGMarketTracker/1.0 (contact: github-actions-collector)"


def fetch_json(endpoint: str):
    url = f"{BASE_URL}/{endpoint}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        print(f"Error fetching {url}: {e}", file=sys.stderr)
        return None


def run_scraper(output_dir: str = "prices"):
    os.makedirs(output_dir, exist_ok=True)
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    print(f"Starting TCGCSV scrape for Cyberpunk TCG (Category {CATEGORY_ID}) on {today}...")

    groups_data = fetch_json(f"{CATEGORY_ID}/groups")
    if not groups_data or "results" not in groups_data:
        print("Failed to fetch groups list.", file=sys.stderr)
        return False

    groups = groups_data["results"]
    print(f"Discovered {len(groups)} set groups.")

    catalog = {}
    total_products = 0

    for grp in groups:
        group_id = grp["groupId"]
        group_name = grp["name"]
        print(f"Fetching group {group_id}: {group_name}...")

        time.sleep(0.2)
        prod_data = fetch_json(f"{CATEGORY_ID}/{group_id}/products")
        time.sleep(0.2)
        price_data = fetch_json(f"{CATEGORY_ID}/{group_id}/prices")

        prices_by_product = {}
        if price_data and "results" in price_data:
            for pr in price_data["results"]:
                pid = pr["productId"]
                sub_type = pr.get("subTypeName", "Normal")
                if pid not in prices_by_product:
                    prices_by_product[pid] = {}
                prices_by_product[pid][sub_type] = {
                    "marketPrice": pr.get("marketPrice"),
                    "lowPrice": pr.get("lowPrice"),
                    "midPrice": pr.get("midPrice"),
                    "highPrice": pr.get("highPrice"),
                    "directLowPrice": pr.get("directLowPrice"),
                }

        if prod_data and "results" in prod_data:
            for p in prod_data["results"]:
                pid = p["productId"]
                total_products += 1

                print_number = None
                rarity = None
                for ext in p.get("extendedData", []):
                    if ext.get("name") == "Number":
                        print_number = ext.get("value")
                    elif ext.get("name") == "Rarity":
                        rarity = ext.get("value")

                catalog[str(pid)] = {
                    "productId": pid,
                    "name": p.get("name"),
                    "cleanName": p.get("cleanName"),
                    "groupId": group_id,
                    "groupName": group_name,
                    "printNumber": print_number,
                    "rarity": rarity,
                    "prices": prices_by_product.get(pid, {}),
                }

    output_payload = {
        "metadata": {
            "category": "Cyberpunk TCG",
            "categoryId": CATEGORY_ID,
            "date": today,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "groupCount": len(groups),
            "productCount": total_products,
        },
        "products": catalog,
    }

    dated_file = os.path.join(output_dir, f"{today}.json")
    latest_file = os.path.join(output_dir, "latest.json")

    with open(dated_file, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, indent=2)

    with open(latest_file, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, indent=2)

    print(f"Scrape completed successfully. Saved {total_products} products to {dated_file} and {latest_file}.")
    return True


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "prices"
    success = run_scraper(out)
    if not success:
        sys.exit(1)
