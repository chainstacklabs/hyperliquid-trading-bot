"""
Test script for cancelling TWAP orders to see how they appear in WebSocket.
TWAP order cancellation uses raw API calls since they're not in the SDK yet.
"""

import asyncio
import json
import os
from dotenv import load_dotenv
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils.signing import get_timestamp_ms, sign_l1_action

load_dotenv()

BASE_URL = os.getenv("HYPERLIQUID_TESTNET_PUBLIC_BASE_URL")
SYMBOL = "PURR/USDC"


async def cancel_twap_order():
    """Cancel a TWAP order using raw API"""
    print("Cancel TWAP Order Test")
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

        # Check for active TWAP orders
        print("🔍 Looking for active TWAP orders...")

        # Note: TWAP orders don't appear in regular open_orders() or frontend_open_orders()
        # In copy trading, you'd get the TWAP ID from WebSocket feed when tracking someone
        # For this test, we use the TWAP ID from place_twap_order.py output
        latest_twap_id = 8154  # Replace with actual TWAP ID you want to cancel
        coin = "PURR/USDC"  # Replace with actual coin symbol

        print(f"🎯 Attempting to cancel TWAP order:")
        print(f"   TWAP ID: {latest_twap_id}")
        print(f"   Coin: {coin}")

        # Get spot metadata to find the asset index
        spot_data = info.spot_meta_and_asset_ctxs()
        if len(spot_data) < 2:
            print("❌ Could not get spot metadata")
            return

        spot_meta = spot_data[0]
        target_pair = None

        # Find the matching asset
        for pair in spot_meta.get("universe", []):
            if pair.get("name") == coin:
                target_pair = pair
                break

        if not target_pair:
            print(f"❌ Could not find asset {coin} in spot universe")
            return

        asset_index = target_pair.get("index")
        asset_name = target_pair.get("name")

        print(
            f"💰 Asset: {asset_name} (#{asset_index}, spot ID: {10000 + asset_index})"
        )
        print(f"🔄 Cancelling TWAP order ID: {latest_twap_id}")

        # Prepare TWAP cancellation action
        twap_cancel_action = {
            "type": "twapCancel",
            "a": 10000 + asset_index,
            "t": latest_twap_id,
        }

        print("📋 TWAP cancel action:")
        print(json.dumps(twap_cancel_action, indent=2))

        # Sign and send TWAP cancellation
        try:
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

            print("📋 TWAP cancel result:")
            print(json.dumps(result, indent=2))

            if result and result.get("status") == "ok":
                response_data = result.get("response", {}).get("data", {})

                if response_data.get("status") == "success":
                    print(f"✅ TWAP order {latest_twap_id} cancelled successfully!")
                    print("🔍 Monitor this cancellation in your WebSocket stream")
                elif (
                    isinstance(response_data.get("status"), dict)
                    and "error" in response_data["status"]
                ):
                    error_msg = response_data["status"]["error"]
                    print(f"❌ TWAP cancel failed: {error_msg}")
                    if (
                        "never placed" in error_msg.lower()
                        or "already canceled" in error_msg.lower()
                    ):
                        print(
                            "💡 TWAP order may have already finished or been cancelled"
                        )
                else:
                    print(f"⚠️ Unexpected response: {response_data}")
            else:
                print(f"❌ TWAP cancel request failed: {result}")

        except Exception as api_error:
            print(f"❌ TWAP Cancel API Error: {api_error}")
            print("⚠️  TWAP cancellation may not be available on testnet")

    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    asyncio.run(cancel_twap_order())
