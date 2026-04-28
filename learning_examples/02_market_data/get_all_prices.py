"""
Fetches current market prices for all assets using SDK and raw HTTP API.
Compares results to verify data consistency between methods.
"""

import asyncio
import os
from typing import Dict, Optional

from dotenv import load_dotenv
import httpx
from hyperliquid.info import Info

load_dotenv()

# You can only use this endpoint on the official Hyperliquid public API.
# It is not available through Chainstack, as the open-source node implementation does not support it yet.
BASE_URL = os.getenv("HYPERLIQUID_TESTNET_PUBLIC_BASE_URL")
# HIP-3: optional builder-deployed dex name (e.g. "felix"). Empty = main perp.
DEX = os.getenv("DEX", "")
ASSETS_TO_SHOW = (
    ["BTC", "ETH", "SOL", "DOGE", "AVAX"] if not DEX else None  # show all on HIP-3
)


async def method_1_sdk() -> Optional[Dict[str, str]]:
    """Method 1: Using Hyperliquid Python SDK"""
    print("Method 1: Hyperliquid SDK")
    print("-" * 30)

    try:
        info = Info(BASE_URL, skip_ws=True)
        all_prices = info.all_mids(dex=DEX)

        print(f"Got prices for {len(all_prices)} assets (dex={DEX or 'main'})")
        assets = ASSETS_TO_SHOW or list(all_prices.keys())[:5]
        for asset in assets:
            if asset in all_prices:
                price = float(all_prices[asset])
                print(f"   {asset}: ${price:,.4f}")

        return all_prices

    except Exception as e:
        print(f"SDK method failed: {e}")
        return None


async def method_2_raw_api() -> Optional[Dict[str, str]]:
    """Method 2: Raw HTTP API call"""
    print("\nMethod 2: Raw HTTP API")
    print("-" * 30)

    try:
        async with httpx.AsyncClient() as client:
            payload = {"type": "allMids"}
            if DEX:
                payload["dex"] = DEX
            response = await client.post(
                f"{BASE_URL}/info",
                json=payload,
                headers={"Content-Type": "application/json"},
            )

            if response.status_code == 200:
                all_prices = response.json()
                print(f"Got prices for {len(all_prices)} assets (dex={DEX or 'main'})")
                assets = ASSETS_TO_SHOW or list(all_prices.keys())[:5]
                for asset in assets:
                    if asset in all_prices:
                        price = float(all_prices[asset])
                        print(f"   {asset}: ${price:,.4f}")

                return all_prices
            else:
                print(f"HTTP failed: {response.status_code}")
                return None

    except Exception as e:
        print(f"HTTP method failed: {e}")
        return None


async def main() -> None:
    print("Hyperliquid Market Prices")
    print("=" * 40)

    sdk_prices = await method_1_sdk()
    http_prices = await method_2_raw_api()

    if sdk_prices and http_prices:
        print("\nComparison:")
        for asset in ["BTC", "ETH", "SOL"]:
            if asset in sdk_prices and asset in http_prices:
                sdk_price = float(sdk_prices[asset])
                http_price = float(http_prices[asset])
                match = "MATCH" if sdk_price == http_price else "DIFF"
                print(
                    f"   {asset}: SDK=${sdk_price:,.2f} | HTTP=${http_price:,.2f} {match}"
                )


if __name__ == "__main__":
    asyncio.run(main())
