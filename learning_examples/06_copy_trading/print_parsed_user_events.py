"""
Monitor all order activity from a leader wallet using WebSocket.
Shows real-time order placements, cancellations, and fills.
"""

import asyncio
import json
import os
import signal
from dotenv import load_dotenv
import websockets

load_dotenv()

WS_URL = os.getenv("HYPERLIQUID_TESTNET_PUBLIC_WS_URL")
LEADER_ADDRESS = os.getenv("TESTNET_WALLET_ADDRESS")


def detect_market_type(coin_field):
    """Detect market type from coin field"""
    if coin_field.startswith("@"):
        # @{index} format = SPOT trading
        result = "SPOT"
        return result
    elif "/" in coin_field:
        # Pairs like PURR/USDC = SPOT trading
        result = "SPOT"
        return result
    else:
        # Direct symbols like SOL, BTC, ETH = PERP trading
        result = "PERP"
        return result


def format_trade_data(data, data_type):
    """Format order or fill data for display"""
    if data_type == "order":
        order = data.get("order", {})
        coin_field = order.get("coin", "N/A")
        return {
            "asset": coin_field,
            "market_type": detect_market_type(coin_field),
            "side": "BUY" if order.get("side") == "B" else "SELL",
            "size": order.get("sz", "N/A"),
            "price": order.get("limitPx", "N/A"),
            "order_id": order.get("oid", "N/A"),
            "status": data.get("status", "unknown"),
        }
    elif data_type == "twap":
        state = data.get("state", {})
        status = data.get("status", {})
        coin_field = state.get("coin", "N/A")
        return {
            "asset": coin_field,
            "market_type": detect_market_type(coin_field),
            "side": "BUY" if state.get("side") == "B" else "SELL",
            "size": state.get("sz", "N/A"),
            "executed_size": state.get("executedSz", "0"),
            "executed_notional": state.get("executedNtl", "0"),
            "minutes": state.get("minutes", "N/A"),
            "status": status.get("status", "unknown"),
            "timestamp": state.get("timestamp", "N/A"),
            "reduce_only": state.get("reduceOnly", False),
            "randomize": state.get("randomize", False),
        }
    else:  # fill
        coin_field = data.get("coin", "N/A")
        return {
            "asset": coin_field,
            "market_type": detect_market_type(coin_field),
            "side": "BUY" if data.get("side") == "B" else "SELL",
            "size": data.get("sz", "N/A"),
            "price": data.get("px", "N/A"),
            "fee": data.get("fee", "N/A"),
            "pnl": data.get("closedPnl", "0"),
        }


async def handle_order_events(data):
    """Process order-related WebSocket events"""
    channel = data.get("channel")

    if channel == "orderUpdates":
        for order_update in data.get("data", []):
            info = format_trade_data(order_update, "order")
            # Skip filled orders - we get better fill info from user channel
            if info["status"] == "filled":
                continue

            # See https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint#query-order-status-by-oid-or-cloid
            status_emoji = {
                "open": "🟢",
                "filled": "✅",
                "canceled": "❌",
                "rejected": "🚫",
                "selfTradeCanceled": "🔄",
                "delistedCanceled": "📋",
                "scheduledCancel": "⏰",
                "tickRejected": "📏",
                "minTradeNtlRejected": "💸",
                "badAloPxRejected": "🎯",
                "iocCancelRejected": "⚠️",
                "marketOrderNoLiquidityRejected": "💧",
                "oracleRejected": "🔮",
                "insufficientSpotBalanceRejected": "💰",
            }.get(info["status"], "📋")
            print(
                f"{status_emoji} {info['status'].upper()}: {info['side']} {info['size']} {info['asset']} @ {info['price']} [{info['market_type']}] (ID: {info['order_id']})"
            )

    elif channel == "user":
        user_data = data.get("data", {})

        # Handle fills
        for fill in user_data.get("fills", []):
            info = format_trade_data(fill, "fill")
            pnl_text = f" | PnL: {info['pnl']}" if float(info["pnl"]) != 0 else ""
            print(
                f"💰 FILL: {info['side']} {info['size']} {info['asset']} @ {info['price']} [{info['market_type']}] (Fee: {info['fee']}){pnl_text}"
            )

        # Handle TWAP orders
        for twap_event in user_data.get("twapHistory", []):
            info = format_trade_data(twap_event, "twap")
            status_emoji = {
                "activated": "🔄",
                "terminated": "🛑",
                "completed": "✅",
                "canceled": "❌",
            }.get(info["status"], "📋")

            twap_details = f"Duration: {info['minutes']}min"
            if float(info["executed_size"]) > 0:
                twap_details += f" | Executed: {info['executed_size']}/{info['size']}"
            if info["reduce_only"]:
                twap_details += " | Reduce-Only"
            if info["randomize"]:
                twap_details += " | Randomized"

            print(
                f"{status_emoji} TWAP {info['status'].upper()}: {info['side']} {info['size']} {info['asset']} [{info['market_type']}] ({twap_details})"
            )

    elif channel == "subscriptionResponse":
        print("✅ Subscription confirmed")


async def unsubscribe_from_feeds(websocket, leader_address):
    """Unsubscribe from all feeds"""
    try:
        order_unsubscribe = {
            "method": "unsubscribe",
            "subscription": {"type": "orderUpdates", "user": leader_address},
        }

        events_unsubscribe = {
            "method": "unsubscribe",
            "subscription": {"type": "userEvents", "user": leader_address},
        }

        await websocket.send(json.dumps(order_unsubscribe))
        await websocket.send(json.dumps(events_unsubscribe))
        print("📤 Unsubscribed from all feeds")

    except Exception as e:
        print(f"⚠️ Unsubscribe failed: {e}")


async def ping_websocket(websocket):
    """Send ping every 30 seconds to keep connection alive"""
    try:
        while True:
            await asyncio.sleep(30)
            ping_message = {"method": "ping"}
            await websocket.send(json.dumps(ping_message))
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"⚠️ Ping failed: {e}")


async def monitor_leader_orders():
    """Connect to WebSocket and monitor leader's order activity"""
    if not LEADER_ADDRESS or LEADER_ADDRESS == "0x...":
        print("❌ Please set LEADER_ADDRESS in the script")
        return

    print(f"Connecting to {WS_URL}")

    shutdown_event = asyncio.Event()

    def signal_handler(signum, frame):
        del signum, frame
        print("\nShutting down...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)

    try:
        async with websockets.connect(WS_URL) as websocket:
            print("✅ WebSocket connected!")

            # Subscribe to order updates
            order_subscription = {
                "method": "subscribe",
                "subscription": {"type": "orderUpdates", "user": LEADER_ADDRESS},
            }

            # Subscribe to user events (fills)
            events_subscription = {
                "method": "subscribe",
                "subscription": {"type": "userEvents", "user": LEADER_ADDRESS},
            }

            await websocket.send(json.dumps(order_subscription))
            await websocket.send(json.dumps(events_subscription))

            print(f"Monitoring orders for: {LEADER_ADDRESS}")
            print("=" * 80)

            # Start ping task
            ping_task = asyncio.create_task(ping_websocket(websocket))

            try:
                async for message in websocket:
                    if shutdown_event.is_set():
                        break

                    try:
                        data = json.loads(message)

                        if data.get("channel") == "pong":
                            print(f"Ping response: {json.dumps(data)}")

                        # Print raw WebSocket messages for debugging
                        # if data.get("channel") in ["orderUpdates", "user"]:
                        # print(f"RAW MESSAGE: {json.dumps(data, indent=2)}")

                        await handle_order_events(data)
                    except json.JSONDecodeError:
                        print("⚠️ Received invalid JSON")
                    except Exception as e:
                        print(f"❌ Error: {e}")
            finally:
                ping_task.cancel()
                await unsubscribe_from_feeds(websocket, LEADER_ADDRESS)

    except websockets.exceptions.ConnectionClosed:
        print("WebSocket connection closed")
    except Exception as e:
        print(f"❌ WebSocket error: {e}")
    finally:
        print("Disconnected")


async def main():
    print("Hyperliquid Order Monitor")
    print("=" * 40)

    if not WS_URL:
        print("❌ Missing HYPERLIQUID_TESTNET_PUBLIC_WS_URL in .env file")
        return

    await monitor_leader_orders()


if __name__ == "__main__":
    asyncio.run(main())
