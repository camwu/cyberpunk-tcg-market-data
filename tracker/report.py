"""
Portfolio report generator for Cyberpunk TCG collections.
Outputs styled console summaries and detailed markdown reports with custom geometric icons.
"""

import datetime
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

RARITY_ORDER = ["Common", "Uncommon", "Rare", "Epic", "Secret", "Iconic", "Nova"]

RARITY_ICONS = {
    "Common": "▽",
    "Uncommon": "△",
    "Rare": "◇",
    "Epic": "◈",
    "Secret": "◆",
    "Iconic": "★",
    "Nova": "▣",
}

COLOR_DOTS = {
    "Green": "🟢",
    "Blue": "🔵",
    "Red": "🔴",
    "Yellow": "🟡",
}


def normalize_rarity(rarity: str) -> str:
    if not rarity:
        return "Unknown"
    r = rarity.strip()
    if r.startswith("Iconic"):
        return "Iconic"
    if r.startswith("Nova"):
        return "Nova"
    return r


def format_rarity(rarity: str, bold: bool = False) -> str:
    norm = normalize_rarity(rarity)
    icon = RARITY_ICONS.get(norm)
    txt = f"{icon} {norm}" if icon else norm
    return f"**{txt}**" if bold else txt


def format_color(color: str) -> str:
    dot = COLOR_DOTS.get(color)
    return f"{dot} {color}" if dot else color


def generate_portfolio_report(
    db_path: str = "data/price_history.db",
    output_md: str = "LATEST_PORTFOLIO_SUMMARY.md",
    price_cache_dir: Optional[str] = None,
    target_date: Optional[str] = None,
):
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found at {db_path}")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(card_metadata)")
    cols = [col[1] for col in cur.fetchall()]
    if "card_type" not in cols:
        cur.execute("ALTER TABLE card_metadata ADD COLUMN card_type TEXT")
        conn.commit()

    cur.execute("PRAGMA table_info(portfolio_daily_summary)")
    sum_cols = [col[1] for col in cur.fetchall()]
    if "collection_updated_at" not in sum_cols:
        cur.execute("ALTER TABLE portfolio_daily_summary ADD COLUMN collection_updated_at TEXT")
        conn.commit()
    if "collection_source" not in sum_cols:
        cur.execute("ALTER TABLE portfolio_daily_summary ADD COLUMN collection_source TEXT")
        conn.commit()
    if "total_cost_basis" not in sum_cols:
        cur.execute("ALTER TABLE portfolio_daily_summary ADD COLUMN total_cost_basis REAL DEFAULT 0.0")
        conn.commit()
    if "net_unrealized_gain" not in sum_cols:
        cur.execute("ALTER TABLE portfolio_daily_summary ADD COLUMN net_unrealized_gain REAL DEFAULT 0.0")
        conn.commit()
    if "net_unrealized_pct" not in sum_cols:
        cur.execute("ALTER TABLE portfolio_daily_summary ADD COLUMN net_unrealized_pct REAL DEFAULT 0.0")
        conn.commit()
    if "purchases_updated_at" not in sum_cols:
        cur.execute("ALTER TABLE portfolio_daily_summary ADD COLUMN purchases_updated_at TEXT")
        conn.commit()

    if target_date:
        cur.execute("SELECT date FROM portfolio_daily_summary WHERE date = ?", (target_date,))
        row = cur.fetchone()
        if not row:
            print(f"No valuation records found for date '{target_date}' in database.", file=sys.stderr)
            conn.close()
            return
        latest_date = target_date
    else:
        cur.execute("SELECT date FROM portfolio_daily_summary ORDER BY date DESC LIMIT 1")
        latest_row = cur.fetchone()
        if not latest_row:
            print("No valuation records found in database.", file=sys.stderr)
            conn.close()
            return
        latest_date = latest_row[0]

    cur.execute("""
    SELECT total_value, total_cards, unique_items, l7d_dollar_delta, l7d_pct_delta,
           lifetime_dollar_gain, lifetime_pct_gain, COALESCE(total_sealed, 0),
           collection_updated_at, collection_source,
           COALESCE(total_cost_basis, 0.0), COALESCE(net_unrealized_gain, 0.0), COALESCE(net_unrealized_pct, 0.0),
           purchases_updated_at
    FROM portfolio_daily_summary
    WHERE date = ?
    """, (latest_date,))
    summary = cur.fetchone()

    total_val, total_cards, unique_items, l7d_dollar, l7d_pct, life_dollar, life_pct, total_sealed, collection_updated_at, collection_source, cost_basis, net_gain, net_pct, purchases_updated_at = summary

    price_timestamp_display = latest_date
    collection_timestamp_display = collection_updated_at or latest_date
    collection_source_display = collection_source or "—"
    purchases_timestamp_display = purchases_updated_at or collection_timestamp_display

    # Get breakdown by rarity (cards only, collapsed into 7 tiers)
    cur.execute("""
    SELECT CASE 
               WHEN m.rarity LIKE 'Iconic%' THEN 'Iconic'
               WHEN m.rarity LIKE 'Nova%' THEN 'Nova'
               ELSE m.rarity 
           END AS clean_rarity,
           COUNT(DISTINCT (m.name || '::' || m.expansion || '::' || m.finish)), 
           SUM(s.quantity), 
           COALESCE(SUM(s.line_total), 0.0)
    FROM daily_snapshots s
    JOIN card_metadata m ON s.card_key = m.card_key
    WHERE s.date = ? AND COALESCE(m.item_type, 'Card') = 'Card'
    GROUP BY clean_rarity
    """, (latest_date,))
    rarity_rows = cur.fetchall()
    rarity_order_map = {name: i for i, name in enumerate(RARITY_ORDER)}
    rarity_breakdown = sorted(rarity_rows, key=lambda x: rarity_order_map.get(x[0], 99))

    # Get breakdown by color (cards only)
    cur.execute("""
    SELECT COALESCE(NULLIF(m.color, ''), 'Unknown') AS clean_color,
           COUNT(DISTINCT (m.name || '::' || m.expansion || '::' || m.finish)),
           SUM(s.quantity),
           COALESCE(SUM(s.line_total), 0.0)
    FROM daily_snapshots s
    JOIN card_metadata m ON s.card_key = m.card_key
    WHERE s.date = ? AND COALESCE(m.item_type, 'Card') = 'Card'
    GROUP BY clean_color
    ORDER BY SUM(s.line_total) DESC
    """, (latest_date,))
    color_breakdown = cur.fetchall()

    # Get breakdown by card type (cards only)
    cur.execute("""
    SELECT COALESCE(NULLIF(m.card_type, ''), 'Unknown') AS clean_type,
           COUNT(DISTINCT (m.name || '::' || m.expansion || '::' || m.finish)),
           SUM(s.quantity),
           COALESCE(SUM(s.line_total), 0.0)
    FROM daily_snapshots s
    JOIN card_metadata m ON s.card_key = m.card_key
    WHERE s.date = ? AND COALESCE(m.item_type, 'Card') = 'Card'
    GROUP BY clean_type
    ORDER BY SUM(s.line_total) DESC
    """, (latest_date,))
    card_type_breakdown = cur.fetchall()

    # Get breakdown by expansion (cards only)
    cur.execute("""
    SELECT COALESCE(NULLIF(m.expansion, ''), 'Unknown') AS clean_expansion,
           COUNT(DISTINCT (m.name || '::' || m.expansion || '::' || m.finish)),
           SUM(s.quantity),
           COALESCE(SUM(s.line_total), 0.0)
    FROM daily_snapshots s
    JOIN card_metadata m ON s.card_key = m.card_key
    WHERE s.date = ? AND COALESCE(m.item_type, 'Card') = 'Card'
    GROUP BY clean_expansion
    ORDER BY SUM(s.line_total) DESC
    """, (latest_date,))
    expansion_breakdown = cur.fetchall()

    # Get sealed products
    cur.execute("""
    SELECT m.name, m.expansion, s.quantity, s.unit_market_price, s.line_total,
           s.baseline_price, s.lifetime_gain_dollar, s.lifetime_gain_pct,
           COALESCE(m.first_seen_date, '') AS acq_date
    FROM daily_snapshots s
    JOIN card_metadata m ON s.card_key = m.card_key
    WHERE s.date = ? AND m.item_type = 'Sealed'
    ORDER BY s.line_total DESC
    """, (latest_date,))
    sealed_products = cur.fetchall()

    # Get top 5 gainers (cards only, ranked by per-unit price delta)
    cur.execute("""
    SELECT m.name, m.rarity, m.color, m.finish, s.quantity, s.unit_market_price, s.baseline_price,
           s.lifetime_gain_dollar, s.lifetime_gain_pct, COALESCE(m.first_seen_date, '') AS acq_date
    FROM daily_snapshots s
    JOIN card_metadata m ON s.card_key = m.card_key
    WHERE s.date = ? AND s.lifetime_gain_dollar > 0 AND COALESCE(m.item_type, 'Card') = 'Card'
    ORDER BY (s.unit_market_price - s.baseline_price) DESC
    LIMIT 5
    """, (latest_date,))
    top_gainers = cur.fetchall()

    # Get top 5 decliners (cards only, ranked by per-unit price delta, excluding unpriced items)
    cur.execute("""
    SELECT m.name, m.rarity, m.color, m.finish, s.quantity, s.unit_market_price, s.baseline_price,
           s.lifetime_gain_dollar, s.lifetime_gain_pct, COALESCE(m.first_seen_date, '') AS acq_date
    FROM daily_snapshots s
    JOIN card_metadata m ON s.card_key = m.card_key
    WHERE s.date = ? AND s.lifetime_gain_dollar < 0 AND s.unit_market_price > 0 AND COALESCE(m.item_type, 'Card') = 'Card'
    ORDER BY (s.unit_market_price - s.baseline_price) ASC
    LIMIT 5
    """, (latest_date,))
    top_decliners = cur.fetchall()

    # Get high-value singles (>= $10.00, cards only)
    cur.execute("""
    SELECT m.name, m.expansion, m.rarity, m.color, m.finish, SUM(s.quantity), s.unit_market_price, SUM(s.line_total),
           COALESCE(NULLIF(m.card_type, ''), 'Unknown') AS card_type
    FROM daily_snapshots s
    JOIN card_metadata m ON s.card_key = m.card_key
    WHERE s.date = ? AND s.unit_market_price >= 10.0 AND COALESCE(m.item_type, 'Card') = 'Card'
    GROUP BY m.name, m.expansion, m.rarity, m.color, m.finish, s.unit_market_price, card_type
    ORDER BY s.unit_market_price DESC
    """, (latest_date,))
    high_value_cards = cur.fetchall()

    # Query L7D baseline date (7th prior recorded date, or oldest available)
    cur.execute("""
    SELECT date
    FROM portfolio_daily_summary
    WHERE date < ?
    ORDER BY date DESC
    LIMIT 7
    """, (latest_date,))
    prior_summary_dates = cur.fetchall()
    l7d_date = prior_summary_dates[-1][0] if prior_summary_dates else None

    top_l7d_gainers = []
    top_l7d_decliners = []
    if l7d_date:
        cur.execute("""
        WITH evaluated AS (
            SELECT m.name, m.rarity, m.color, m.finish, s.quantity,
                   COALESCE(NULLIF(s.unit_market_price, 0.0), m.baseline_market_price, 0.0) AS curr_price,
                   COALESCE(NULLIF(prev.unit_market_price, 0.0), m.baseline_market_price, 0.0) AS prev_price,
                   m.item_type,
                   COALESCE(m.first_seen_date, '') AS acq_date
            FROM daily_snapshots s
            JOIN daily_snapshots prev ON s.card_key = prev.card_key AND prev.date = ?
            JOIN card_metadata m ON s.card_key = m.card_key
            WHERE s.date = ? AND COALESCE(m.item_type, 'Card') = 'Card'
        )
        SELECT name, rarity, color, finish, quantity, curr_price, prev_price,
               ROUND((curr_price - prev_price) * quantity, 2) AS dollar_gain,
               CASE WHEN prev_price > 0 THEN ROUND(((curr_price - prev_price) / prev_price) * 100.0, 1) ELSE 0.0 END AS pct_gain,
               acq_date
        FROM evaluated
        WHERE curr_price > prev_price
        ORDER BY (curr_price - prev_price) DESC
        LIMIT 5
        """, (l7d_date, latest_date))
        top_l7d_gainers = cur.fetchall()

        cur.execute("""
        WITH evaluated AS (
            SELECT m.name, m.rarity, m.color, m.finish, s.quantity,
                   COALESCE(NULLIF(s.unit_market_price, 0.0), m.baseline_market_price, 0.0) AS curr_price,
                   COALESCE(NULLIF(prev.unit_market_price, 0.0), m.baseline_market_price, 0.0) AS prev_price,
                   m.item_type,
                   COALESCE(m.first_seen_date, '') AS acq_date
            FROM daily_snapshots s
            JOIN daily_snapshots prev ON s.card_key = prev.card_key AND prev.date = ?
            JOIN card_metadata m ON s.card_key = m.card_key
            WHERE s.date = ? AND COALESCE(m.item_type, 'Card') = 'Card'
        )
        SELECT name, rarity, color, finish, quantity, curr_price, prev_price,
               ROUND((curr_price - prev_price) * quantity, 2) AS dollar_gain,
               CASE WHEN prev_price > 0 THEN ROUND(((curr_price - prev_price) / prev_price) * 100.0, 1) ELSE 0.0 END AS pct_gain,
               acq_date
        FROM evaluated
        WHERE curr_price < prev_price
        ORDER BY (curr_price - prev_price) ASC
        LIMIT 5
        """, (l7d_date, latest_date))
        top_l7d_decliners = cur.fetchall()

    conn.close()

    sealed_summary_line = f"\n| **Total Sealed Items** | **{total_sealed}** {'unit' if total_sealed == 1 else 'units'} |" if total_sealed > 0 else ""
    cost_summary_lines = f"\n| **Total Invested Cost Basis** | **`${cost_basis:,.2f}`** |\n| **Net Unrealized Gain / Loss** | **{'+' if net_gain >= 0 else ''}${net_gain:,.2f}** ({'+' if net_pct >= 0 else ''}{net_pct:.2f}%) |" if cost_basis > 0 else ""

    cost_print = f" | Cost Basis: ${cost_basis:,.2f} | Net Unrealized Gain: {'+' if net_gain >= 0 else ''}${net_gain:,.2f} ({'+' if net_pct >= 0 else ''}{net_pct:.2f}%)" if cost_basis > 0 else ""
    print(f"\nPortfolio valuation report generated for {latest_date}: ${total_val:,.2f}{cost_print} across {total_cards} cards and {total_sealed} sealed items.")

    sealed_section = ""
    if sealed_products:
        sealed_section = """
### Sealed Products

| Product Name | Expansion | Acquired | Qty | Unit Price | Total Value | Baseline Price | Lifetime Gain |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
"""
        for name, exp, qty, u_price, total, base, gain, pct, acq_date in sealed_products:
            gain_str = f"**{'+' if gain >= 0 else ''}${gain:,.2f}** ({'+' if pct >= 0 else ''}{pct:.1f}%)"
            acq_display = f"`{acq_date}`" if acq_date else "—"
            sealed_section += f"| **{name}** | {exp} | {acq_display} | {qty} | `${u_price:,.2f}` | `${total:,.2f}` | `${base:,.2f}` | {gain_str} |\n"

    report_generated = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %I:%M %p")
    purchases_line = f"**Purchases Last Updated**: `{purchases_timestamp_display}`  \n" if cost_basis > 0 else ""

    # Format Markdown Output
    md_content = f"""# 📊 Cyberpunk TCG Portfolio Valuation Report

**Collection Last Updated**: `{collection_timestamp_display}`  
**Collection Source**: `{collection_source_display}`  
**Prices Last Updated**: `{price_timestamp_display}`  
{purchases_line}**Report Generated**: `{report_generated}`

---

## 💼 Executive Summary

| Metric | Value |
| :--- | :--- |
| **Total Portfolio Market Value** | **`${total_val:,.2f}`** |{cost_summary_lines}
| **Total Physical Cards** | **{total_cards}** copies |{sealed_summary_line}
| **Unique Inventory Entries** | **{unique_items}** entries |
| **Rolling L7D Performance** | **{'+' if l7d_dollar >= 0 else ''}${l7d_dollar:,.2f}** ({'+' if l7d_pct >= 0 else ''}{l7d_pct:.2f}%) |
| **Lifetime Market Gain / Loss** | **{'+' if life_dollar >= 0 else ''}${life_dollar:,.2f}** ({'+' if life_pct >= 0 else ''}{life_pct:.2f}%) |

---

## 📊 Portfolio Breakdown

### Rarity

| Rarity | Unique Items | Physical Copies | Market Value | % of Portfolio |
| :--- | :---: | :---: | :---: | :---: |
"""

    for rarity, entries, qty, r_val in rarity_breakdown:
        pct_of_total = (r_val / total_val * 100.0) if total_val > 0 else 0.0
        r_lbl = format_rarity(rarity, bold=True)
        md_content += f"| {r_lbl} | {entries} | {qty} | `${r_val:,.2f}` | {pct_of_total:.1f}% |\n"

    md_content += """
### Color

| Color | Unique Items | Physical Copies | Market Value | % of Portfolio |
| :--- | :---: | :---: | :---: | :---: |
"""

    for color, entries, qty, c_val in color_breakdown:
        pct_of_total = (c_val / total_val * 100.0) if total_val > 0 else 0.0
        dot = COLOR_DOTS.get(color)
        c_lbl = f"{dot} {color}" if dot else color
        md_content += f"| **{c_lbl}** | {entries} | {qty} | `${c_val:,.2f}` | {pct_of_total:.1f}% |\n"

    if card_type_breakdown:
        md_content += """
### Card Type

| Card Type | Unique Items | Physical Copies | Market Value | % of Portfolio |
| :--- | :---: | :---: | :---: | :---: |
"""
        for ctype, entries, qty, ct_val in card_type_breakdown:
            pct_of_total = (ct_val / total_val * 100.0) if total_val > 0 else 0.0
            md_content += f"| **{ctype}** | {entries} | {qty} | `${ct_val:,.2f}` | {pct_of_total:.1f}% |\n"

    if expansion_breakdown:
        md_content += """
### Expansion

| Expansion | Unique Items | Physical Copies | Market Value | % of Portfolio |
| :--- | :---: | :---: | :---: | :---: |
"""
        for exp, entries, qty, exp_val in expansion_breakdown:
            pct_of_total = (exp_val / total_val * 100.0) if total_val > 0 else 0.0
            md_content += f"| **{exp}** | {entries} | {qty} | `${exp_val:,.2f}` | {pct_of_total:.1f}% |\n"

    def render_high_value_table(cards):
        table = """| Card Name | Type | Expansion | Rarity | Finish | Qty | Unit Price | Total Value |
| :--- | :--- | :--- | :--- | :--- | :---: | :---: | :---: |
"""
        for name, exp, rarity, color, finish, qty, price, total, ctype in cards:
            r_str = format_rarity(rarity, bold=True)
            dot = COLOR_DOTS.get(color, "")
            card_display = f"{dot} **{name}**" if dot else f"**{name}**"
            table += f"| {card_display} | {ctype} | {exp} | {r_str} | {finish} | {qty} | `${price:,.2f}` | `${total:,.2f}` |\n"
        return table

    iconic_singles = [c for c in high_value_cards if (c[2] or "").startswith("Iconic")]
    nova_singles = [c for c in high_value_cards if (c[2] or "").startswith("Nova")]
    non_iconic_nova_singles = [c for c in high_value_cards if not (c[2] or "").startswith("Iconic") and not (c[2] or "").startswith("Nova")]

    md_content += """
### High-Value Singles (`$10.00`+)
"""
    if iconic_singles:
        md_content += """
#### Iconic Singles

""" + render_high_value_table(iconic_singles)

    if nova_singles:
        md_content += """
#### Nova Rare Singles

""" + render_high_value_table(nova_singles)

    if non_iconic_nova_singles:
        md_content += """
#### Non-Iconic/Nova Rare Singles

""" + render_high_value_table(non_iconic_nova_singles)

    if not high_value_cards:
        md_content += "\n*No singles currently valued at $10.00 or higher.*\n"

    if sealed_section:
        md_content += sealed_section

    md_content += """
---

## 📈 Top Gainers

### Lifetime

| Card Name | Acquired | Rarity | Finish | Qty | Unit Price | Baseline Price | Dollar Gain | Percent Gain |
| :--- | :---: | :--- | :--- | :---: | :---: | :---: | :---: | :---: |
"""

    for name, rarity, color, finish, qty, price, base, gain, pct, acq_date in top_gainers:
        r_str = format_rarity(rarity, bold=True)
        dot = COLOR_DOTS.get(color, "")
        card_display = f"{dot} **{name}**" if dot else f"**{name}**"
        acq_display = f"`{acq_date}`" if acq_date else "—"
        md_content += f"| {card_display} | {acq_display} | {r_str} | {finish} | {qty} | `${price:,.2f}` | `${base:,.2f}` | **{'+' if gain >= 0 else ''}${gain:,.2f}** | {'+' if pct >= 0 else ''}{pct:.1f}% |\n"

    if l7d_date:
        md_content += f"""
### L7D (Since `{l7d_date}`)

| Card Name | Acquired | Rarity | Finish | Qty | Unit Price | 7D Prior Price | Dollar Gain | Percent Gain |
| :--- | :---: | :--- | :--- | :---: | :---: | :---: | :---: | :---: |
"""
        for name, rarity, color, finish, qty, price, prev_price, gain, pct, acq_date in top_l7d_gainers:
            r_str = format_rarity(rarity, bold=True)
            dot = COLOR_DOTS.get(color, "")
            card_display = f"{dot} **{name}**" if dot else f"**{name}**"
            acq_display = f"`{acq_date}`" if acq_date else "—"
            md_content += f"| {card_display} | {acq_display} | {r_str} | {finish} | {qty} | `${price:,.2f}` | `${prev_price:,.2f}` | **{'+' if gain >= 0 else ''}${gain:,.2f}** | {'+' if pct >= 0 else ''}{pct:.1f}% |\n"

    md_content += """
---

## 📉 Top Decliners

### Lifetime

| Card Name | Acquired | Rarity | Finish | Qty | Unit Price | Baseline Price | Dollar Loss | Percent Loss |
| :--- | :---: | :--- | :--- | :---: | :---: | :---: | :---: | :---: |
"""

    for name, rarity, color, finish, qty, price, base, gain, pct, acq_date in top_decliners:
        r_str = format_rarity(rarity, bold=True)
        dot = COLOR_DOTS.get(color, "")
        card_display = f"{dot} **{name}**" if dot else f"**{name}**"
        acq_display = f"`{acq_date}`" if acq_date else "—"
        md_content += f"| {card_display} | {acq_display} | {r_str} | {finish} | {qty} | `${price:,.2f}` | `${base:,.2f}` | **{'+' if gain >= 0 else ''}${gain:,.2f}** | {'+' if pct >= 0 else ''}{pct:.1f}% |\n"

    if l7d_date:
        md_content += f"""
### L7D (Since `{l7d_date}`)

| Card Name | Acquired | Rarity | Finish | Qty | Unit Price | 7D Prior Price | Dollar Loss | Percent Loss |
| :--- | :---: | :--- | :--- | :---: | :---: | :---: | :---: | :---: |
"""
        for name, rarity, color, finish, qty, price, prev_price, gain, pct, acq_date in top_l7d_decliners:
            r_str = format_rarity(rarity, bold=True)
            dot = COLOR_DOTS.get(color, "")
            card_display = f"{dot} **{name}**" if dot else f"**{name}**"
            acq_display = f"`{acq_date}`" if acq_date else "—"
            md_content += f"| {card_display} | {acq_display} | {r_str} | {finish} | {qty} | `${price:,.2f}` | `${prev_price:,.2f}` | **{'+' if gain >= 0 else ''}${gain:,.2f}** | {'+' if pct >= 0 else ''}{pct:.1f}% |\n"

    os.makedirs(os.path.dirname(output_md) or ".", exist_ok=True)
    with open(output_md, "w", encoding="utf-8") as f:
        f.write(md_content)

    pointer_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".latest_report")
    try:
        with open(pointer_file, "w", encoding="utf-8") as f:
            f.write(os.path.abspath(output_md))
    except Exception:
        pass

    print(f"Markdown portfolio summary saved to {output_md}.")
