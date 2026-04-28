"""
Retrieves current funding rates for all perpetual contracts.
Shows which assets have positive funding (perps pay spot holders).
"""

import asyncio
import os
from typing import Dict, List, Optional
from dotenv import load_dotenv
import httpx
from hyperliquid.info import Info

load_dotenv()

BASE_URL = os.getenv("HYPERLIQUID_PUBLIC_BASE_URL")
MIN_FUNDING_RATE = 0.0001  # 0.01% minimum threshold
# HIP-3 dex sweep is comma-separated; "" means main perp.
# Example: DEXES="" or DEXES=",felix,unit" or DEXES="all" to iterate every dex.
DEXES_ENV = os.getenv("DEXES", "")


async def get_funding_rates_sdk() -> Optional[List[Dict]]:
    """Method 1: Using Hyperliquid Python SDK across all perp dexes (HIP-3)."""
    print("Method 1: Hyperliquid SDK (multi-dex)")
    print("-" * 30)

    try:
        info = Info(BASE_URL, skip_ws=True)
        # Build the list of dexes to query.
        if DEXES_ENV == "all":
            dexes = info.perp_dexs() or []
            dex_names = [""] + [
                d.get("name") for d in dexes if isinstance(d, dict) and d.get("name")
            ]
        elif DEXES_ENV:
            dex_names = [d.strip() for d in DEXES_ENV.split(",")]
        else:
            dex_names = [""]

        funding_opportunities: List[Dict] = []
        for dex_name in dex_names:
            try:
                meta = info.meta(dex=dex_name)
                # meta_and_asset_ctxs is main-only on the SDK; fall back per-dex
                # to a raw POST when the helper is unavailable.
                ctxs = info.post(
                    "/info", {"type": "metaAndAssetCtxs", **({"dex": dex_name} if dex_name else {})}
                )
                if not (isinstance(ctxs, list) and len(ctxs) >= 2):
                    continue
                _, asset_ctxs = ctxs[0], ctxs[1]
                universe = meta.get("universe", [])
                for i, asset_ctx in enumerate(asset_ctxs):
                    asset_name = universe[i]["name"] if i < len(universe) else f"UNKNOWN_{i}"
                    funding_rate = float(asset_ctx.get("funding", "0"))
                    mark_price = float(asset_ctx.get("markPx", "0"))
                    if funding_rate > MIN_FUNDING_RATE:
                        funding_opportunities.append({
                            "asset": asset_name,
                            "dex": dex_name or "main",
                            "funding_rate": funding_rate,
                            "funding_rate_pct": funding_rate * 100,
                            "annual_rate_pct": funding_rate * 100 * 365 * 24,
                            "mark_price": mark_price,
                        })
            except Exception as e:
                print(f"  skip dex={dex_name!r}: {e}")

        funding_opportunities.sort(key=lambda x: x["funding_rate"], reverse=True)
        print(f"Found {len(funding_opportunities)} positive funding opportunities across {len(dex_names)} dex(es)")
        for i, opp in enumerate(funding_opportunities[:10], 1):
            print(f"{i:2d}. [{opp['dex']:>6}] {opp['asset']:>14}: {opp['funding_rate_pct']:+7.4f}% "
                  f"(Annual: {opp['annual_rate_pct']:+7.1f}%) @ ${opp['mark_price']:,.4f}")
        return funding_opportunities

    except Exception as e:
        print(f"SDK method failed: {e}")
        return None


async def get_funding_rates_raw() -> Optional[List[Dict]]:
    """Method 2: Raw HTTP API call"""
    print("\nMethod 2: Raw HTTP API")
    print("-" * 30)

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{BASE_URL}/info",
                json={"type": "metaAndAssetCtxs"},
                headers={"Content-Type": "application/json"},
            )

            if response.status_code == 200:
                data = response.json()
                funding_opportunities = []
                
                if len(data) >= 2:
                    meta = data[0]
                    asset_ctxs = data[1]
                    
                    # Map asset names from universe to contexts by index
                    for i, asset_ctx in enumerate(asset_ctxs):
                        asset_name = meta["universe"][i]["name"] if i < len(meta["universe"]) else f"UNKNOWN_{i}"
                        funding_rate = float(asset_ctx.get("funding", "0"))
                        mark_price = float(asset_ctx.get("markPx", "0"))
                        
                        if funding_rate > MIN_FUNDING_RATE:
                            funding_opportunities.append({
                                "asset": asset_name,
                                "funding_rate": funding_rate,
                                "funding_rate_pct": funding_rate * 100,
                                "annual_rate_pct": funding_rate * 100 * 365 * 24,
                                "mark_price": mark_price
                            })
                    
                    funding_opportunities.sort(key=lambda x: x["funding_rate"], reverse=True)
                    
                    print(f"Found {len(funding_opportunities)} positive funding opportunities")
                    print()
                    
                    for i, opp in enumerate(funding_opportunities[:10], 1):
                        print(f"{i:2d}. {opp['asset']:>6}: {opp['funding_rate_pct']:+7.4f}% "
                              f"(Annual: {opp['annual_rate_pct']:+7.1f}%) @ ${opp['mark_price']:,.2f}")
                    
                    return funding_opportunities
            else:
                print(f"HTTP failed: {response.status_code}")
                return None

    except Exception as e:
        print(f"HTTP method failed: {e}")
        return None


async def get_predicted_fundings() -> Optional[Dict]:
    """Get predicted funding rates across exchanges"""
    print("\nPredicted Funding Rates (Cross-Exchange)")
    print("-" * 45)

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{BASE_URL}/info",
                json={"type": "predictedFundings"},
                headers={"Content-Type": "application/json"},
            )

            if response.status_code == 200:
                predicted_fundings = response.json()
                
                # Process the list-based response format
                if isinstance(predicted_fundings, list):
                    hl_positive_fundings = []
                    
                    for item in predicted_fundings:
                        if len(item) >= 2:
                            asset_name = item[0]
                            exchange_data = item[1]
                            
                            for exchange_info in exchange_data:
                                if len(exchange_info) >= 2:
                                    exchange_name = exchange_info[0]
                                    exchange_details = exchange_info[1]
                                    
                                    if exchange_name == 'HlPerp' and exchange_details and 'fundingRate' in exchange_details:
                                        funding_rate = float(exchange_details['fundingRate'])
                                        if funding_rate > MIN_FUNDING_RATE:
                                            hl_positive_fundings.append((asset_name, funding_rate))
                    
                    # Display Hyperliquid positive funding rates
                    if hl_positive_fundings:
                        hl_positive_fundings.sort(key=lambda x: x[1], reverse=True)
                        print("Hyperliquid Perp - Top positive funding rates:")
                        for asset, rate in hl_positive_fundings[:10]:
                            print(f"   {asset:>8}: {rate*100:+7.4f}%")
                    else:
                        print("No positive funding opportunities found")
                
                else:
                    print(f"Unexpected API response format: {type(predicted_fundings)}")
                
                return predicted_fundings
            else:
                print(f"HTTP failed: {response.status_code}")
                return None

    except Exception as e:
        print(f"Predicted fundings failed: {e}")
        return None


def calculate_profit_potential(funding_rate: float, position_value: float, hours_held: int = 1) -> Dict:
    """
    Calculate potential profit from spot-long + perp-short funding arbitrage
    
    Strategy: Buy spot asset, short equivalent amount on perp
    - Collect positive funding payments (perp shorts receive funding when rate > 0)
    - Market neutral (spot long hedges perp short price risk)
    """
    funding_payments = hours_held
    gross_profit = funding_rate * position_value * funding_payments
    
    # Estimate trading fees for spot-perp arbitrage:
    # - Buy spot: ~0.040% taker fee
    # - Short perp: ~0.015% taker fee  
    # - Sell spot: ~0.040% taker fee (exit)
    # - Close perp: ~0.015% taker fee (exit)
    estimated_fees = position_value * 0.0011  # Total ~0.11%
    net_profit = gross_profit - estimated_fees
    
    return {
        "funding_payments": funding_payments,
        "gross_profit": gross_profit,
        "estimated_fees": estimated_fees,
        "net_profit": net_profit,
        "net_profit_pct": (net_profit / position_value) * 100
    }


async def main():
    print("Hyperliquid Funding Rate Discovery")
    print("=" * 50)

    sdk_rates = await get_funding_rates_sdk()
    raw_rates = await get_funding_rates_raw()
    predicted = await get_predicted_fundings()

    if sdk_rates:
        print("\nSpot-Perp Funding Arbitrage Analysis ($10,000 position)")
        print("-" * 60)
        print("Strategy: Long spot + Short perp (market neutral)")
        
        for opp in sdk_rates[:3]:
            profit_1h = calculate_profit_potential(opp["funding_rate"], 10000, 1)
            print(f"\n{opp['asset']} (Funding: {opp['funding_rate_pct']:+.4f}%):")
            print(f"   1h profit: ${profit_1h['net_profit']:+.2f} ({profit_1h['net_profit_pct']:+.3f}%)")


if __name__ == "__main__":
    asyncio.run(main())