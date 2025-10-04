"""
Mirror spot orders from a leader wallet with fixed sizing.
Monitors leader's spot orders and places corresponding orders for follower.
Handles order placement, cancellation, and fills with real-time WebSocket monitoring.

Fixed infinite loop issue when using same wallet for leader/follower:
- Added message queue to process WebSocket messages sequentially
- Each message processed completely before next one starts
- Prevents race condition where follower orders appear while still placing them
"""

import asyncio
import json
import os
import signal
from typing import Dict, Optional
from dotenv import load_dotenv
import websockets
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils.signing import OrderType as HLOrderType

load_dotenv()

# Configuration
WS_URL = os.getenv("HYPERLIQUID_TESTNET_PUBLIC_WS_URL")
BASE_URL = os.getenv("HYPERLIQUID_TESTNET_PUBLIC_BASE_URL")

# For tests, you can use the same wallet as a leader and follower.
# Follower's orders will be ignored in the mirroring logic.
LEADER_ADDRESS = os.getenv("TESTNET_WALLET_ADDRESS")
FIXED_ORDER_VALUE_USDC = 15.0

running = False
order_mappings: Dict[int, int] = {}  # leader_order_id -> follower_order_id


def signal_handler(signum, frame):
    """Handle Ctrl+C gracefully"""
    del signum, frame  # Unused parameters
    global running
    print("\nShutting down...")
    running = False


def detect_market_type(coin_field):
    """Detect market type from coin field"""
    if coin_field.startswith("@"):
        return "SPOT"
    elif "/" in coin_field:
        return "SPOT"
    else:
        return "PERP"


def is_spot_order(coin_field):
    """Check if order is for spot trading - basic format validation only"""
    if not coin_field or coin_field == "N/A":
        return False

    market_type = detect_market_type(coin_field)
    if market_type != "SPOT":
        return False

    # Basic format validation for @index
    if coin_field.startswith("@"):
        try:
            asset_index = int(coin_field[1:])
            # Only reject obviously invalid indices
            if asset_index < 0:
                return False
        except ValueError:
            return False

    return True


async def get_spot_asset_info(info: Info, coin_field: str) -> Optional[dict]:
    """Get spot asset price and metadata for proper order sizing"""
    try:
        if coin_field.startswith("@"):
            # For @index format, use spot API
            spot_data = info.spot_meta_and_asset_ctxs()
            if len(spot_data) >= 2:
                spot_meta = spot_data[0]  # First element is metadata
                asset_ctxs = spot_data[1]  # Second element is asset contexts

                # Extract index number
                index = int(coin_field[1:])
                if index < len(asset_ctxs):
                    ctx = asset_ctxs[index]
                    # Try midPx first, fallback to markPx
                    price = float(ctx.get("midPx", ctx.get("markPx", 0)))

                    if price > 0:
                        # Get token metadata for size decimals
                        universe = spot_meta.get("universe", [])
                        tokens = spot_meta.get("tokens", [])

                        # Find the pair info
                        pair_info = None
                        for pair in universe:
                            if pair.get("index") == index:
                                pair_info = pair
                                break

                        # Get token info for size decimals
                        size_decimals = 6  # Default fallback
                        if pair_info and "tokens" in pair_info:
                            token_indices = pair_info["tokens"]
                            if len(token_indices) > 0:
                                base_token_index = token_indices[0]
                                if base_token_index < len(tokens):
                                    token_info = tokens[base_token_index]
                                    size_decimals = token_info.get("szDecimals", 6)

                        return {
                            "price": price,
                            "szDecimals": size_decimals,
                            "coin": coin_field,
                        }
                    else:
                        print(
                            f"⚠️ No spot price for {coin_field} (midPx={ctx.get('midPx')}, markPx={ctx.get('markPx')})"
                        )
                        return None
                else:
                    print(
                        f"⚠️ Spot index {coin_field} out of range (max: @{len(asset_ctxs) - 1})"
                    )
                    return None

        elif "/" in coin_field:
            # For PAIR/USDC format, need to find the corresponding @index first
            spot_meta = info.spot_meta()
            universe = spot_meta.get("universe", [])

            # Find the matching pair in spot universe
            for pair_info in universe:
                if pair_info.get("name") == coin_field:
                    pair_index = pair_info.get("index")
                    # Get info using the index
                    return await get_spot_asset_info(info, f"@{pair_index}")

            print(f"⚠️ Spot pair {coin_field} not found in universe")
            return None

        else:
            print(f"⚠️ Unsupported coin format for spot: {coin_field}")
            return None

    except Exception as e:
        print(f"⚠️ Error getting spot info for {coin_field}: {e}")
        return None


async def place_follower_order(
    exchange: Exchange, info: Info, leader_order_data: dict
) -> Optional[int]:
    """Place corresponding follower order for spot trades"""
    try:
        coin_field = leader_order_data.get("coin", "")
        side = leader_order_data.get("side")  # "B" or "A"
        price = float(leader_order_data.get("limitPx", 0))

        if not is_spot_order(coin_field):
            return None

        # Get current asset info size decimals
        asset_info = await get_spot_asset_info(info, coin_field)
        if not asset_info:
            print(f"❌ Could not get asset info for {coin_field}")
            return None

        # Round to proper precision based on asset's size decimals
        order_size = round(FIXED_ORDER_VALUE_USDC / price, asset_info["szDecimals"])

        if order_size <= 0:
            print(f"❌ Invalid order size calculated for {coin_field}")
            return None

        is_buy = side == "B"

        print(
            f"🔄 Placing follower order: {'BUY' if is_buy else 'SELL'} {order_size} {coin_field} @ ${price}"
        )

        # Place the order
        result = exchange.order(
            name=coin_field,
            is_buy=is_buy,
            sz=order_size,
            limit_px=price,
            order_type=HLOrderType({"limit": {"tif": "Gtc"}}),
            reduce_only=False,
        )

        if result and result.get("status") == "ok":
            response_data = result.get("response", {}).get("data", {})
            statuses = response_data.get("statuses", [])

            if statuses:
                status_info = statuses[0]
                if "resting" in status_info:
                    follower_order_id = status_info["resting"]["oid"]
                    print(f"✅ Follower order placed! ID: {follower_order_id}")
                    return follower_order_id
                elif "filled" in status_info:
                    follower_order_id = status_info["filled"]["oid"]
                    print(
                        f"✅ Follower order filled immediately! ID: {follower_order_id}"
                    )
                    return follower_order_id

        print(f"❌ Failed to place follower order: {result}")
        return None

    except Exception as e:
        print(f"❌ Error placing follower order: {e}")
        return None


async def cancel_follower_order(
    exchange: Exchange, follower_order_id: int, coin_field: str
) -> bool:
    """Cancel follower order"""
    try:
        print("🔄 Cancelling follower order ID:", follower_order_id)

        result = exchange.cancel(name=coin_field, oid=follower_order_id)

        if result and result.get("status") == "ok":
            print("✅ Follower order cancelled successfully")
            return True
        else:
            print(f"❌ Failed to cancel follower order: {result}")
            return False

    except Exception as e:
        print(f"❌ Error cancelling follower order: {e}")
        return False


async def handle_leader_order_events(data: dict, exchange: Exchange, info: Info):
    """Process leader's order-related WebSocket events"""
    channel = data.get("channel")

    if channel == "orderUpdates":
        orders = data.get("data", [])
        for order_update in orders:
            order = order_update.get("order", {})
            status = order_update.get("status", "unknown")
            coin_field = order.get("coin", "")

            # Only process valid spot orders
            if not is_spot_order(coin_field):
                continue

            leader_order_id = order.get("oid")

            # Skip follower orders, but allow processing of known leader orders for cancellation/modification
            if leader_order_id in order_mappings.values():
                print(f"DEBUG: Skipping follower order {leader_order_id}:{status}")
                continue

            print(
                f"LEADER ORDER {status.upper()}: {order.get('side')} {order.get('sz')} {coin_field} @ {order.get('limitPx')} (ID: {leader_order_id})"
            )

            if status == "open":
                # New order - mirror it
                follower_order_id = await place_follower_order(exchange, info, order)
                if follower_order_id:
                    order_mappings[leader_order_id] = follower_order_id
                    print(f"Mapped {leader_order_id} -> {follower_order_id}")

            elif status == "canceled" and leader_order_id in order_mappings:
                follower_order_id = order_mappings[leader_order_id]
                if follower_order_id > 0:
                    await cancel_follower_order(exchange, follower_order_id, coin_field)
                del order_mappings[leader_order_id]

    elif channel == "user":
        user_data = data.get("data", {})

        # Handle fills - just log them
        for fill in user_data.get("fills", []):
            coin_field = fill.get("coin", "N/A")
            if is_spot_order(coin_field):
                fill_order_id = fill.get("oid")
                if fill_order_id and fill_order_id in order_mappings.values():
                    continue
                side = "BUY" if fill.get("side") == "B" else "SELL"
                print(
                    f"LEADER FILL: {side} {fill.get('sz')} {coin_field} @ {fill.get('px')}"
                )

    elif channel == "subscriptionResponse":
        print("✅ WebSocket subscription confirmed")


async def monitor_and_mirror_spot_orders():
    """Connect to WebSocket and monitor leader's spot order activity"""
    global running

    private_key = os.getenv("HYPERLIQUID_TESTNET_PRIVATE_KEY")
    if not private_key:
        print("❌ Missing HYPERLIQUID_TESTNET_PRIVATE_KEY in .env file")
        return

    # Initialize follower trading components
    try:
        wallet = Account.from_key(private_key)
        exchange = Exchange(wallet, BASE_URL)
        info = Info(BASE_URL, skip_ws=True)
        print(f"✅ Follower wallet initialized: {wallet.address}")
    except Exception as e:
        print(f"❌ Failed to initialize follower wallet: {e}")
        return

    print(f"🔗 Connecting to {WS_URL}")
    signal.signal(signal.SIGINT, signal_handler)

    try:
        async with websockets.connect(WS_URL) as websocket:
            print("✅ WebSocket connected!")

            # Subscribe to leader's order updates
            order_subscription = {
                "method": "subscribe",
                "subscription": {"type": "orderUpdates", "user": LEADER_ADDRESS},
            }

            # Subscribe to leader's user events (fills)
            events_subscription = {
                "method": "subscribe",
                "subscription": {"type": "userEvents", "user": LEADER_ADDRESS},
            }

            await websocket.send(json.dumps(order_subscription))
            await websocket.send(json.dumps(events_subscription))

            print(f"📊 Monitoring SPOT orders for leader: {LEADER_ADDRESS}")
            print(f"💰 Fixed order value: ${FIXED_ORDER_VALUE_USDC} USDC per order")
            print(f"👤 Follower wallet: {wallet.address}")
            print("=" * 80)

            running = True
            message_queue = asyncio.Queue()

            # Task to receive messages and put them in queue
            async def message_receiver():
                async for message in websocket:
                    if not running:
                        break
                    await message_queue.put(message)

            # Task to process messages one by one from queue
            async def message_processor():
                while running:
                    try:
                        # Wait for next message with timeout
                        message = await asyncio.wait_for(
                            message_queue.get(), timeout=1.0
                        )

                        try:
                            data = json.loads(message)

                            # print(f"RAW MESSAGE: {json.dumps(data, indent=2)}")
                            # print("-" * 40)

                            # Process message completely before moving to next
                            await handle_leader_order_events(data, exchange, info)
                        except json.JSONDecodeError:
                            print("⚠️ Received invalid JSON")
                        except Exception as e:
                            print(f"❌ Error processing message: {e}")
                        finally:
                            message_queue.task_done()

                    except asyncio.TimeoutError:
                        continue  # No message received, continue loop

            # Run both tasks concurrently
            await asyncio.gather(message_receiver(), message_processor())

    except websockets.exceptions.ConnectionClosed:
        print("🔌 WebSocket connection closed")
    except Exception as e:
        print(f"❌ WebSocket error: {e}")
    finally:
        print("👋 Disconnected")
        print(f"📊 Final order mappings: {len(order_mappings)} active")


async def main():
    print("Hyperliquid Spot Order Mirror")
    print("=" * 40)

    if not WS_URL or not BASE_URL:
        print("❌ Missing required environment variables:")
        print("   HYPERLIQUID_TESTNET_PUBLIC_WS_URL")
        print("   HYPERLIQUID_TESTNET_PUBLIC_BASE_URL")
        return

    if not LEADER_ADDRESS or LEADER_ADDRESS == "0x...":
        print("❌ Please set LEADER_ADDRESS in the script")
        return

    await monitor_and_mirror_spot_orders()


if __name__ == "__main__":
    asyncio.run(main())
