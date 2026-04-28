"""
Test script for placing TWAP orders to see how they appear in WebSocket.
TWAP orders use raw API calls since they're not in the SDK yet.
"""

import asyncio
import json
import os
from dotenv import load_dotenv

from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils.signing import get_timestamp_ms, sign_l1_action, float_to_wire


load_dotenv()

BASE_URL = os.getenv("HYPERLIQUID_TESTNET_PUBLIC_BASE_URL")
SYMBOL = "PURR/USDC"  # Spot pair to match place_order.py
ORDER_SIZE = 20.0  # Total size
TWAP_DURATION_MINUTES = 5  # 5 min duration


async def place_twap_order():
    """Place a TWAP order using raw API"""
    print("Place TWAP Order Test")
    print("=" * 40)

    private_key = os.getenv("HYPERLIQUID_TESTNET_PRIVATE_KEY")
    if not private_key:
        print("❌ Missing HYPERLIQUID_TESTNET_PRIVATE_KEY in .env file")
        return

    try:
        wallet = Account.from_key(private_key)
        exchange = Exchange(wallet, BASE_URL)
        info = Info(BASE_URL, skip_ws=True)

        master_address = os.getenv("TESTNET_WALLET_ADDRESS")
        if master_address and master_address.lower() != wallet.address.lower():
            print(f"📱 Signer (agent): {wallet.address}")
            print(f"🏦 Trades on master: {master_address}")
        else:
            print(f"📱 Wallet: {wallet.address}")

        # Get spot metadata to find the asset
        spot_data = info.spot_meta_and_asset_ctxs()
        if len(spot_data) >= 2:
            spot_meta = spot_data[0]

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

            print(f"💰 Asset: {SYMBOL} (#{pair_index}, spot ID: {10000 + pair_index})")
            print(
                f"📝 Placing TWAP BUY order: {ORDER_SIZE} {SYMBOL} over {TWAP_DURATION_MINUTES} minutes"
            )

            twap_action = {
                "type": "twapOrder",
                "twap": {
                    "a": 10000 + pair_index,  # Asset
                    "b": True,  # Buy/sell
                    "s": float_to_wire(
                        ORDER_SIZE
                    ),  # Use SDK's conversion to avoid trailing zeros
                    "r": False,  # Reduce-only
                    "m": TWAP_DURATION_MINUTES,  # Minutes
                    "t": False,  # Randomize
                },
            }

            print("📋 TWAP order action:")
            print(json.dumps(twap_action, indent=2))

            try:
                print(f"🔐 Sending TWAP order with wallet: {exchange.wallet.address}")

                # Use exchange's internal methods to sign and send the TWAP order
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

                print("📋 TWAP order result:")
                print(json.dumps(result, indent=2))

                if result and result.get("status") == "ok":
                    response_data = result.get("response", {}).get("data", {})
                    status_info = response_data.get("status", {})

                    if "running" in status_info:
                        twap_id = status_info["running"]["twapId"]
                        print(f"✅ TWAP order placed successfully! TWAP ID: {twap_id}")
                        print("🔍 Monitor this TWAP order in your WebSocket stream")
                        print(
                            f"⏱️  Order will execute over {TWAP_DURATION_MINUTES} minutes"
                        )
                    else:
                        print(f"⚠️ Unexpected TWAP status: {status_info}")
                else:
                    print(f"❌ TWAP order failed: {result}")
                    if (
                        result
                        and isinstance(result, dict)
                        and "Invalid TWAP duration" in str(result.get("response", ""))
                    ):
                        print(
                            "💡 Try increasing TWAP_DURATION_MINUTES (minimum may be required)"
                        )

            except Exception as api_error:
                import traceback

                print(f"❌ TWAP API Error: {str(api_error)}")
                print(f"❌ Error type: {type(api_error).__name__}")
                print("❌ Full traceback:")
                traceback.print_exc()
                print(
                    "⚠️  TWAP orders may not be available on testnet or for this asset"
                )
                print("💡 Try with a mainnet connection or different asset if needed")
        else:
            print("❌ Could not get spot metadata")

    except Exception as e:
        print(f"❌ Error: {e}")
        print("💡 Note: TWAP orders may not be available on testnet or for all assets")


if __name__ == "__main__":
    asyncio.run(place_twap_order())
