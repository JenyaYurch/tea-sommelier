"""Refresh the PL/DE Chinese green price index into data/eu_green_price_index.json.

Daily checklist (same storage as the teashop.by catalog: a JSON file under data/,
copied into the tea-agent image at build time; no Cloud SQL):

  1. uv run python scripts/refresh_eu_price_index.py --status
  2. If needs_refresh: uv run python scripts/refresh_eu_price_index.py
  3. Spot-check the summary (shop errors, a few URLs and prices)
  4. Commit data/eu_green_price_index.json and redeploy tea-agent

A shop that returns an error is recorded and contributes no offers. Offers with
an unclear pack size stay in the file with price_per_100g null.

Usage:
  uv run python scripts/refresh_eu_price_index.py --status
  uv run python scripts/refresh_eu_price_index.py
  uv run python scripts/refresh_eu_price_index.py --dry-run
  uv run python scripts/refresh_eu_price_index.py --shop thetea --shop tezen
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tea_agent.eu_price_index import index_meta, index_path  # noqa: E402
from tea_agent.eu_shops.config import load_shops  # noqa: E402
from tea_agent.eu_shops.refresh import (  # noqa: E402
    SHOP_CONCURRENCY,
    build_document,
    format_summary,
    refresh_shops,
    should_write,
    utc_now_stamp,
    write_index,
)


def print_status() -> int:
    """Print index freshness. Exit 1 when a refresh is due."""
    meta = index_meta()
    print(f"path: {meta['path']}")
    print(f"exists: {meta['exists']}")
    print(f"count: {meta['count']}")
    print(f"shops: {meta['shops']}")
    print(f"last_checked: {meta['last_checked']}")
    print(f"fetched_at: {meta['fetched_at']}")
    print(f"age_days: {meta['age_days']}")
    print(f"refresh_interval_days: {meta['refresh_interval_days']}")
    print(f"needs_refresh: {meta['needs_refresh']}")
    if meta["needs_refresh"]:
        print(
            "Action: run `uv run python scripts/refresh_eu_price_index.py` "
            "then commit data/eu_green_price_index.json and redeploy tea-agent"
        )
        return 1
    print("Price index is within the 1 day refresh window.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Refresh the PL/DE Chinese green tea price index"
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show last_checked / freshness and exit 1 if a refresh is due",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and print the summary without writing the index",
    )
    parser.add_argument(
        "--shop",
        action="append",
        default=[],
        help="Limit the run to this shop id (repeatable)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Cap catalog pages per shop (default: adapter limit)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=SHOP_CONCURRENCY,
        help="How many shops to read at once (default: 2)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output JSON path (default: data/eu_green_price_index.json)",
    )
    args = parser.parse_args()
    logging.basicConfig(format="%(message)s", level=logging.INFO)
    if args.status:
        raise SystemExit(print_status())

    shops = load_shops()
    if args.shop:
        wanted = set(args.shop)
        known = {shop.id for shop in shops}
        missing = wanted - known
        if missing:
            joined = ", ".join(sorted(missing))
            raise SystemExit(f"unknown shop id: {joined}")
        shops = [shop for shop in shops if shop.id in wanted]

    fetched_at = utc_now_stamp()
    results = refresh_shops(
        shops,
        fetched_at=fetched_at,
        max_pages=args.max_pages,
        concurrency=max(1, args.concurrency),
    )
    print(format_summary(results))
    refusal = should_write(results)
    if args.dry_run:
        if refusal:
            raise SystemExit(refusal)
        return
    if refusal:
        raise SystemExit(refusal)
    destination = args.out or index_path()
    document = build_document(results, fetched_at=fetched_at)
    write_index(document, destination)
    print(f"wrote {destination} ({document['count']} offers)")


if __name__ == "__main__":
    main()
