"""
NOT APPLICABLE FOR SPOT MARKET

Test script for placing trigger orders (Take Profit and Stop Loss) on spot market.
Tests scenarios 10-13: Take Profit and Stop Loss orders with Market and Limit execution.

Available scenarios (10-13):
=== TRIGGER ORDERS ===
10. Take Profit (Market)  11. Take Profit (Limit)
12. Stop Loss (Market)    13. Stop Loss (Limit)

Trigger orders use realistic trigger prices (±10% from current market price).
"""

import asyncio
import os
from dotenv import load_dotenv
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils.signing import OrderType as HLOrderType

load_dotenv()

BASE_URL = os.getenv("HYPERLIQUID_TESTNET_PUBLIC_BASE_URL")
SYMBOL = "PURR/USDC"  # Spot pair
ORDER_SIZE = 3.0  # Size to meet minimum $10 USDC requirement

def round_to_tick_size(price: float, tick_size: float) -> float:
    """Round price to the nearest valid tick size"""
    if tick_size <= 0:
        return price
    return round(price / tick_size) * tick_size

# Test scenarios - trigger orders only
SCENARIOS = {
    # === TRIGGER ORDERS ===
    # Take Profit orders (triggered when price goes up)
    10: {"name": "Take Profit (Market)", "order_type": HLOrderType({"trigger": {"isMarket": True, "triggerPx": "0", "tpsl": "tp"}}), "reduce_only": False, "is_buy": False, "is_trigger": True},
    11: {"name": "Take Profit (Limit)", "order_type": HLOrderType({"trigger": {"isMarket": False, "triggerPx": "0", "tpsl": "tp"}}), "reduce_only": False, "is_buy": False, "is_trigger": True},

    # Stop Loss orders (triggered when price goes down)
    12: {"name": "Stop Loss (Market)", "order_type": HLOrderType({"trigger": {"isMarket": True, "triggerPx": "0", "tpsl": "sl"}}), "reduce_only": False, "is_buy": False, "is_trigger": True},
    13: {"name": "Stop Loss (Limit)", "order_type": HLOrderType({"trigger": {"isMarket": False, "triggerPx": "0", "tpsl": "sl"}}), "reduce_only": False, "is_buy": False, "is_trigger": True},
}

async def place_trigger_orders():
    """Place trigger orders for scenarios 10-13"""
    print("Running Trigger Order Scenarios (10-13)")
    print("=" * 50)

    private_key = os.getenv("HYPERLIQUID_TESTNET_PRIVATE_KEY")
    if not private_key:
        print("❌ Missing HYPERLIQUID_TESTNET_PRIVATE_KEY in .env file")
        return

    try:
        wallet = Account.from_key(private_key)
        print("📱 Wallet: " + wallet.address)

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
        for pair in spot_meta.get('universe', []):
            if pair.get('name') == SYMBOL:
                target_pair = pair
                break

        if not target_pair:
            print("❌ Could not find " + SYMBOL + " in spot universe")
            return

        pair_index = target_pair.get('index')
        if pair_index >= len(asset_ctxs):
            print("❌ Asset index " + str(pair_index) + " out of range")
            return

        # Get price decimals and calculate tick size
        price_decimals = target_pair.get('priceDecimals', 2)
        tick_size = 1 / (10**price_decimals)
        print("📏 Price decimals: " + str(price_decimals) + ", Tick size: $" + str(tick_size))

        # Get current price
        ctx = asset_ctxs[pair_index]
        market_price = float(ctx.get('midPx', ctx.get('markPx', 0)))
        if market_price <= 0:
            print("❌ Could not get valid price for " + SYMBOL)
            return

        print("💰 Current " + SYMBOL + " price: $" + str(market_price))
        print()

        # Run each scenario
        for scenario_id, scenario in SCENARIOS.items():
            print("🔹 Scenario " + str(scenario_id) + ": " + scenario['name'])
            print("-" * 40)

            # Create fresh exchange instance for each scenario
            exchange = Exchange(wallet, BASE_URL)

            is_buy = scenario['is_buy']
            order_side = "BUY" if is_buy else "SELL"

            try:
                # Determine order type from scenario name
                is_take_profit = "Take Profit" in scenario['name']
                is_market_order = "Market" in scenario['name']

                if is_take_profit:
                    # Take profit: trigger above current price
                    trigger_price = market_price * 1.1  # 10% above current price
                    limit_price = market_price * 1.05   # 5% above current price (for limit orders)
                else:  # stop loss
                    # Stop loss: trigger below current price
                    trigger_price = market_price * 0.9  # 10% below current price
                    limit_price = market_price * 0.95   # 5% below current price (for limit orders)

                trigger_price = round_to_tick_size(trigger_price, tick_size)
                limit_price = round_to_tick_size(limit_price, tick_size)

                # Determine tpsl type from scenario
                tpsl_type = "tp" if is_take_profit else "sl"

                # Create the order type with proper trigger price (must be float, not string)
                updated_order_type = HLOrderType({
                    "trigger": {
                        "isMarket": is_market_order,
                        "triggerPx": trigger_price,  # Keep as float, not string
                        "tpsl": tpsl_type
                    }
                })

                if is_market_order:
                    print("📝 Placing " + scenario['name'] + " " + order_side + " order: " + str(ORDER_SIZE) + " " + SYMBOL + " triggered @ $" + str(trigger_price) + " (market execution)")
                else:
                    print("📝 Placing " + scenario['name'] + " " + order_side + " order: " + str(ORDER_SIZE) + " " + SYMBOL + " triggered @ $" + str(trigger_price) + ", limit @ $" + str(limit_price))

                try:
                    result = exchange.order(
                        name=SYMBOL,
                        is_buy=is_buy,
                        sz=float(ORDER_SIZE),
                        limit_px=float(limit_price),
                        order_type=updated_order_type,
                        reduce_only=scenario['reduce_only'],
                    )
                except Exception as order_error:
                    print("❌ Order placement error: " + str(order_error))
                    continue

                if result and result.get("status") == "ok":
                    response_data = result.get("response", {}).get("data", {})
                    statuses = response_data.get("statuses", [])

                    if statuses:
                        status_info = statuses[0]
                        if "resting" in status_info:
                            order_id = status_info["resting"]["oid"]
                            print("✅ Order placed successfully! ID: " + str(order_id))
                        elif "filled" in status_info:
                            print("✅ Order filled immediately!")
                        else:
                            print("⚠️ Unexpected status: " + str(status_info))
                else:
                    print("❌ Order failed: " + str(result))

            except Exception as e:
                print("❌ Scenario " + str(scenario_id) + " failed: " + str(e))

            print()  # Empty line between scenarios
            await asyncio.sleep(1)  # Small delay between orders

    except Exception as e:
        print("❌ Error: " + str(e))


if __name__ == "__main__":
    asyncio.run(place_trigger_orders())