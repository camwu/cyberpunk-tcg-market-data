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
LAST_UPDATED_URL = "https://tcgcsv.com/last-updated.txt"
ARCHIVE_BASE_URL = "https://tcgcsv.com/archive/tcgplayer"
GITHUB_RAW_BASE = "https://raw.githubusercontent.com/camwu/cyberpunk-tcg-market-data/main"
GITHUB_RAW_URL = f"{GITHUB_RAW_BASE}/prices"
USER_AGENT = "CyberpunkTCGMarketTracker/1.0"
REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_PRICES_DIR = REPO_ROOT / "prices"


def sync_cards_catalog(target_dir: str = "prices") -> str:
    """Ensures cards.json catalog is present locally or in repository root."""
    repo_cards = REPO_ROOT / "cards.json"
    target_cards = os.path.join(target_dir, "cards.json")
    parent_cards = os.path.join(os.path.dirname(target_dir), "cards.json")

    if os.path.isfile(target_cards):
        return target_cards
    if os.path.isfile(parent_cards):
        return parent_cards
    if repo_cards.is_file():
        try:
            shutil.copy2(str(repo_cards), target_cards)
            return target_cards
        except Exception:
            return str(repo_cards)

    url = f"{GITHUB_RAW_BASE}/cards.json"
    data = fetch_json(url)
    if data:
        with open(target_cards, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return target_cards
    return target_cards


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


def fetch_text(url: str) -> Optional[str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8").strip()
    except Exception as e:
        print(f"Error fetching {url}: {e}", file=sys.stderr)
        return None


def fetch_json(url: str, quiet_not_found: bool = False):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if quiet_not_found and e.code == 404:
            return None
        print(f"Error fetching {url}: {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Error fetching {url}: {e}", file=sys.stderr)
        return None


def sync_market_prices(
    price_dir: str = "prices",
    target_date: Optional[str] = None,
    force: bool = False,
    live: bool = False,
) -> str:
    """
    Ensures market prices for target_date (default today) are available in price_dir.
    Checks local directory, repo directory, GitHub raw, then live TCGCSV (if live=True).
    If live=False and prices are unpublished remotely, cleanly falls back to latest.json.
    """
    os.makedirs(price_dir, exist_ok=True)
    sync_cards_catalog(price_dir)
    today = target_date or datetime.datetime.now().astimezone().strftime("%Y-%m-%d")
    target_file = os.path.join(price_dir, f"{today}.json")
    latest_file = os.path.join(price_dir, "latest.json")

    if not force and not live and os.path.exists(target_file):
        print(f"Using existing price data for {today} from {target_file}.")
        return target_file

    # 1. Check repo root prices directory if price_dir points elsewhere
    repo_prices = REPO_PRICES_DIR
    repo_price_file = repo_prices / f"{today}.json"
    if not live and repo_price_file.is_file() and str(repo_prices.resolve()) != str(Path(price_dir).resolve()):
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
    if not live:
        remote_url = f"{GITHUB_RAW_URL}/{today}.json"
        remote_data = fetch_json(remote_url, quiet_not_found=True)
        if remote_data and (remote_data.get("products") or remote_data.get("prices")):
            with open(target_file, "w", encoding="utf-8") as f:
                json.dump(remote_data, f, indent=2)
            with open(latest_file, "w", encoding="utf-8") as f:
                json.dump(remote_data, f, indent=2)
            print(f"Synced {today} prices from GitHub remote repository.")
            return target_file

    # 3. If live is False, fall back cleanly to latest available price snapshot
    if not live:
        fallback_candidates = [
            latest_file,
            os.path.join(str(repo_prices), "latest.json"),
        ]
        fallback_file = next((f for f in fallback_candidates if os.path.isfile(f)), None)

        if not fallback_file:
            search_dirs = [price_dir]
            if os.path.isdir(str(repo_prices)):
                search_dirs.append(str(repo_prices))
            dated_files = []
            for d in search_dirs:
                for f in glob.glob(os.path.join(d, "*.json")):
                    bname = os.path.basename(f)
                    if bname not in ("latest.json", "cards.json") and bname.replace(".json", "").replace("-", "").isdigit():
                        dated_files.append(f)
            if dated_files:
                dated_files.sort(reverse=True)
                fallback_file = dated_files[0]

        if fallback_file:
            fallback_date = "latest"
            try:
                with open(fallback_file, "r", encoding="utf-8") as f:
                    fb_data = json.load(f)
                    fallback_date = fb_data.get("date", os.path.basename(fallback_file).replace(".json", ""))
            except Exception:
                pass

            print(f"Notice: Market prices for {today} are not yet published remotely (daily sync runs at 20:17 UTC).")
            print(f"Proceeding with latest available price snapshot ({fallback_date}). Pass --live to scrape current prices.")
            return fallback_file

        raise FileNotFoundError(
            f"No market price snapshot found for {today} locally or remotely, and no cached price snapshots are available. "
            f"Pass --live to scrape current prices from TCGCSV."
        )

    # 4. Live scrape from TCGCSV (only reached if live=True)
    last_updated = fetch_text(LAST_UPDATED_URL)
    if last_updated:
        print(f"TCGCSV upstream build timestamp: {last_updated}")
    print(f"Fetching live market prices for {today} from TCGCSV...")
    groups_data = fetch_json(f"{BASE_URL}/{CATEGORY_ID}/groups")
    if not groups_data or not groups_data.get("results"):
        raise RuntimeError("Failed to fetch group catalog from TCGCSV.")

    cards_file = sync_cards_catalog(price_dir)
    existing_cards = {}
    if os.path.isfile(cards_file):
        try:
            with open(cards_file, "r", encoding="utf-8") as f:
                existing_cards = json.load(f)
        except Exception:
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
    total_groups = len(groups_data["results"])

    for idx, group in enumerate(groups_data["results"], start=1):
        gid = group["groupId"]
        gname = group["name"]
        group_modified = group.get("modifiedOn")
        cached_group = existing_groups.get(str(gid))

        need_products = (
            force
            or gid not in existing_group_ids
            or not cached_group
            or (group_modified and cached_group.get("modifiedOn") != group_modified)
        )

        time.sleep(0.2)
        price_data = fetch_json(f"{BASE_URL}/{CATEGORY_ID}/{gid}/prices")

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
            print(f"[{idx}/{total_groups}] Fetching {gname} (ID: {gid}) metadata...")
            time.sleep(0.2)
            prod_data = fetch_json(f"{BASE_URL}/{CATEGORY_ID}/{gid}/products")
            if prod_data and "results" in prod_data:
                for p in prod_data["results"]:
                    pid = p["productId"]
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
                        "groupId": gid,
                        "groupName": gname,
                        "printNumber": print_number,
                        "rarity": rarity,
                    }
        else:
            print(f"[{idx}/{total_groups}] Fetching {gname} (ID: {gid}) prices only...")

        updated_groups[str(gid)] = {
            "name": gname,
            "modifiedOn": group_modified,
        }

    # Update cards.json catalog
    if catalog or updated_groups != existing_groups:
        existing_cards["_groups"] = updated_groups
        existing_cards.update(catalog)
        with open(cards_file, "w", encoding="utf-8") as f:
            json.dump(existing_cards, f, indent=2)

    total_products = len([k for k in existing_cards if not k.startswith("_")])

    payload = {
        "date": today,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "tcgcsvBuild": last_updated,
        "category": "Cyberpunk TCG",
        "categoryId": CATEGORY_ID,
        "productCount": total_products,
        "prices": all_prices,
    }

    with open(target_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    with open(latest_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print(f"Saved {len(all_prices)} price mappings to {target_file}.")
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
    repo_prices = REPO_PRICES_DIR
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
