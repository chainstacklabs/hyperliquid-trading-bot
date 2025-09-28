"""
Mirror spot TWAP orders from a leader wallet with fixed sizing.
Monitors leader's spot TWAP orders and places corresponding TWAP orders for follower.
Handles TWAP placement, cancellation, and monitoring with real-time WebSocket monitoring.

TWAP tracking (for tests with same wallet as leader and follower):
- WebSocket events don't include TWAP IDs, only TWAP properties (coin, side, size, etc)
- Uses combination keys (coin_side_minutes_randomize_size) to detect duplicates
- Tracks leader vs follower combinations separately since follower adjusts size
- Prevents processing our own follower TWAPs as new leader orders
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
from hyperliquid.utils.signing import get_timestamp_ms, sign_l1_action, float_to_wire

load_dotenv()

# Configuration
WS_URL = os.getenv("HYPERLIQUID_TESTNET_PUBLIC_WS_URL")
BASE_URL = os.getenv("HYPERLIQUID_TESTNET_PUBLIC_BASE_URL")

# For tests, you can use the same wallet as a leader and follower.
# Follower's TWAPs will be ignored in the mirroring logic.
LEADER_ADDRESS = os.getenv("TESTNET_WALLET_ADDRESS")
FIXED_ORDER_VALUE_USDC = 60.0

running = False
leader_twap_combinations: set = set()      # Track processed leader TWAPs with size
follower_twap_combinations: set = set()    # Track our placed follower TWAPs with adjusted size
twap_mappings: Dict[str, int] = {}         # leader_combination -> follower_twap_id


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


def create_leader_twap_combination(state: dict) -> str:
    """Create leader TWAP combination ID with original size"""
    coin_field = state.get("coin", "")
    side = state.get("side")
    minutes = state.get("minutes", 1)
    randomize = state.get("randomize", False)
    size = state.get("sz", "0")

    return f"{coin_field}_{side}_{minutes}_{randomize}_{size}"


def create_follower_twap_combination(coin_field: str, side: str, minutes: int, randomize: bool, follower_size: float) -> str:
    """Create follower TWAP combination ID with adjusted size"""
    return f"{coin_field}_{side}_{minutes}_{randomize}_{follower_size}"


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


async def place_follower_twap_order(
    exchange: Exchange, info: Info, leader_twap_data: dict, leader_combination: str
) -> Optional[int]:
    """Place corresponding follower TWAP order for spot trades"""
    try:
        state = leader_twap_data.get("state", {})
        coin_field = state.get("coin", "")
        side = state.get("side")  # "B" or "A"
        minutes = state.get("minutes", 1)
        randomize = state.get("randomize", False)
        reduce_only = state.get("reduceOnly", False)

        if not is_spot_order(coin_field):
            return None

        # Get current asset info for proper order sizing
        asset_info = await get_spot_asset_info(info, coin_field)
        if not asset_info:
            print(f"❌ Could not get asset info for TWAP {coin_field}")
            return None

        # Calculate equivalent TWAP size based on fixed USDC value
        # Use current price to determine follower's size
        follower_total_size = round(
            FIXED_ORDER_VALUE_USDC / asset_info["price"], asset_info["szDecimals"]
        )

        if follower_total_size <= 0:
            print(f"❌ Invalid TWAP size calculated for {coin_field}")
            return None

        is_buy = side == "B"

        print(
            f"🔄 Placing follower TWAP: {'BUY' if is_buy else 'SELL'} {follower_total_size} {coin_field} over {minutes}min"
        )

        # Get spot metadata to find asset index
        try:
            if coin_field.startswith("@"):
                asset_index = int(coin_field[1:])
            elif "/" in coin_field:
                spot_meta = info.spot_meta()
                universe = spot_meta.get("universe", [])
                asset_index = None
                for pair_info in universe:
                    if pair_info.get("name") == coin_field:
                        asset_index = pair_info.get("index")
                        break
                if asset_index is None:
                    print(f"❌ Could not find asset index for {coin_field}")
                    return None
            else:
                print(f"❌ Unsupported coin format for TWAP: {coin_field}")
                return None

            # Prepare TWAP action
            twap_action = {
                "type": "twapOrder",
                "twap": {
                    "a": 10000 + asset_index,  # Asset
                    "b": is_buy,  # Buy/sell
                    "s": float_to_wire(follower_total_size),  # Size
                    "r": reduce_only,  # Reduce-only
                    "m": minutes,  # Minutes
                    "t": randomize,  # Randomize
                },
            }

            # Sign and send TWAP order
            timestamp = get_timestamp_ms()
            signature = sign_l1_action(
                exchange.wallet,
                twap_action,
                exchange.vault_address,
                timestamp,
                exchange.expires_after,
                False,
            )

            result = exchange._post_action(
                twap_action,
                signature,
                timestamp,
            )

            if result and result.get("status") == "ok":
                response_data = result.get("response", {}).get("data", {})
                status_info = response_data.get("status", {})

                if "running" in status_info:
                    follower_twap_id = status_info["running"]["twapId"]
                    print(f"✅ Follower TWAP placed! ID: {follower_twap_id}")

                    # Track the follower TWAP combination to filter it from future messages
                    follower_combination = create_follower_twap_combination(
                        coin_field, side, minutes, randomize, follower_total_size
                    )
                    follower_twap_combinations.add(follower_combination)
                    print(f"Tracking follower combination: {follower_combination}")

                    return follower_twap_id
                else:
                    print(f"⚠️ Unexpected TWAP status: {status_info}")

            print(f"❌ Failed to place follower TWAP: {result}")
            return None

        except Exception as e:
            print(f"❌ Error with TWAP asset lookup: {e}")
            return None

    except Exception as e:
        print(f"❌ Error placing follower TWAP: {e}")
        return None


async def cancel_follower_twap_order(
    exchange: Exchange, info: Info, follower_twap_id: int, coin_field: str
) -> bool:
    """Cancel follower TWAP order"""
    try:
        print("🔄 Cancelling follower TWAP ID:", follower_twap_id)

        # Get asset index for cancellation
        try:
            if coin_field.startswith("@"):
                asset_index = int(coin_field[1:])
            elif "/" in coin_field:
                spot_meta = info.spot_meta()
                universe = spot_meta.get("universe", [])
                asset_index = None
                for pair_info in universe:
                    if pair_info.get("name") == coin_field:
                        asset_index = pair_info.get("index")
                        break
                if asset_index is None:
                    print(f"❌ Could not find asset index for TWAP cancel {coin_field}")
                    return False
            else:
                print(f"❌ Unsupported coin format for TWAP cancel: {coin_field}")
                return False

            # Prepare TWAP cancellation action
            twap_cancel_action = {
                "type": "twapCancel",
                "a": 10000 + asset_index,
                "t": follower_twap_id,
            }

            # Sign and send TWAP cancellation
            timestamp = get_timestamp_ms()
            signature = sign_l1_action(
                exchange.wallet,
                twap_cancel_action,
                exchange.vault_address,
                timestamp,
                exchange.expires_after,
                False,
            )

            result = exchange._post_action(
                twap_cancel_action,
                signature,
                timestamp,
            )

            if result and result.get("status") == "ok":
                response_data = result.get("response", {}).get("data", {})
                if response_data.get("status") == "success":
                    print("✅ Follower TWAP cancelled successfully")
                    return True
                else:
                    error_msg = response_data.get("status", {}).get(
                        "error", "Unknown error"
                    )
                    print(f"❌ Failed to cancel follower TWAP: {error_msg}")
                    return False
            else:
                print(f"❌ Failed to cancel follower TWAP: {result}")
                return False

        except Exception as e:
            print(f"❌ Error with TWAP cancel asset lookup: {e}")
            return False

    except Exception as e:
        print(f"❌ Error cancelling follower TWAP: {e}")
        return False


async def handle_leader_twap_events(data: dict, exchange: Exchange, info: Info):
    """Process leader's TWAP-related WebSocket events"""
    channel = data.get("channel")

    if channel == "user":
        user_data = data.get("data", {})

        # Handle TWAP orders - mirror them
        for twap_event in user_data.get("twapHistory", []):
            state = twap_event.get("state", {})
            coin_field = state.get("coin", "")

            if not is_spot_order(coin_field):
                continue

            # Create TWAP combination ID with size for precise tracking
            leader_combination = create_leader_twap_combination(state)
            twap_status = twap_event.get("status", {}).get("status", "unknown")

            print(
                f"TWAP {twap_status.upper()}: {state.get('side')} {state.get('sz')} {coin_field} (Leader: {leader_combination})"
            )

            # Check if this is our own follower TWAP - skip processing
            # We need to create potential follower combination to check
            try:
                side = state.get("side")
                minutes = state.get("minutes", 1)
                randomize = state.get("randomize", False)
                current_size = float(state.get("sz", "0"))

                potential_follower_combination = create_follower_twap_combination(
                    coin_field, side, minutes, randomize, current_size
                )

                if potential_follower_combination in follower_twap_combinations:
                    print(f"DEBUG: Skipping our own follower TWAP: {potential_follower_combination}")
                    continue
            except (ValueError, TypeError):
                pass  # Continue processing if size conversion fails

            if twap_status == "activated":
                # Skip if we already processed this leader TWAP combination
                if leader_combination in leader_twap_combinations:
                    print(f"DEBUG: Skipping already processed leader TWAP: {leader_combination}")
                    continue

                # Mark this combination as processed to avoid duplicates
                leader_twap_combinations.add(leader_combination)

                # New TWAP order placed - attempt to mirror it
                try:
                    follower_twap_id = await place_follower_twap_order(
                        exchange, info, twap_event, leader_combination
                    )
                    if follower_twap_id:
                        twap_mappings[leader_combination] = follower_twap_id
                        print(f"Mapped leader TWAP {leader_combination} -> follower ID {follower_twap_id}")
                except Exception as e:
                    print(f"Error mirroring TWAP {leader_combination}: {e}")

            elif twap_status in ["canceled", "terminated"]:
                # TWAP cancelled/terminated - cancel corresponding follower TWAP
                if leader_combination in twap_mappings:
                    follower_twap_id = twap_mappings[leader_combination]
                    await cancel_follower_twap_order(
                        exchange, info, follower_twap_id, coin_field
                    )
                    del twap_mappings[leader_combination]
                    # Remove from processed combinations so it can be placed again later
                    leader_twap_combinations.discard(leader_combination)

                    # Also remove corresponding follower combination from tracking
                    # Find and remove the follower combination
                    to_remove = None
                    for follower_combo in follower_twap_combinations:
                        if follower_combo.startswith(f"{coin_field}_{state.get('side')}_{state.get('minutes')}_{state.get('randomize', False)}_"):
                            to_remove = follower_combo
                            break
                    if to_remove:
                        follower_twap_combinations.discard(to_remove)

    elif channel == "subscriptionResponse":
        print("✅ WebSocket subscription confirmed")


async def monitor_and_mirror_spot_twap_orders():
    """Connect to WebSocket and monitor leader's spot TWAP order activity"""
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

            # Subscribe to leader's user events (TWAP orders)
            events_subscription = {
                "method": "subscribe",
                "subscription": {"type": "userEvents", "user": LEADER_ADDRESS},
            }

            await websocket.send(json.dumps(events_subscription))

            print(f"📊 Monitoring SPOT TWAP orders for leader: {LEADER_ADDRESS}")
            print(f"💰 Fixed TWAP value: ${FIXED_ORDER_VALUE_USDC} USDC per TWAP")
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
                            await handle_leader_twap_events(data, exchange, info)
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
        print(f"📊 Final TWAP mappings: {len(twap_mappings)} active")
        print(f"📊 Leader combinations processed: {len(leader_twap_combinations)} total")
        print(f"📊 Follower combinations tracked: {len(follower_twap_combinations)} total")


async def main():
    print("Hyperliquid Spot TWAP Order Mirror")
    print("=" * 40)

    if not WS_URL or not BASE_URL:
        print("❌ Missing required environment variables:")
        print("   HYPERLIQUID_TESTNET_PUBLIC_WS_URL")
        print("   HYPERLIQUID_TESTNET_PUBLIC_BASE_URL")
        return

    if not LEADER_ADDRESS or LEADER_ADDRESS == "0x...":
        print("❌ Please set LEADER_ADDRESS in the script")
        return

    await monitor_and_mirror_spot_twap_orders()


if __name__ == "__main__":
    asyncio.run(main())
