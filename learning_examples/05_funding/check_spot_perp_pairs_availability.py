"""
Checks which assets are available for BOTH spot markets/pairs and perpetual trading.

1) Uses spot market pairs (from spot universe), not just token names.
2) Prefers spotMetaAndAssetCtxs to get spot universe + spot contexts (for tradability + spot liquidity/price fields).

NOTE:
- This still treats "eligible" as: (base asset has a spot market) AND (base asset has a perp market).
- For real arb, you'd also check spot book depth/spread per pair (not added here).
"""

import asyncio
import os
from typing import Dict, List, Set, Optional
from dotenv import load_dotenv
import httpx
from hyperliquid.info import Info

load_dotenv()

CHAINSTACK_BASE_URL = os.getenv("HYPERLIQUID_CHAINSTACK_BASE_URL")
PUBLIC_BASE_URL = os.getenv("HYPERLIQUID_PUBLIC_BASE_URL")

MIN_FUNDING_RATE = 0.0001  # 0.01% threshold


async def get_spot_markets() -> Optional[Dict[str, Dict]]:
    """
    Get all spot markets (pair ids) and spot contexts using spotMetaAndAssetCtxs.

    Returns mapping keyed by BASE token symbol:
      {
        "PURR": {
            "market_ids": {"PURR/USDC"},        # canonical name or @index
            "by_market_id": {
                "PURR/USDC": {"ctx": {...}, "quote": "USDC", "market_index": 0},
                ...
            }
        },
        "HFUN": {
            "market_ids": {"@1"},
            "by_market_id": {"@1": {"ctx": {...}, "quote": "USDC", "market_index": 1}}
        },
        ...
      }
    """
    print("Spot Markets (Pairs/Ids) via spotMetaAndAssetCtxs")
    print("-" * 55)

    try:
        async with httpx.AsyncClient() as client:
            # You can only use this endpoint on the official Hyperliquid public API
            resp = await client.post(
                f"{PUBLIC_BASE_URL}/info",
                json={"type": "spotMetaAndAssetCtxs"},
                headers={"Content-Type": "application/json"},
            )

            if resp.status_code != 200:
                print(f"HTTP failed: {resp.status_code}")
                return None

            data = resp.json()
            if not (isinstance(data, list) and len(data) >= 2):
                print("Unexpected response format for spotMetaAndAssetCtxs")
                return None

            spot_meta = data[0]
            spot_ctxs = data[1]

            tokens = spot_meta.get("tokens", [])
            universe = spot_meta.get("universe", [])

            if not tokens or not universe:
                print("Missing tokens/universe in spotMetaAndAssetCtxs")
                return None

            # Build token index -> token symbol
            token_by_index = {}
            for t in tokens:
                idx = t.get("index")
                name = t.get("name")
                if isinstance(idx, int) and isinstance(name, str) and name:
                    token_by_index[idx] = name

            spot_markets: Dict[str, Dict] = {}

            for i, mkt in enumerate(universe):
                token_idxs = mkt.get("tokens", [])
                if not (isinstance(token_idxs, list) and len(token_idxs) >= 2):
                    continue

                base_idx, quote_idx = token_idxs[0], token_idxs[1]
                base = token_by_index.get(base_idx)
                quote = token_by_index.get(quote_idx)

                # Spot market identifier: "PURR/USDC" for canonical, "@1" for most others
                market_id = mkt.get("name") or f"@{mkt.get('index', i)}"
                market_index = mkt.get("index", i)

                if not base or not market_id:
                    continue

                ctx = spot_ctxs[i] if i < len(spot_ctxs) and isinstance(spot_ctxs[i], dict) else {}

                spot_markets.setdefault(base, {"market_ids": set(), "by_market_id": {}})
                spot_markets[base]["market_ids"].add(market_id)
                spot_markets[base]["by_market_id"][market_id] = {
                    "ctx": ctx,
                    "quote": quote,
                    "market_index": market_index,
                    "isCanonical": bool(mkt.get("isCanonical", False)),
                }

            # Print a compact summary
            total_markets = sum(len(v["market_ids"]) for v in spot_markets.values())
            print(f"Found {total_markets} spot markets across {len(spot_markets)} base tokens")
            preview = []
            for base, blob in sorted(spot_markets.items()):
                for mid in sorted(blob["market_ids"]):
                    preview.append(f"{base}:{mid}")
            for row_i in range(0, min(len(preview), 36), 6):
                print("   " + " | ".join(preview[row_i:row_i+6]))
            if len(preview) > 36:
                print(f"   ... +{len(preview)-36} more")

            return spot_markets

    except Exception as e:
        print(f"Spot markets failed: {e}")
        return None


async def get_perp_assets() -> Optional[Set[str]]:
    """Get all assets available for perpetual trading."""
    print("\nPerpetual Assets")
    print("-" * 35)

    try:
        info = Info(CHAINSTACK_BASE_URL, skip_ws=True)
        meta = info.meta()
        perp_assets = set()

        if "universe" in meta:
            for asset_info in meta["universe"]:
                asset_name = asset_info.get("name", "")
                if asset_name:
                    perp_assets.add(asset_name)

        print(f"Found {len(perp_assets)} perpetual assets")
        sorted_assets = sorted(list(perp_assets))
        for i in range(0, len(sorted_assets), 6):
            row = sorted_assets[i:i+6]
            print(f"   {', '.join(f'{asset:>6}' for asset in row)}")

        return perp_assets

    except Exception as e:
        print(f"Perp assets failed: {e}")
        return None


async def find_arbitrage_eligible_assets() -> Optional[List[Dict]]:
    """
    Find assets available in BOTH:
      - at least one spot market pair (from spot universe)
      - perpetual markets
    Then attach perp funding + mark price for those eligible assets.
    """
    print("\nFunding Arbitrage Eligible Assets (Spot pairs + Perps)")
    print("=" * 55)

    spot_markets = await get_spot_markets()
    perp_assets = await get_perp_assets()

    if not spot_markets or not perp_assets:
        print("Failed to get market data")
        return None

    spot_base_assets = set(spot_markets.keys())
    eligible_assets = spot_base_assets.intersection(perp_assets)

    if not eligible_assets:
        print("No base assets found in both spot markets and perpetual markets")
        return None

    print(f"\nFound {len(eligible_assets)} base assets available in BOTH markets.")
    for base in sorted(list(eligible_assets)):
        pairs = sorted(list(spot_markets[base]["market_ids"]))
        pairs_preview = ", ".join(pairs[:6]) + (f" ...(+{len(pairs)-6})" if len(pairs) > 6 else "")
        print(f"   {base:>6}: {pairs_preview}")

    # Attach current perp funding for eligible assets
    try:
        info = Info(PUBLIC_BASE_URL, skip_ws=True)
        meta_and_contexts = info.meta_and_asset_ctxs()

        eligible_with_funding = []

        if meta_and_contexts and len(meta_and_contexts) >= 2:
            meta = meta_and_contexts[0]
            asset_ctxs = meta_and_contexts[1]

            for i, asset_ctx in enumerate(asset_ctxs):
                asset_name = meta["universe"][i]["name"] if i < len(meta["universe"]) else f"UNKNOWN_{i}"

                if asset_name in eligible_assets:
                    funding_rate = float(asset_ctx.get("funding", "0"))
                    mark_price = float(asset_ctx.get("markPx", "0"))

                    pairs = sorted(list(spot_markets[asset_name]["market_ids"]))
                    eligible_with_funding.append({
                        "asset": asset_name,
                        "spot_pairs": pairs,  # ✅ pairs, not tokens
                        "funding_rate": funding_rate,
                        "funding_rate_pct": funding_rate * 100,
                        "perp_mark_price": mark_price,
                        "eligible_for_arbitrage": funding_rate > MIN_FUNDING_RATE
                    })

            eligible_with_funding.sort(key=lambda x: x["funding_rate"], reverse=True)

            print(f"\nFunding Rates for Eligible Assets:")
            print("-" * 90)
            print(f"{'Asset':>6} {'Funding %':>10} {'Perp Mark':>12} {'Arb?':>8}  {'Spot pairs (preview)':<40}")
            print("-" * 90)

            for a in eligible_with_funding:
                arb = "✓ YES" if a["eligible_for_arbitrage"] else "✗ No"
                pairs_preview = ", ".join(a["spot_pairs"][:3]) + (f" ...(+{len(a['spot_pairs'])-3})" if len(a["spot_pairs"]) > 3 else "")
                print(f"{a['asset']:>6} {a['funding_rate_pct']:>9.4f}% "
                      f"${a['perp_mark_price']:>10,.2f} {arb:>8}  {pairs_preview:<40}")

            return eligible_with_funding

        return []

    except Exception as e:
        print(f"Funding rate lookup failed: {e}")
        return []


async def get_market_liquidity_info() -> None:
    """Get basic perp liquidity information for a few high-liquidity assets."""
    print("\nPerp Market Liquidity Analysis")
    print("-" * 35)

    try:
        async with httpx.AsyncClient() as client:
            test_assets = ["BTC", "ETH", "SOL"]

            for asset in test_assets:
                response = await client.post(
                    f"{PUBLIC_BASE_URL}/info",
                    json={"type": "l2Book", "coin": asset},
                    headers={"Content-Type": "application/json"},
                )

                if response.status_code == 200:
                    book_data = response.json()
                    levels = book_data.get("levels", [])

                    if len(levels) >= 2:
                        bids = levels[0]
                        asks = levels[1]

                        if bids and asks:
                            best_bid_price = float(bids[0]["px"])
                            best_ask_price = float(asks[0]["px"])
                            spread = best_ask_price - best_bid_price
                            spread_pct = (spread / best_bid_price) * 100 if best_bid_price > 0 else 0

                            bid_size = sum(float(level["sz"]) for level in bids[:5])
                            ask_size = sum(float(level["sz"]) for level in asks[:5])

                            print(f"   {asset}: Spread {spread_pct:.3f}%, "
                                  f"Bid depth: {bid_size:.2f}, Ask depth: {ask_size:.2f}")

    except Exception as e:
        print(f"Liquidity analysis failed: {e}")


async def main():
    print("Hyperliquid Spot (Pairs) vs Perpetual Market Analysis")
    print("=" * 65)

    eligible_assets = await find_arbitrage_eligible_assets()
    await get_market_liquidity_info()

    if eligible_assets:
        positive_funding_assets = [a for a in eligible_assets if a["eligible_for_arbitrage"]]
        print("\nSummary:")
        print(f"   Total eligible base assets: {len(eligible_assets)}")
        print(f"   Assets with positive funding > {MIN_FUNDING_RATE*100:.4f}%: {len(positive_funding_assets)}")

        if positive_funding_assets:
            best = positive_funding_assets[0]
            print(f"   Best (by funding): {best['asset']} ({best['funding_rate_pct']:+.4f}%)")


if __name__ == "__main__":
    asyncio.run(main())
