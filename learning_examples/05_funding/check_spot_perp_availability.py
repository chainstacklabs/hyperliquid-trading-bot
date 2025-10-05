"""
Checks which assets are available for both spot and perpetual trading.
Essential for funding arbitrage - need both markets to execute the strategy.
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


async def get_spot_assets() -> Optional[Set[str]]:
    """Get all assets available for spot trading"""
    print("Spot Assets")
    print("-" * 30)

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{CHAINSTACK_BASE_URL}/info",
                json={"type": "spotMeta"},
                headers={"Content-Type": "application/json"},
            )

            if response.status_code == 200:
                spot_meta = response.json()
                spot_assets = set()
                
                if "tokens" in spot_meta:
                    for token_info in spot_meta["tokens"]:
                        asset_name = token_info.get("name", "")
                        if asset_name:
                            spot_assets.add(asset_name)
                
                print(f"Found {len(spot_assets)} spot assets")
                sorted_assets = sorted(list(spot_assets))
                for i in range(0, len(sorted_assets), 6):
                    row = sorted_assets[i:i+6]
                    print(f"   {', '.join(f'{asset:>6}' for asset in row)}")
                
                return spot_assets
            else:
                print(f"HTTP failed: {response.status_code}")
                return None

    except Exception as e:
        print(f"Spot assets failed: {e}")
        return None


async def get_perp_assets() -> Optional[Set[str]]:
    """Get all assets available for perpetual trading"""
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
    """Find assets available in both spot and perpetual markets"""
    print("\nFunding Arbitrage Eligible Assets")
    print("=" * 40)

    spot_assets = await get_spot_assets()
    perp_assets = await get_perp_assets()
    
    if not spot_assets or not perp_assets:
        print("Failed to get market data")
        return None
    
    eligible_assets = spot_assets.intersection(perp_assets)
    
    if not eligible_assets:
        print("No assets found in both spot and perpetual markets")
        return None
    
    print(f"\nFound {len(eligible_assets)} assets available in BOTH markets:")
    sorted_eligible = sorted(list(eligible_assets))
    for i in range(0, len(sorted_eligible), 8):
        row = sorted_eligible[i:i+8]
        print(f"   {', '.join(f'{asset:>5}' for asset in row)}")
    
    # Get current funding rates for eligible assets
    try:
        info = Info(PUBLIC_BASE_URL, skip_ws=True)
        meta_and_contexts = info.meta_and_asset_ctxs()
        
        eligible_with_funding = []
        
        if meta_and_contexts and len(meta_and_contexts) >= 2:
            meta = meta_and_contexts[0]
            asset_ctxs = meta_and_contexts[1]
            
            # Map asset names from universe to contexts by index
            for i, asset_ctx in enumerate(asset_ctxs):
                asset_name = meta["universe"][i]["name"] if i < len(meta["universe"]) else f"UNKNOWN_{i}"
                if asset_name in eligible_assets:
                    funding_rate = float(asset_ctx.get("funding", "0"))
                    mark_price = float(asset_ctx.get("markPx", "0"))
                    
                    eligible_with_funding.append({
                        "asset": asset_name,
                        "funding_rate": funding_rate,
                        "funding_rate_pct": funding_rate * 100,
                        "mark_price": mark_price,
                        "eligible_for_arbitrage": funding_rate > 0.0001  # Positive funding threshold
                    })
            
            eligible_with_funding.sort(key=lambda x: x["funding_rate"], reverse=True)
            
            print(f"\nFunding Rates for Eligible Assets:")
            print("-" * 45)
            print(f"{'Asset':>6} {'Funding %':>10} {'Price':>12} {'Arbitrage':>10}")
            print("-" * 45)
            
            for asset_data in eligible_with_funding:
                arbitrage_status = "✓ YES" if asset_data["eligible_for_arbitrage"] else "✗ No"
                print(f"{asset_data['asset']:>6} {asset_data['funding_rate_pct']:>9.4f}% "
                      f"${asset_data['mark_price']:>10,.2f} {arbitrage_status:>10}")
            
            return eligible_with_funding
    
    except Exception as e:
        print(f"Funding rate lookup failed: {e}")
        return []


async def get_market_liquidity_info() -> None:
    """Get basic liquidity information for top arbitrage candidates"""
    print(f"\nMarket Liquidity Analysis")
    print("-" * 30)

    try:
        async with httpx.AsyncClient() as client:
            # Get order book depth for top candidates
            test_assets = ["BTC", "ETH", "SOL"]  # Common high-liquidity assets
            
            for asset in test_assets:
                response = await client.post(
                    f"{CHAINSTACK_BASE_URL}/info",
                    json={"type": "l2Book", "coin": asset},
                    headers={"Content-Type": "application/json"},
                )
                
                if response.status_code == 200:
                    book_data = response.json()
                    levels = book_data.get("levels", [])
                    
                    if len(levels) >= 2:
                        bids = levels[0]  # Buy orders
                        asks = levels[1]  # Sell orders
                        
                        if bids and asks:
                            best_bid_price = float(bids[0]["px"]) if bids else 0
                            best_ask_price = float(asks[0]["px"]) if asks else 0
                            spread = best_ask_price - best_bid_price
                            spread_pct = (spread / best_bid_price) * 100 if best_bid_price > 0 else 0
                            
                            bid_size = sum(float(level["sz"]) for level in bids[:5])  # Top 5 levels
                            ask_size = sum(float(level["sz"]) for level in asks[:5])
                            
                            print(f"   {asset}: Spread {spread_pct:.3f}%, "
                                  f"Bid depth: {bid_size:.2f}, Ask depth: {ask_size:.2f}")

    except Exception as e:
        print(f"Liquidity analysis failed: {e}")


async def main():
    print("Hyperliquid Spot vs Perpetual Market Analysis")
    print("=" * 55)

    eligible_assets = await find_arbitrage_eligible_assets()
    await get_market_liquidity_info()
    
    if eligible_assets:
        positive_funding_assets = [a for a in eligible_assets if a["eligible_for_arbitrage"]]
        print(f"\nSummary:")
        print(f"   Total eligible assets: {len(eligible_assets)}")
        print(f"   Assets with positive funding: {len(positive_funding_assets)}")
        
        if positive_funding_assets:
            best_opportunity = positive_funding_assets[0]
            print(f"   Best opportunity: {best_opportunity['asset']} "
                  f"({best_opportunity['funding_rate_pct']:+.4f}%)")


if __name__ == "__main__":
    asyncio.run(main())