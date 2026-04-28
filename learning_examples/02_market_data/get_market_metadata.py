"""
Retrieves trading metadata including size/price decimals, max leverage, and trading constraints.
Calculates minimum order sizes and price tick sizes for major assets.
"""

import asyncio
import os
from dotenv import load_dotenv
import httpx
from hyperliquid.info import Info

load_dotenv()

BASE_URL = os.getenv("HYPERLIQUID_CHAINSTACK_BASE_URL") or os.getenv(
    "HYPERLIQUID_TESTNET_PUBLIC_BASE_URL"
)
ASSETS_TO_ANALYZE = ["BTC", "ETH", "SOL"]
PERP_PX_MAX_DECIMALS = 6


def price_tick(sz_decimals: int) -> float:
    return 10 ** -(PERP_PX_MAX_DECIMALS - sz_decimals)


async def method_1_sdk():
    """Method 1: Using Hyperliquid Python SDK"""
    print("Method 1: Hyperliquid SDK")
    print("-" * 30)

    try:
        info = Info(BASE_URL, skip_ws=True)
        meta = info.meta()
        universe = meta.get("universe", [])

        print(f"Found {len(universe)} trading pairs")

        for asset_info in universe:
            asset_name = asset_info.get("name", "")
            if asset_name in ASSETS_TO_ANALYZE:
                sz = int(asset_info.get("szDecimals", 0))
                px_decimals = max(0, PERP_PX_MAX_DECIMALS - sz)
                print(f"\n{asset_name}:")
                print(f"   Size decimals: {sz}")
                print(f"   Price decimals: {px_decimals} (= {PERP_PX_MAX_DECIMALS} - {sz})")
                print(f"   Price tick: ${price_tick(sz):.{px_decimals}f}")
                print(f"   Max leverage: {asset_info.get('maxLeverage')}x")
                print(f"   Only isolated: {asset_info.get('onlyIsolated', False)}")

        return meta

    except Exception as e:
        print(f"SDK method failed: {e}")
        return None


async def method_2_raw_api():
    """Method 2: Raw HTTP API call"""
    print("\nMethod 2: Raw HTTP API")
    print("-" * 30)

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{BASE_URL}/info",
                json={"type": "meta"},
                headers={"Content-Type": "application/json"},
            )

            if response.status_code == 200:
                meta = response.json()
                universe = meta.get("universe", [])

                print(f"Found {len(universe)} trading pairs")

                for asset_info in universe:
                    asset_name = asset_info.get("name", "")
                    if asset_name in ASSETS_TO_ANALYZE:
                        sz = int(asset_info.get("szDecimals", 0))
                        px_decimals = max(0, PERP_PX_MAX_DECIMALS - sz)
                        print(f"\n{asset_name}:")
                        print(f"   Size decimals: {sz}")
                        print(f"   Price decimals: {px_decimals}")
                        print(f"   Price tick: ${price_tick(sz):.{px_decimals}f}")
                        print(f"   Max leverage: {asset_info.get('maxLeverage')}x")
                        print(
                            f"   Only isolated: {asset_info.get('onlyIsolated', False)}"
                        )

                return meta
            else:
                print(f"HTTP failed: {response.status_code}")
                return None

    except Exception as e:
        print(f"HTTP method failed: {e}")
        return None


async def calculate_trading_constraints():
    """Calculate minimum sizes and price ticks"""
    print("\nTrading Constraints")
    print("-" * 25)

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{BASE_URL}/info",
                json={"type": "meta"},
                headers={"Content-Type": "application/json"},
            )

            if response.status_code == 200:
                meta = response.json()
                universe = meta.get("universe", [])

                for asset_info in universe[:3]:
                    name = asset_info.get("name", "")
                    sz_decimals = int(asset_info.get("szDecimals", 0))
                    price_decimals = max(0, PERP_PX_MAX_DECIMALS - sz_decimals)
                    min_size = 10 ** -sz_decimals if sz_decimals > 0 else 1.0
                    tick = price_tick(sz_decimals)

                    print(f"\n{name}:")
                    print(f"   Min order size: {min_size:.{sz_decimals}f} {name}")
                    print(f"   Price tick size: ${tick:.{price_decimals}f}")
                    print(f"   Max leverage: {asset_info.get('maxLeverage')}x")

    except Exception as e:
        print(f"Analysis failed: {e}")


async def main():
    print("Hyperliquid Market Metadata")
    print("=" * 40)

    await method_1_sdk()
    await method_2_raw_api()
    await calculate_trading_constraints()


if __name__ == "__main__":
    asyncio.run(main())
