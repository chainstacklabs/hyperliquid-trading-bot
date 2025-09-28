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

        print(f"🎯 Found {len(spot_orders)} spot orders to modify:")

        # Modify each order individually
        successful_modifies = 0
        failed_modifies = 0

        for order in spot_orders:
            order_id = order.get("oid")
            coin_field = order.get("coin")
            side = "BUY" if order.get("side") == "B" else "SELL"
            current_size = float(order.get("sz", 0))
            current_price = float(order.get("limitPx", 0))

            # Calculate new values
            price_modifier = 0.9 if side == "BUY" else 1.1  # Small price adjustment
            new_price = round(current_price * price_modifier, 6)

            print(
                f"   Modifying ID {order_id}: {side} {current_size} -> {current_size} {coin_field} @ ${current_price} -> ${new_price}"
            )

            try:
                result = exchange.modify_order(
                    oid=order_id,
                    name=coin_field,
                    is_buy=(side == "BUY"),
                    sz=current_size,
                    limit_px=new_price,
                    order_type={"limit": {"tif": "Gtc"}},
                    reduce_only=False,
                )

                if result and result.get("status") == "ok":
                    print(f"   ✅ Order {order_id} modified successfully")
                    successful_modifies += 1
                else:
                    print(f"   ❌ Order {order_id} modify failed: {result}")
                    failed_modifies += 1
            except Exception as e:
                print(f"   ❌ Order {order_id} modify error: {e}")
                failed_modifies += 1

        print(f"📋 Modify Summary:")
        print(f"   ✅ Successful: {successful_modifies}")
        print(f"   ❌ Failed: {failed_modifies}")
        print(f"🔍 Monitor these modifications in your WebSocket stream")

    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    asyncio.run(modify_multiple_spot_orders())
