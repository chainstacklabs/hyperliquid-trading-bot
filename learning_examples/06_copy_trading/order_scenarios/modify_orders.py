"""
Test script for bulk modifying multiple spot orders to see how it appears in WebSocket.
Modifies all open spot orders using bulk_modify_orders_new method.
"""

import asyncio
import os
from dotenv import load_dotenv
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info

load_dotenv()

BASE_URL = os.getenv("HYPERLIQUID_TESTNET_PUBLIC_BASE_URL")


async def modify_multiple_spot_orders():
    """Modify multiple spot orders using bulk_modify_orders_new"""
    print("Modify Multiple Spot Orders Test")
    print("=" * 40)

    private_key = os.getenv("HYPERLIQUID_TESTNET_PRIVATE_KEY")
    if not private_key:
        print("❌ Missing HYPERLIQUID_TESTNET_PRIVATE_KEY in .env file")
        return

    try:
        wallet = Account.from_key(private_key)
        exchange = Exchange(wallet, BASE_URL)
        info = Info(BASE_URL, skip_ws=True)

        print(f"📱 Wallet: {wallet.address}")

        # Get open orders using environment wallet address
        wallet_address = os.getenv("TESTNET_WALLET_ADDRESS") or wallet.address
        open_orders = info.open_orders(wallet_address)
        print(f"📋 Found {len(open_orders)} total open orders")

        if not open_orders:
            print("❌ No open orders to modify")
            print("💡 Run place_order.py multiple times to create orders")
            return

        # Find all spot orders
        spot_orders = []
        for order in open_orders:
            coin = order.get("coin", "")
            if coin.startswith("@") or "/" in coin:  # Spot order indicators
                spot_orders.append(order)

        if not spot_orders:
            print("❌ No spot orders found to modify")
            print("💡 Only perpetual orders are open")
            return

        print(
            f"🎯 Found {len(spot_orders)} spot orders to modify via bulk_modify_orders_new:"
        )
        modify_requests = []
        for order in spot_orders:
            side_is_buy = order.get("side") == "B"
            current_size = float(order.get("sz", 0))
            current_price = float(order.get("limitPx", 0))
            new_price = round(current_price * (0.9 if side_is_buy else 1.1), 6)
            print(
                f"   - oid {order.get('oid')}: {'BUY' if side_is_buy else 'SELL'} "
                f"{current_size} {order.get('coin')} @ ${current_price} -> ${new_price}"
            )
            modify_requests.append(
                {
                    "oid": order.get("oid"),
                    "order": {
                        "coin": order.get("coin"),
                        "is_buy": side_is_buy,
                        "sz": current_size,
                        "limit_px": new_price,
                        "order_type": {"limit": {"tif": "Gtc"}},
                        "reduce_only": False,
                    },
                }
            )

        # Single bulk_modify_orders_new call: one signed action, atomic on the
        # WS observer's view. Replaces a per-order loop that drifted from the
        # docstring's claim.
        result = exchange.bulk_modify_orders_new(modify_requests)
        if not (result and result.get("status") == "ok"):
            print(f"❌ bulk_modify_orders_new failed: {result}")
            return

        statuses = result.get("response", {}).get("data", {}).get("statuses", [])
        print(
            f"📋 Modify Summary: {len(statuses)}/{len(modify_requests)} statuses received"
        )
        if len(statuses) != len(modify_requests):
            print(
                f"⚠️ Expected {len(modify_requests)} statuses, got {len(statuses)}"
            )
        for req, status in zip(modify_requests, statuses, strict=False):
            mark = "✅" if (isinstance(status, dict) and "resting" in status) else "❌"
            print(f"   {mark} oid {req['oid']}: {status}")
        print(f"🔍 Monitor these modifications in your WebSocket stream")

    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    asyncio.run(modify_multiple_spot_orders())
