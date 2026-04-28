"""
Real-time price monitoring using WebSocket connections.
Demonstrates subscribing to live market data and handling price updates.
"""

import asyncio
import json
import os
import signal
from dotenv import load_dotenv
import websockets
from hyperliquid.info import Info

load_dotenv()

WS_URL = os.getenv("HYPERLIQUID_TESTNET_PUBLIC_WS_URL")
BASE_URL = os.getenv("HYPERLIQUID_TESTNET_CHAINSTACK_BASE_URL") or os.getenv(
    "HYPERLIQUID_TESTNET_PUBLIC_BASE_URL"
)
# HIP-3: optional builder-deployed dex name. Empty = main perp.
DEX = os.getenv("DEX", "")
# When DEX is set the universe is HIP-3-namespaced (e.g. "felix:CRCL"); show
# everything from that universe instead of the default majors list.
ASSETS_TO_TRACK = ["BTC", "ETH", "SOL", "DOGE", "AVAX"] if not DEX else None

# Global state for demo
prices = {}
id_to_symbol = {}
running = False


def signal_handler(signum, frame):
    """Handle Ctrl+C gracefully"""
    global running
    print("\nShutting down...")
    running = False


async def load_symbol_mapping():
    """Load mapping from asset IDs to symbols"""
    global id_to_symbol

    info = Info(BASE_URL, skip_ws=True)
    meta = info.meta(dex=DEX)

    for i, asset_info in enumerate(meta["universe"]):
        symbol = asset_info["name"]
        id_to_symbol[str(i)] = symbol

    print(f"Loaded {len(id_to_symbol)} asset mappings (dex={DEX or 'main'})")


async def handle_price_message(data):
    """Process price update messages"""
    global prices

    channel = data.get("channel")
    if channel == "allMids":
        # Get the mids data from the nested structure
        mids_data = data.get("data", {}).get("mids", {})

        # Update prices and show changes for tracked assets
        for asset_id_with_at, price_str in mids_data.items():
            # Remove @ prefix from asset ID
            asset_id = asset_id_with_at.lstrip("@")
            symbol = id_to_symbol.get(asset_id)

            if symbol and (ASSETS_TO_TRACK is None or symbol in ASSETS_TO_TRACK):
                try:
                    new_price = float(price_str)
                    old_price = prices.get(symbol)

                    # Store new price
                    prices[symbol] = new_price

                    if old_price is not None:
                        change = new_price - old_price
                        change_pct = (change / old_price) * 100 if old_price != 0 else 0

                        # Show all updates
                        direction = "📈" if change > 0 else "📉" if change < 0 else "➡️"
                        print(
                            f"{direction} {symbol}: ${new_price:,.2f} ({change_pct:+.2f}%)"
                        )
                    else:
                        # First price update
                        print(f"🔄 {symbol}: ${new_price:,.2f}")

                except (ValueError, TypeError):
                    continue

    elif channel == "subscriptionResponse":
        print("✅ Subscription confirmed")


async def monitor_prices():
    """Connect to WebSocket and monitor real-time prices"""
    global running

    print("🔗 Loading asset mappings...")
    await load_symbol_mapping()

    print(f"🔗 Connecting to {WS_URL}")

    signal.signal(signal.SIGINT, signal_handler)

    try:
        async with websockets.connect(WS_URL) as websocket:
            print("✅ WebSocket connected!")

            subscription = {"type": "allMids"}
            if DEX:
                subscription["dex"] = DEX
            subscribe_message = {"method": "subscribe", "subscription": subscription}

            await websocket.send(json.dumps(subscribe_message))
            label = ", ".join(ASSETS_TO_TRACK) if ASSETS_TO_TRACK else f"all {DEX} pairs"
            print(f"📊 Monitoring {label}")
            print("=" * 40)

            running = True

            # Listen for messages
            async for message in websocket:
                if not running:
                    break

                try:
                    data = json.loads(message)
                    await handle_price_message(data)

                except json.JSONDecodeError:
                    print("⚠️ Received invalid JSON")
                except Exception as e:
                    print(f"❌ Error: {e}")

    except websockets.exceptions.ConnectionClosed:
        print("🔌 WebSocket connection closed")
    except Exception as e:
        print(f"❌ WebSocket error: {e}")
    finally:
        print("👋 Disconnected")


async def main():
    print("Hyperliquid Real-time Price Monitor")
    print("=" * 40)

    if not WS_URL or not BASE_URL:
        print("❌ Missing environment variables")
        print(
            "Set Hyperliquid endpoints in your .env file"
        )
        return

    await monitor_prices()


if __name__ == "__main__":
    print("Starting WebSocket demo...")
    asyncio.run(main())
