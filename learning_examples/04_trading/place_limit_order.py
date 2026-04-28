"""
Places limit orders using the Hyperliquid SDK with proper price calculation.
Demonstrates order placement with market offset and result verification.
"""

import asyncio
import json
import os
from typing import Optional

from dotenv import load_dotenv
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils.signing import OrderType as HLOrderType

load_dotenv()

# You can only use this endpoint on the official Hyperliquid public API.
# It is not available through Chainstack, as the open-source node implementation does not support it yet.
BASE_URL = os.getenv("HYPERLIQUID_TESTNET_PUBLIC_BASE_URL")
# HIP-3: optional builder-deployed dex name. Empty = main perp.
# When set, SYMBOL should be the namespaced name (e.g. SYMBOL="felix:CRCL").
DEX = os.getenv("DEX", "")
SYMBOL = os.getenv("SYMBOL", "BTC")
ORDER_SIZE = float(os.getenv("ORDER_SIZE", "0.001"))
PRICE_OFFSET_PCT = -5  # 5% below market for buy order


async def method_sdk(private_key: str) -> Optional[str]:
    """Method: Using Hyperliquid Python SDK"""
    print("Method: Hyperliquid SDK")
    print("-" * 30)

    try:
        wallet = Account.from_key(private_key)
        exchange = Exchange(wallet, BASE_URL)
        info = Info(BASE_URL, skip_ws=True)

        all_prices = info.all_mids(dex=DEX)
        market_price = float(all_prices.get(SYMBOL, 0))

        if market_price == 0:
            print(f"Could not get {SYMBOL} price")
            return None

        order_price = market_price * (1 + PRICE_OFFSET_PCT / 100)
        order_price = round(order_price, 0)

        print(f"Current {SYMBOL} price: ${market_price:,.2f}")
        print(f"Placing buy order: {ORDER_SIZE} {SYMBOL} @ ${order_price:,.2f}")

        result = exchange.order(
            name=SYMBOL,
            is_buy=True,
            sz=ORDER_SIZE,
            limit_px=order_price,
            order_type=HLOrderType({"limit": {"tif": "Gtc"}}),
            reduce_only=False,
        )

        print(f"Order result:")
        print(json.dumps(result, indent=2))

        if result and result.get("status") == "ok":
            response_data = result.get("response", {}).get("data", {})
            statuses = response_data.get("statuses", [])

            if statuses:
                status_info = statuses[0]
                if "resting" in status_info:
                    order_id = status_info["resting"]["oid"]
                    print(f"Order placed successfully! ID: {order_id}")
                    return order_id
                elif "filled" in status_info:
                    print(f"Order filled immediately!")
                    return "filled"

        print(f"Order placement unclear")
        return None

    except Exception as e:
        print(f"SDK method failed: {e}")
        return None


async def main() -> None:
    print("Hyperliquid Limit Orders")
    print("=" * 40)

    private_key = os.getenv("HYPERLIQUID_TESTNET_PRIVATE_KEY")
    if not private_key:
        print("Set HYPERLIQUID_TESTNET_PRIVATE_KEY in your .env file")
        print("Create .env file with: HYPERLIQUID_TESTNET_PRIVATE_KEY=0x...")
        print("WARNING: This will place REAL orders on testnet!")
        return

    order_id = await method_sdk(private_key)

    if order_id:
        print("\nOrder placed successfully!")
        print("Check open orders to verify placement")


if __name__ == "__main__":
    asyncio.run(main())
