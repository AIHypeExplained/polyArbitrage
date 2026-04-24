#!/usr/bin/env python3

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone


GAMMA_BASE_URL = "https://gamma-api.polymarket.com/markets"
CLOB_PRICES_URL = "https://clob.polymarket.com/prices"
USER_AGENT = "polyArbitrage/1.0"


def fetch_json(url: str, data=None):
    headers = {"User-Agent": USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def get_markets(page_size: int, pages: int):
    markets = []
    seen = set()
    for page in range(pages):
        params = {
            "limit": page_size,
            "offset": page * page_size,
            "closed": "false",
            "active": "true",
            "enableOrderBook": "true",
        }
        url = GAMMA_BASE_URL + "?" + urllib.parse.urlencode(params)
        batch = fetch_json(url)
        if not batch:
            break
        for market in batch:
            market_id = market.get("id")
            if market_id in seen:
                continue
            seen.add(market_id)
            markets.append(market)
    return markets


def chunked(items, size):
    for idx in range(0, len(items), size):
        yield items[idx : idx + size]


def get_buy_prices(token_ids):
    requests = [{"token_id": token_id, "side": "BUY"} for token_id in token_ids]
    prices = {}
    for chunk in chunked(requests, 200):
        payload = json.dumps(chunk).encode()
        prices.update(fetch_json(CLOB_PRICES_URL, data=payload))
    return prices


def parse_market(market):
    try:
        outcomes = json.loads(market.get("outcomes", "[]"))
        token_ids = json.loads(market.get("clobTokenIds", "[]"))
    except json.JSONDecodeError:
        return None

    if outcomes != ["Yes", "No"] or len(token_ids) != 2:
        return None

    updated_at = market.get("updatedAt")
    updated_hours_ago = None
    if updated_at:
        updated_dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        updated_hours_ago = round(
            (datetime.now(timezone.utc) - updated_dt).total_seconds() / 3600, 2
        )

    event = (market.get("events") or [{}])[0]
    return {
        "id": market.get("id"),
        "question": market.get("question"),
        "slug": market.get("slug"),
        "liquidity": float(market.get("liquidity") or 0),
        "volume24hr": float(market.get("volume24hr") or 0),
        "updated_hours_ago": updated_hours_ago,
        "negRisk": bool(market.get("negRisk")),
        "event_id": event.get("id"),
        "event_title": event.get("title"),
        "event_slug": event.get("slug"),
        "token_yes": token_ids[0],
        "token_no": token_ids[1],
    }


def scan_binary(markets, prices, max_age_hours):
    rows = []
    for market in markets:
        yes_buy = prices.get(market["token_yes"], {}).get("BUY")
        no_buy = prices.get(market["token_no"], {}).get("BUY")
        if yes_buy is None or no_buy is None:
            continue

        if (
            market["updated_hours_ago"] is not None
            and market["updated_hours_ago"] > max_age_hours
        ):
            continue

        total = float(yes_buy) + float(no_buy)
        rows.append(
            {
                "question": market["question"],
                "slug": market["slug"],
                "yes_buy": float(yes_buy),
                "no_buy": float(no_buy),
                "sum_buy": round(total, 6),
                "edge": round(1 - total, 6),
                "liquidity": market["liquidity"],
                "volume24hr": market["volume24hr"],
                "updated_hours_ago": market["updated_hours_ago"],
                "negRisk": market["negRisk"],
            }
        )

    rows.sort(key=lambda row: (row["edge"], row["volume24hr"], row["liquidity"]), reverse=True)
    return rows


def scan_neg_risk_groups(markets, prices, max_age_hours):
    groups = defaultdict(list)
    for market in markets:
        if not market["negRisk"] or not market["event_id"]:
            continue

        if (
            market["updated_hours_ago"] is not None
            and market["updated_hours_ago"] > max_age_hours
        ):
            continue

        yes_buy = prices.get(market["token_yes"], {}).get("BUY")
        if yes_buy is None:
            continue

        groups[market["event_id"]].append(
            {
                "question": market["question"],
                "slug": market["slug"],
                "yes_buy": float(yes_buy),
                "liquidity": market["liquidity"],
                "volume24hr": market["volume24hr"],
                "event_title": market["event_title"],
                "event_slug": market["event_slug"],
            }
        )

    rows = []
    for event_id, legs in groups.items():
        if len(legs) < 2:
            continue
        total = sum(leg["yes_buy"] for leg in legs)
        rows.append(
            {
                "event_id": event_id,
                "event_title": legs[0]["event_title"],
                "event_slug": legs[0]["event_slug"],
                "markets": len(legs),
                "sum_yes_buy": round(total, 6),
                "edge": round(1 - total, 6),
                "total_liquidity": round(sum(leg["liquidity"] for leg in legs), 2),
                "total_volume24hr": round(sum(leg["volume24hr"] for leg in legs), 2),
                "legs": sorted(legs, key=lambda leg: leg["yes_buy"], reverse=True),
            }
        )

    rows.sort(
        key=lambda row: (row["edge"], row["total_volume24hr"], row["total_liquidity"]),
        reverse=True,
    )
    return rows


def build_report(page_size: int, pages: int, max_age_hours: float, top: int):
    raw_markets = get_markets(page_size=page_size, pages=pages)
    parsed_markets = [market for market in (parse_market(m) for m in raw_markets) if market]
    token_ids = []
    for market in parsed_markets:
        token_ids.append(market["token_yes"])
        token_ids.append(market["token_no"])
    prices = get_buy_prices(token_ids)

    binary = scan_binary(parsed_markets, prices, max_age_hours=max_age_hours)
    neg_risk = scan_neg_risk_groups(parsed_markets, prices, max_age_hours=max_age_hours)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scan_config": {
            "page_size": page_size,
            "pages": pages,
            "max_age_hours": max_age_hours,
            "top": top,
        },
        "summary": {
            "markets_scanned": len(parsed_markets),
            "binary_under_1_count": sum(1 for row in binary if row["sum_buy"] < 1),
            "neg_risk_under_1_count": sum(1 for row in neg_risk if row["sum_yes_buy"] < 1),
        },
        "binary_under_1": [row for row in binary if row["sum_buy"] < 1][:top],
        "neg_risk_under_1": [row for row in neg_risk if row["sum_yes_buy"] < 1][:top],
        "binary_top_all": binary[:top],
        "neg_risk_top_all": neg_risk[:top],
    }


def print_summary(report):
    summary = report["summary"]
    print(f"Markets scanned: {summary['markets_scanned']}")
    print(f"Binary candidates under $1: {summary['binary_under_1_count']}")
    print(f"Negative-risk baskets under $1: {summary['neg_risk_under_1_count']}")

    print("\nTop negative-risk baskets:")
    for row in report["neg_risk_under_1"][:5]:
        print(
            f"- {row['event_title']}: sum_yes_buy={row['sum_yes_buy']:.3f}, "
            f"edge={row['edge']:.3f}, markets={row['markets']}"
        )

    print("\nTop binary candidates:")
    for row in report["binary_under_1"][:5]:
        print(
            f"- {row['question']}: yes={row['yes_buy']:.3f}, no={row['no_buy']:.3f}, "
            f"sum={row['sum_buy']:.3f}, edge={row['edge']:.3f}"
        )


def parse_args():
    parser = argparse.ArgumentParser(description="Scan Polymarket for arbitrage candidates.")
    parser.add_argument("--page-size", type=int, default=500, help="Markets fetched per page.")
    parser.add_argument("--pages", type=int, default=3, help="Number of pages to fetch.")
    parser.add_argument(
        "--max-age-hours",
        type=float,
        default=24.0,
        help="Ignore markets updated earlier than this threshold.",
    )
    parser.add_argument("--top", type=int, default=20, help="Max rows per section in the report.")
    parser.add_argument("--output", help="Optional path to write the full JSON report.")
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        report = build_report(
            page_size=args.page_size,
            pages=args.pages,
            max_age_hours=args.max_age_hours,
            top=args.top,
        )
    except urllib.error.URLError as exc:
        print(f"Network error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Failed to scan Polymarket: {exc}", file=sys.stderr)
        return 1

    print_summary(report)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
