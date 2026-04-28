"""
Test script for bulk cancelling multiple spot orders to see how it appears in WebSocket.
Cancels all open spot orders using bulk_cancel method.
"""

import asyncio
import os
from dotenv import load_dotenv
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info

load_dotenv()

BASE_URL = os.getenv("HYPERLIQUID_TESTNET_PUBLIC_BASE_URL")


async def cancel_multiple_spot_orders():
    """Cancel multiple spot orders using bulk_cancel"""
    print("Cancel Multiple Spot Orders Test")
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
            print("❌ No open orders to cancel")
            print("💡 Run place_order.py multiple times to create orders")
            return

        # Find all spot orders
        spot_orders = []
        for order in open_orders:
            coin = order.get("coin", "")
            if coin.startswith("@") or "/" in coin:  # Spot order indicators
                spot_orders.append(order)

        if not spot_orders:
            print("❌ No spot orders found to cancel")
            print("💡 Only perpetual orders are open")
            return

        print(f"🎯 Found {len(spot_orders)} spot orders to cancel via bulk_cancel:")
        for order in spot_orders:
            print(
                f"   - ID {order.get('oid')}: "
                f"{'BUY' if order.get('side') == 'B' else 'SELL'} "
                f"{order.get('sz')} {order.get('coin')} @ ${order.get('limitPx')}"
            )

        # Single bulk_cancel call: one signed action, one network round trip,
        # atomic from the WS observer's perspective. This is what the
        # docstring promises — the prior loop-of-singles version had drifted.
        cancel_requests = [
            {"coin": o.get("coin"), "oid": o.get("oid")} for o in spot_orders
        ]
        result = exchange.bulk_cancel(cancel_requests)

        if not (result and result.get("status") == "ok"):
            print(f"❌ bulk_cancel failed: {result}")
            return

        statuses = result.get("response", {}).get("data", {}).get("statuses", [])
        successful = sum(1 for s in statuses if s == "success")
        print(f"📋 Cancel Summary: {successful}/{len(cancel_requests)} requested")
        if len(statuses) != len(cancel_requests):
            print(
                f"⚠️ Expected {len(cancel_requests)} statuses, got {len(statuses)}"
            )
        for req, status in zip(cancel_requests, statuses, strict=False):
            mark = "✅" if status == "success" else "❌"
            print(f"   {mark} oid {req['oid']}: {status}")
        print(f"🔍 Monitor these cancellations in your WebSocket stream")

    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    asyncio.run(cancel_multiple_spot_orders())
