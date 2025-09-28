"""
Test script for placing different types of limit orders on spot market.
Tests scenarios 1-9: GTC, IOC, ALO limit orders and time-limited orders.

Available scenarios (1-9):
=== LIMIT ORDERS ===
1. GTC Limit Buy        2. IOC Limit Buy        3. ALO Limit Buy
4. GTC Limit Sell       5. IOC Limit Sell       6. ALO Limit Sell
7. GTC Reduce-Only Buy  8. GTC Reduce-Only Sell 9. GTC Expires-After (30s)

All limit orders use 50% price offset to avoid immediate fills.
"""

import asyncio
import os
import time
from dotenv import load_dotenv
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils.signing import OrderType as HLOrderType

load_dotenv()

BASE_URL = os.getenv("HYPERLIQUID_TESTNET_PUBLIC_BASE_URL")
SYMBOL = "PURR/USDC"  # Spot pair
ORDER_SIZE = 3.0  # Size to meet minimum $10 USDC requirement
PRICE_OFFSET_PCT = -50  # 50% below market for buy order (won't fill)


def round_to_tick_size(price: float, tick_size: float) -> float:
    """Round price to the nearest valid tick size"""
    if tick_size <= 0:
        return price
    return round(price / tick_size) * tick_size


# Test scenarios - limit orders only
SCENARIOS = {
    # === LIMIT ORDERS ===
    # Buy orders
    1: {
        "name": "GTC Limit Buy",
        "order_type": HLOrderType({"limit": {"tif": "Gtc"}}),
        "reduce_only": False,
        "is_buy": True,
    },
    2: {
        "name": "IOC Limit Buy",
        "order_type": HLOrderType({"limit": {"tif": "Ioc"}}),
        "reduce_only": False,
        "is_buy": True,
    },
    3: {
        "name": "ALO Limit Buy",
        "order_type": HLOrderType({"limit": {"tif": "Alo"}}),
        "reduce_only": False,
        "is_buy": True,
    },
    # Sell orders
    4: {
        "name": "GTC Limit Sell",
        "order_type": HLOrderType({"limit": {"tif": "Gtc"}}),
        "reduce_only": False,
        "is_buy": False,
    },
    5: {
        "name": "IOC Limit Sell",
        "order_type": HLOrderType({"limit": {"tif": "Ioc"}}),
        "reduce_only": False,
        "is_buy": False,
    },
    6: {
        "name": "ALO Limit Sell",
        "order_type": HLOrderType({"limit": {"tif": "Alo"}}),
        "reduce_only": False,
        "is_buy": False,
    },
    # Reduce-only orders (not applicable for spot, but included for completeness)
    # 7: {"name": "GTC Reduce-Only Buy", "order_type": HLOrderType({"limit": {"tif": "Gtc"}}), "reduce_only": True, "is_buy": True},
    # 8: {"name": "GTC Reduce-Only Sell", "order_type": HLOrderType({"limit": {"tif": "Gtc"}}), "reduce_only": True, "is_buy": False},
    # Time-limited orders
    9: {
        "name": "GTC Expires-After (30s)",
        "order_type": HLOrderType({"limit": {"tif": "Gtc"}}),
        "reduce_only": False,
        "is_buy": True,
        "expires_after": 15,
    },
}


async def place_limit_orders():
    """Place limit orders for scenarios 1-9"""
    print("Running Limit Order Scenarios (1-9)")
    print("=" * 50)

    private_key = os.getenv("HYPERLIQUID_TESTNET_PRIVATE_KEY")
    if not private_key:
        print("❌ Missing HYPERLIQUID_TESTNET_PRIVATE_KEY in .env file")
        return

    try:
        wallet = Account.from_key(private_key)
        print(f"📱 Wallet: {wallet.address}")

        # Get spot metadata once
        info = Info(BASE_URL, skip_ws=True)
        spot_data = info.spot_meta_and_asset_ctxs()
        if len(spot_data) < 2:
            print("❌ Could not get spot metadata")
            return

        spot_meta = spot_data[0]
        asset_ctxs = spot_data[1]

        # Find PURR/USDC
        target_pair = None
        for pair in spot_meta.get("universe", []):
            if pair.get("name") == SYMBOL:
                target_pair = pair
                break

        if not target_pair:
            print(f"❌ Could not find {SYMBOL} in spot universe")
            return

        pair_index = target_pair.get("index")
        if pair_index >= len(asset_ctxs):
            print(f"❌ Asset index {pair_index} out of range")
            return

        # Get price decimals and calculate tick size
        price_decimals = target_pair.get("priceDecimals", 2)
        tick_size = 1 / (10**price_decimals)
        print(f"📏 Price decimals: {price_decimals}, Tick size: ${tick_size}")

        # Get current price
        ctx = asset_ctxs[pair_index]
        market_price = float(ctx.get("midPx", ctx.get("markPx", 0)))
        if market_price <= 0:
            print(f"❌ Could not get valid price for {SYMBOL}")
            return

        print(f"💰 Current {SYMBOL} price: ${market_price}")
        print()

        # Run each scenario
        for scenario_id, scenario in SCENARIOS.items():
            print(f"🔹 Scenario {scenario_id}: {scenario['name']}")
            print("-" * 40)

            # Create fresh exchange instance for each scenario
            exchange = Exchange(wallet, BASE_URL)

            # Set expires_after if the scenario requires it
            if "expires_after" in scenario:
                expires_time = int(time.time() * 1000) + (
                    scenario["expires_after"] * 1000
                )
                exchange.set_expires_after(expires_time)
                print(f"⏰ Order will expire in {scenario['expires_after']} seconds")

            is_buy = scenario["is_buy"]
            order_side = "BUY" if is_buy else "SELL"

            try:
                # Handle regular limit orders
                # For buy orders: below market price, for sell orders: above market price
                price_multiplier = (
                    (1 + PRICE_OFFSET_PCT / 100)
                    if is_buy
                    else (1 - PRICE_OFFSET_PCT / 100)
                )
                order_price = market_price * price_multiplier
                order_price = round_to_tick_size(order_price, tick_size)
                print(
                    f"📝 Placing {scenario['name']} {order_side} order: {ORDER_SIZE} {SYMBOL} @ ${order_price}"
                )

                result = exchange.order(
                    name=SYMBOL,
                    is_buy=is_buy,
                    sz=ORDER_SIZE,
                    limit_px=order_price,
                    order_type=scenario["order_type"],
                    reduce_only=scenario["reduce_only"],
                )

                if result and result.get("status") == "ok":
                    response_data = result.get("response", {}).get("data", {})
                    statuses = response_data.get("statuses", [])

                    if statuses:
                        status_info = statuses[0]
                        if "resting" in status_info:
                            order_id = status_info["resting"]["oid"]
                            print(f"✅ Order placed successfully! ID: {order_id}")
                        elif "filled" in status_info:
                            print("✅ Order filled immediately!")
                        else:
                            print(f"⚠️ Unexpected status: {status_info}")
                else:
                    print(f"❌ Order failed: {result}")

            except Exception as e:
                print(f"❌ Scenario {scenario_id} failed: {e}")

            print()  # Empty line between scenarios
            await asyncio.sleep(1)  # Small delay between orders

    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    asyncio.run(place_limit_orders())
