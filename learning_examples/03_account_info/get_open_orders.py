"""
Retrieves and displays open orders from your account.
"""

import asyncio
import os

import httpx
from dotenv import load_dotenv
from hyperliquid.info import Info

load_dotenv()

# You can only use this endpoint on the official Hyperliquid public API.
# It is not available through Chainstack, as the open-source node implementation does not support it yet.
BASE_URL = os.getenv("HYPERLIQUID_TESTNET_PUBLIC_BASE_URL")
WALLET_ADDRESS = os.getenv("TESTNET_WALLET_ADDRESS")
# HIP-3: optional builder-deployed dex name. Empty = main perp.
DEX = os.getenv("DEX", "")


async def method_1_sdk():
    """Method 1: Using Hyperliquid Python SDK"""
    print("Method 1: Hyperliquid SDK")
    print("-" * 30)

    try:
        info = Info(BASE_URL, skip_ws=True)
        open_orders = info.open_orders(WALLET_ADDRESS, dex=DEX)

        print(f"Found {len(open_orders)} open orders (dex={DEX or 'main'})")

        if open_orders:
            for order in open_orders:
                oid = order.get("oid", "")
                coin = order.get("coin", "")
                side = "BUY" if order.get("side") == "B" else "SELL"
                size = order.get("sz", "0")
                limit_px = order.get("limitPx", "0")
                timestamp = order.get("timestamp", 0)

                order_value = float(size) * float(limit_px)
                print(f"\nOrder {oid}:")
                print(f"   {side} {size} {coin} @ ${float(limit_px):,.2f}")
                print(f"   Total value: ${order_value:,.2f}")
                print(f"   Timestamp: {timestamp}")
        else:
            print("No open orders")

        return open_orders

    except Exception as e:
        print(f"SDK method failed: {e}")
        return None


async def method_2_raw_api():
    """Method 2: Raw HTTP API call"""
    print("\nMethod 2: Raw HTTP API")
    print("-" * 30)

    private_key = os.getenv("HYPERLIQUID_TESTNET_PRIVATE_KEY")
    if not private_key:
        print("Set HYPERLIQUID_TESTNET_PRIVATE_KEY in your .env file")
        print("Create .env file with: HYPERLIQUID_TESTNET_PRIVATE_KEY=0x...")
        return None

    try:
        async with httpx.AsyncClient() as client:
            payload = {"type": "openOrders", "user": WALLET_ADDRESS}
            if DEX:
                payload["dex"] = DEX
            response = await client.post(
                f"{BASE_URL}/info",
                json=payload,
                headers={"Content-Type": "application/json"},
            )

            if response.status_code == 200:
                open_orders = response.json()

                print(f"Found {len(open_orders)} open orders")

                if open_orders:
                    for order in open_orders:
                        oid = order.get("oid", "")
                        coin = order.get("coin", "")
                        side = "BUY" if order.get("side") == "B" else "SELL"
                        size = order.get("sz", "0")
                        limit_px = order.get("limitPx", "0")

                        print(f"\nOrder {oid}:")
                        print(f"   {side} {size} {coin} @ ${float(limit_px):,.2f}")

                return open_orders
            else:
                print(f"HTTP failed: {response.status_code}")
                return None

    except Exception as e:
        print(f"HTTP method failed: {e}")
        return None


async def main():
    print("Hyperliquid Open Orders")
    print("=" * 40)

    await method_1_sdk()
    await method_2_raw_api()


if __name__ == "__main__":
    asyncio.run(main())
