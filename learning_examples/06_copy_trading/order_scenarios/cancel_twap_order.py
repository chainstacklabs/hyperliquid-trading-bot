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

load_dotenv()

BASE_URL = os.getenv("HYPERLIQUID_TESTNET_PUBLIC_BASE_URL")


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

        # For this demo, we'll prompt for TWAP ID or use a placeholder
        # In practice, you'd get this from the response of placing a TWAP order
        print("💡 This script demonstrates TWAP cancellation")
        print("💡 You need a valid TWAP ID from a previous TWAP order")

        # Allow user to input TWAP ID or use placeholder
        user_input = input("Enter TWAP ID to cancel (or press Enter for demo with ID 123): ").strip()
        if user_input:
            try:
                twap_id = int(user_input)
                print(f"🎯 Using TWAP ID: {twap_id}")
            except ValueError:
                print("❌ Invalid TWAP ID, using demo ID 123")
                twap_id = 123
        else:
            twap_id = 123
            print(f"⚠️  Using demo TWAP ID: {twap_id}")
            print("💡 This will likely fail unless you have a real TWAP order with this ID")

        # Get asset number (using PURR/USDC as example)
        spot_data = info.spot_meta_and_asset_ctxs()
        if len(spot_data) >= 2:
            spot_meta = spot_data[0]

            # Find PURR/USDC (or first available asset)
            target_pair = None
            for pair in spot_meta.get('universe', []):
                if pair.get('name') == "PURR/USDC":
                    target_pair = pair
                    break

            if not target_pair and spot_meta.get('universe'):
                # Use first available asset as fallback
                target_pair = spot_meta['universe'][0]

            if not target_pair:
                print("❌ Could not find any assets")
                return

            asset_number = target_pair.get('index')
            asset_name = target_pair.get('name')

            print(f"💰 Asset: {asset_name} (#{asset_number})")
            print(f"🔄 Cancelling TWAP order ID: {twap_id}")

            # Prepare TWAP cancellation using proper API action format
            from hyperliquid.utils.signing import get_timestamp_ms, sign_l1_action
            from hyperliquid.utils.constants import MAINNET_API_URL

            timestamp = get_timestamp_ms()
            twap_cancel_action = {
                "type": "twapCancel",
                "a": asset_number,
                "t": twap_id
            }

            print("📋 TWAP cancel action:")
            print(json.dumps(twap_cancel_action, indent=2))

            # Sign and send TWAP cancellation using the exchange API
            try:
                signature = sign_l1_action(
                    exchange.wallet,
                    twap_cancel_action,
                    exchange.vault_address,
                    timestamp,
                    exchange.expires_after,
                    exchange.base_url == MAINNET_API_URL,
                )

                result = exchange._post_action(
                    twap_cancel_action,
                    signature,
                    timestamp,
                )

                print(f"📋 TWAP cancel result:")
                print(json.dumps(result, indent=2))

                if result and result.get("status") == "ok":
                    response_data = result.get("response", {}).get("data", {})
                    print(f"✅ TWAP order {twap_id} cancelled successfully!")
                    print(f"🔍 Monitor this cancellation in your WebSocket stream")

                    if response_data:
                        print(f"📊 Response data: {response_data}")
                else:
                    print(f"❌ TWAP cancel failed: {result}")
                    if result and "not found" in str(result).lower():
                        print("💡 TWAP ID may not exist or may already be finished/cancelled")
                    elif result and "invalid" in str(result).lower():
                        print("💡 Check if the TWAP ID is valid and belongs to your account")

            except Exception as api_error:
                print(f"❌ TWAP Cancel API Error: {api_error}")
                print("⚠️  TWAP cancellation may not be available on testnet")
                print("💡 Try with a mainnet connection or valid TWAP ID if needed")

        else:
            print("❌ Could not get spot metadata")

    except Exception as e:
        print(f"❌ Error: {e}")
        print("💡 Note: TWAP orders may not be available on testnet or for all assets")


if __name__ == "__main__":
    asyncio.run(cancel_twap_order())