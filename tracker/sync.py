"""
Market price synchronization module for Cyberpunk TCG (Category 92).
Fetches prices from local cache, GitHub raw data, or live TCGCSV endpoints and archives.
"""

import datetime
import glob
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Optional
import urllib.error
import urllib.request

CATEGORY_ID = 92
BASE_URL = "https://tcgcsv.com/tcgplayer"
ARCHIVE_BASE_URL = "https://tcgcsv.com/archive/tcgplayer"
GITHUB_RAW_URL = "https://raw.githubusercontent.com/camwu/cyberpunk-tcg-market-data/main/prices"
USER_AGENT = "CyberpunkTCGMarketTracker/1.0"


def find_7z() -> Optional[str]:
    """Finds 7-Zip executable from PATH or standard install locations."""
    system_7z = shutil.which("7z")
    if system_7z:
        return system_7z
    standard_paths = [
        r"C:\Program Files\7-Zip\7z.exe",
        r"C:\Program Files (x86)\7-Zip\7z.exe",
    ]
    for p in standard_paths:
        if os.path.isfile(p):
            return p
    return None


def fetch_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"Error fetching {url}: {e}", file=sys.stderr)
        return None


def sync_market_prices(price_dir: str = "prices", target_date: Optional[str] = None, force: bool = False) -> str:
    """
    Ensures market prices for target_date (default today) are available in price_dir.
    Checks local directory, repo directory, GitHub raw, then live TCGCSV.
    """
    os.makedirs(price_dir, exist_ok=True)
    today = target_date or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    target_file = os.path.join(price_dir, f"{today}.json")
    latest_file = os.path.join(price_dir, "latest.json")

    if not force and os.path.exists(target_file):
        print(f"Using existing price data for {today} from {target_file}.")
        return target_file

    # 1. Check repo root prices directory if price_dir points elsewhere
    repo_prices = Path(__file__).resolve().parent.parent / "prices"
    repo_price_file = repo_prices / f"{today}.json"
    if repo_price_file.is_file() and str(repo_prices.resolve()) != str(Path(price_dir).resolve()):
        try:
            with open(repo_price_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            with open(target_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            with open(latest_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            print(f"Synced {today} prices from repository data directory.")
            return target_file
        except Exception:
            pass

    # 2. Check GitHub Raw remote URL
    remote_url = f"{GITHUB_RAW_URL}/{today}.json"
    remote_data = fetch_json(remote_url)
    if remote_data and remote_data.get("products"):
        with open(target_file, "w", encoding="utf-8") as f:
            json.dump(remote_data, f, indent=2)
        with open(latest_file, "w", encoding="utf-8") as f:
            json.dump(remote_data, f, indent=2)
        print(f"Synced {today} prices from GitHub remote repository.")
        return target_file

    # 3. Live scrape from TCGCSV
    print(f"Fetching live market prices for {today} from TCGCSV...")
    groups_data = fetch_json(f"{BASE_URL}/{CATEGORY_ID}/groups")
    if not groups_data or not groups_data.get("results"):
        raise RuntimeError("Failed to fetch group catalog from TCGCSV.")

    all_products = {}
    total_groups = len(groups_data["results"])

    for idx, group in enumerate(groups_data["results"], start=1):
        gid = group["groupId"]
        gname = group["name"]
        print(f"[{idx}/{total_groups}] Fetching {gname} (ID: {gid})...")
        prod_data = fetch_json(f"{BASE_URL}/{CATEGORY_ID}/{gid}/productsAndPrices")
        if prod_data and prod_data.get("results"):
            for item in prod_data["results"]:
                item["groupId"] = gid
                item["groupName"] = gname
                pid = item["productId"]
                all_products[str(pid)] = item
        time.sleep(0.2)

    payload = {
        "date": today,
        "scrapedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "totalProducts": len(all_products),
        "products": all_products,
    }

    with open(target_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    with open(latest_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print(f"Saved {len(all_products)} products to {target_file}.")
    return target_file


def backfill_market_prices(date_str: str, price_dir: str = "prices") -> str:
    """
    Backfills historical market prices for date_str from GitHub Raw or TCGCSV archives.
    """
    os.makedirs(price_dir, exist_ok=True)
    target_file = os.path.join(price_dir, f"{date_str}.json")

    if os.path.exists(target_file):
        print(f"Price cache already exists for {date_str} at {target_file}.")
        return target_file

    # 1. Check local repo
    repo_prices = Path(__file__).resolve().parent.parent / "prices"
    repo_price_file = repo_prices / f"{date_str}.json"
    if repo_price_file.is_file():
        shutil.copyfile(str(repo_price_file), target_file)
        print(f"Copied {date_str} prices from repository data.")
        return target_file

    # 2. Check GitHub Raw
    remote_url = f"{GITHUB_RAW_URL}/{date_str}.json"
    remote_data = fetch_json(remote_url)
    if remote_data and remote_data.get("products"):
        with open(target_file, "w", encoding="utf-8") as f:
            json.dump(remote_data, f, indent=2)
        print(f"Downloaded {date_str} prices from GitHub remote.")
        return target_file

    # 3. Download TCGCSV 7z archive
    seven_zip = find_7z()
    if not seven_zip:
        raise RuntimeError("7-Zip (7z) executable is required to extract TCGCSV historical archives.")

    archive_url = f"{ARCHIVE_BASE_URL}/prices-{date_str}.ppmd.7z"
    temp_archive = f"temp_{date_str}.7z"
    extract_dir = f"temp_extract_{date_str}"

    print(f"Downloading historical archive for {date_str}...")
    req = urllib.request.Request(archive_url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp, open(temp_archive, "wb") as out_f:
            shutil.copyfileobj(resp, out_f)
    except Exception as e:
        raise RuntimeError(f"Failed to download archive from {archive_url}: {e}")

    try:
        print(f"Extracting Category {CATEGORY_ID} files with 7-Zip...")
        cmd = [seven_zip, "e", temp_archive, f"-o{extract_dir}", f"tcgplayer/{CATEGORY_ID}/*", "-r", "-y"]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        extracted_files = glob.glob(os.path.join(extract_dir, "*"))
        all_products = {}
        for fpath in extracted_files:
            fname = os.path.basename(fpath)
            if fname.isdigit() or fname.endswith(".json"):
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    items = data.get("results") or (data if isinstance(data, list) else [])
                    for item in items:
                        pid = item.get("productId")
                        if pid:
                            all_products[str(pid)] = item
                except Exception:
                    pass

        if not all_products:
            raise RuntimeError(f"No products found for Category {CATEGORY_ID} in archive {date_str}.")

        payload = {
            "date": date_str,
            "scrapedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "totalProducts": len(all_products),
            "products": all_products,
        }

        with open(target_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        print(f"Backfilled {len(all_products)} products for {date_str} to {target_file}.")
        return target_file

    finally:
        if os.path.exists(temp_archive):
            os.remove(temp_archive)
        if os.path.exists(extract_dir):
            shutil.rmtree(extract_dir, ignore_errors=True)
